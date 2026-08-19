"""
Mixin column drift detection and repair for Custom Object Type tables.

Phase 2 of issue #391: when NetBox is upgraded and a mixin (e.g.
ChangeLoggingMixin) gains a new concrete column, existing COT tables will be
missing that column.  This module provides:

  heal_cot(cot, verbosity, dry_run)          — check and repair a single COT table
  heal_all_cots(verbosity, dry_run)          — iterate over all COTs on one connection
  heal_branch(branch, verbosity, dry_run)    — heal_all_cots() against one Branch's schema
  heal_all_branches(verbosity, dry_run)      — heal_branch() for every live Branch
  heal_unmasked_fields(cot, model, schema_conn)
                                              — add mixin columns unmasked by a
                                                field rename/delete

heal_cot/heal_all_cots/heal_branch/heal_all_branches are called from:
  - The post_migrate signal handler in __init__.py (automatic, zero-config)
  - The upgrade_custom_objects management command (explicit, with --dry-run)

heal_unmasked_fields is called directly from CustomObjectTypeField.save()/
delete() in models.py, right after a rename or delete, rather than waiting
for the next post_migrate pass.

Safety rules
------------
  ADD allowed  : new column is nullable OR has a Django-level default
  Warn only    : new column is NOT NULL with no default (would fail for existing rows)
  Warn only    : column type appears to have changed
  Warn only    : a field's name collides with another field's backing column
                 (see detect_backing_column_collisions() in models.py)
  Never        : auto-drop a column that is no longer in the base class
"""

import logging

from django.apps import apps as django_apps
from django.db import DEFAULT_DB_ALIAS, connections

