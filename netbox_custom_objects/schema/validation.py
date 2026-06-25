"""
Shared JSON-Schema validation for COT portable-schema documents.

Single source of truth for loading ``cot_schema_v1.json`` and validating a
schema document against it.  Both the REST API (``api/views.py``) and the JSON
tab on the COT add page reuse this so the validation behaviour is identical.
"""

import functools
import json
from pathlib import Path

import jsonschema

_SCHEMA_FILE = Path(__file__).parent / 'cot_schema_v1.json'


@functools.lru_cache(maxsize=1)
def get_validator():
    """Load the COT JSON Schema file and return a validator. Cached after first call."""
    with open(_SCHEMA_FILE) as f:
        schema = json.load(f)
    return jsonschema.Draft202012Validator(schema)


def iter_schema_errors(schema_doc):
    """Yield ``jsonschema`` validation errors for *schema_doc*, sorted by path."""
    validator = get_validator()
    return sorted(validator.iter_errors(schema_doc), key=lambda e: list(e.path))


def schema_error_dicts(schema_doc, limit=10):
    """
    Return up to *limit* validation errors as ``{"path": [...], "message": str}``
    dicts.  An empty list means the document is valid.
    """
    return [
        {'path': list(e.path), 'message': e.message}
        for e in iter_schema_errors(schema_doc)[:limit]
    ]
