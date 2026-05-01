"""
Mixin column drift detection and repair for Custom Object Type tables.

Phase 2 of issue #391: when NetBox is upgraded and a mixin (e.g.
ChangeLoggingMixin) gains a new concrete column, existing COT tables will be
missing that column.  This module provides:

  heal_cot(cot, verbosity, dry_run)   — check and repair a single COT table
  heal_all_cots(verbosity, dry_run)   — iterate over all COTs

Both are called from:
  - The post_migrate signal handler in __init__.py (automatic, zero-config)
  - The upgrade_custom_objects management command (explicit, with --dry-run)

Safety rules
------------
  ADD allowed  : new column is nullable OR has a Django-level default
  Warn only    : new column is NOT NULL with no default (would fail for existing rows)
  Warn only    : column type appears to have changed
  Never        : auto-drop a column that is no longer in the base class
"""

import logging

from django.db import connection

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _expected_base_fields(cot, model=None):
    """
    Return {db_column_name: Django field instance} for every concrete column
    that the current CustomObject mixin hierarchy contributes to *cot*'s DB
    table, excluding user-defined fields.

    Keyed by f.column (the actual DB column name) so results can be compared
    directly against _actual_column_names() output, which returns DB column
    names from introspection.  Using f.name would produce incorrect comparisons
    for FK fields (where f.name='foo' but f.column='foo_id') or any field that
    overrides db_column.

    User fields are excluded by matching against their Python attribute names
    (f.name).  This is equivalent to matching by f.column for user-defined COT
    fields because they are never created with db_column overrides.

    Pass *model* to avoid a second get_model() call when the caller already
    holds the model reference.
    """
    if model is None:
        model = cot.get_model()
    user_field_names = set(cot.fields.values_list("name", flat=True))
    return {
        f.column: f
        for f in model._meta.concrete_fields
        if f.name not in user_field_names
    }


def _actual_column_names(table_name):
    """
    Return the set of column names currently present in *table_name*.

    Raises OperationalError / ProgrammingError if the table does not exist.
    """
    with connection.cursor() as cursor:
        return {
            col.name
            for col in connection.introspection.get_table_description(cursor, table_name)
        }


