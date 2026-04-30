"""
COT state comparator / diff engine (issue #387).

Compares an incoming schema document (produced by the exporter or hand-authored)
against the live DB state of each referenced CustomObjectType and returns a
structured diff that the upgrade executor (#389) can consume.

Public API
----------
    diff_document(schema_doc)  → list[COTDiff]
    diff_cot(type_def)         → COTDiff

Data model
----------
    COTDiff          — top-level result for one COT
      .cot_changes   — {attr: (db_val, schema_val)} for COT-level attribute changes
      .field_changes — list[FieldChange]
      .warnings      — non-fatal issues (e.g. untracked DB fields)

    FieldChange
      .op            — FieldOp.ADD | REMOVE | ALTER
      .schema_id     — the stable numeric field identifier
      .db_name       — current DB field name (None for ADD)
      .schema_def    — raw schema field dict (for ADD; also available for ALTER)
      .changed_attrs — {attr: (db_val, schema_val)} — populated for ALTER

Notes
-----
- Fields are matched by schema_id.  Fields in the DB without a schema_id cannot
  be tracked and are reported as warnings, not as removals.
- A REMOVE operation is only emitted when the field's schema_id appears in the
  schema's removed_fields tombstone list.  A field absent from both schema.fields
  and schema.removed_fields is ambiguous (possibly added outside the workflow)
  and generates a warning instead.
- Type changes are included in changed_attrs but are not validated here; the
  executor decides whether to allow or reject them.  When the type changes,
  type-specific attributes from the *old* DB type (e.g. validation_regex on a
  text field being converted to integer) are not included in the diff — only
  attributes relevant to the incoming schema type are compared.
- related_object_type values are compared in their encoded schema form
  ("app_label/model" or "custom-objects/<slug>") so the diff output is
  round-trip compatible with the schema format.
"""

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

from netbox_custom_objects import constants

if TYPE_CHECKING:
    from django.contrib.contenttypes.models import ContentType
from netbox_custom_objects.schema.format import (
    CUSTOM_OBJECTS_APP_LABEL_SLUG,
    FIELD_DEFAULTS,
    FIELD_TYPE_ATTRS,
    SCHEMA_TYPE_TO_CHOICES,
)

# Matches Table<id>Model (generated model names for custom object types).
_TABLE_MODEL_RE = re.compile(r'^table(\d+)model$')

