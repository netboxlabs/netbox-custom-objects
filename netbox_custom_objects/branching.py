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

from django.db import connections, models

logger = logging.getLogger('netbox_custom_objects.branching')


def _fields_schema_differ(branch_f, main_f):
    """
    Return True if the two ``CustomObjectTypeField`` instances differ in any
    attribute that affects the physical DB column, meaning an ALTER TABLE is
    needed to bring the branch schema up to date.
    """
    return (
        branch_f.name != main_f.name
        or branch_f.type != main_f.type
        or branch_f.required != main_f.required
        or branch_f.default != main_f.default
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
    from extras.choices import CustomFieldTypeChoices
    from netbox_branching.utilities import activate_branch
    from netbox_custom_objects.constants import APP_LABEL
    from netbox_custom_objects.field_types import FIELD_TYPE_CLASS
    from netbox_custom_objects.models import CustomObjectType
    from netbox_custom_objects.utilities import generate_model

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

            # ── add_field ────────────────────────────────────────────────────
            for fi in to_add:
                logger.debug('add_field %r on %s', fi.name, cot)
                ft = FIELD_TYPE_CLASS[fi.type]()
                mf = ft.get_model_field(fi)
                mf.contribute_to_class(model, fi.name)
                schema_editor.add_field(model, mf)
                if fi.type == CustomFieldTypeChoices.TYPE_MULTIOBJECT:
                    ft.create_m2m_table(fi, model, fi.name, schema_conn=branch_connection)

            # ── remove_field ─────────────────────────────────────────────────
            for fi in to_remove:
                logger.debug('remove_field %r on %s', fi.name, cot)
                ft = FIELD_TYPE_CLASS[fi.type]()
                mf = ft.get_model_field(fi)
                mf.contribute_to_class(model, fi.name)
                if fi.type == CustomFieldTypeChoices.TYPE_MULTIOBJECT:
                    through_table = f'custom_objects_{cot.pk}_{fi.name}'
                    if through_table in branch_tables:
                        ThroughMeta = type(
                            'Meta', (),
                            {'db_table': through_table, 'app_label': APP_LABEL, 'managed': True},
                        )
                        through_model = type(
                            f'_TempThrough_{through_table}',
                            (models.Model,),
                            {'Meta': ThroughMeta, '__module__': 'netbox_custom_objects.models'},
                        )
                        schema_editor.delete_model(through_model)
                schema_editor.remove_field(model, mf)

            # ── alter_field ──────────────────────────────────────────────────
            for old_fi, new_fi in to_alter:
                logger.debug(
                    'alter_field %r → %r on %s',
                    old_fi.name, new_fi.name, cot,
                )
                old_mf = FIELD_TYPE_CLASS[old_fi.type]().get_model_field(old_fi)
                new_mf = FIELD_TYPE_CLASS[new_fi.type]().get_model_field(new_fi)
                old_mf.contribute_to_class(model, old_fi.name)
                new_mf.contribute_to_class(model, new_fi.name)

                # When a MULTIOBJECT field is renamed, the through table must
                # be renamed first (same logic as CustomObjectTypeField.save()).
                if (
                    new_fi.type == CustomFieldTypeChoices.TYPE_MULTIOBJECT
                    and old_fi.name != new_fi.name
                ):
                    old_through = f'custom_objects_{cot.pk}_{old_fi.name}'
                    new_through = f'custom_objects_{cot.pk}_{new_fi.name}'
                    if old_through in branch_tables:
                        OldThroughMeta = type(
                            'Meta', (),
                            {'db_table': old_through, 'app_label': APP_LABEL, 'managed': True},
                        )
                        old_through_model = generate_model(
                            f'_TempOldThrough_{old_through}',
                            (models.Model,),
                            {
                                '__module__': 'netbox_custom_objects.models',
                                'Meta': OldThroughMeta,
                                'id': models.AutoField(primary_key=True),
                                'source': models.ForeignKey(
                                    model, on_delete=models.CASCADE,
                                    db_column='source_id', related_name='+',
                                ),
                                'target': models.ForeignKey(
                                    model, on_delete=models.CASCADE,
                                    db_column='target_id', related_name='+',
                                ),
                            },
                        )
                        schema_editor.alter_db_table(old_through_model, old_through, new_through)

                schema_editor.alter_field(model, old_mf, new_mf)
