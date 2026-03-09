from django.db import migrations, transaction


def ensure_existing_fk_constraints(apps, schema_editor):
    """
    Go through all existing CustomObjectType models and ensure FK constraints
    are properly set for any OBJECT type fields.
    """
    # Import the actual model class (not the historical version) to access methods.
    # On a fresh install, later migrations may have added fields to the live model
    # that don't exist in the DB yet when this migration runs (e.g. group_name from 0004).
    # We use a savepoint so that if the ORM query fails, we can roll back to a clean
    # transaction state rather than leaving PostgreSQL in an aborted-transaction state.
    from netbox_custom_objects.models import CustomObjectType

    sid = transaction.savepoint()
    try:
        queryset = list(CustomObjectType.objects.all())
    except Exception as e:
        transaction.savepoint_rollback(sid)
        print(f"Warning: Could not query CustomObjectType during migration (skipping FK constraint check): {e}")
        return
    transaction.savepoint_commit(sid)

    for custom_object_type in queryset:
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
