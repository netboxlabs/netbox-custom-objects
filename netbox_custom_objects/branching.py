"""
Branching support for NetBox Custom Objects.

When netbox-branching runs ``Branch.migrate()``, the Django migration pass only
handles normal app migrations.  Custom object field changes (add/alter/remove
column) are applied directly via the schema editor and are therefore invisible
to Django's migration framework.

To close that gap, ``on_branch_migrated`` fires after the migration pass and
reconciles each custom object type's physical schema in the branch against the
current field definitions in main.  The reconciliation mirrors what
``CustomObjectTypeField.save()`` already does when detecting old-vs-new field
state: it compares the branch's ``CustomObjectTypeField`` rows (snapshot from
provision time) with main's current rows, matched by primary key, and calls
``add_field`` / ``alter_field`` / ``remove_field`` for any differences.

``CustomObjectTypeField`` has ``ChangeLoggingMixin``, so its rows are replicated
into the branch schema at provision time — the same source-of-truth mechanism
used for all other branchable models.
"""

import logging

from django.db import connections

logger = logging.getLogger('netbox_custom_objects.branching')


def check_pending_branch_migrations():
    """
    Scan all READY branches at startup and mark any that have custom object
    schema drift as PENDING_MIGRATIONS.

    This catches drift that pre-dates the signal handler — for example, after
    upgrading the plugin on an instance that already has branches, or if field
    changes were applied while the application was not running.

    Called once from ``CustomObjectsPluginConfig.ready()`` after the DB is
    confirmed ready.
    """
    try:
        from netbox_branching.choices import BranchStatusChoices
        from netbox_branching.models import Branch
        from netbox_custom_objects.models import CustomObjectType
    except ImportError:
        return

    ready_branches = list(Branch.objects.filter(status=BranchStatusChoices.READY))
    if not ready_branches:
        return

    cots = list(CustomObjectType.objects.all())
    if not cots:
        return

    to_update = []
    for branch in ready_branches:
        branch_connection = connections[branch.connection_name]
        with branch_connection.cursor() as cursor:
            branch_tables = branch_connection.introspection.table_names(cursor)

        for cot in cots:
            if cot.get_database_table_name() not in branch_tables:
                continue
            if cot.has_branch_schema_drift(branch):
                branch.status = BranchStatusChoices.PENDING_MIGRATIONS
                to_update.append(branch)
                break  # One drifted COT is enough — no need to check the rest

    if to_update:
        Branch.objects.bulk_update(to_update, ['status'])
        logger.info(
            'Marked %d branch(es) as PENDING_MIGRATIONS at startup due to custom object schema drift',
            len(to_update),
        )


def on_custom_object_field_changed(sender, instance, **kwargs):
    """
    Mark any READY branches that contain the affected custom object type's table
    as PENDING_MIGRATIONS when a ``CustomObjectTypeField`` is created, modified,
    or deleted in the main schema.

    This surfaces the pending state in the branching UI exactly like a normal
    Django-migration, prompting users to click "Migrate Branch".  That action
    calls ``Branch.migrate()``, which fires ``on_branch_migrated`` and reconciles
    the physical column differences.

    Skipped when the change happens inside a branch context — the field edit only
    affects that branch's schema, not main, so no other branches need updating.
    """
    try:
        from netbox_branching.contextvars import active_branch
        if active_branch.get() is not None:
            return
    except ImportError:
        return

    from netbox_branching.choices import BranchStatusChoices
    from netbox_branching.models import Branch

    cot = instance.custom_object_type
    db_table = cot.get_database_table_name()

    ready_branches = list(Branch.objects.filter(status=BranchStatusChoices.READY))
    to_update = []
    for branch in ready_branches:
        branch_connection = connections[branch.connection_name]
        with branch_connection.cursor() as cursor:
            branch_tables = branch_connection.introspection.table_names(cursor)
        if db_table in branch_tables:
            branch.status = BranchStatusChoices.PENDING_MIGRATIONS
            to_update.append(branch)

    if to_update:
        Branch.objects.bulk_update(to_update, ['status'])
        logger.info(
            'Marked %d branch(es) as PENDING_MIGRATIONS due to field changes on %s',
            len(to_update),
            cot,
        )


