"""Shared constants used across the backend."""

import re
from pathlib import Path

# Policy decision trees
TREE_PATH = "rheumatoid_arthritis_initial_auth_decision_tree_enriched.json"
TREE_PATH_ORIGINAL = "rheumatoid_arthritis_initial_auth_decision_tree.json"

# Patient notes directories
NOTES_ROOT = Path("soap_notes")       # legacy single-file structure
NOTES_ROOT_NEW = Path("notes")        # new multi-note structure
BENCHMARK_NOTES_ROOT = Path("benchmark_soap_notes")  # 47-patient benchmark set

# FHIR bundle locations
FHIR_ROOT = Path("generations")       # full local dataset
FHIR_BUNDLED = Path("fhir")           # pre-selected bundles for deployment

# Filename pattern for legacy soap_notes/*.txt files
UUID_PATTERN = re.compile(
    r"^(?P<name>.+)_(?P<uuid>[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})\.txt$"
)
