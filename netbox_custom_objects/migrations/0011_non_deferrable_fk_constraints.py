"""
Force FK constraints on custom_objects_* tables to be non-DEFERRABLE.

DEFERRABLE constraints queue trigger events that block subsequent ALTER TABLE
calls (e.g. remove_field during a branch revert) with "cannot ALTER TABLE
because it has pending trigger events". Recreate any existing DEFERRABLE FKs
on custom_objects_* tables as non-DEFERRABLE.
"""

import hashlib

from django.db import migrations

_PG_MAX_IDENTIFIER_LEN = 63


def _safe_constraint_name(table_name, column_name, suffix='_fk_cascade'):
    """
    Return a constraint name that fits within PostgreSQL's 63-char identifier limit.

    Uses the same truncate-and-hash strategy as field_types._safe_pg_identifier so
    that long through-table names (e.g. custom_objects_14_technical_account_accounts)
    combined with column names do not silently collide after PostgreSQL truncation.
    """
    full_name = f'{table_name}_{column_name}{suffix}'
    if len(full_name) <= _PG_MAX_IDENTIFIER_LEN:
        return full_name
    digest = hashlib.md5(full_name.encode()).hexdigest()[:8]
    prefix = full_name[:_PG_MAX_IDENTIFIER_LEN - 9].rstrip('_')
    return f'{prefix}_{digest}'


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
            new_constraint_name = _safe_constraint_name(table_name, column_name)
            # IF EXISTS on the old name handles partial re-runs where the old constraint
            # was already dropped in a previous (failed) attempt.
            cursor.execute(
                f'ALTER TABLE "{table_name}" DROP CONSTRAINT IF EXISTS "{constraint_name}"'
            )
            # Pre-drop the new name too: if a previous run converted this constraint and
            # then failed later (leaving the non-DEFERRABLE copy behind), the ADD below
            # would collide. Dropping first makes every iteration idempotent.
            cursor.execute(
                f'ALTER TABLE "{table_name}" DROP CONSTRAINT IF EXISTS "{new_constraint_name}"'
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
