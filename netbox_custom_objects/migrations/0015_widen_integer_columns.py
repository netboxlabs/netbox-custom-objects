"""
Widen existing integer custom-object columns from 32-bit to 64-bit (bigint).

Integer fields previously mapped to Django's IntegerField, i.e. a 32-bit signed
PostgreSQL ``integer`` column (max 2_147_483_647). They now map to BigIntegerField
(``bigint``). New columns are created as bigint automatically by the schema editor,
but columns on already-created custom_objects_* tables must be widened in place.

``integer -> bigint`` is a lossless widening (every int32 value fits in int64), so
no USING clause or data transformation is needed. The conversion rewrites the table
under an ACCESS EXCLUSIVE lock; this is fine for typical custom-object table sizes.

The reverse is intentionally a no-op: narrowing bigint back to integer could fail or
lose data for any value outside the 32-bit range.

See issue #532.
"""

from django.db import migrations


def widen_integer_columns(apps, schema_editor):
    """ALTER every integer-typed custom-object column to bigint, in place."""
    CustomObjectTypeField = apps.get_model("netbox_custom_objects", "CustomObjectTypeField")

    # Drive off field metadata (not blind introspection of every custom_objects_*
    # column) so we only touch user integer fields, never base-model columns
    # inherited from NetBox mixins. After migration 0014 all field names are
    # lowercase and the scalar column name equals the field name exactly.
    integer_fields = CustomObjectTypeField.objects.filter(type="integer")

    with schema_editor.connection.cursor() as cursor:
        for field in integer_fields:
            table_name = f"custom_objects_{field.custom_object_type_id}"
            column_name = field.name

            # Idempotent + safe: only act on a column that exists and is still a
            # 32-bit integer. A no-op on fresh installs (already bigint) and on
            # partial re-runs.
            cursor.execute(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = %s
                  AND column_name = %s
                  AND data_type = 'integer'
                """,
                [table_name, column_name],
            )
            if cursor.fetchone():
                # quote_name() both identifiers (consistent with the %s-parameterised
                # check above) so a field name containing a quote can't break out.
                cursor.execute(
                    f"ALTER TABLE {schema_editor.quote_name(table_name)} "
                    f"ALTER COLUMN {schema_editor.quote_name(column_name)} TYPE bigint"
                )


class Migration(migrations.Migration):

    dependencies = [
        ("netbox_custom_objects", "0014_fix_mixed_case_field_names"),
    ]

    operations = [
        migrations.RunPython(widen_integer_columns, migrations.RunPython.noop),
    ]
