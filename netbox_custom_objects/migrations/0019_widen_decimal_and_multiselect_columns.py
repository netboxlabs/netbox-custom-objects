"""
Widen existing decimal and multi-select custom-object columns.

Decimal fields previously mapped to ``numeric(8,2)`` (capping values at 999,999.99,
while the UI form accepted 12 digits with 4 decimal places) and multi-select fields
to ``varchar(50)[]`` (while a choice value, like a select column, may be 100
characters). Both now use one shared size: ``numeric(16,4)`` for decimals, matching
NetBox's custom fields, and ``varchar(100)[]`` for multi-select arrays. New columns
get the new types from the schema editor; columns on already-created
``custom_objects_*`` tables must be widened in place.

Each of these changes rewrites the whole table on PostgreSQL (numeric 8,2 -> 16,4
changes the scale, and varchar(50)[] -> varchar(100)[] is not binary-coercible), so
every column of one table is altered in a single ``ALTER TABLE`` statement: one
rewrite per table, under one ACCESS EXCLUSIVE lock, however many columns it has.

The reverse is intentionally a no-op: narrowing back could fail or truncate for any
value that only fits the wider type.

See issue #769.
"""

from collections import defaultdict

from django.db import migrations

# The legacy column type that is widened, and what it becomes, keyed by field type.
# Only an exact legacy match is altered, so a column someone already widened further
# (e.g. numeric(20,6)) is left alone rather than narrowed.
LEGACY_TYPES = {
    "decimal": "numeric(8,2)",
    "multiselect": "character varying(50)[]",
}
TARGET_TYPES = {
    "decimal": "numeric(16,4)",
    "multiselect": "character varying(100)[]",
}


def _column_types(cursor, table_name):
    """{column_name: formatted type} for *table_name* in the current schema, or {} if it doesn't exist.

    ``information_schema.columns`` doesn't expose the element length or precision of
    array columns, so the type is read from ``pg_catalog`` via ``format_type()``, which
    renders exactly what ``ALTER COLUMN ... TYPE`` accepts (e.g. ``numeric(8,2)``,
    ``character varying(50)[]``).
    """
    cursor.execute(
        """
        SELECT a.attname, pg_catalog.format_type(a.atttypid, a.atttypmod)
        FROM pg_catalog.pg_attribute a
        JOIN pg_catalog.pg_class c ON c.oid = a.attrelid
        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = current_schema()
          AND c.relname = %s
          AND a.attnum > 0
          AND NOT a.attisdropped
        """,
        [table_name],
    )
    return dict(cursor.fetchall())


def _target_type(field_type, current_type):
    """The type *current_type* should become for a field of *field_type*, or None to leave it alone."""
    # Anything but the legacy type is skipped: a no-op on fresh installs and re-runs.
    if current_type != LEGACY_TYPES[field_type]:
        return None
    return TARGET_TYPES[field_type]


def widen_decimal_and_multiselect_columns(apps, schema_editor):
    """ALTER every decimal and multi-select column to its new type, one statement per table."""
    CustomObjectTypeField = apps.get_model("netbox_custom_objects", "CustomObjectTypeField")
    quote_name = schema_editor.quote_name

    # Drive off field metadata (not blind introspection of every custom_objects_*
    # column) so only user fields are touched, never base-model columns. Only
    # columns that predate this release are read (type, name, custom_object_type_id).
    fields = CustomObjectTypeField.objects.filter(
        type__in=list(TARGET_TYPES),
    ).order_by("custom_object_type_id", "name").values_list("custom_object_type_id", "name", "type")

    fields_by_table = defaultdict(list)
    for cot_id, name, field_type in fields:
        fields_by_table[f"custom_objects_{cot_id}"].append((name, field_type))

    with schema_editor.connection.cursor() as cursor:
        for table_name, table_fields in fields_by_table.items():
            column_types = _column_types(cursor, table_name)
            clauses = []
            for column_name, field_type in table_fields:
                current_type = column_types.get(column_name)
                if current_type is None:
                    continue
                target = _target_type(field_type, current_type)
                if target:
                    clauses.append(f"ALTER COLUMN {quote_name(column_name)} TYPE {target}")
            if clauses:
                cursor.execute(f"ALTER TABLE {quote_name(table_name)} " + ", ".join(clauses))


class Migration(migrations.Migration):

    dependencies = [
        ("netbox_custom_objects", "0018_alter_customobjecttypefield_schema_id"),
    ]

    operations = [
        migrations.RunPython(widen_decimal_and_multiselect_columns, migrations.RunPython.noop),
    ]
