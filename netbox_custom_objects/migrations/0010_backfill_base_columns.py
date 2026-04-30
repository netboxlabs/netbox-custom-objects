"""
Data migration: populate schema_document["base_columns"] for all existing
CustomObjectType rows that were created before this snapshot feature existed.

Strategy
--------
For each CustomObjectType whose schema_document does not yet contain a
"base_columns" key:

1. Introspect the actual DB table to get its current column names and
   nullability (this reflects ground truth, not model assumptions).
2. Subtract the names of user-defined fields (from CustomObjectTypeField) to
   isolate the columns contributed by the CustomObject base class / mixins.
3. Cross-reference with the live CustomObject abstract model to attach a
   Django field_class name to each base column where possible.
4. Write the result back to schema_document["base_columns"].

The reverse migration is intentionally a no-op: rolling back would leave
schema_document in a valid state (missing "base_columns" is the pre-feature
default), and the forward migration is idempotent (skips rows that already
have the key).

Note: this migration intentionally imports from live plugin code (CustomObject)
rather than using the historical ORM state.  CustomObject is an abstract base
class, not a tracked Django model, so its field definitions are not available
via apps.get_model().  Because CustomObject's base columns are intended to be
stable across plugin versions, this is safe.
"""

import logging

from django.conf import settings
from django.db import migrations

logger = logging.getLogger(__name__)


def backfill_base_columns(apps, schema_editor):
    from django.db import connection

    # Import the live abstract base to get field metadata.
    # See module docstring for rationale.
    from netbox_custom_objects.models import CustomObject, USER_TABLE_DATABASE_NAME_PREFIX  # noqa: PLC0415

    CustomObjectType = apps.get_model("netbox_custom_objects", "CustomObjectType")
    CustomObjectTypeField = apps.get_model("netbox_custom_objects", "CustomObjectTypeField")

    # Build a name → {field_class, null} map from CustomObject's abstract hierarchy.
    # _meta.get_fields() on an abstract model returns fields declared on it and its
    # abstract bases.  We filter to concrete fields (those with a "column" attribute).
    base_field_info = {}
    for f in CustomObject._meta.get_fields():
        if hasattr(f, "column") and f.column:
            base_field_info[f.name] = {
                "field_class": f.__class__.__name__,
                "null": getattr(f, "null", False),
            }
    # "id" comes from models.Model, which is a concrete base not tracked by
    # CustomObject's abstract _meta; add it explicitly.  Derive the class name
    # from DEFAULT_AUTO_FIELD so it matches whatever BigAutoField (or subclass)
    # concrete models use — CustomObject._meta.pk is always None for abstract
    # models, so we cannot read it from there.
    pk_class = getattr(
        settings, "DEFAULT_AUTO_FIELD", "django.db.models.BigAutoField"
    ).rsplit(".", 1)[-1]
    base_field_info.setdefault("id", {"field_class": pk_class, "null": False})

    for cot in CustomObjectType.objects.all():
        # Skip rows that already have the snapshot.
        if cot.schema_document and "base_columns" in cot.schema_document:
            continue

        table_name = f"{USER_TABLE_DATABASE_NAME_PREFIX}{cot.id}"

        user_field_names = set(
            CustomObjectTypeField.objects.filter(custom_object_type=cot)
            .values_list("name", flat=True)
        )

        try:
            with connection.cursor() as cursor:
                col_rows = connection.introspection.get_table_description(cursor, table_name)
        except Exception as exc:
            logger.warning(
                "backfill_base_columns: could not introspect table %r for COT %s: %s",
                table_name, cot.pk, exc,
            )
            continue

        # col_rows is a list of FieldInfo namedtuples; .name and .null_ok are stable
        # across supported Django/PostgreSQL versions.
        base_columns = []
        for col in sorted(col_rows, key=lambda c: c.name):
            if col.name in user_field_names:
                continue
            entry = {
                "name": col.name,
                "field_class": base_field_info.get(col.name, {}).get("field_class", "UnknownField"),
                "null": bool(col.null_ok),
            }
            base_columns.append(entry)

        doc = cot.schema_document or {}
        doc["base_columns"] = base_columns
        CustomObjectType.objects.filter(pk=cot.pk).update(schema_document=doc)


class Migration(migrations.Migration):

    dependencies = [
        ("netbox_custom_objects", "0009_alter_customobjecttype_version"),
    ]

    operations = [
        migrations.RunPython(backfill_base_columns, migrations.RunPython.noop),
    ]