def _can_auto_add(field):
    """
    Return True if it is safe to ADD COLUMN for *field* on a table that
    already has rows.

    A column is safe to add when existing rows can receive a value without
    violating constraints:
      - Nullable columns default to NULL for existing rows.
      - Columns with a Django-level default use that value.
    """
    return field.null or field.has_default()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def heal_cot(cot, verbosity=1, dry_run=False):
    """
    Detect and repair mixin column drift for a single CustomObjectType.

    Parameters
    ----------
    cot       : CustomObjectType instance
    verbosity : int  0=silent, 1=changes+warnings, 2=verbose
    dry_run   : bool  if True, report but do not modify the DB

    Returns
    -------
    dict with keys:
      "added"   : list of column names successfully added (or would-be added)
      "warned"  : list of dicts {type, field, message} for non-auto-fixable issues
    """
    table_name = cot.get_database_table_name()
    added = []
    warned = []

    try:
        actual_names = _actual_column_names(table_name)
    except Exception as exc:
        logger.warning(
            "upgrade_custom_objects: cannot introspect table %r (COT %s): %s",
            table_name, cot.pk, exc,
        )
        return {"added": added, "warned": warned}

    # Resolve model once; pass it through to avoid a duplicate get_model() call.
    model = cot.get_model()
    expected = _expected_base_fields(cot, model)

    # Build a lookup of what was stored in the last snapshot for type comparison.
    # schema_document["base_columns"] stores column names as f.column (DB column
    # name), consistent with expected's f.column keys.
    stored_col_info = {
        c["name"]: c
        for c in (cot.schema_document or {}).get("base_columns", [])
    }

    # ── New columns in expected but missing from actual ──────────────────────
    for col_name, field in expected.items():
        if col_name in actual_names:
            continue

        if not _can_auto_add(field):
            entry = {
                "type": "new_non_nullable",
                "field": col_name,
                "message": (
                    f"Table {table_name!r}: new base column {col_name!r} "
                    f"({field.__class__.__name__}) is NOT NULL with no default — "
                    f"cannot be added automatically. Add a default or make it "
                    f"nullable upstream, then re-run 'manage.py upgrade_custom_objects'."
                ),
            }
            warned.append(entry)
            logger.warning(entry["message"])
            continue

        if dry_run:
            added.append(col_name)
            continue

        try:
            with connection.schema_editor() as editor:
                editor.add_field(model, field)
            added.append(col_name)
            if verbosity >= 1:
                logger.info(
                    "upgrade_custom_objects: added column %r to table %r",
                    col_name, table_name,
                )
        except Exception as exc:
            entry = {
                "type": "add_failed",
                "field": col_name,
                "message": (
                    f"Failed to ADD COLUMN {col_name!r} to {table_name!r}: {exc}"
                ),
            }
            warned.append(entry)
            logger.error(entry["message"])

    # ── Type changes on columns present in both expected and actual ──────────
    for col_name, field in expected.items():
        if col_name not in actual_names:
            continue  # already handled above as a new column
        stored = stored_col_info.get(col_name)
        if not stored or not stored.get("field_class"):
            continue  # no prior snapshot to compare against
        if stored["field_class"] != field.__class__.__name__:
            entry = {
                "type": "type_changed",
                "field": col_name,
                "message": (
                    f"Table {table_name!r}: column {col_name!r} type may have changed "
                    f"(was {stored['field_class']!r}, now {field.__class__.__name__!r}). "
                    f"Manual inspection and migration required."
                ),
            }
            warned.append(entry)
            logger.warning(entry["message"])

    # ── Columns removed from base class but still in DB ─────────────────────
    stored_base_names = set(stored_col_info)
    for col_name in sorted(stored_base_names - set(expected)):
        if col_name in actual_names:
            entry = {
                "type": "removed_from_model",
                "field": col_name,
                "message": (
                    f"Table {table_name!r}: column {col_name!r} still exists in the "
                    f"database but is no longer in the CustomObject base class. "
                    f"Manual cleanup may be required."
                ),
            }
            warned.append(entry)
            logger.warning(entry["message"])

    # ── Refresh snapshot after successful additions ──────────────────────────
    if added and not dry_run:
        # We cannot use _store_base_column_snapshot(model) here because the
        # generated model's _meta is built from the CustomObject class definition
        # and does not include columns added directly to the DB by this heal pass.
        # Instead, merge the newly-added field info into the existing snapshot.
        doc = cot.schema_document or {}
        current_cols = {c["name"]: c for c in doc.get("base_columns", [])}
        for col_name in added:
            field = expected[col_name]
            current_cols[col_name] = {
                "name": col_name,
                "field_class": field.__class__.__name__,
                "null": field.null,
            }
        doc["base_columns"] = list(current_cols.values())
        cot.__class__.objects.filter(pk=cot.pk).update(schema_document=doc)
        cot.schema_document = doc

    return {"added": added, "warned": warned}


def heal_all_cots(verbosity=1, dry_run=False):
    """
    Run heal_cot() for every CustomObjectType.

    Called by the post_migrate signal handler.  The upgrade_custom_objects
    management command iterates COTs directly so it can print per-COT output
    to stdout.

    Returns
    -------
    dict with keys:
      "total"    : number of COTs checked
      "healed"   : number of COTs that had columns added
      "warnings" : total number of non-auto-fixable issues
    """
    from netbox_custom_objects.models import CustomObjectType  # noqa: PLC0415

    total = healed = warnings = 0

    for cot in CustomObjectType.objects.all():
        total += 1
        result = heal_cot(cot, verbosity=verbosity, dry_run=dry_run)
        if result["added"]:
            healed += 1
        warnings += len(result["warned"])

    if verbosity >= 2:
        logger.info(
            "upgrade_custom_objects: %d COT(s) checked, %d healed, %d warning(s)",
            total, healed, warnings,
        )
    elif verbosity >= 1 and (healed > 0 or warnings > 0):
        logger.info(
            "upgrade_custom_objects: %d COT(s) healed, %d warning(s)",
            healed, warnings,
        )

    return {"total": total, "healed": healed, "warnings": warnings}
