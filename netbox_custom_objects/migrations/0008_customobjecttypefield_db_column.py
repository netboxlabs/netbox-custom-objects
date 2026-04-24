"""
Add ``db_column`` to CustomObjectTypeField and back-fill it from ``name``.

``db_column`` is frozen at field creation time so that subsequent renames are
pure metadata operations — the physical database column name never changes.
This prevents cross-schema column-name mismatches when a field is renamed in
one schema (e.g. a branch) and the model is then used to query a different
schema (e.g. main) that still has the original column name.

The data migration sets ``db_column = name`` for all existing fields so that
``effective_db_column`` returns the same value as before the migration.
"""

from django.db import migrations, models


def backfill_db_column(apps, schema_editor):
    """Set db_column = name for all existing CustomObjectTypeField rows."""
    CustomObjectTypeField = apps.get_model('netbox_custom_objects', 'CustomObjectTypeField')
    CustomObjectTypeField.objects.filter(db_column='').update(db_column=models.F('name'))


class Migration(migrations.Migration):

    dependencies = [
        ('netbox_custom_objects', '0007_fix_object_field_fk_deferrable'),
    ]

    operations = [
        migrations.AddField(
            model_name='customobjecttypefield',
            name='db_column',
            field=models.CharField(
                blank=True,
                default='',
                help_text=(
                    'Physical database column name. Set once at creation and never changed, '
                    'so renames are pure metadata changes that do not require DDL.'
                ),
                max_length=50,
                verbose_name='database column',
            ),
            preserve_default=False,
        ),
        migrations.RunPython(
            backfill_db_column,
            migrations.RunPython.noop,
        ),
    ]
