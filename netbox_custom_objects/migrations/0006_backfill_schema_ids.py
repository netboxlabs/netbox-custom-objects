"""
Data migration: assign schema_id to existing CustomObjectTypeField rows that
predate the schema-format feature and never received one.

Strategy
--------
For each CustomObjectType:
  1. Find the current maximum schema_id already in use (may be 0 if none).
  2. Assign the next available integer to every field with schema_id=NULL,
     ordered by the field's primary-key (creation order) for determinism.
  3. Update next_schema_id on the parent CustomObjectType to the highest ID
     now assigned, so that future field additions continue from the right value.

The reverse operation is intentionally a no-op: rolling back would leave the
schema_id column in an indeterminate state, and re-running the forward
migration is safe (it only touches NULL rows).
"""

from django.db import migrations
from django.db.models import Max


# Exposed as a module-level name so tests can import and call it directly
# without going through the migration runner.
def assign_schema_ids(apps, schema_editor):
    CustomObjectType = apps.get_model('netbox_custom_objects', 'CustomObjectType')
    CustomObjectTypeField = apps.get_model('netbox_custom_objects', 'CustomObjectTypeField')

    for cot in CustomObjectType.objects.all():
        # Highest schema_id already in use for this COT (0 if none).
        current_max = (
            CustomObjectTypeField.objects
            .filter(custom_object_type=cot, schema_id__isnull=False)
            .aggregate(max_id=Max('schema_id'))['max_id'] or 0
        )

        # Assign the next integers to all unassigned fields, ordered by pk.
        next_id = current_max + 1
        for field in (
            CustomObjectTypeField.objects
            .filter(custom_object_type=cot, schema_id__isnull=True)
            .order_by('id')
        ):
            CustomObjectTypeField.objects.filter(pk=field.pk).update(schema_id=next_id)
            next_id += 1

        # Sync next_schema_id upward.  Never decrease it.
        highest_assigned = next_id - 1  # equals current_max when no NULLs existed
        if highest_assigned > cot.next_schema_id:
            CustomObjectType.objects.filter(pk=cot.pk).update(
                next_schema_id=highest_assigned
            )


class Migration(migrations.Migration):

    dependencies = [
        ('netbox_custom_objects', '0005_customobjecttype_next_schema_id_and_more'),
    ]

    operations = [
        migrations.RunPython(assign_schema_ids, migrations.RunPython.noop),
    ]