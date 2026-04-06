"""
Constants and helpers for the COT portable schema format.

The schema format is a YAML (or JSON) document describing one or more Custom
Object Type definitions in a portable, versionable way.  A multi-type export
always uses a top-level ``types:`` list; the importer also accepts a bare
single-type document for convenience.

Format version history
----------------------
"1"  Initial version (introduced alongside schema_id / deprecated field support).
"""

from extras.choices import CustomFieldTypeChoices

# ── Format version ──────────────────────────────────────────────────────────
# Bump this only when the format itself changes in a breaking way.
SCHEMA_FORMAT_VERSION = "1"

# ── Field type names (value → schema string) ────────────────────────────────
# These are the canonical type names used in schema documents.
# They happen to match CustomFieldTypeChoices values, but are redefined here
# explicitly so the schema format is not silently broken by upstream changes.
FIELD_TYPE_TEXT = "text"
FIELD_TYPE_LONGTEXT = "longtext"
FIELD_TYPE_INTEGER = "integer"
FIELD_TYPE_DECIMAL = "decimal"
FIELD_TYPE_BOOLEAN = "boolean"
FIELD_TYPE_DATE = "date"
FIELD_TYPE_DATETIME = "datetime"
FIELD_TYPE_URL = "url"
FIELD_TYPE_JSON = "json"
FIELD_TYPE_SELECT = "select"
FIELD_TYPE_MULTISELECT = "multiselect"
FIELD_TYPE_OBJECT = "object"
FIELD_TYPE_MULTIOBJECT = "multiobject"

# Mapping from CustomFieldTypeChoices values to schema type names.
# Used by the exporter; the importer uses the inverse.
CHOICES_TO_SCHEMA_TYPE = {
    CustomFieldTypeChoices.TYPE_TEXT: FIELD_TYPE_TEXT,
    CustomFieldTypeChoices.TYPE_LONGTEXT: FIELD_TYPE_LONGTEXT,
    CustomFieldTypeChoices.TYPE_INTEGER: FIELD_TYPE_INTEGER,
    CustomFieldTypeChoices.TYPE_DECIMAL: FIELD_TYPE_DECIMAL,
    CustomFieldTypeChoices.TYPE_BOOLEAN: FIELD_TYPE_BOOLEAN,
    CustomFieldTypeChoices.TYPE_DATE: FIELD_TYPE_DATE,
    CustomFieldTypeChoices.TYPE_DATETIME: FIELD_TYPE_DATETIME,
    CustomFieldTypeChoices.TYPE_URL: FIELD_TYPE_URL,
    CustomFieldTypeChoices.TYPE_JSON: FIELD_TYPE_JSON,
    CustomFieldTypeChoices.TYPE_SELECT: FIELD_TYPE_SELECT,
    CustomFieldTypeChoices.TYPE_MULTISELECT: FIELD_TYPE_MULTISELECT,
    CustomFieldTypeChoices.TYPE_OBJECT: FIELD_TYPE_OBJECT,
    CustomFieldTypeChoices.TYPE_MULTIOBJECT: FIELD_TYPE_MULTIOBJECT,
}

SCHEMA_TYPE_TO_CHOICES = {v: k for k, v in CHOICES_TO_SCHEMA_TYPE.items()}

# ── related_object_type encoding ─────────────────────────────────────────────
# Built-in NetBox objects:  "dcim/device"  (app_label/model)
# Custom Object Types:      "custom-objects/circuit"  (using the COT slug)
CUSTOM_OBJECTS_APP_LABEL_SLUG = "custom-objects"

# ── Field attribute defaults ─────────────────────────────────────────────────
# Attributes that match these defaults MAY be omitted from the schema document.
# The importer applies them when a key is absent.
FIELD_DEFAULTS = {
    # label resolves to name.replace("_", " ").capitalize() at runtime. Importer must implement this same logic.
    # An empty or absent label means "derive from name".
    "label": "",
    "description": "",
    "group_name": "",
    "primary": False,
    "required": False,
    "unique": False,
    "default": None,
    "weight": 100,
    "search_weight": 500,
    "filter_logic": "loose",
    "ui_visible": "always",
    "ui_editable": "yes",
    "is_cloneable": False,
    "deprecated": False,
    "deprecated_since": "",
    "scheduled_removal": "",
    # type-specific defaults
    "validation_regex": "",
    "validation_minimum": None,
    "validation_maximum": None,
    "related_object_filter": None,
}

# ── Field groups by type ─────────────────────────────────────────────────────
# Which type-specific attributes are valid for each field type.
# Used by the exporter to omit irrelevant keys and by the JSON Schema.
# Note: The exporter should use FIELD_TYPE_ATTRS to drop irrelevant keys inherited from field_base.
FIELD_TYPE_ATTRS = {
    FIELD_TYPE_TEXT: {"validation_regex"},
    FIELD_TYPE_LONGTEXT: {"validation_regex"},
    FIELD_TYPE_INTEGER: {"validation_minimum", "validation_maximum"},
    FIELD_TYPE_DECIMAL: {"validation_minimum", "validation_maximum"},
    FIELD_TYPE_BOOLEAN: set(),
    FIELD_TYPE_DATE: set(),
    FIELD_TYPE_DATETIME: set(),
    FIELD_TYPE_URL: set(),
    FIELD_TYPE_JSON: set(),
    FIELD_TYPE_SELECT: {"choice_set"},
    FIELD_TYPE_MULTISELECT: {"choice_set"},
    FIELD_TYPE_OBJECT: {"related_object_type", "related_object_filter"},
    FIELD_TYPE_MULTIOBJECT: {"related_object_type", "related_object_filter"},
}