def _fields_schema_differ(branch_f, main_f):
    """
    Return True if the two ``CustomObjectTypeField`` instances differ in any
    attribute that affects the physical DB column, meaning an ALTER TABLE is
    needed to bring the branch schema up to date.

    Excluded (application-level only, no DB impact):
    - required: enforced by forms/serializers; all field types use null=True
      regardless, so required never maps to a NOT NULL column constraint.
    - default: Python-level default applied by the ORM, not a DB DEFAULT
      clause; changing it on an existing column needs no ALTER TABLE.
    """
    return (
        branch_f.name != main_f.name
        or branch_f.type != main_f.type
        or branch_f.unique != main_f.unique
        or branch_f.related_object_type_id != main_f.related_object_type_id
    )


def on_branch_migrated(sender, branch, user, **kwargs):
    """
    Reconcile each custom object type's physical schema in the branch against
    the current field definitions in main.

    For each ``CustomObjectType`` whose table exists in the branch schema:
      - Fields present in main but absent from the branch → ``add_field``
      - Fields absent from main but present in the branch → ``remove_field``
      - Fields present in both with differing definitions → ``alter_field``

    Matching is done by primary key so renames are detected correctly (the pk
    exists in both, with different ``name`` values) rather than being treated
    as an unrelated delete + add.
    """
    from netbox_branching.utilities import activate_branch
    from netbox_custom_objects.models import (
        CustomObjectType,
        _schema_add_field,
        _schema_alter_field,
        _schema_remove_field,
    )

    branch_connection = connections[branch.connection_name]

    with branch_connection.cursor() as cursor:
        branch_tables = branch_connection.introspection.table_names(cursor)

    for cot in CustomObjectType.objects.all():
        db_table = cot.get_database_table_name()
        if db_table not in branch_tables:
            # Table absent — COT was created after this branch was provisioned
            # and the branch hasn't been synced yet.  Skip; the user needs to
            # sync first to pull in the new table.
            logger.debug('Skipping %s — table %r not in branch schema', cot, db_table)
            continue

        # Main's current field definitions (queried from the public schema since
        # active_branch is not set here).
        main_fields = {
            f.pk: f
            for f in cot.fields.select_related('related_object_type', 'choice_set').all()
        }

        # Branch's field snapshot (as of provision time, or last sync).
        with activate_branch(branch):
            branch_fields = {
                f.pk: f
                for f in cot.fields.select_related('related_object_type', 'choice_set').all()
            }

        main_pks = set(main_fields)
        branch_pks = set(branch_fields)

        to_add = [main_fields[pk] for pk in main_pks - branch_pks]
        to_remove = [branch_fields[pk] for pk in branch_pks - main_pks]
        to_alter = [
            (branch_fields[pk], main_fields[pk])          # (old, new)
            for pk in main_pks & branch_pks
            if _fields_schema_differ(branch_fields[pk], main_fields[pk])
        ]

        if not (to_add or to_remove or to_alter):
            continue

        logger.info(
            'Migrating branch schema for %s: %d add, %d remove, %d alter',
            cot, len(to_add), len(to_remove), len(to_alter),
        )

        model = cot.get_model()

        with branch_connection.schema_editor() as schema_editor:

            for fi in to_add:
                logger.debug('add_field %r on %s', fi.name, cot)
                _schema_add_field(fi, model, schema_editor, branch_connection)

            for fi in to_remove:
                logger.debug('remove_field %r on %s', fi.name, cot)
                _schema_remove_field(fi, model, schema_editor, existing_tables=branch_tables)

            for old_fi, new_fi in to_alter:
                logger.debug('alter_field %r → %r on %s', old_fi.name, new_fi.name, cot)
                _schema_alter_field(
                    old_fi, new_fi, model, schema_editor, branch_connection,
                    existing_tables=branch_tables,
                )
