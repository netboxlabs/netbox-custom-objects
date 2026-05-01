"""
Tests for deletion scenarios with cascading effects.

Uses TransactionTestCase so that DDL statements (CREATE/DROP TABLE) issued during
setup and teardown are not wrapped in Django's per-test rollback transaction.  That
lets us verify table-level changes and FK SET NULL/CASCADE/PROTECT behaviour that
cannot be observed inside a rolled-back savepoint.
"""
from django.apps import apps as django_apps
from django.db import connection
from django.db.utils import IntegrityError
from django.test import TransactionTestCase

from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site
from netbox_custom_objects.choices import ObjectFieldOnDeleteChoices
from netbox_custom_objects.constants import APP_LABEL
from netbox_custom_objects.models import CustomObjectType, CustomObjectTypeField

from .base import CustomObjectsTestCase, TransactionCleanupMixin


class DeletionTestCase(TransactionCleanupMixin, CustomObjectsTestCase, TransactionTestCase):
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

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _table_exists(self, table_name):
        with connection.cursor() as cursor:
            return table_name in connection.introspection.table_names(cursor)

    def _field_exists_on_model(self, model, field_name):
        return field_name in {f.name for f in model._meta.get_fields()}

    def _make_device(self, suffix=""):
        """Create a minimal Device and return it."""
        site = Site.objects.create(name=f'Del Test Site{suffix}', slug=f'del-test-site{suffix}')
        manufacturer = Manufacturer.objects.create(name=f'Del Test Mfr{suffix}', slug=f'del-test-mfr{suffix}')
        device_type = DeviceType.objects.create(
            manufacturer=manufacturer, model=f'Del Test Type{suffix}', slug=f'del-test-type{suffix}'
        )
        role = DeviceRole.objects.create(
            name=f'Del Test Role{suffix}', slug=f'del-test-role{suffix}', color='aaaaaa'
        )
        return Device.objects.create(
            name=f'Del Test Device{suffix}', site=site, device_type=device_type, role=role
        )

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
        """#283/#471 – Deleting a CO that is the target of an object field must SET NULL
        the referencing field on the source CO, not delete the source CO."""
        cot_a = self.create_simple_custom_object_type(name='typea', slug='type-a')
        cot_b = self.create_simple_custom_object_type(name='typeb', slug='type-b')

        # cot_b.ref_a → cot_a (FK SET NULL via _ensure_field_fk_constraint)
        self.create_custom_object_type_field(
            cot_b,
            name='ref_a',
            label='Reference A',
            type='object',
            related_object_type=cot_a.object_type,
            on_delete_behavior=ObjectFieldOnDeleteChoices.SET_NULL,
        )

        model_a = cot_a.get_model()
        model_b = cot_b.get_model()

        obj_a = model_a.objects.create(name='Object A')
        obj_b = model_b.objects.create(name='Object B', ref_a=obj_a)
        self.assertEqual(obj_b.ref_a_id, obj_a.pk)

        # Deleting obj_a must set obj_b.ref_a to NULL and leave obj_b intact.
        obj_a.delete()

        self.assertTrue(
            model_b.objects.filter(pk=obj_b.pk).exists(),
            "Custom Object B must survive when Object A is deleted (SET NULL, not CASCADE).",
        )
        obj_b.refresh_from_db()
        self.assertIsNone(
            obj_b.ref_a_id,
            "The ref_a field on Object B must be NULL after Object A is deleted.",
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

    def test_delete_referenced_core_object_set_null(self):
        """#471 – on_delete_behavior=set_null: deleting the referenced core object must SET NULL
        on the CO field, not delete the CO.

        The SET NULL behaviour is enforced at the database level via the ON DELETE SET NULL
        FK constraint added by _ensure_field_fk_constraint().  We use a raw-SQL DELETE to
        bypass Django's Python-level cascade collector and prove the DB constraint is in effect.
        """
        device = self._make_device()

        cot = self.create_simple_custom_object_type(name='devref-sn', slug='dev-ref-sn')
        self.create_custom_object_type_field(
            cot,
            name='device',
            label='Device',
            type='object',
            related_object_type=self.get_device_object_type(),
            on_delete_behavior=ObjectFieldOnDeleteChoices.SET_NULL,
        )
        model = cot.get_model()

        co = model.objects.create(name='CO with Device', device=device)
        self.assertEqual(co.device_id, device.pk)

        device_pk = device.pk
        with connection.cursor() as cursor:
            cursor.execute('DELETE FROM dcim_device WHERE id = %s', [device_pk])

        self.assertFalse(Device.objects.filter(pk=device_pk).exists())
        self.assertTrue(
            model.objects.filter(pk=co.pk).exists(),
            "Custom Object must survive when Device is deleted (SET NULL).",
        )
        co.refresh_from_db()
        self.assertIsNone(co.device_id, "device field must be NULL after Device is deleted.")

    def test_delete_referenced_core_object_cascade(self):
        """on_delete_behavior=cascade: deleting the referenced core object must also delete the CO."""
        device = self._make_device(suffix='-casc')

        cot = self.create_simple_custom_object_type(name='devref-casc', slug='dev-ref-casc')
        self.create_custom_object_type_field(
            cot,
            name='device',
            label='Device',
            type='object',
            related_object_type=self.get_device_object_type(),
            on_delete_behavior=ObjectFieldOnDeleteChoices.CASCADE,
        )
        model = cot.get_model()

        co = model.objects.create(name='CO with Device Cascade', device=device)
        co_pk = co.pk
        device_pk = device.pk

        # Delete via raw SQL to exercise the DB-level CASCADE constraint directly.
        with connection.cursor() as cursor:
            cursor.execute('DELETE FROM dcim_device WHERE id = %s', [device_pk])

        self.assertFalse(Device.objects.filter(pk=device_pk).exists())
        self.assertFalse(
            model.objects.filter(pk=co_pk).exists(),
            "Custom Object must be deleted when Device is deleted (CASCADE).",
        )

    def test_delete_referenced_core_object_protect(self):
        """on_delete_behavior=protect: deleting the referenced core object must raise an error
        at the database level (RESTRICT), leaving both objects intact."""
        device = self._make_device(suffix='-prot')

        cot = self.create_simple_custom_object_type(name='devref-prot', slug='dev-ref-prot')
        self.create_custom_object_type_field(
            cot,
            name='device',
            label='Device',
            type='object',
            related_object_type=self.get_device_object_type(),
            on_delete_behavior=ObjectFieldOnDeleteChoices.PROTECT,
        )
        model = cot.get_model()

        co = model.objects.create(name='CO with Device Protect', device=device)
        device_pk = device.pk

        # The DB-level RESTRICT constraint should prevent deletion.
        # PostgreSQL raises an IntegrityError wrapping a ForeignKeyViolation.
        with self.assertRaises(IntegrityError, msg="RESTRICT should prevent deletion of the referenced Device"):
            with connection.cursor() as cursor:
                cursor.execute('DELETE FROM dcim_device WHERE id = %s', [device_pk])

        # Both objects must remain intact.
        self.assertTrue(Device.objects.filter(pk=device_pk).exists())
        self.assertTrue(model.objects.filter(pk=co.pk).exists())

    # Keep the original test name as an alias so existing test runs don't lose coverage.
    def test_delete_referenced_core_object(self):
        """Alias for test_delete_referenced_core_object_set_null (default behavior)."""
        self.test_delete_referenced_core_object_set_null()