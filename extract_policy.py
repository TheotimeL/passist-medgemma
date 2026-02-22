"""Policy extraction pipeline - standalone script version of langextract_test.ipynb.

Reads the policy PDF, cleans text, extracts criteria with LangExtract + Gemini,
builds decision tree with search_description fields for downstream MedGemma use.

Requires LANGEXTRACT_API_KEY environment variable (Google AI API key).
"""

import os

if "LANGEXTRACT_API_KEY" not in os.environ:
    raise SystemExit("ERROR: LANGEXTRACT_API_KEY environment variable is required. Set it in .env or export it.")

import json
import logging
import re
import textwrap
import time
from pathlib import Path

import fitz
import google.generativeai as genai
import langextract as lx

logging.getLogger("absl").setLevel(logging.ERROR)

from policy_tree import enrich_tree

genai.configure(api_key=os.environ["LANGEXTRACT_API_KEY"])

# ── Config ──────────────────────────────────────────────────────────
POLICY_PDF = "UHC_Commercial_Medical_Policy_Adalimumab.pdf"
DISEASE_NAME = "Rheumatoid Arthritis"
DRUG_NAME = "Adalimumab"
AUTH_TYPE = "Initial Auth"

CLEANING_MODEL = "gemini-2.0-flash"
EXTRACTION_MODEL = "gemini-2.5-flash"
MAX_PDF_PAGES = 9999

def _slug(name):
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")

disease_slug = _slug(DISEASE_NAME)
auth_slug = _slug(AUTH_TYPE)
OUTPUT_CLEAN_TEXT = f"{disease_slug}_{auth_slug}_clean.txt"
OUTPUT_FLAT_JSONL = f"{disease_slug}_{auth_slug}_extractions.jsonl"
OUTPUT_TREE_JSON = f"{disease_slug}_{auth_slug}_decision_tree.json"
OUTPUT_HTML = f"{disease_slug}_{auth_slug}_visualization.html"

# ── Generic example for LangExtract ────────────────────────────────
GENERIC_EXAMPLE_TEXT = (
    "Initial Authorization requires all of the following criteria: "
    "(1) Diagnosis of moderate to severe [condition]. "
    "(2) One of the following: "
    "(a) History of an inadequate response to a 3-month trial of one conventional therapy "
    "[e.g., Drug A, Drug B, Drug C] at maximally tolerated doses "
    "(document drug, date, and duration of trial); "
    "(b) Patient has been previously treated with a targeted therapy "
    "[e.g., Medication X, Medication Y, Medication Z] "
    "as documented by claims history or submission of medical records "
    "(document drug, date, and duration of therapy); "
    "(c) Both of the following: "
    "i. Patient is currently receiving the requested medication "
    "as documented by claims history or submission of medical records "
    "(document date and duration of therapy); "
    "ii. Patient has not received a manufacturer-supplied sample at no cost. "
    "(3) Patient is not receiving the requested medication in combination with "
    "another targeted therapy [e.g., Medication X, Medication Y, Medication Z]. "
    "(4) Prescribed by or in consultation with a relevant specialist."
)

_EXAMPLE_AUTH = "Initial Authorization"

