"""
Force FK constraints on custom_objects_* tables to be non-DEFERRABLE.

DEFERRABLE constraints queue trigger events that block subsequent ALTER TABLE
calls (e.g. remove_field during a branch revert) with "cannot ALTER TABLE
because it has pending trigger events". Recreate any existing DEFERRABLE FKs
on custom_objects_* tables as non-DEFERRABLE.
"""

from django.db import migrations


def fix_deferrable_fk_constraints(apps, schema_editor):
    """Re-create DEFERRABLE FK constraints on custom_objects_* tables as non-DEFERRABLE."""
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("""
            SELECT
                tc.table_name,
                tc.constraint_name,
                kcu.column_name,
                ccu.table_name AS foreign_table_name
            FROM information_schema.table_constraints AS tc
            JOIN information_schema.key_column_usage AS kcu
                ON tc.constraint_name = kcu.constraint_name
                AND tc.table_schema = kcu.table_schema
            JOIN information_schema.constraint_column_usage AS ccu
                ON ccu.constraint_name = tc.constraint_name
                AND ccu.table_schema = tc.table_schema
            JOIN information_schema.referential_constraints AS rc
                ON tc.constraint_name = rc.constraint_name
                AND tc.table_schema = rc.constraint_schema
            WHERE tc.constraint_type = 'FOREIGN KEY'
                AND tc.table_name LIKE 'custom_objects\\_%'
                AND tc.is_deferrable = 'YES'
        """)
        rows = cursor.fetchall()

        for table_name, constraint_name, column_name, foreign_table in rows:
            new_constraint_name = f'{table_name}_{column_name}_fk_cascade'
            cursor.execute(
                f'ALTER TABLE "{table_name}" DROP CONSTRAINT "{constraint_name}"'
            )
            cursor.execute(f"""
                ALTER TABLE "{table_name}"
                ADD CONSTRAINT "{new_constraint_name}"
                FOREIGN KEY ("{column_name}")
                REFERENCES "{foreign_table}" ("id")
                ON DELETE CASCADE
            """)


class Migration(migrations.Migration):

    dependencies = [
        ('netbox_custom_objects', '0010_backfill_base_columns'),
    ]

    operations = [
        migrations.RunPython(
            fix_deferrable_fk_constraints,
            migrations.RunPython.noop,
        ),
    ]