from netbox_custom_objects.models import detect_backing_column_collisions

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

    Multi-column custom field types (e.g. CoordinatesFieldType's "_latitude"/
    "_longitude", URLFieldType's "_title") are NOT excluded here, since their
    backing columns' attribute names never equal the user field's own f.name --
    so this heal pass also covers them as a deliberate side effect. This is why
    a newly introduced sub-column for one of those types needs no dedicated
    migration/upgrade code: as long as it's nullable, the next post_migrate run
    (or `upgrade_custom_objects`) auto-adds it to every existing COT table.

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


def _actual_column_names(table_name, using=DEFAULT_DB_ALIAS):
    """
    Return the set of column names currently present in *table_name* on the
    *using* connection (a branch's own schema when healing a branch).

    Raises OperationalError / ProgrammingError if the table does not exist.
    """
    conn = connections[using]
    with conn.cursor() as cursor:
        return {
            col.name
            for col in conn.introspection.get_table_description(cursor, table_name)
        }


def _branch_schema_exists(schema_name, using=DEFAULT_DB_ALIAS):
    """
    Return True if *schema_name* is a real PostgreSQL schema in the database.

    Queried via the *default* connection's catalog, not the branch's own
    connection -- a branch connection's search path falls through to main
    when its own schema doesn't exist, so introspecting through it would
    silently report main's tables instead of raising. information_schema
    is per-database (not per-schema), so it's visible regardless of which
    schema is actually live.
    """
    with connections[using].cursor() as cursor:
        cursor.execute(
            "SELECT 1 FROM information_schema.schemata WHERE schema_name = %s", [schema_name]
        )
        return cursor.fetchone() is not None


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

def heal_unmasked_fields(cot, model, schema_conn):
    """
    Add missing columns for CustomObject mixin fields unmasked by renaming or
    deleting a same-named user field (e.g. 'owner' shadowing OwnerMixin.owner).

    Schema-connection-aware (branch-safe) counterpart to the add-column loop
    in heal_cot(), meant to be called right after a CustomObjectTypeField
    rename/delete rather than waiting for the next post_migrate heal pass.
    """
    expected = _expected_base_fields(cot, model)
    with schema_conn.cursor() as cursor:
        actual_cols = {
            col.name
            for col in schema_conn.introspection.get_table_description(cursor, model._meta.db_table)
        }

    missing = []
    for col_name, field in expected.items():
        if col_name in actual_cols:
            continue
        if not _can_auto_add(field):
            logger.warning(
                "heal_unmasked_fields: unmasked base column %r (field %r) on %s is not "
                "nullable and has no default — cannot auto-add. Run "
                "'manage.py upgrade_custom_objects'.",
                col_name, field.name, model._meta.db_table,
            )
            continue
        missing.append(field)

    if not missing:
        return

    with schema_conn.schema_editor() as schema_editor:
        # Flush pending DEFERRABLE FK trigger events before ALTER TABLE, matching
        # every other add_field() call site in this codebase.
        schema_editor.execute('SET CONSTRAINTS ALL IMMEDIATE')
        for field in missing:
            schema_editor.add_field(model, field)


def heal_cot(cot, verbosity=1, dry_run=False, using=DEFAULT_DB_ALIAS):
    """
    Detect and repair mixin column drift for a single CustomObjectType.

    Parameters
    ----------
    cot       : CustomObjectType instance
    verbosity : int  0=silent, 1=changes+warnings, 2=verbose
    dry_run   : bool  if True, report but do not modify the DB
    using     : str   connection alias to introspect/alter — a branch's own
                connection when healing a branch schema (see heal_branch())

    Returns
    -------
    dict with keys:
      "added"   : list of column names successfully added (or would-be added)
      "warned"  : list of dicts {type, field, message} for non-auto-fixable issues
    """
    table_name = cot.get_database_table_name()
    added = []
    warned = []

    # Detect pre-existing field-name collisions with a multi-column type's
    # synthesized backing column (e.g. a plain field literally named
    # "<url_field>_title" predating the validation that now blocks this).
    # Independent of DB introspection -- purely a field-definition check --
    # so it runs even if the table itself can't be introspected below.
    for collision in detect_backing_column_collisions(cot):
        entry = {
            "type": "backing_column_collision",
            "field": collision["field"],
            "safe_to_rename": collision["safe_to_rename"],
            "message": collision["message"],
        }
        warned.append(entry)
        logger.warning(entry["message"])

    try:
        actual_names = _actual_column_names(table_name, using=using)
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
            with connections[using].schema_editor() as editor:
                # Flush pending DEFERRABLE FK trigger events before ALTER TABLE;
                # PostgreSQL rejects ADD COLUMN when deferred triggers are pending.
                editor.execute('SET CONSTRAINTS ALL IMMEDIATE')
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
        cot.__class__.objects.using(using).filter(pk=cot.pk).update(schema_document=doc)
        cot.schema_document = doc

    return {"added": added, "warned": warned}


def heal_all_cots(verbosity=1, dry_run=False, using=DEFAULT_DB_ALIAS):
    """
    Run heal_cot() for every CustomObjectType.

    Called by the post_migrate signal handler.  The upgrade_custom_objects
    management command iterates COTs directly so it can print per-COT output
    to stdout.  heal_branch() calls this with using set to a branch's own
    connection, from inside activate_branch(), to heal that branch's schema.

    Returns
    -------
    dict with keys:
      "total"    : number of COTs checked
      "healed"   : number of COTs that had columns added
      "warnings" : total number of non-auto-fixable issues
    """
    from netbox_custom_objects.models import CustomObjectType  # noqa: PLC0415

    total = healed = warnings = 0

    for cot in CustomObjectType.objects.using(using).all():
        total += 1
        result = heal_cot(cot, verbosity=verbosity, dry_run=dry_run, using=using)
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


def heal_branch(branch, verbosity=1, dry_run=False):
    """
    Run heal_all_cots() against a single Branch's own PostgreSQL schema.

    A schema change like #496's <name>_title column ships with no Django
    migration of its own (URLFieldType.get_model_field() just changes what
    columns a dynamically generated model has -- there's no migration file
    for netbox-branching's MigrationExecutor to detect as "pending" against
    a branch). Branch.migrate() therefore has nothing to apply and never
    fires, so this can't rely on being triggered by a branch's own migrate
    step or its post_migrate signal. heal_all_branches() (below) is the
    reliable trigger: it's called unconditionally from the main upgrade
    path (post_migrate on the default connection, and
    `manage.py upgrade_custom_objects`), independent of any branch's
    migration state.

    activate_branch() makes get_model() resolve each COT's branch-specific
    model variant (a branch may have renamed columns that don't exist in
    main); using=branch.connection_name makes the actual introspection/DDL
    target the branch's own schema, not main's. Both are required together.
    """
    from netbox_branching.utilities import activate_branch  # noqa: PLC0415

    with activate_branch(branch):
        return heal_all_cots(verbosity=verbosity, dry_run=dry_run, using=branch.connection_name)


def heal_all_branches(verbosity=1, dry_run=False):
    """
    Run heal_branch() for every Branch that has a real PostgreSQL schema.

    Called unconditionally from the main upgrade path (the post_migrate
    signal handler in __init__.py, and the upgrade_custom_objects management
    command) rather than from any branch-specific migration signal -- see
    heal_branch()'s docstring for why that signal can't be relied on.

    Returns immediately (all-zero summary) when netbox-branching isn't
    installed -- checked via apps.is_installed(), NOT a bare
    "import netbox_branching" try/except: netbox-branching can be pip-
    installed in the environment without being enabled in PLUGINS (e.g. a
    shared venv, or during the version-check window in checks.py), and in
    that case importing its models raises `RuntimeError: Model class ...
    isn't in an application in INSTALLED_APPS` -- not ImportError -- because
    the package itself resolves fine; only its Django app isn't registered.

    Excludes branches with no live schema: NEW and PROVISIONING haven't been
    provisioned yet (or are mid-provision); ARCHIVED has had its schema
    dropped. Every other status (READY, PENDING_MIGRATIONS, the transitional
    SYNCING/MIGRATING/MERGING/REVERTING, MERGED, FAILED) normally still has
    its schema and is a healing candidate, but two further checks are needed
    before actually touching one:

    - FAILED can mean provisioning itself failed after the schema was
      created (or the schema was since dropped) -- its status alone doesn't
      guarantee a live schema the way READY etc. do. Verified explicitly via
      _branch_schema_exists() rather than assumed from status, since healing
      through a branch connection whose schema doesn't exist would silently
      introspect and alter *main*'s tables instead (see that helper's
      docstring) -- exactly the kind of silent cross-branch corruption this
      sweep must not risk.
    - A branch with pending migrations has an ORM that may not match its
      actual (outdated) schema yet -- heal_cot() could misdiagnose drift, or
      the branch's schema might be missing entire unrelated tables/columns
      current model code assumes exist. Skipped here in favor of
      _heal_branch_on_migrate() (netbox_branching's own per-branch
      post_migrate receiver in __init__.py), which re-heals that branch once
      it actually migrates and its schema catches up.

    Each branch's pending_migrations check and heal_branch() call are wrapped
    individually (one shared try/finally) so one branch's unexpected failure
    doesn't abort the sweep for every other branch, and its connection is
    always closed afterward rather than accumulating across the loop;
    heal_cot()'s own per-COT try/except handles introspection failures
    within a single otherwise-healthy branch.

    Returns
    -------
    dict with keys:
      "total"    : number of branches checked
      "healed"   : number of branches with at least one COT healed
      "warnings" : total number of non-auto-fixable issues across all branches
    """
    if not django_apps.is_installed('netbox_branching'):
        return {"total": 0, "healed": 0, "warnings": 0}

    from netbox_branching.choices import BranchStatusChoices  # noqa: PLC0415
    from netbox_branching.models import Branch  # noqa: PLC0415

    no_schema_statuses = (
        BranchStatusChoices.NEW,
        BranchStatusChoices.PROVISIONING,
        BranchStatusChoices.ARCHIVED,
    )

    total = healed = warnings = 0
    for branch in Branch.objects.exclude(status__in=no_schema_statuses):
        total += 1

        if not _branch_schema_exists(branch.schema_name):
            logger.warning(
                "upgrade_custom_objects: skipping branch %r (id=%s, status=%s) -- schema %r "
                "does not exist (likely a failed or interrupted provision); it will be healed "
                "once it has a live schema.",
                branch.name, branch.pk, branch.status, branch.schema_name,
            )
            warnings += 1
            continue

        # pending_migrations and heal_branch() share one try/finally: both open a
        # connection on branch.connection_name (pending_migrations via MigrationExecutor),
        # and neither closes it. netbox_branching hit the same leak in its own per-branch
        # migration sweep (issue #581) -- accumulating open connections across many
        # branches can exhaust PostgreSQL's connection limit during `manage.py migrate`.
        # The shared try also means a broken pending_migrations check can't abort the
        # sweep for every other branch, matching heal_branch()'s own isolation.
        try:
            if branch.pending_migrations:
                if verbosity >= 2:
                    logger.info(
                        "upgrade_custom_objects: skipping branch %r (id=%s) -- has pending "
                        "migrations; will be healed by its own post_migrate hook once migrated.",
                        branch.name, branch.pk,
                    )
                continue

            result = heal_branch(branch, verbosity=verbosity, dry_run=dry_run)
        except Exception:
            logger.exception(
                "upgrade_custom_objects: unexpected error healing branch %r (id=%s)",
                branch.name, branch.pk,
            )
            warnings += 1
            continue
        finally:
            connections[branch.connection_name].close()

        if result["healed"]:
            healed += 1
        warnings += result["warnings"]

    if verbosity >= 2:
        logger.info(
            "upgrade_custom_objects: %d branch(es) checked, %d healed, %d warning(s)",
            total, healed, warnings,
        )
    elif verbosity >= 1 and (healed > 0 or warnings > 0):
        logger.info(
            "upgrade_custom_objects: %d branch(es) healed, %d warning(s)",
            healed, warnings,
        )

    return {"total": total, "healed": healed, "warnings": warnings}
