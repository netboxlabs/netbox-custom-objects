import hashlib

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


def _normalize_fk_constraint(cursor, table_name, new_col_name):
    """
    ALTER TABLE RENAME COLUMN leaves FK constraint names unchanged (they still
    reference the old mixed-case column name).  Find all FK constraints on the
    renamed column via pg_attribute (which IS updated by RENAME COLUMN), drop
    them, and recreate under the standard {table}_{col}_fk name so that
    _ensure_field_fk_constraint can manage them correctly going forward.
    """
    cursor.execute(
        """
        SELECT c.conname, ref.relname, c.confdeltype
        FROM pg_constraint c
        JOIN pg_class t  ON c.conrelid  = t.oid
        JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = ANY(c.conkey)
        JOIN pg_class ref ON c.confrelid = ref.oid
        WHERE t.relname = %s
          AND a.attname = %s
          AND c.contype = 'f'
        """,
        [table_name, new_col_name],
    )
    rows = cursor.fetchall()
    if not rows:
        return

    foreign_table = rows[0][1]
    on_delete_sql = {
        'a': 'NO ACTION', 'r': 'RESTRICT', 'c': 'CASCADE',
        'n': 'SET NULL',  'd': 'SET DEFAULT',
    }.get(rows[0][2], 'NO ACTION')

    for (constraint_name, _, _) in rows:
        cursor.execute(
            f'ALTER TABLE "{table_name}" DROP CONSTRAINT IF EXISTS "{constraint_name}"'
        )

    cursor.execute(
        f'ALTER TABLE "{table_name}"'
        f' ADD CONSTRAINT "{table_name}_{new_col_name}_fk"'
        f' FOREIGN KEY ("{new_col_name}")'
        f' REFERENCES "{foreign_table}" ("id")'
        f' ON DELETE {on_delete_sql}'
    )


def fix_mixed_case_field_names(apps, schema_editor):
    CustomObjectTypeField = apps.get_model("netbox_custom_objects", "CustomObjectTypeField")

    mixed_case_fields = [
        f for f in CustomObjectTypeField.objects.select_related("custom_object_type").all()
        if f.name != f.name.lower()
    ]

    if not mixed_case_fields:
        return

    # Detect case-collisions: e.g. 'fieldname' and 'FieldName' on the same COT.
    # The UniqueConstraint on (name, custom_object_type) is case-sensitive, so both
    # can coexist. Renaming either to lowercase would collide with the other's DB
    # column/table — we cannot resolve this automatically.
    lower_name_groups = {}
    for f in CustomObjectTypeField.objects.select_related("custom_object_type").all():
        key = (f.custom_object_type_id, f.name.lower())
        lower_name_groups.setdefault(key, []).append(f)

    collisions = [fields for fields in lower_name_groups.values() if len(fields) > 1]

    if collisions:
        print("\nCannot automatically rename: the following fields would collide after lowercasing.\n")
        for fields in collisions:
            names = ", ".join(repr(f.name) for f in fields)
            cot = fields[0].custom_object_type
            print(
                f"  COT #{cot.pk} ({cot.name!r}): {names} all lower to {fields[0].name.lower()!r}"
            )
        print("\nRename or delete the conflicting fields manually, then re-run this migration.")
        raise RuntimeError(
            "Case-collision detected among CustomObjectTypeField names. "
            "Manual intervention required before this migration can proceed."
        )

    print(f"\nRenaming {len(mixed_case_fields)} mixed-case CustomObjectTypeField name(s) to lowercase.\n")
    for field in mixed_case_fields:
        print(
            f"  COT #{field.custom_object_type_id} ({field.custom_object_type.name!r})"
            f"  field #{field.pk}: {field.name!r}  →  {field.name.lower()!r}"
        )
    print()

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
                    # RENAME COLUMN doesn't update FK constraint names; normalize them so
                    # _ensure_field_fk_constraint can find and manage them going forward.
                    _normalize_fk_constraint(cursor, main_table, new_col)

            else:
                # Scalar field: column name equals field name.
                if _column_exists(cursor, main_table, old_name):
                    cursor.execute(
                        f'ALTER TABLE "{main_table}" RENAME COLUMN "{old_name}" TO "{new_name}"'
                    )

            CustomObjectTypeField.objects.filter(pk=field.pk).update(name=new_name)

    print("Done. All mixed-case field names have been renamed to lowercase.")


class Migration(migrations.Migration):

    dependencies = [
        ("netbox_custom_objects", "0013_polymorphic_object_fields"),
    ]

    operations = [
        migrations.RunPython(fix_mixed_case_field_names, reverse_code=migrations.RunPython.noop),
    ]
