"""
Branching support for custom object tables.

Add a frozen ``db_column`` to ``CustomObjectTypeField``.  The physical column
name is set once at creation and never updated, so renames are metadata-only
and cannot mismatch across schemas (branch vs. main).  Existing rows are
back-filled with ``db_column = name``.

(The non-DEFERRABLE FK constraint fix that branching also requires is
performed by 0011_non_deferrable_fk_constraints.)
"""

from django.db import migrations, models


def backfill_db_column(apps, schema_editor):
    """Set db_column = name for all existing CustomObjectTypeField rows."""
    CustomObjectTypeField = apps.get_model('netbox_custom_objects', 'CustomObjectTypeField')
    CustomObjectTypeField.objects.filter(db_column='').update(db_column=models.F('name'))


class Migration(migrations.Migration):

    dependencies = [
        ('netbox_custom_objects', '0013_polymorphic_object_fields'),
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
