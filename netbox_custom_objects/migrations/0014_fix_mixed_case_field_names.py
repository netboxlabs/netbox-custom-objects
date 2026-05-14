import hashlib
import sys

from django.db import connection, migrations


# Reproduced from field_types._safe_pg_identifier — must not import from app code in migrations.
def _safe_pg_identifier(full_name):
    _PG_MAX_IDENTIFIER_LEN = 63
    if len(full_name) <= _PG_MAX_IDENTIFIER_LEN:
        return full_name
    digest = hashlib.md5(full_name.encode()).hexdigest()[:8]
    prefix = full_name[:_PG_MAX_IDENTIFIER_LEN - 9].rstrip("_")
    return f"{prefix}_{digest}"


def _column_exists(cursor, table_name, column_name):
    cursor.execute(
        """
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = %s
          AND column_name = %s
        """,
        [table_name, column_name],
    )
    return cursor.fetchone() is not None


def _table_exists(cursor, table_name):
    cursor.execute(
        """
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = current_schema()
          AND table_name = %s
        """,
        [table_name],
    )
    return cursor.fetchone() is not None


def fix_mixed_case_field_names(apps, schema_editor):
    CustomObjectTypeField = apps.get_model("netbox_custom_objects", "CustomObjectTypeField")

    mixed_case_fields = [
        f for f in CustomObjectTypeField.objects.select_related("custom_object_type").all()
        if f.name != f.name.lower()
    ]

    if not mixed_case_fields:
        return

    print("\nThe following CustomObjectTypeField records have mixed-case names.")
    print("Mixed-case names create quoted PostgreSQL identifiers, which can")
    print("cause query failures when referenced without quotes.\n")
    for field in mixed_case_fields:
        print(
            f"  COT #{field.custom_object_type_id} ({field.custom_object_type.name!r})"
            f"  field #{field.pk}: {field.name!r}  →  {field.name.lower()!r}"
        )
    print()

    if not sys.stdin.isatty():
        raise RuntimeError(
            "Mixed-case CustomObjectTypeField names detected but stdin is not a TTY "
            "(non-interactive run). Run this migration interactively to fix them. "
            "The migration has NOT been recorded as applied."
        )

    answer = input("Rename all fields to lowercase now? [y/N] ").strip().lower()
    if answer != "y":
        raise RuntimeError(
            "Aborted by user. The migration has NOT been recorded as applied "
            "and can be re-run once you are ready to rename the fields."
        )

    with connection.cursor() as cursor:
        for field in mixed_case_fields:
            cot_id = field.custom_object_type_id
            main_table = f"custom_objects_{cot_id}"
            old_name = field.name
            new_name = old_name.lower()
            field_type = field.type

            if field_type == "multiobject":
                # No column on the main table; rename the through table.
                # Both polymorphic and non-polymorphic use the same naming formula.
                old_through = _safe_pg_identifier(f"custom_objects_{cot_id}_{old_name}")
                new_through = _safe_pg_identifier(f"custom_objects_{cot_id}_{new_name}")
                if _table_exists(cursor, old_through):
                    cursor.execute(f'ALTER TABLE "{old_through}" RENAME TO "{new_through}"')

            elif field_type == "object" and field.is_polymorphic:
                # Polymorphic object: two concrete columns on the main table.
                for old_col, new_col in [
                    (f"{old_name}_content_type_id", f"{new_name}_content_type_id"),
                    (f"{old_name}_object_id", f"{new_name}_object_id"),
                ]:
                    if _column_exists(cursor, main_table, old_col):
                        cursor.execute(
                            f'ALTER TABLE "{main_table}" RENAME COLUMN "{old_col}" TO "{new_col}"'
                        )

            elif field_type == "object":
                # Non-polymorphic FK: Django stores the column as {name}_id.
                old_col = f"{old_name}_id"
                new_col = f"{new_name}_id"
                if _column_exists(cursor, main_table, old_col):
                    cursor.execute(
                        f'ALTER TABLE "{main_table}" RENAME COLUMN "{old_col}" TO "{new_col}"'
                    )

            else:
                # Scalar field: column name equals field name.
                if _column_exists(cursor, main_table, old_name):
                    cursor.execute(
                        f'ALTER TABLE "{main_table}" RENAME COLUMN "{old_name}" TO "{new_name}"'
                    )

            CustomObjectTypeField.objects.filter(pk=field.pk).update(name=new_name)

    print("Done. All mixed-case field names have been renamed to lowercase.")


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("netbox_custom_objects", "0013_polymorphic_object_fields"),
    ]

    operations = [
        migrations.RunPython(fix_mixed_case_field_names, reverse_code=noop),
    ]