GENERIC_EXAMPLE_EXTRACTIONS = [
    lx.data.Extraction(
        extraction_class="LogicGate",
        extraction_text="all of the following criteria",
        attributes={
            "logic_path": json.dumps([f"{_EXAMPLE_AUTH} (AND)"]),
            "type": "LogicGate",
        },
    ),
    lx.data.Extraction(
        extraction_class="Criterion",
        extraction_text="(1) Diagnosis of moderate to severe [condition]",
        attributes={
            "logic_path": json.dumps([f"{_EXAMPLE_AUTH} (AND)", "Diagnosis"]),
            "type": "Mandatory",
            "search_description": "Patient has a confirmed diagnosis of moderate to severe [condition] (look for diagnosis, disease status, severity level)",
        },
    ),
    lx.data.Extraction(
        extraction_class="LogicGate",
        extraction_text="(2) One of the following:",
        attributes={
            "logic_path": json.dumps([f"{_EXAMPLE_AUTH} (AND)", "Step Therapy (OR)"]),
            "type": "LogicGate",
        },
    ),
    lx.data.Extraction(
        extraction_class="Criterion",
        extraction_text="(a) History of an inadequate response to a 3-month trial of one conventional therapy [e.g., Drug A, Drug B, Drug C] at maximally tolerated doses (document drug, date, and duration of trial)",
        attributes={
            "logic_path": json.dumps([f"{_EXAMPLE_AUTH} (AND)", "Step Therapy (OR)"]),
            "type": "Criterion",
            "search_description": "Patient has failed at least one conventional therapy from: Drug A, Drug B, Drug C. Look for any of these drugs in medication history as PRESCRIBED or STARTED, with evidence of inadequate response, side effects, or discontinuation. A recommendation to 'consider' a drug does NOT count — the patient must have actually taken it and it must have failed.",
        },
    ),
    lx.data.Extraction(
        extraction_class="Criterion",
        extraction_text="(b) Patient has been previously treated with a targeted therapy [e.g., Medication X, Medication Y, Medication Z] as documented by claims history or submission of medical records (document drug, date, and duration of therapy)",
        attributes={
            "logic_path": json.dumps([f"{_EXAMPLE_AUTH} (AND)", "Step Therapy (OR)"]),
            "type": "Criterion",
            "search_description": "Patient was previously treated with at least one targeted therapy from: Medication X, Medication Y, Medication Z. Look for any of these in past medications, treatment history, or claims records with dates and duration of therapy.",
        },
    ),
    lx.data.Extraction(
        extraction_class="LogicGate",
        extraction_text="(c) Both of the following:",
        attributes={
            "logic_path": json.dumps([f"{_EXAMPLE_AUTH} (AND)", "Step Therapy (OR)", "Current User (AND)"]),
            "type": "LogicGate",
        },
    ),
    lx.data.Extraction(
        extraction_class="EvidenceRequirement",
        extraction_text="as documented by claims history or submission of medical records (document date and duration of therapy)",
        attributes={
            "logic_path": json.dumps([f"{_EXAMPLE_AUTH} (AND)", "Step Therapy (OR)", "Current User (AND)", "i. Patient is currently receiving the requested medication"]),
            "type": "EvidenceRequirement",
        },
    ),
    lx.data.Extraction(
        extraction_class="Criterion",
        extraction_text="i. Patient is currently receiving the requested medication as documented by claims history or submission of medical records",
        attributes={
            "logic_path": json.dumps([f"{_EXAMPLE_AUTH} (AND)", "Step Therapy (OR)", "Current User (AND)"]),
            "type": "SubRequirement",
            "search_description": "Patient is currently taking or prescribed the requested medication (look for active prescriptions, current medications list, or recent dispensing records)",
        },
    ),
    lx.data.Extraction(
        extraction_class="Criterion",
        extraction_text="ii. Patient has not received a manufacturer-supplied sample at no cost",
        attributes={
            "logic_path": json.dumps([f"{_EXAMPLE_AUTH} (AND)", "Step Therapy (OR)", "Current User (AND)"]),
            "type": "SubRequirement",
            "search_description": "Patient has NOT received free drug samples or manufacturer assistance (met if there is NO mention of free samples or manufacturer programs)",
        },
    ),
    lx.data.Extraction(
        extraction_class="Criterion",
        extraction_text="(3) Patient is not receiving the requested medication in combination with another targeted therapy [e.g., Medication X, Medication Y, Medication Z]",
        attributes={
            "logic_path": json.dumps([f"{_EXAMPLE_AUTH} (AND)", "Combination Therapy"]),
            "type": "Mandatory",
            "negated": "true",
            "search_description": "Patient is NOT taking another targeted therapy at the same time (met if there is NO mention of concurrent targeted therapy use)",
        },
    ),
    lx.data.Extraction(
        extraction_class="Criterion",
        extraction_text="(4) Prescribed by or in consultation with a relevant specialist",
        attributes={
            "logic_path": json.dumps([f"{_EXAMPLE_AUTH} (AND)", "Prescriber"]),
            "type": "Mandatory",
            "search_description": "A relevant specialist is involved in prescribing (look for specialist name, title, department, or consultation notes in signatures or headers)",
        },
    ),
]

# ── Utility functions ──────────────────────────────────────────────

def read_pdf(pdf_path, max_pages):
    if not Path(pdf_path).exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")
    doc = fitz.open(pdf_path)
    return "".join([page.get_text() for page in doc[:max_pages]])


