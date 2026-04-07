"""
Exporter for the COT portable schema format (issue #388).

Converts live CustomObjectType DB state into a schema document dict that
conforms to cot_schema_v1.json.  The returned dict can be serialised to YAML
or JSON by the caller.

Public API
----------
    export_cot(cot)     → dict   # single COT definition (no top-level wrapper)
    export_cots(cots)   → dict   # full schema document  { schema_version, types }

Notes
-----
- Fields without a schema_id (created before the schema-format feature) are
  skipped with a WARNING log entry.  They cannot be tracked across installs.
- Attribute values that equal FIELD_DEFAULTS are omitted to keep the output
  minimal (round-trip safe: the importer re-applies the same defaults).
- Tombstones (removed_fields) are read from the COT's schema_document.  Until
  the apply endpoint (#390) is implemented this will always be empty; once
  apply is wired up, deletions will be persisted there automatically.
"""

import logging
import re

from netbox_custom_objects import constants
from netbox_custom_objects.schema_format import (
    CHOICES_TO_SCHEMA_TYPE,
    CUSTOM_OBJECTS_APP_LABEL_SLUG,
    FIELD_DEFAULTS,
    FIELD_TYPE_ATTRS,
    SCHEMA_FORMAT_VERSION,
)

logger = logging.getLogger(__name__)

# Matches the generated model name produced by CustomObjectType.get_table_model_name().
# Capturing group 1 is the numeric COT id.
_TABLE_MODEL_RE = re.compile(r'^table(\d+)model$', re.IGNORECASE)

# Ordered list of field_base attributes to check for non-default values.
# Type-specific attributes (validation_*, choice_set, related_*) are handled
# separately via FIELD_TYPE_ATTRS.
_BASE_ATTRS = (
    "label",
    "description",
    "group_name",
    "primary",
    "required",
    "unique",
    "default",
    "weight",
    "search_weight",
    "filter_logic",
    "ui_visible",
    "ui_editable",
    "is_cloneable",
    "deprecated",
    "deprecated_since",
    "scheduled_removal",
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _encode_related_object_type(rot) -> str:
    """
    Encode an ObjectType FK as a schema ``related_object_type`` string.

    Built-in NetBox objects → ``"<app_label>/<model>"``  (e.g. ``"dcim/device"``)
    Custom Object Types     → ``"custom-objects/<slug>"``
    """
    if rot.app_label == constants.APP_LABEL:
        m = _TABLE_MODEL_RE.match(rot.model)
        if m:
            # Avoid a circular import — import here so the module can be loaded
            # independently of the full Django app stack in unit tests.
            from netbox_custom_objects.models import CustomObjectType  # noqa: PLC0415
            cot_id = int(m.group(1))
            slug = CustomObjectType.objects.values_list('slug', flat=True).get(pk=cot_id)
            return f"{CUSTOM_OBJECTS_APP_LABEL_SLUG}/{slug}"
    return f"{rot.app_label}/{rot.model}"


def _export_field(field) -> dict:
    """
    Serialise a single ``CustomObjectTypeField`` instance to a schema field dict.

    Raises ``ValueError`` if ``field.schema_id`` is ``None``; callers should
    pre-filter or handle this case before calling this function.
    """
    if field.schema_id is None:
        raise ValueError(
            f"Field {field.name!r} on COT {field.custom_object_type_id!r} "
            "has no schema_id and cannot be exported."
        )

    schema_type = CHOICES_TO_SCHEMA_TYPE[field.type]

    result = {
        "id": field.schema_id,
        "name": field.name,
        "type": schema_type,
    }

    # ── Base attributes (omit when equal to documented defaults) ────────────
    for attr in _BASE_ATTRS:
        value = getattr(field, attr)
        if value != FIELD_DEFAULTS.get(attr):
            result[attr] = value

    # ── Type-specific attributes ─────────────────────────────────────────────
    for attr in sorted(FIELD_TYPE_ATTRS[schema_type]):
        if attr == "choice_set":
            # Required for select/multiselect; validate.
            if field.choice_set is None:
                raise ValueError(
                    f"Field {field.name!r} is type {schema_type!r} but has no choice_set assigned."
                )
            result["choice_set"] = field.choice_set.name
        elif attr == "related_object_type":
            # Required for object/multiobject; always present.
            result["related_object_type"] = _encode_related_object_type(
                field.related_object_type
            )
        elif attr == "related_object_filter":
            value = field.related_object_filter
            if value != FIELD_DEFAULTS.get("related_object_filter"):
                result["related_object_filter"] = value
        elif attr in ("validation_regex", "validation_minimum", "validation_maximum"):
            value = getattr(field, attr)
            if value != FIELD_DEFAULTS.get(attr):
                result[attr] = value

    return result


def _removed_fields_from_document(cot) -> list:
    """
    Extract the ``removed_fields`` tombstone list for *cot* from its stored
    ``schema_document``.  Returns an empty list if the document is absent or
    does not reference this COT.
    """
    if not cot.schema_document:
        return []
    # NOTE: matches by COT name. If the COT is renamed after tombstones
    # are persisted, they will not be found. This will be addressed when
    # #390 (apply) is implemented and tombstones are managed more explicitly.
    for type_def in cot.schema_document.get("types", []):
        if type_def.get("name") == cot.name:
            return list(type_def.get("removed_fields", []))
    return []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def export_cot(cot) -> dict:
    """
    Serialise a single ``CustomObjectType`` to its schema definition dict
    (the inner object that goes inside the ``types`` list).

    Fields without a ``schema_id`` are skipped; a WARNING is logged for each.
    """
    result: dict = {
        "name": cot.name,
        "slug": cot.slug,
    }

    # Optional COT-level attributes — omit when blank/unset.
    if cot.version:
        result["version"] = cot.version
    if cot.verbose_name:
        result["verbose_name"] = cot.verbose_name
    if cot.verbose_name_plural:
        result["verbose_name_plural"] = cot.verbose_name_plural
    if cot.description:
        result["description"] = cot.description
    if cot.group_name:
        result["group_name"] = cot.group_name

    # Active + deprecated fields, ordered by schema_id for stable output.
    exported_fields = []
    for field in cot.fields.order_by("schema_id"):
        if field.schema_id is None:
            logger.warning(
                "Skipping field %r on COT %r during export: no schema_id assigned. "
                "This field was likely created before the schema-format feature was "
                "introduced and cannot be tracked portably.",
                field.name,
                cot.name,
            )
            continue
        exported_fields.append(_export_field(field))

    if exported_fields:
        result["fields"] = exported_fields

    # Tombstones from previous apply operations.
    removed = _removed_fields_from_document(cot)
    if removed:
        result["removed_fields"] = removed

    return result


def export_cots(cots) -> dict:
    """
    Serialise one or more ``CustomObjectType`` instances to a complete schema
    document dict (``{ schema_version, types }``) that validates against
    ``cot_schema_v1.json``.

    *cots* may be any iterable of ``CustomObjectType`` instances.
    """
    if not cots:
        raise ValueError("Minimum 1 Custom Object Type required.")
    return {
        "schema_version": SCHEMA_FORMAT_VERSION,
        "types": [export_cot(cot) for cot in cots],
    }
