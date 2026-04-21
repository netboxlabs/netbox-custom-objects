"""
Drop DEFERRABLE INITIALLY DEFERRED from FK constraints on custom object tables.

Prior to this migration, _ensure_field_fk_constraint() created FK constraints
with DEFERRABLE INITIALLY DEFERRED.  That attribute causes PostgreSQL to queue
trigger events that block subsequent ALTER TABLE calls (e.g. remove_field during
a branch revert), raising "cannot ALTER TABLE because it has pending trigger
events".

This migration finds all DEFERRABLE FK constraints on tables whose names start
with "custom_objects_" and recreates them as non-DEFERRABLE with ON DELETE
CASCADE, matching the behaviour of the updated _ensure_field_fk_constraint().
"""

from django.db import migrations


def fix_deferrable_fk_constraints(apps, schema_editor):
    """
    Re-create any DEFERRABLE FK constraints on custom object tables as
    non-DEFERRABLE.  Uses information_schema so no Django model loading
    is required — safe to run during the migration pass even though dynamic
    models are not yet registered.
    """
    with schema_editor.connection.cursor() as cursor:
        # Find all DEFERRABLE FK constraints on custom_objects_* tables.
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
                AND tc.table_name LIKE 'custom_objects\\_%%'
                AND rc.is_deferrable = 'YES'
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
        ('netbox_custom_objects', '0006_customobjecttypefield_related_name_and_more'),
    ]

    operations = [
        migrations.RunPython(
            fix_deferrable_fk_constraints,
            migrations.RunPython.noop,
        ),
    ]