def _strip_markdown_fences(text):
    lines = text.split("\n")
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _normalize_whitespace(text):
    text = text.replace("\u2018", "'").replace("\u2019", "'")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)
    text = re.sub(r" +", " ", text)
    return text.strip()


def clean_policy_text(raw_text, disease_name, model_id):
    print(f"\nCleaning policy text for {disease_name}...")
    model = genai.GenerativeModel(model_id)
    prompt = textwrap.dedent(f"""
        Clean this raw PDF text for extraction.

        TASKS:
        1. Isolate the \"{disease_name}\" section ONLY.
           - Start at \"Initial Authorization\" or the disease name
           - Stop before the next disease or section
        2. Remove all footers, page numbers, headers, and copyrights.
        3. MERGE sentences broken by page breaks.
        4. PRESERVE the numbering structure ((1), (2), (a), (b), i, ii...)
           exactly as written — it is vital for logic extraction.
        5. Output ONLY the cleaned plain text. Do NOT wrap in markdown code fences.

        RAW TEXT:
        {raw_text}
    """)
    response = model.generate_content(prompt)
    cleaned = _strip_markdown_fences(response.text.strip())
    if not cleaned:
        raise ValueError(f"Cleaning returned empty text for {disease_name}")
    return cleaned


def extract_criteria(clean_text, disease_name, drug_name, auth_type, model_id):
    print(f"\nExtracting criteria for {drug_name} ({disease_name}) - {auth_type}...")

    prompt = textwrap.dedent(f"""
        Extract Prior Authorization approval criteria from a medical policy document.

        FOCUS: Extract ONLY "{auth_type}" criteria. Ignore Reauthorization or other sections.

        EXTRACTION CLASSES:
        - "LogicGate": Phrases defining logical structure ("all of the following", "one of the following", "both of the following").
        - "Criterion": Individual requirements, sub-requirements, or drug names.
        - "EvidenceRequirement": Documentation requirements that specify what must be submitted to justify a criterion.

        ATTRIBUTES (required for each extraction):
        - type: One of "LogicGate", "Mandatory", "SubRequirement", "Drug", or "EvidenceRequirement"
        - logic_path: JSON array of strings representing the path in the decision tree.
          Use (AND) or (OR) suffixes to indicate logic type.
          For EvidenceRequirement, include the specific criterion text as the last path segment
          when the requirement applies to a specific criterion rather than the whole group.
        - search_description (REQUIRED for Criterion and Drug only, NOT for LogicGate or EvidenceRequirement):
          A plain-English sentence describing what to look for in a clinical document to determine
          if this criterion is met. This should be written for a clinical note reviewer who needs
          to find evidence. Include:
          * What specifically to search for (drug names, diagnosis terms, specialist titles, etc.)
          * Where in the document it might appear (medication list, active problems, signatures, etc.)
          * For negated criteria: explain that it is met when there is NO mention
          * For drug criteria with a drug list: include ALL drug names in the search_description so
            the reviewer knows which drugs to check for. The patient must have ACTUALLY TAKEN at least
            one of the listed drugs, not just been recommended to take it.
          * Use simple, direct language. Example: "Patient has failed at least one of: Drug A, Drug B.
            Look for any of these in medication history as PRESCRIBED or STARTED, with evidence of
            inadequate response, side effects, or discontinuation."

        RULES:
        1. extraction_text MUST be a single string copied verbatim from the source text. NEVER return a list or array.
        2. When a criterion lists drugs in brackets [e.g., drug1, drug2], keep it as a SINGLE criterion
           with the full drug list preserved in the extraction_text and search_description.
           Do NOT expand into individual drug entries. The drug list is part of the criterion — the
           evidence reviewer will check which specific drugs from the list apply.
           Include ALL drug names in the search_description so the reviewer knows what to look for.
        3. Root logic is always AND: "{auth_type} (AND)"
        4. "One of the following" = OR, "Both of the following" = AND, "All of the following" = AND
        5. For negated criteria (e.g., "not receiving"), set negated="true" in attributes.
        6. Extract EvidenceRequirement entries for any criterion that specifies what documentation is needed.

        Policy: {disease_name} - {auth_type}
        Drug: {drug_name}
    """)

    examples = [lx.data.ExampleData(text=GENERIC_EXAMPLE_TEXT, extractions=GENERIC_EXAMPLE_EXTRACTIONS)]
    normalized_text = _normalize_whitespace(clean_text)

    max_retries = 3
    last_error = None
    for attempt in range(max_retries):
        try:
            result = lx.extract(
                text_or_documents=normalized_text,
                prompt_description=prompt,
                examples=examples,
                model_id=model_id,
                extraction_passes=1,
                max_char_buffer=15000,
                max_workers=4,
                use_schema_constraints=False,
            )
            if not result.extractions:
                raise ValueError(f"No extractions returned for {disease_name} - {auth_type}")
            return result
        except (ValueError, RuntimeError) as e:
            last_error = e
            print(f"   Attempt {attempt + 1}/{max_retries} failed: {e}")
            if attempt < max_retries - 1:
                time.sleep(2)

    raise RuntimeError(f"Extraction failed after {max_retries} attempts: {last_error}")


