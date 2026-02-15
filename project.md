[The MedGemma Impact Challenge](https://www.kaggle.com/competitions/med-gemma-impact-challenge)

| Idea | What is solved ?  | What is the input ?  | What is the output ?  | Challenges? |
| --- | --- | --- | --- | --- |
| Multimodal Evidence-Linked Agent for Prior Authorization | Social security / insurance paperwork for prior authorization of therapies. 
Address trust issues by providing source linking and human review step.  | Patient folder / notes | 1. Pre-filled form with source-linking for critical data points requiring human review 
2. Standardized output, integrable in other softwares  | Compliance
Data Privacy
Speed/Reliability
How easily it can be integrated in the user’s workflow 

https://www.reddit.com/r/PriorAuthorization/comments/1p85gn8/what_are_the_biggest_pain_points_you_deal_with_in/
https://www.reddit.com/r/healthIT/comments/1n81e8g/prior_authorization_whats_your_1_frustration_and/
[https://www.reddit.com/r/france/comments/1pw2rqh/le_dossier_médical_partagé_cest_un_fouillis/](https://www.reddit.com/r/france/comments/1pw2rqh/le_dossier_m%C3%A9dical_partag%C3%A9_cest_un_fouillis/)

 |
| [Not a project but could be in the method]
EHR > Classification ([method](https://arxiv.org/abs/2305.09756))
 | Assistance to Decision-Making:
- Irregular length of clinical notes
- Contextual Ambiguity (e.g. “rhythm” can refer to ECG or daily routine)
- Traditional models lose hierarchical details | EHR (Patient Record) > represented as a Hypergraph at different hierarchies (node-level, taxonomy-level…) | 1. Patient-level embedding (can be used in downstream models)
2. Prediction probability (task-dependent, for example in-hospital mortality…) | Data processing
Integration to solution |
| Emergency Co-Pilot: Leveraging on-device ASR & agents to prepare ER teams for clinical admission. | Scenario: Ambulance drives a patient, the paramedic orally diagnoses vitals and receives severity score / possible risks and what team can better treat the patient.
Motivation: Every second matters!! | Oral diagnosis → Speech-Recognition of vitals (e.g. pulse, which body part is broken/damaged…) → MedGemma + Tools → Severity or confidence score / possible failures / what action should be taken upon admission to hospital. | cf input | Training Scheme (clinical guidelines? Train individual components + orchestrate?)
Confidence/Reliability
On-device requirements (compute and speed) |

## Multimodal Evidence-Linked Agent for Prior Authorization

### Existing Projects

[Google - LangExtract]

[GitHub - google/langextract: A Python library for extracting structured information from unstructured text using LLMs with precise source grounding and interactive visualization.](https://github.com/google/langextract?tab=readme-ov-file)

<aside>
💡

- “For health-related applications, use of LangExtract is also subject to the [Health AI Developer Foundations Terms of Use](https://developers.google.com/health-ai-developer-foundations/terms).”
- Already has an example use case (Medication Extraction) https://github.com/google/langextract/blob/main/docs/examples/medication_examples.md
    
    ![Capture d’écran 2026-02-10 à 16.39.15.png](attachment:d122c263-8dd1-4f09-beb5-14a5f2b9a1d8:Capture_decran_2026-02-10_a_16.39.15.png)
    
</aside>

[Anthropic - Claude Code]

[GitHub - anthropics/healthcare](https://github.com/anthropics/healthcare)

[Advancing Claude in healthcare and the life sciences](https://www.anthropic.com/news/healthcare-life-sciences)

[Healthcare | Claude](https://claude.com/solutions/healthcare)

[Automation - AutoAuth + Azure] https://github.com/pablosalvador10/gbb-ai-hls-factory-prior-auth

[PA Approval Classification] https://github.com/domagal9/classifymymeds

<aside>
💡

Survey Papers:

https://arxiv.org/pdf/2406.03712

https://arxiv.org/pdf/2405.13055

</aside>

- The time spent by the doc on administrative work is benevolently allotted, unpaid and takes about 30% of daily schedule (en généraliste).
    
    > Depuis une dizaine d’années, les médecins généralistes ont vu leur charge administrative augmenter avec l’ajout de nouvelles missions non rémunérées : saisie dans SI-DEP, coordination post-hospitalisation (PRADO), obligations RGPD et numériques, participation aux CPTS et DAC, déclarations vaccinales et j’en passe. Cette évolution a presque doublé le temps consacré à l’administratif, passant à 20–25 % du temps de travail annuel.
    
    Cette pression croissante grève directement le temps médical disponible pour les patients, alors qu’aujourd’hui plus qu’hier on manque de médecins.
    > 
    
    → Identifier depuis la consultation quelle sub-task automatiser?
    
- HAI-DEF Models Use Case
    
    
    | **Model** | **Best For...** |
    | --- | --- |
    | **MedGemma 27B (Text)** | Deep clinical reasoning, summarizing long patient histories, and guidelines-based advice. |
    | **MedGemma 4B (Multimodal)** | Report generation from X-rays, MRIs, or CTs; running on edge/mobile devices. |
    | **MedASR** | Transcribing clinical dictations or patient-doctor conversations with high medical accuracy. |
    | **MedSigLIP** | "Search by image" (finding similar cases in a database) or fast, zero-shot classification. |
    | **TxGemma** | Drug discovery, molecule property prediction, and therapeutic research. |
    
    ---
    

# 🏥 Technical Architecture: Evidence-Linked Clinical Agent

**Project Status:** 2026 MedGemma Impact Challenge

**Core Objective:** Automate administrative documentation (Prior Authorizations) using a "Local-First" agentic workflow that links every claim to verifiable clinical source text.

---

This architecture represents a "Local-First AI Clinic," where data privacy is absolute and trust is built through verifiable evidence. By integrating **LangExtract**, we move from mere "generative AI" to a "grounded clinical assistant" that can prove its reasoning.

### 1. The Global Architecture Overview

The system is divided into three functional layers that communicate over a secure local network (LAN), ensuring patient data never leaves the facility.
⇒ Demo: Roleplay Patient vs Doc ? OK 

### **Layer 1: The Control Plane (Frontend)**

- **Tech:** React/Streamlit dashboard.
- **Role:** The clinician's "Cockpit." It captures dictations and displays the "Evidence-Linked" output.
- **Integration:** Uses the character offsets from LangExtract to highlight source text (e.g., in a scanned PDF) directly on the screen.

### **Layer 2: The Intelligence Engine (Backend)**

- **Models:** `MedASR` (transcription), `MedGemma 4B` (multimodal parsing), and `MedGemma 27B` (reasoning).
- **The Glue (LangExtract):** This is the verification layer. It forces the model to extract verbatim spans of text to support every claim (e.g., "Elevated ALT" or "Treatment Failure").
- **Orchestrator (FastAPI):** Manages the "Reasoning-over-Graph" logic.

### **Layer 3: The Hybrid Data Layer (Memory)**

Instead of a simple database, we use a **Vector DB** and a **Hypergraph**.

- **Vector DB (ChromaDB):** Handles semantic retrieval (e.g., "Find all notes related to liver toxicity").
- **Hypergraph (NetworkX):** Models "Clinical Stories." While a normal graph connects two points, a **Hyperedge** connects a whole group of nodes (e.g., a Medication + a Lab Result + a Clinical Symptom) into a single "Medical Necessity" event.

---

### 2. The Verification Loop: LangExtract in Action

Using the documentation you provided, here is how we technically implement the "grounding" so the doctor can trust the output.

```python
import langextract as lx

# 1. Define the 'Prior Authorization' logic
prompt = "Extract the current medication, the reason for failure, and the exact supporting evidence from the labs."

# 2. Few-shot clinical examples to ensure model alignment
examples = [
    lx.data.ExampleData(
        text="Patient reported nausea on 10/01. Blood tests from 12/01 show ALT at 150 (high). Discontinued MTX.",
        extractions=[
            lx.data.Extraction(
                extraction_class="failed_drug", 
                extraction_text="MTX",
                attributes={"status": "discontinued"}
            ),
            lx.data.Extraction(
                extraction_class="evidence", 
                extraction_text="ALT at 150 (high)", # VERBATIM from source
                attributes={"type": "lab_finding"}
            )
        ]
    )
]

# 3. Execution using our local MedGemma 27B model
result = lx.extract(
    text_or_documents=input_ehr_content,
    prompt_description=prompt,
    examples=examples,
    model_id="medgemma:27b",
    model_url="http://localhost:11434", # Pointing to local Ollama/vLLM
)

# 4. Resulting visualization (offsets for the frontend)
lx.io.save_annotated_documents([result], output_name="verification.jsonl")
```

---

### 3. How to Showcase This (The "Wow" Moment)

To demonstrate this in the Kaggle challenge, your showcase should follow the "Chain of Evidence" flow:

1. **The Input:** Upload a "messy" PDF of a lab result and a 3-page clinical history.
2. **The Processing:** Show a split-screen. On the left, the **Hypergraph** visualizes the connection between a 2024 drug prescription and a 2025 liver spike.
3. **The Output:** A pre-filled "Prior Authorization" form.
4. **The Proof:** When the judge hovers over the "Reason for Discontinuation" field, the system **highlights the exact sentence** in the 2025 PDF scan that proves the patient's liver was struggling.

> **Judge-Ready Pitch:** *"Our agent doesn't just predict text; it navigates a patient's history as a hypergraph of evidence. By using LangExtract for source-grounding, we eliminate hallucinations and give clinicians a verifiable 'reasoning path' they can sign off on in seconds."*
> 

---

- Chiffrer les stats liées à la Rheumatoid Arthritis
    
    ![Capture d’écran 2026-02-11 à 22.06.50.png](attachment:61ba0304-8f39-43c5-a8e1-f377e4bb3773:Capture_decran_2026-02-11_a_22.06.50.png)
    

![Capture d’écran 2026-02-11 à 22.07.03.png](attachment:86ebc728-3476-4ac5-afe4-fa11c969f950:Capture_decran_2026-02-11_a_22.07.03.png)

[Fixing prior auth: Nearly 40 prior authorizations a week is way too many](https://www.ama-assn.org/practice-management/prior-authorization/fixing-prior-auth-nearly-40-prior-authorizations-week-way)

Source - 80.7% blabla

![[https://www.hmpgloballearningnetwork.com/site/rheum/conference-coverage/rheumatoid-arthritis-burden-varies-widely-across-us-states#:~:text=Geographic hotspots%3A Montana%2C Wyoming%2C,RA prevalence and DALY rates](https://www.hmpgloballearningnetwork.com/site/rheum/conference-coverage/rheumatoid-arthritis-burden-varies-widely-across-us-states#:~:text=Geographic%20hotspots%3A%20Montana%2C%20Wyoming%2C,RA%20prevalence%20and%20DALY%20rates).](attachment:58a0c033-5075-488d-9cd7-14f609c8ad56:Capture_decran_2026-02-12_a_11.34.08.png)

[https://www.hmpgloballearningnetwork.com/site/rheum/conference-coverage/rheumatoid-arthritis-burden-varies-widely-across-us-states#:~:text=Geographic hotspots%3A Montana%2C Wyoming%2C,RA prevalence and DALY rates](https://www.hmpgloballearningnetwork.com/site/rheum/conference-coverage/rheumatoid-arthritis-burden-varies-widely-across-us-states#:~:text=Geographic%20hotspots%3A%20Montana%2C%20Wyoming%2C,RA%20prevalence%20and%20DALY%20rates).

- Medication Pipeline (cf Module Builder):
    
    Cinq Symptômes → Diagnostic initial (disorder) → RA_CarePlan starts: Regime + Ice therapy + Physical exercice → Naproxen (OTC) → Methotrexate OR predniSONE → Soit fini, soit 3-15 de “je vis avec” → Hip/Knee replacement
    
    Dans notre cas, le PA pour Actemra ou Tyenne se ferait après le Methotrexate/predniSONE
    

TODO : 

- [x]  Faire des patients synthéa pr la bonne maladie  [Output: Facts]
(https://github.com/synthetichealth/synthea/wiki 
https://synthetichealth.github.io/module-builder/#rheumatoid_arthritis)
    - Seeding
        1. I generated a RA module with 100% initial prevalence (does not guarantee the final FHIR diagnosis), placed it in a `modules/` directory then ran:
        
        [rheumatoid_arthritis_100%.json](attachment:a2f2d569-4328-4142-b5a9-3c96ba84d21b:rheumatoid_arthritis_100.json)
        
        <aside>
        💡
        
        ```bash
        ./run_synthea -p 1000 --exporter.baseDirectory="./output_montana/" Montana -s 42 -cs 42 -r 20260212 -d modules -m "rheumatoid_arthritis_100%" --exporter.text.export=true --exporter.csv.export=true --generate.append_numbers_to_person_names=false
        ```
        
        ```bash
        ./run_synthea -p 900 --exporter.baseDirectory="./output_wyoming/" Wyoming -s 43 -cs 43 -r 20260212 -d modules -m "rheumatoid_arthritis_100%" --exporter.text.export=true --exporter.csv.export=true --generate.append_numbers_to_person_names=false
        ```
        
        ```bash
        ./run_synthea -p 800 --exporter.baseDirectory="./output_new_mexico/" "New Mexico" -s 44 -cs 44 -r 20260212 -d modules -m "rheumatoid_arthritis_100%" --exporter.text.export=true --exporter.csv.export=true --generate.append_numbers_to_person_names=false
        ```
        
        </aside>
        
        1. Vibe-coded a script to filter `output` → `ra_diagnosed` + `ra_not_diagnosed` subfolders
            
            [split_ra_patients.py](attachment:8164cd63-563b-4557-9579-711e5d46920f:split_ra_patients.py)
            
            |  | Montana (1000) | Wyoming (900) | New Mexico (800) |
            | --- | --- | --- | --- |
            | Diagnosed | 227 | 181 | 135 |
            | Not Diagnosed | 773 | 719 | 665 |
- [x]  Choisir des patients cool
    
    DL Link: https://drive.google.com/file/d/1upetoOTPYX296s10l1xRXEjokwS_4bEm/view?usp=sharing
    
- [ ]  Faire à partir du FHIR des notes de consultation (voir comment) / depuis synthea si on peut [Facts → Curated FHIR - Réfléchir au format]
- [ ]  Transformer la policy en critères [Keywords → To be used in LangExtract]
(https://www.uhcprovider.com/content/dam/provider/docs/public/prior-auth/drugs-pharmacy/commercial/a-g/PA-Med-Nec-Actemra_Tyrenne.pdf)
    - [ ]  ⚠️https://www.uhcprovider.com/content/dam/provider/docs/public/prior-auth/drugs-pharmacy/commercial/a-g/PA-Med-Nec-Adalimumab.pdf
    Cette policy est à prioriser car c’est un traitement qui est favorisé par l’assureur. Le précédent est prescrit uniquement en cas d’échec d’autres comme celui-ci. 
    Potentiellement pour un lvl 2 faire ça.
- [ ]  Transformer le formulaire en un template [Backend (React/Streamlit? PyPDFForm)]
( https://www.uhcprovider.com/content/dam/provider/docs/public/prior-auth/uhccp-pharmacy-forms/PA-Request-Form-UHC-Community-Plan.pdf)
- [ ]  Faire un notebook avec les features suivantes
    - [ ]  Prescription d’un traitement à un patient (note écrite) → Patient, Traitement, Première fois / Renouvellement [Parsing(?)]
    - [ ]  Patient → Dossier du patient (FHIR + fichiers potentiels)
    - [ ]  Traitement → Policy (pdf)
    - [ ]  Policy → Critères d’acceptation (liste des critères et combinaisons and / or)
    - [ ]  *Critères d’acceptation**, template de formulaire, dossier patient → Infos pertinentes
    - [ ]  Infos, template de formulaire, critères d’acceptation → BOUM

---

- [ ]  How to evaluate model performance? → Human-Feedback (metric?)
- **Faithfulness Score (Grounding) :** Quel pourcentage des champs remplis ont une preuve textuelle valide extraite par LangExtract ?
- **Clinical Accuracy :** Demande à MedGemma 27B (en mode juge) de comparer le formulaire rempli avec la policy UHC : *"Est-ce que cette PA serait acceptée selon les critères X, Y, Z ?"*
- **Hallucination Rate :** Nombre de fois où l'agent a inventé une date ou un score non présent dans le FHIR/Note.
- [ ]  Where to use MedGemma (& How to fine-tune)
- **MedGemma 4B :** Utilise-le pour les tâches de "bas niveau" répétitives (Extraction d'entités, structuration de petits segments de texte). C'est parfait pour l'Edge AI.
- **MedGemma 27B :** Réserve-le pour le "High-level Reasoning". C'est lui qui décide si, oui ou non, le dossier du patient remplit les conditions de la page 4 de la policy UHC.
- [ ]  VectorDB & Hypergraph (https://arxiv.org/abs/2305.09756)?

https://github.com/smart-on-fhir/fhir-parser