from django.db import migrations


def ensure_existing_fk_constraints(apps, schema_editor):
    """
    Go through all existing CustomObjectType models and ensure FK constraints
    are properly set for any OBJECT type fields.
    """
    # Import the actual model class (not the historical version) to access methods
    from netbox_custom_objects.models import CustomObjectType

    for custom_object_type in CustomObjectType.objects.all():
        try:
            model = custom_object_type.get_model()
            custom_object_type._ensure_all_fk_constraints(model)
        except Exception as e:
            print(f"Warning: Could not ensure FK constraints for {custom_object_type}: {e}")


class Migration(migrations.Migration):

    dependencies = [
        ('netbox_custom_objects', '0002_customobjecttype_cache_timestamp'),
    ]

    operations = [
        migrations.RunPython(
            ensure_existing_fk_constraints,
            reverse_code=migrations.RunPython.noop
        ),
    ]