def _clean_segment_name(segment):
    return segment.replace("(AND)", "").replace("(OR)", "").strip()

def _get_node_type(segment):
    return "OR" if "(OR)" in segment else "AND"

def _get_char_interval(item):
    ci = getattr(item, "char_interval", None)
    if ci is None:
        return {}
    if isinstance(ci, dict):
        return ci
    return {"start_pos": getattr(ci, "start_pos", None), "end_pos": getattr(ci, "end_pos", None)}


def reconstruct_tree(extractions, disease_name):
    tree_root = {"name": f"{disease_name} Policy", "type": "ROOT", "children": []}
    if not extractions:
        return tree_root

    for item in extractions:
        item_attrs = getattr(item, "attributes", {})
        path_raw = item_attrs.get("logic_path", ["Uncategorized"])
        if isinstance(path_raw, str):
            try:
                path = json.loads(path_raw)
            except (json.JSONDecodeError, ValueError):
                path = [path_raw]
        else:
            path = path_raw

        item_type = item_attrs.get("type", "Criterion")

        current_node_list = tree_root["children"]
        parent_node = None

        for segment in path:
            clean_name = _clean_segment_name(segment)
            node_type = _get_node_type(segment)
            found_node = next((n for n in current_node_list if n.get("name") == clean_name), None)
            if not found_node:
                found_node = {
                    "name": clean_name, "type": node_type, "children": [],
                    "id": f"node_{clean_name.lower().replace(' ', '_')}",
                }
                current_node_list.append(found_node)
            # Ensure the node has children (might be a LEAF being reused as internal)
            if "children" not in found_node:
                found_node["children"] = []
            parent_node = found_node
            current_node_list = found_node["children"]

        if item_type == "LogicGate":
            parent_node["source_text"] = item.extraction_text
            parent_node["char_interval"] = _get_char_interval(item)
        elif item_type == "EvidenceRequirement":
            if "evidence_requirements" not in parent_node:
                parent_node["evidence_requirements"] = []
            parent_node["evidence_requirements"].append({
                "source_text": item.extraction_text,
                "char_interval": _get_char_interval(item),
            })
        else:
            negated = str(item_attrs.get("negated", "false")).lower() == "true"
            leaf = {
                "name": item.extraction_text[:50],
                "type": "LEAF",
                "source_text": item.extraction_text,
                "char_interval": _get_char_interval(item),
                "negated": negated,
                "id": f"leaf_{getattr(item, 'extraction_index', 'u')}",
            }
            search_desc = item_attrs.get("search_description", "")
            if search_desc:
                leaf["search_description"] = search_desc
            parent_node["children"].append(leaf)

    return tree_root


def print_tree(node, indent="", is_last=True):
    if not isinstance(node, dict):
        return
    marker = "└── " if is_last else "├── "
    node_type = node.get("type", "LEAF")
    name = node.get("name", "Unnamed")

    if node_type == "LEAF":
        leaf_id = node.get("id", "")
        id_str = f" [{leaf_id}]" if leaf_id else ""
        print(f"{indent}{marker} {name[:80]}{id_str}")
        source = node.get("source_text", "")
        if source:
            print(f"{indent}    Source: {source[:100]}...")
        search_desc = node.get("search_description", "")
        if search_desc:
            print(f"{indent}    Search: {search_desc[:120]}...")
    else:
        node_id = node.get("id", "")
        id_str = f" [{node_id}]" if node_id else ""
        source = node.get("source_text", "")
        source_str = ""
        if source:
            source_str = f"\n{indent}    Source: {source[:100]}..."
        print(f"{indent}{marker}{node_type}: {name}{id_str}{source_str}")

    children = node.get("children", [])
    for i, child in enumerate(children):
        new_indent = indent + ("    " if is_last else "│   ")
        print_tree(child, new_indent, i == len(children) - 1)


