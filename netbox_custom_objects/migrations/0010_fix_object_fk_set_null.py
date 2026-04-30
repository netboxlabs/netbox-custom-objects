"""
Data migration: change ON DELETE behavior for Object-type field FK constraints
from CASCADE to SET NULL.

Previously, deleting a referenced object (e.g. a Contact) would silently delete
all Custom Objects that held a reference to it via an Object-type field.  The
correct behaviour is to null the FK column so the Custom Object is preserved.

This migration iterates every CustomObjectType, introspects the FK constraints on
its dynamic table for Object-type fields, drops any CASCADE constraint found, and
recreates it as SET NULL.
"""

import logging

from django.db import migrations

logger = logging.getLogger(__name__)


def fix_object_fk_constraints(apps, schema_editor):
    from django.db import connection

    from extras.choices import CustomFieldTypeChoices

    CustomObjectTypeField = apps.get_model("netbox_custom_objects", "CustomObjectTypeField")

    object_fields = CustomObjectTypeField.objects.filter(
        type=CustomFieldTypeChoices.TYPE_OBJECT
    ).select_related("custom_object_type")

    for field in object_fields:
        cot = field.custom_object_type
        table_name = f"custom_objects_{cot.id}"
        # Object FK columns are stored with an _id suffix in PostgreSQL
        column_name = f"{field.name}_id"

        try:
            with connection.cursor() as cursor:
                # Find existing FK constraints on this column
                cursor.execute(
                    """
                    SELECT tc.constraint_name, kcu.column_name,
                           ccu.table_name AS referenced_table
                    FROM information_schema.table_constraints AS tc
                    JOIN information_schema.key_column_usage AS kcu
                        ON tc.constraint_name = kcu.constraint_name
                        AND tc.table_schema = kcu.table_schema
                    JOIN information_schema.constraint_column_usage AS ccu
                        ON ccu.constraint_name = tc.constraint_name
                        AND ccu.table_schema = tc.table_schema
                    WHERE tc.constraint_type = 'FOREIGN KEY'
                        AND tc.table_name = %s
                        AND kcu.column_name = %s
                    """,
                    [table_name, column_name],
                )
                rows = cursor.fetchall()

                for constraint_name, _col, referenced_table in rows:
                    # Check if this constraint is already SET NULL (skip if so)
                    cursor.execute(
                        """
                        SELECT update_rule, delete_rule
                        FROM information_schema.referential_constraints
                        WHERE constraint_name = %s
                        """,
                        [constraint_name],
                    )
                    ref_row = cursor.fetchone()
                    if ref_row and ref_row[1].upper() == "SET NULL":
                        continue  # already correct

                    cursor.execute(
                        f'ALTER TABLE "{table_name}" DROP CONSTRAINT IF EXISTS "{constraint_name}"'
                    )
                    new_name = f"{table_name}_{column_name}_fk"
                    cursor.execute(
                        f"""
                        ALTER TABLE "{table_name}"
                        ADD CONSTRAINT "{new_name}"
                        FOREIGN KEY ("{column_name}")
                        REFERENCES "{referenced_table}" ("id")
                        ON DELETE SET NULL
                        DEFERRABLE INITIALLY DEFERRED
                        """
                    )
                    logger.info(
                        "fix_object_fk_constraints: updated %r.%r constraint to SET NULL",
                        table_name,
                        column_name,
                    )

        except Exception as exc:
            logger.warning(
                "fix_object_fk_constraints: could not fix constraint on %r.%r: %s",
                table_name,
                column_name,
                exc,
            )


class Migration(migrations.Migration):

    dependencies = [
        ("netbox_custom_objects", "0009_alter_customobjecttype_version"),
    ]

    operations = [
        migrations.RunPython(fix_object_fk_constraints, migrations.RunPython.noop),
    ]
