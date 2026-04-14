from django.db import migrations, transaction


def ensure_existing_fk_constraints(apps, schema_editor):
    """
    Go through all existing CustomObjectType models and ensure FK constraints
    are properly set for any OBJECT type fields.
    """
    # Fast path: if there are no rows, there is nothing to do.  We check via
    # raw SQL (SELECT id only) to avoid touching columns that may not exist in
    # the DB schema yet when this migration runs — e.g. group_name is added by
    # the *next* migration (0004), so the live model class already declares it,
    # but the column is absent from the table until 0004 runs.  A plain
    # CustomObjectType.objects.all() would generate SELECT … group_name … and
    # raise a ProgrammingError even when the table is empty, which causes a
    # confusing warning on every fresh install.
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            "SELECT EXISTS(SELECT 1 FROM netbox_custom_objects_customobjecttype)"
        )
        has_rows = cursor.fetchone()[0]

    if not has_rows:
        return

    # Import the actual model class (not the historical version) to access methods.
    # On an upgrade where this migration and 0004 are applied together, the live
    # model may reference columns that don't exist yet.  We use a savepoint so
    # that if the ORM query fails we can roll back to a clean transaction state.
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
