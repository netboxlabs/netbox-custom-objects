"""
Tests for deletion scenarios with cascading effects.

Uses TransactionTestCase so that DDL statements (CREATE/DROP TABLE) issued during
setup and teardown are not wrapped in Django's per-test rollback transaction.  That
lets us verify table-level changes and FK CASCADE behaviour that cannot be observed
inside a rolled-back savepoint.
"""
from django.apps import apps as django_apps
from django.db import connection
from django.test import TransactionTestCase

from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site
from netbox_custom_objects.constants import APP_LABEL
from netbox_custom_objects.models import CustomObjectType, CustomObjectTypeField

from .base import CustomObjectsTestCase


class DeletionTestCase(CustomObjectsTestCase, TransactionTestCase):
    """Test deletion scenarios with cascading effects."""

    def setUp(self):
        """Purge stale dynamic models left in the app registry by earlier TestCase
        classes whose setUpTestData transaction was rolled back (dropping the backing
        tables).  Leaving them registered causes Django's cascade-delete collector to
        query non-existent tables when a related core object is deleted.
        """
        super().setUp()
        stale = [
            name
            for name, model in list(django_apps.all_models.get(APP_LABEL, {}).items())
            if getattr(model, '_generated_table_model', False)
        ]
        for name in stale:
            django_apps.all_models[APP_LABEL].pop(name, None)
        if stale:
            django_apps.clear_cache()

    def tearDown(self):
        """Remove any COTs that were not deleted as part of the test so that
        their backing tables are dropped before the database flush."""
        for cot in CustomObjectType.objects.all():
            try:
                cot.delete()
            except Exception as exc:
                # Log but do not re-raise: tearDown must not mask the original
                # test failure.  A best-effort cleanup is still better than none.
                print(f"WARNING: tearDown could not delete COT {cot.pk}: {exc}")
        super().tearDown()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _table_exists(self, table_name):
        with connection.cursor() as cursor:
            return table_name in connection.introspection.table_names(cursor)

    def _field_exists_on_model(self, model, field_name):
        return field_name in {f.name for f in model._meta.get_fields()}

    # ------------------------------------------------------------------
    # Tests
    # ------------------------------------------------------------------

    def test_delete_cot_with_instances(self):
        """#140 – Deleting a COT must drop the backing table (and therefore all instances)."""
        cot = self.create_simple_custom_object_type(name='deltest', slug='del-test')
        model = cot.get_model()
        table_name = cot.get_database_table_name()

        model_name = model.__name__.lower()
        model.objects.create(name='Instance 1')
        model.objects.create(name='Instance 2')
        self.assertEqual(model.objects.count(), 2)
        self.assertTrue(self._table_exists(table_name))
        self.assertIn(model_name, django_apps.all_models.get(APP_LABEL, {}))

        cot.delete()

        self.assertFalse(
            self._table_exists(table_name),
            f"Table '{table_name}' should have been dropped when the COT was deleted.",
        )
        self.assertNotIn(
            model_name,
            django_apps.all_models.get(APP_LABEL, {}),
            "Deleted COT's model must be removed from the app registry.",
        )

    def test_delete_co_referenced_by_another_co(self):
        """#283 – Deleting a CO that is the target of an object field should CASCADE."""
        cot_a = self.create_simple_custom_object_type(name='typea', slug='type-a')
        cot_b = self.create_simple_custom_object_type(name='typeb', slug='type-b')

        # cot_b.ref_a → cot_a (FK CASCADE via _ensure_field_fk_constraint)
        self.create_custom_object_type_field(
            cot_b,
            name='ref_a',
            label='Reference A',
            type='object',
            related_object_type=cot_a.object_type,
        )

        model_a = cot_a.get_model()
        model_b = cot_b.get_model()

        obj_a = model_a.objects.create(name='Object A')
        obj_b = model_b.objects.create(name='Object B', ref_a=obj_a)
        self.assertEqual(obj_b.ref_a_id, obj_a.pk)

        # Deleting obj_a should cascade and remove obj_b
        obj_a.delete()

        self.assertFalse(
            model_b.objects.filter(pk=obj_b.pk).exists(),
            "Custom Object B should have been deleted by FK CASCADE when Object A was deleted.",
        )

    def test_delete_cot_referenced_by_another_cot(self):
        """#183 – Deleting a COT must also clean up object fields in other COTs that reference it."""
        cot_target = self.create_simple_custom_object_type(name='target', slug='target-type')
        cot_source = self.create_simple_custom_object_type(name='source', slug='source-type')

        ref_field = self.create_custom_object_type_field(
            cot_source,
            name='ref_target',
            label='Reference Target',
            type='object',
            related_object_type=cot_target.object_type,
        )
        ref_field_id = ref_field.id

        # Deleting the target COT must remove the field that references it
        cot_target.delete()

        self.assertFalse(
            CustomObjectTypeField.objects.filter(pk=ref_field_id).exists(),
            "Field referencing the deleted COT should have been removed.",
        )
        # The source COT itself must survive
        self.assertTrue(CustomObjectType.objects.filter(pk=cot_source.pk).exists())

    def test_delete_cotf_with_data(self):
        """#367 – Deleting a field whose instances already contain data should succeed."""
        cot = self.create_simple_custom_object_type(name='fielddeltest', slug='field-del-test')

        extra_field = self.create_custom_object_type_field(
            cot,
            name='extra',
            label='Extra',
            type='text',
        )
        model = cot.get_model()

        model.objects.create(name='Item 1', extra='value1')
        model.objects.create(name='Item 2', extra='value2')

        # Deletion should not raise even though rows contain data in 'extra'
        extra_field.delete()

        # Regenerate the model and confirm the column is gone
        cot.clear_model_cache(cot.id)
        fresh_model = cot.get_model()

        self.assertFalse(
            self._field_exists_on_model(fresh_model, 'extra'),
            "Field 'extra' should no longer appear on the model after deletion.",
        )
        # Existing rows must still be accessible
        self.assertEqual(fresh_model.objects.count(), 2)

    def test_delete_referenced_core_object(self):
        """Deleting a core NetBox object must CASCADE to COs that reference it via an object field.

        The cascade is enforced at the database level via the ON DELETE CASCADE FK constraint
        added by _ensure_field_fk_constraint().  We use a raw-SQL DELETE to bypass Django's
        Python-level cascade collector, which can fail when stale dynamic models from other
        test classes remain in the app registry but their backing tables have been dropped.
        This approach also proves the database constraint is actually in effect.
        """
        site = Site.objects.create(name='Del Test Site', slug='del-test-site')
        manufacturer = Manufacturer.objects.create(name='Del Test Mfr', slug='del-test-mfr')
        device_type = DeviceType.objects.create(
            manufacturer=manufacturer, model='Del Test Type', slug='del-test-type'
        )
        role = DeviceRole.objects.create(
            name='Del Test Role', slug='del-test-role', color='aaaaaa'
        )
        device = Device.objects.create(
            name='Del Test Device', site=site, device_type=device_type, role=role
        )

        cot = self.create_simple_custom_object_type(name='devref', slug='dev-ref')
        self.create_custom_object_type_field(
            cot,
            name='device',
            label='Device',
            type='object',
            related_object_type=self.get_device_object_type(),
        )
        model = cot.get_model()

        co = model.objects.create(name='CO with Device', device=device)
        self.assertEqual(co.device_id, device.pk)

        # Delete the device via raw SQL to exercise the database-level FK CASCADE
        # constraint directly, bypassing Django's Python cascade collector (which can
        # be confused by stale dynamic models left in the app registry by other tests).
        device_pk = device.pk
        with connection.cursor() as cursor:
            cursor.execute('DELETE FROM dcim_device WHERE id = %s', [device_pk])

        # Verify the device row is actually gone.
        self.assertFalse(
            Device.objects.filter(pk=device_pk).exists(),
            "Device should have been deleted by the raw SQL DELETE.",
        )
        # The custom object must have been removed by the DB-level FK CASCADE.
        self.assertFalse(
            model.objects.filter(pk=co.pk).exists(),
            "Custom Object should have been deleted by the DB-level FK CASCADE when Device was deleted.",
        )