# ── Clinical Translation / Evidence Signatures ───────────────────
def clinically_enrich_tree(tree_path: str, output_path: str | None = None, model_id: str = "gemini-2.5-flash") -> dict:
    """Enrich a policy tree with clinical translations using Gemini.

    For each LEAF criterion, Gemini generates:
    - search_description: Clinically-translated guidance for what to look for in a note
    - keywords: Terms that SHOULD appear in valid evidence
    - anti_keywords: Terms that should DISQUALIFY evidence

    This is a scalable approach — works for any policy tree, no hardcoding.
    """
    with open(tree_path) as f:
        tree = json.load(f)

    # Collect all leaf criteria
    leaves = []
    def _collect_leaves(node, path=""):
        if node.get("type") == "LEAF":
            leaves.append({"node": node, "path": path})
            return
        for child in node.get("children", []):
            child_path = f"{path} > {child.get('name', '')}" if path else child.get("name", "")
            _collect_leaves(child, child_path)
    _collect_leaves(tree)

    if not leaves:
        print("No leaf criteria found!")
        return tree

    # Build batch prompt for Gemini
    criteria_descriptions = []
    for i, leaf_info in enumerate(leaves):
        node = leaf_info["node"]
        criteria_descriptions.append(
            f"CRITERION {i+1}:\n"
            f"  ID: {node.get('id', 'unknown')}\n"
            f"  Policy text: {node.get('source_text', '')}\n"
            f"  Tree path: {leaf_info['path']}\n"
            f"  Negated: {node.get('negated', False)}\n"
            f"  Current search_description: {node.get('search_description', 'NONE')}"
        )

    batch_text = "\n\n".join(criteria_descriptions)

    prompt = textwrap.dedent(f"""
        You are a clinical informatics expert. Your task is to create "Evidence Signatures"
        for insurance prior authorization criteria.

        For each criterion below, generate a clinically-translated search description that a
        medical AI model can use to find evidence in clinical notes (SOAP notes, progress notes, etc).

        For each criterion, output a JSON object with:
        1. "id": The criterion ID (copy exactly)
        2. "search_description": A detailed plain-English description of what to look for in a
           clinical note. Include:
           - Clinical synonyms and equivalent phrasings a clinician might use
           - Where in the note this evidence typically appears (medications list, assessment, plan, etc)
           - For drug criteria: what constitutes "failure" (discontinuation, adverse effects,
             inadequate response) vs what does NOT count (currently taking, plan to start)
           - For specialist criteria: which specialties qualify and which do NOT
           - For negated criteria: explain that absence of mention = criterion met
        3. "keywords": Array of 3-8 key terms that SHOULD appear in valid evidence for this criterion.
           Use lowercase. Include both brand names and generic names for drugs.
           Also include standard clinical abbreviations that clinicians commonly use
           in SOAP notes and progress notes.
        4. "anti_keywords": Array of terms that should make evidence SUSPECT for this criterion.
           For example, for a "rheumatologist" criterion, anti_keywords might include specialties
           that are NOT rheumatology (e.g., "internal medicine", "family medicine", "primary care").
           For drug failure criteria, anti_keywords might include phrases suggesting the drug
           was NOT actually taken.

        IMPORTANT GUIDELINES:
        - Be medically precise. When a criterion requires a specific specialist type, that
          specialist is exactly what it says — not a general practitioner or unrelated specialty.
          For example, if the policy requires a specific specialist, a general internist or
          family medicine physician does NOT qualify unless an explicit consultation with the
          required specialist is documented.
        - For drug criteria that list multiple drugs: keywords MUST include ALL drug names
          (both brand and generic names) from the list. Evidence must mention at least ONE of
          these drugs by name. Generic failure terms like "failed", "discontinued", "intolerant"
          should come AFTER the drug-specific names in the keywords array.
        - For drug criteria under "failure" groups: the patient must have ACTUALLY TAKEN at least
          one drug from the list AND it must have failed. Currently taking it successfully is NOT failure.
          CRITICAL: A drug discontinued because the disease went into REMISSION is NOT a drug failure.
          "Discontinued due to remission" means the treatment SUCCEEDED. Anti_keywords for drug failure
          criteria MUST include remission-related terms: "remission", "resolved", "in remission",
          "disease controlled", "successful treatment".
          IMPORTANT: Anti_keywords for drug failure must indicate CURRENT use (e.g., "currently on [drug]",
          "currently taking [drug]"). Do NOT use bare phrases like "on [drug]" or "[drug] started"
          because those also appear in legitimate failure narratives (e.g., "started on methotrexate
          in 2018, discontinued due to toxicity"). Always prefix with "currently" for current-use patterns.
          ALSO CRITICAL: Anti_keywords for drug failure criteria MUST include drug INITIATION patterns
          — imperative forms indicating a drug is being NEWLY PRESCRIBED, not previously tried and
          failed. For each drug in the criterion, include: "start [drug]", "initiate [drug]"
          (e.g., "start methotrexate", "initiate leflunomide", "start sulfasalazine").
          Also include general initiation phrases: "first-line DMARD", "first-line therapy",
          "must be trialed", "never been prescribed", "never treated with".
          These imperative forms ("start X") are naturally distinct from past-tense failure
          narratives ("started X in 2018, discontinued") and will not cause false rejections.
        - For diagnosis criteria with SEVERITY THRESHOLDS (e.g., "moderate to severe",
          "moderately to severely active"): anti_keywords MUST comprehensively cover ALL lower
          severity levels and states that do NOT meet the required threshold. Include:
          * Low activity terms: "low disease activity", "minimal disease activity", "low activity score"
          * Near-remission terms: "near remission", "approaching remission"
          * Mild terms: "mild", "mildly active"
          * Controlled terms: "well controlled", "adequately controlled", "stable on current therapy"
          * Remission terms: "remission", "in remission", "resolved", "inactive", "no active disease",
            "drug-free remission", "sustained remission"
        - For specialist/prescriber criteria: anti_keywords MUST include ALL common non-qualifying
          provider types and specialties (e.g., general practice, family medicine, internal medicine,
          and any unrelated specialties). Also include non-physician provider types that typically
          cannot independently prescribe the requested therapy (e.g., "physician assistant",
          "nurse practitioner") unless they are working under or in consultation with the required
          specialist.
        - For "currently on therapy" criteria: evidence must show ACTIVE use, not just a plan to start.
          Anti_keywords MUST include initiation language indicating the drug is being newly prescribed:
          "initiate [drug]", "start [drug]", "begin [drug]" for the specific drug,
          plus general terms: "plan to initiate", "will start", "pending initiation", "pending authorization".
        - Keep keywords focused on the SPECIFIC criterion, not generic clinical terms.
        - Anti_keywords should flag common false positive patterns.
        - CRITICAL: All anti_keywords MUST be multi-word phrases (at least 2 words). NEVER use
          single-word abbreviations like "fm", "im", "gp", "np", "pa" — these cause false
          substring matches in clinical text (e.g., "im" matches inside "Kim", "pa" matches
          inside "Patel"). Instead, always use the full descriptive phrase:
          "family medicine" instead of "fm", "internal medicine" instead of "im",
          "general practitioner" instead of "gp", "nurse practitioner" instead of "np",
          "physician assistant" instead of "pa".
        - Include multi-word phrases as anti_keywords when single words are ambiguous
          (e.g., "in remission" is more specific than just "remission").

        Output a JSON array of objects, one per criterion. No markdown fences.

        CRITERIA:
        {batch_text}
    """)

    print(f"\nEnriching {len(leaves)} criteria with clinical translations...")
    model = genai.GenerativeModel(model_id)
    response = model.generate_content(prompt)
    raw_text = response.text.strip()

    # Parse response
    raw_text = _strip_markdown_fences(raw_text)
    try:
        enrichments = json.loads(raw_text)
    except json.JSONDecodeError:
        # Try to find JSON array in response
        match = re.search(r'\[.*\]', raw_text, re.DOTALL)
        if match:
            enrichments = json.loads(match.group(0))
        else:
            print("ERROR: Could not parse Gemini response!")
            print(raw_text[:1000])
            return tree

    # Apply enrichments back to tree
    enrichment_map = {e["id"]: e for e in enrichments}
    applied = 0
    for leaf_info in leaves:
        node = leaf_info["node"]
        cid = node.get("id", "")
        if cid in enrichment_map:
            e = enrichment_map[cid]
            node["search_description"] = e.get("search_description", node.get("search_description", ""))
            if e.get("keywords"):
                node["keywords"] = e["keywords"]
            if e.get("anti_keywords"):
                node["anti_keywords"] = e["anti_keywords"]
            applied += 1

    print(f"  Applied enrichments to {applied}/{len(leaves)} criteria")

    # Save enriched tree
    out = output_path or tree_path.replace(".json", "_enriched.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(tree, f, indent=2, ensure_ascii=False)
    print(f"  Saved enriched tree: {out}")

    return tree


# ── Run pipeline ──────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--enrich-only", type=str, default=None,
                    help="Skip extraction, only enrich an existing tree JSON with clinical translations")
    ap.add_argument("--enrich-output", type=str, default=None,
                    help="Output path for enriched tree (default: <input>_enriched.json)")
    cli_args = ap.parse_args()

    if cli_args.enrich_only:
        clinically_enrich_tree(cli_args.enrich_only, cli_args.enrich_output)
        print("\nDone!")
        exit(0)

    print(f"Policy: {DISEASE_NAME} / {DRUG_NAME} / {AUTH_TYPE}")
    print(f"PDF: {POLICY_PDF}")

    # Step 1: Read PDF
    print(f"\nReading PDF: {POLICY_PDF}...")
    raw_text = read_pdf(POLICY_PDF, MAX_PDF_PAGES)
    print(f"  Loaded {len(raw_text)} characters")

    # Step 2: Clean text
    clean_text = clean_policy_text(raw_text, DISEASE_NAME, CLEANING_MODEL)
    print(f"  Cleaned to {len(clean_text)} characters")
    with open(OUTPUT_CLEAN_TEXT, "w", encoding="utf-8") as f:
        f.write(clean_text)
    print(f"  Saved: {OUTPUT_CLEAN_TEXT}")

    # Step 3: Extract criteria with LangExtract
    result = extract_criteria(clean_text, DISEASE_NAME, DRUG_NAME, AUTH_TYPE, EXTRACTION_MODEL)
    print(f"  Extracted {len(result.extractions)} items")

    # Step 4: Save flat JSONL
    lx.io.save_annotated_documents([result], output_name=OUTPUT_FLAT_JSONL, output_dir=".")
    print(f"  Saved: {OUTPUT_FLAT_JSONL}")

    # Step 5: Build and enrich tree
    tree = reconstruct_tree(result.extractions, f"{DISEASE_NAME} - {AUTH_TYPE}")
    tree = enrich_tree(tree)
    with open(OUTPUT_TREE_JSON, "w", encoding="utf-8") as f:
        json.dump(tree, f, indent=2, ensure_ascii=False)
    print(f"  Saved: {OUTPUT_TREE_JSON}")

    # Step 6: Pretty-print tree
    print(f"\n{'='*80}")
    print(f"DECISION TREE: {DISEASE_NAME} / {DRUG_NAME} / {AUTH_TYPE}")
    print(f"{'='*80}")
    print_tree(tree)

    # Step 7: Verify search_descriptions
    from policy_tree import load_tree, get_all_criteria
    tree_node = load_tree(OUTPUT_TREE_JSON)
    all_criteria = get_all_criteria(tree_node)
    print(f"\n{'='*80}")
    print(f"SEARCH DESCRIPTIONS ({len(all_criteria)} criteria)")
    print(f"{'='*80}")
    missing = 0
    for cid, leaf in all_criteria.items():
        desc = leaf.search_description
        if desc:
            print(f"  {cid}")
            print(f"    -> {desc[:150]}")
        else:
            print(f"  {cid}")
            print(f"    -> [MISSING search_description]  source: {leaf.source_text[:80]}")
            missing += 1

    if missing:
        print(f"\nWARNING: {missing}/{len(all_criteria)} criteria missing search_description!")
    else:
        print(f"\nAll {len(all_criteria)} criteria have search_descriptions.")

    print("\nDone!")
