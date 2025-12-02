# Generated migration to ensure FK constraints for existing OBJECT fields

from django.db import migrations


def ensure_existing_fk_constraints(apps, schema_editor):
    """
    Go through all existing CustomObjectType models and ensure FK constraints
    are properly set for any OBJECT type fields.

    This is needed because the _ensure_fk_constraints method was refactored to work
    on individual fields rather than all fields, and this migration ensures existing
    fields have proper CASCADE constraints.
    """
    CustomObjectType = apps.get_model('netbox_custom_objects', 'CustomObjectType')

    for custom_object_type in CustomObjectType.objects.all():
        try:
            # Get the dynamically generated model for this CustomObjectType
            model = custom_object_type.get_model()

            # Use the _ensure_all_fk_constraints method which processes all OBJECT fields
            # This method is kept specifically for migration purposes
            custom_object_type._ensure_all_fk_constraints(model)
        except Exception as e:
            # Log but don't fail the migration if a specific type has issues
            print(f"Warning: Could not ensure FK constraints for {custom_object_type}: {e}")


class Migration(migrations.Migration):

    dependencies = [
        ('netbox_custom_objects', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(
            ensure_existing_fk_constraints,
            reverse_code=migrations.RunPython.noop
        ),
    ]
