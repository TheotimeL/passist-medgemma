"""Policy decision tree: enrichment, loading, and evaluation.

Provides:
- enrich_tree(): post-process raw reconstruct_tree() output to add IDs
  derived from the tree structure (no hardcoded mappings).
- PolicyNode / CriterionResult dataclasses for typed tree manipulation.
- load_tree(), get_all_criteria(), evaluate(), get_status() for
  programmatic pass/fail evaluation of PA criteria.

All tree structure (drug expansion, node naming, logic gates) is expected
to come from LangExtract — this module only adds IDs and evaluation logic.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

class NodeType(str, Enum):
    ROOT = "ROOT"
    AND = "AND"
    OR = "OR"
    LEAF = "LEAF"


@dataclass
class PolicyNode:
    type: NodeType
    name: str = ""
    id: str = ""
    summary: str = ""
    source_text: str = ""
    negated: bool = False
    char_interval: dict = field(default_factory=dict)
    evidence_requirements: list[dict] = field(default_factory=list)
    children: list[PolicyNode] = field(default_factory=list)


@dataclass
class CriterionResult:
    criterion_id: str
    met: bool | None = None
    evidence: str = ""
    source_ref: str = ""


@dataclass
class PolicyStatus:
    overall: bool | None
    criteria: list[dict]
    met_count: int
    total_count: int
    pending_count: int


# ---------------------------------------------------------------------------
# Enrichment — operates on raw dicts from reconstruct_tree()
# ---------------------------------------------------------------------------

def _slug(name: str) -> str:
    """Convert a node name to a snake_case slug for ID generation."""
    return re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_')


def _assign_ids(node: dict, parent_path: str = "") -> None:
    """Recursively assign dot-notation IDs derived from the tree structure.

    IDs are built by concatenating slugified parent node names.
    For leaves, the slug of source_text (or summary) is appended.
    """
    if node.get("type") == "ROOT":
        for child in node.get("children", []):
            _assign_ids(child, parent_path)
        return

    node_name = node.get("name", "")

    if node.get("type") == "LEAF":
        # Use source_text as the leaf slug (falls back to summary)
        leaf_text = node.get("source_text", "") or node.get("summary", "")
        leaf_slug = _slug(leaf_text)
        # Truncate long slugs (e.g. full policy sentences) to first 60 chars
        if len(leaf_slug) > 60:
            leaf_slug = leaf_slug[:60].rstrip('_')
        node["id"] = f"{parent_path}.{leaf_slug}" if parent_path else leaf_slug
        return

    # Internal node: extend the path with this node's name
    current_path = parent_path
    if node_name:
        slug = _slug(node_name)
        current_path = f"{parent_path}.{slug}" if parent_path else slug

    # Assign ID to internal (AND/OR) nodes too
    if current_path:
        node["id"] = current_path

    for child in node.get("children", []):
        _assign_ids(child, current_path)


def enrich_tree(tree_dict: dict) -> dict:
    """Enrich a raw decision tree dict with auto-generated IDs.

    IDs are derived from the tree structure (node names → slugs).
    No hardcoded mappings — all structure comes from LangExtract.

    Modifies the dict in-place and returns it.
    Should be called on the output of reconstruct_tree() before saving to JSON.
    """
    _assign_ids(tree_dict)
    return tree_dict


# ---------------------------------------------------------------------------
# Loading & query
# ---------------------------------------------------------------------------

def _dict_to_node(d: dict) -> PolicyNode:
    """Recursively convert an enriched dict into PolicyNode objects."""
    children = [_dict_to_node(c) for c in d.get("children", [])]
    return PolicyNode(
        type=NodeType(d.get("type", "AND")),
        name=d.get("name", ""),
        id=d.get("id", ""),
        summary=d.get("summary", ""),
        source_text=d.get("source_text", ""),
        negated=d.get("negated", False),
        char_interval=d.get("char_interval", {}),
        evidence_requirements=d.get("evidence_requirements", []),
        children=children,
    )


def load_tree(path: str) -> PolicyNode:
    """Load an enriched JSON decision tree into a PolicyNode tree."""
    with open(path) as f:
        data = json.load(f)
    return _dict_to_node(data)


def get_all_criteria(tree: PolicyNode) -> dict[str, PolicyNode]:
    """Return a flat dict of {criterion_id: leaf_node} for all LEAFs."""
    result = {}
    if tree.type == NodeType.LEAF and tree.id:
        result[tree.id] = tree
    for child in tree.children:
        result.update(get_all_criteria(child))
    return result


def tree_to_dict(node: PolicyNode) -> dict:
    """Convert a PolicyNode tree back to a JSON-serializable dict."""
    d: dict = {"type": node.type.value}
    if node.name:
        d["name"] = node.name
    if node.id:
        d["id"] = node.id
    if node.summary:
        d["summary"] = node.summary
    if node.source_text:
        d["source_text"] = node.source_text
    if node.negated:
        d["negated"] = True
    if node.char_interval:
        d["char_interval"] = node.char_interval
    if node.evidence_requirements:
        d["evidence_requirements"] = node.evidence_requirements
    if node.children:
        d["children"] = [tree_to_dict(c) for c in node.children]
    return d


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def set_criterion(
    results: dict[str, CriterionResult],
    criterion_id: str,
    met: bool,
    evidence: str = "",
    source_ref: str = "",
) -> None:
    """Record a criterion evaluation result."""
    results[criterion_id] = CriterionResult(
        criterion_id=criterion_id,
        met=met,
        evidence=evidence,
        source_ref=source_ref,
    )


def _eval_and(child_results: list[bool | None]) -> bool | None:
    if any(r is False for r in child_results):
        return False
    if all(r is True for r in child_results):
        return True
    return None


def _eval_or(child_results: list[bool | None]) -> bool | None:
    if any(r is True for r in child_results):
        return True
    if all(r is False for r in child_results):
        return False
    return None


def evaluate(node: PolicyNode, results: dict[str, CriterionResult]) -> bool | None:
    """Recursively evaluate the tree against collected results.

    Returns True (pass), False (fail), or None (insufficient data).
    Respects the negated flag by inverting the subtree result.
    """
    if node.type == NodeType.LEAF:
        cr = results.get(node.id)
        if cr is None or cr.met is None:
            return None
        return cr.met

    child_results = [evaluate(c, results) for c in node.children]

    if node.type in (NodeType.ROOT, NodeType.AND):
        raw = _eval_and(child_results)
    elif node.type == NodeType.OR:
        raw = _eval_or(child_results)
    else:
        raw = None

    if raw is not None and node.negated:
        return not raw
    return raw


def get_status(tree: PolicyNode, results: dict[str, CriterionResult]) -> PolicyStatus:
    """Summarize all criteria statuses and the overall evaluation result."""
    all_criteria = get_all_criteria(tree)
    criteria_list = []
    met = 0
    pending = 0

    for cid, leaf in all_criteria.items():
        cr = results.get(cid)
        if cr is None or cr.met is None:
            status = "pending"
            pending += 1
        elif cr.met:
            status = "met"
            met += 1
        else:
            status = "not_met"

        criteria_list.append({
            "id": cid,
            "name": leaf.name or leaf.summary,
            "status": status,
            "evidence": cr.evidence if cr else "",
            "source_ref": cr.source_ref if cr else "",
        })

    overall = evaluate(tree, results)

    return PolicyStatus(
        overall=overall,
        criteria=criteria_list,
        met_count=met,
        total_count=len(all_criteria),
        pending_count=pending,
    )