# Ordered base attributes compared between DB and schema for each field.
# Does NOT include 'name' or 'type' — those are handled separately.
_FIELD_BASE_ATTRS = (
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

# COT-level attributes that may change between schema versions.
# Each maps to its schema-absent default (empty string).
_COT_ATTRS = (
    "name",
    "version",
    "verbose_name",
    "verbose_name_plural",
    "description",
    "group_name",
)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

class FieldOp(Enum):
    ADD = "add"        # field exists in schema but not in DB
    REMOVE = "remove"  # field tombstoned in schema; still exists in DB
    ALTER = "alter"    # field in both; has differences (may include rename/type change)


@dataclass
class FieldChange:
    """A single field-level operation within a COTDiff."""
    op: FieldOp
    schema_id: int
    db_name: str | None                          # current DB name; None for ADD
    schema_def: dict                             # the schema field dict
    changed_attrs: dict[str, tuple] = field(default_factory=dict)
    # {attr: (db_value, schema_value)} — populated for ALTER;
    # includes "name" if renamed, "type" if type differs.

    @property
    def is_rename(self) -> bool:
        return "name" in self.changed_attrs

    @property
    def is_type_change(self) -> bool:
        return "type" in self.changed_attrs


@dataclass
class COTDiff:
    """All changes needed to bring one COT in sync with a schema definition."""
    name: str                                    # from schema
    slug: str                                    # from schema (used as lookup key)
    is_new: bool                                 # True → COT does not yet exist in DB
    cot_changes: dict[str, tuple] = field(default_factory=dict)
    # {attr: (db_val, schema_val)} for COT-level attribute differences
    field_changes: list[FieldChange] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        """True if there are attribute-level or field-level changes to apply.

        Note: a brand-new COT with no fields yields ``is_new=True`` but
        ``has_changes=False`` — there are no individual changes to apply, but
        the COT itself must still be created.  Callers should check ``is_new``
        independently when deciding whether to run a create operation.
        """
        return bool(self.cot_changes or self.field_changes)

    @property
    def has_destructive_changes(self) -> bool:
        """True if any field will be dropped from the DB."""
        return any(fc.op is FieldOp.REMOVE for fc in self.field_changes)

    @property
    def adds(self) -> list[FieldChange]:
        return [fc for fc in self.field_changes if fc.op is FieldOp.ADD]

    @property
    def removes(self) -> list[FieldChange]:
        return [fc for fc in self.field_changes if fc.op is FieldOp.REMOVE]

    @property
    def alters(self) -> list[FieldChange]:
        return [fc for fc in self.field_changes if fc.op is FieldOp.ALTER]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _encode_related_object_type(rot: "ContentType", cot_slug_cache: dict, warnings: list) -> str:
    """
    Encode a related ObjectType FK as a schema ``related_object_type`` string.
    Shares the same encoding logic as ``exporter._encode_related_object_type``
    but uses a pre-fetched slug cache and a warnings list rather than a live
    DB query, making it safe to call in a tight loop.

    *cot_slug_cache* is a ``{pk: slug}`` dict pre-fetched in :func:`diff_cot`
    to avoid one DB query per object field.  If a custom COT is referenced but
    not present in the cache (i.e. its DB row was deleted), a warning is
    appended to *warnings* and a stable fallback string is returned so the
    diff can still proceed.
    """
    if rot.app_label == constants.APP_LABEL:
        m = _TABLE_MODEL_RE.match(rot.model)
        if m:
            cot_id = int(m.group(1))
            slug = cot_slug_cache.get(cot_id)
            if slug is None:
                warnings.append(
                    f"CustomObjectType pk={cot_id} is referenced by a field but "
                    "no longer exists in the DB. The related_object_type comparison "
                    "for this field may be inaccurate."
                )
                return f"{CUSTOM_OBJECTS_APP_LABEL_SLUG}/<deleted:{cot_id}>"
            return f"{CUSTOM_OBJECTS_APP_LABEL_SLUG}/{slug}"
    return f"{rot.app_label}/{rot.model}"


def _compare_field_attrs(db_field, schema_field: dict, cot_slug_cache: dict, warnings: list) -> dict[str, tuple]:
    """
    Return ``{attr: (db_value, schema_value)}`` for every attribute that
    differs between *db_field* (a ``CustomObjectTypeField`` instance) and
    *schema_field* (the schema dict for that field).
    """
    changes: dict[str, tuple] = {}
    schema_type = schema_field["type"]  # schema string, e.g. "text"

    # ── name ────────────────────────────────────────────────────────────────
    schema_name = schema_field["name"]
    if db_field.name != schema_name:
        changes["name"] = (db_field.name, schema_name)

    # ── type ─────────────────────────────────────────────────────────────────
    expected_choice = SCHEMA_TYPE_TO_CHOICES[schema_type]
    if db_field.type != expected_choice:
        changes["type"] = (db_field.type, expected_choice)

    # ── base scalar attributes ───────────────────────────────────────────────
    for attr in _FIELD_BASE_ATTRS:
        db_val = getattr(db_field, attr)
        schema_val = schema_field.get(attr, FIELD_DEFAULTS.get(attr))
        if db_val != schema_val:
            changes[attr] = (db_val, schema_val)

    # ── type-specific attributes ─────────────────────────────────────────────
    type_specific = FIELD_TYPE_ATTRS.get(schema_type, set())

    if "validation_regex" in type_specific:
        dv = db_field.validation_regex or ""
        sv = schema_field.get("validation_regex", "")
        if dv != sv:
            changes["validation_regex"] = (dv, sv)

    if "validation_minimum" in type_specific:
        dv = db_field.validation_minimum
        sv = schema_field.get("validation_minimum", None)
        if dv != sv:
            changes["validation_minimum"] = (dv, sv)

    if "validation_maximum" in type_specific:
        dv = db_field.validation_maximum
        sv = schema_field.get("validation_maximum", None)
        if dv != sv:
            changes["validation_maximum"] = (dv, sv)

    if "choice_set" in type_specific:
        dv = db_field.choice_set.name if db_field.choice_set_id else None
        sv = schema_field.get("choice_set")
        if dv != sv:
            changes["choice_set"] = (dv, sv)

    if "related_object_type" in type_specific:
        dv = (
            _encode_related_object_type(db_field.related_object_type, cot_slug_cache, warnings)
        ) if db_field.related_object_type_id else None
        sv = schema_field.get("related_object_type")
        if dv != sv:
            changes["related_object_type"] = (dv, sv)

    if "related_object_filter" in type_specific:
        dv = db_field.related_object_filter
        sv = schema_field.get("related_object_filter", None)
        if dv != sv:
            changes["related_object_filter"] = (dv, sv)

    return changes


def _compare_cot_attrs(cot, type_def: dict) -> dict[str, tuple]:
    """
    Return ``{attr: (db_value, schema_value)}`` for COT-level attributes
    that differ.  Absent schema keys are treated as empty string (same
    convention as the exporter).
    """
    changes: dict[str, tuple] = {}
    for attr in _COT_ATTRS:
        # All _COT_ATTRS are string fields; None and "" both mean "absent" — same
        # convention as the exporter.  _COT_ATTRS must never include numeric or
        # boolean fields, as `or ""` would swallow falsy values like 0 or False.
        db_val = getattr(cot, attr) or ""
        schema_val = type_def.get(attr) or ""
        if db_val != schema_val:
            changes[attr] = (db_val, schema_val)
    return changes


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def diff_cot(type_def: dict) -> COTDiff:
    """
    Compare one COT schema definition against the live DB state.

    *type_def* is a single entry from the ``types`` list of a schema document
    (as produced by :func:`export_cot` or hand-authored).

    Returns a :class:`COTDiff` describing what would need to change.
    """
    missing = [k for k in ("slug", "name") if k not in type_def]
    if missing:
        raise ValueError(
            f"type_def is missing required key(s) {missing}; got keys: {list(type_def)}"
        )

    from netbox_custom_objects.models import CustomObjectType  # noqa: PLC0415

    slug = type_def["slug"]
    name = type_def["name"]

    # ── COT existence check ──────────────────────────────────────────────────
    try:
        cot = CustomObjectType.objects.get(slug=slug)
    except CustomObjectType.DoesNotExist:
        # Brand-new COT — every schema field is an ADD.
        field_changes = [
            FieldChange(
                op=FieldOp.ADD,
                schema_id=sf["id"],
                db_name=None,
                schema_def=sf,
            )
            for sf in type_def.get("fields", [])
        ]
        return COTDiff(
            name=name,
            slug=slug,
            is_new=True,
            field_changes=field_changes,
        )

    diff = COTDiff(name=name, slug=slug, is_new=False)

    # ── COT-level attribute diff ─────────────────────────────────────────────
    diff.cot_changes = _compare_cot_attrs(cot, type_def)

    # ── Build lookup indexes ─────────────────────────────────────────────────
    schema_fields: dict[int, dict] = {
        sf["id"]: sf for sf in type_def.get("fields", [])
    }
    # Map schema_id → full tombstone dict so REMOVE FieldChanges carry the
    # original field definition (name, type, etc.) rather than an empty dict.
    tombstoned: dict[int, dict] = {
        rf["id"]: rf for rf in type_def.get("removed_fields", [])
    }
    # Single query for all fields; partition into tracked/untracked in Python.
    db_fields: dict[int, object] = {}
    for f in cot.fields.select_related("choice_set", "related_object_type"):
        if f.schema_id is None:
            diff.warnings.append(
                f"Field {f.name!r} (pk={f.pk}) has no schema_id and cannot be "
                "tracked by the schema diff. It will not be affected by apply operations."
            )
        else:
            db_fields[f.schema_id] = f

    # Pre-fetch slugs for all custom-COT related_object_type references in a
    # single query to avoid one DB round-trip per object/multiobject field.
    cot_ids: set[int] = set()
    for f in db_fields.values():
        if f.related_object_type_id and f.related_object_type.app_label == constants.APP_LABEL:
            m = _TABLE_MODEL_RE.match(f.related_object_type.model)
            if m:
                cot_ids.add(int(m.group(1)))
    cot_slug_cache: dict[int, str] = (
        dict(CustomObjectType.objects.filter(pk__in=cot_ids).values_list("pk", "slug"))
        if cot_ids else {}
    )

    # ── Schema fields → ADD or ALTER ─────────────────────────────────────────
    for schema_id, schema_field in schema_fields.items():
        if schema_id in db_fields:
            db_field = db_fields[schema_id]
            changed = _compare_field_attrs(db_field, schema_field, cot_slug_cache, diff.warnings)
            if changed:
                diff.field_changes.append(FieldChange(
                    op=FieldOp.ALTER,
                    schema_id=schema_id,
                    db_name=db_field.name,
                    schema_def=schema_field,
                    changed_attrs=changed,
                ))
        else:
            diff.field_changes.append(FieldChange(
                op=FieldOp.ADD,
                schema_id=schema_id,
                db_name=None,
                schema_def=schema_field,
            ))

    # ── DB fields absent from schema → REMOVE or warn ────────────────────────
    for schema_id, db_field in db_fields.items():
        if schema_id in schema_fields:
            continue  # already handled above
        if schema_id in tombstoned:
            diff.field_changes.append(FieldChange(
                op=FieldOp.REMOVE,
                schema_id=schema_id,
                db_name=db_field.name,
                schema_def=tombstoned[schema_id],
            ))
        else:
            diff.warnings.append(
                f"Field {db_field.name!r} (schema_id={schema_id}) exists in the DB "
                "but is absent from both schema.fields and schema.removed_fields. "
                "It was likely added outside the schema workflow and will not be "
                "affected by apply operations."
            )

    return diff


def diff_document(schema_doc: dict) -> list[COTDiff]:
    """
    Diff all COTs in a schema document against current DB state.

    Returns a list of :class:`COTDiff` objects, one per entry in
    ``schema_doc["types"]``.
    """
    return [diff_cot(type_def) for type_def in schema_doc.get("types", [])]
