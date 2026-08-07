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
        # TransactionCleanupMixin.setUp() purges stale generated models and
        # CustomObjectsTestCase.setUp() creates the test user and client.
        super().setUp()

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

        # Generate source (model_b) first so it interns the target model; then
        # refresh cot_a so its Python-side cache_timestamp is current and
        # get_model() returns the same class that model_b's FK points to.
        model_b = cot_b.get_model()
        cot_a.refresh_from_db()
        model_a = cot_a.get_model()

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

    def test_delete_referenced_core_object(self):
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

    def test_delete_co_referenced_by_another_co_cascade(self):
        """CO-to-CO object field with CASCADE: deleting the target CO cascades to the source CO."""
        cot_target = self.create_simple_custom_object_type(name='casctarget', slug='casc-target')
        cot_source = self.create_simple_custom_object_type(name='cascsource', slug='casc-source')

        self.create_custom_object_type_field(
            cot_source,
            name='ref_target',
            label='Reference Target',
            type='object',
            related_object_type=cot_target.object_type,
            on_delete_behavior=ObjectFieldOnDeleteChoices.CASCADE,
        )

        # Generate source first so it interns the target model internally; then
        # refresh cot_target so its Python-side cache_timestamp is up-to-date and
        # get_model() returns the same class that model_source's FK points to.
        model_source = cot_source.get_model()
        cot_target.refresh_from_db()
        model_target = cot_target.get_model()

        obj_target = model_target.objects.create(name='Target Object')
        obj_source = model_source.objects.create(name='Source Object', ref_target=obj_target)
        obj_source_pk = obj_source.pk

        # Django ORM delete: collector walks _meta.related_objects and cascades.
        obj_target.delete()

        self.assertFalse(
            model_source.objects.filter(pk=obj_source_pk).exists(),
            "Source CO must be deleted when its CASCADE target CO is deleted.",
        )

    def test_delete_co_referenced_by_another_co_protect(self):
        """CO-to-CO object field with PROTECT: deleting the target CO raises ProtectedError."""
        from django.db.models import ProtectedError

        cot_target = self.create_simple_custom_object_type(name='prottarget', slug='prot-target')
        cot_source = self.create_simple_custom_object_type(name='protsource', slug='prot-source')

        self.create_custom_object_type_field(
            cot_source,
            name='ref_target',
            label='Reference Target',
            type='object',
            related_object_type=cot_target.object_type,
            on_delete_behavior=ObjectFieldOnDeleteChoices.PROTECT,
        )

        model_source = cot_source.get_model()
        cot_target.refresh_from_db()
        model_target = cot_target.get_model()

        obj_target = model_target.objects.create(name='Target Object')
        model_source.objects.create(name='Source Object', ref_target=obj_target)

        with self.assertRaises(ProtectedError):
            obj_target.delete()

        # Both objects must remain intact.
        self.assertTrue(
            model_target.objects.filter(pk=obj_target.pk).exists(),
            "Target CO must survive when deletion is blocked by PROTECT.",
        )

    def test_object_field_save_bumps_related_cot_cache_timestamp(self):
        """Creating a TYPE_OBJECT field must bump the related COT's cache_timestamp for cross-worker invalidation."""
        cot_target = self.create_simple_custom_object_type(name='cttarget', slug='ct-target')
        cot_source = self.create_simple_custom_object_type(name='ctsource', slug='ct-source')

        cot_target.refresh_from_db()
        initial_ts = cot_target.cache_timestamp

        self.create_custom_object_type_field(
            cot_source,
            name='ref_target',
            label='Reference Target',
            type='object',
            related_object_type=cot_target.object_type,
        )

        cot_target.refresh_from_db()
        self.assertGreater(
            cot_target.cache_timestamp,
            initial_ts,
            "Creating a TYPE_OBJECT field must bump the related COT's cache_timestamp.",
        )

    def test_object_field_save_clears_related_cot_model_cache(self):
        """Creating a TYPE_OBJECT field must evict the related COT's model from the in-process cache."""
        cot_target = self.create_simple_custom_object_type(name='mctarget', slug='mc-target')
        cot_source = self.create_simple_custom_object_type(name='mcsource', slug='mc-source')

        # Warm up the cache for the target COT.
        cot_target.get_model()
        self.assertTrue(CustomObjectType.is_model_cached(cot_target.id))

        self.create_custom_object_type_field(
            cot_source,
            name='ref_target',
            label='Reference Target',
            type='object',
            related_object_type=cot_target.object_type,
        )

        self.assertFalse(
            CustomObjectType.is_model_cached(cot_target.id),
            "Saving a TYPE_OBJECT field must evict the related COT's model from cache.",
        )

    def test_on_delete_behavior_change_bumps_related_cot_cache_timestamp(self):
        """Changing on_delete_behavior on an existing TYPE_OBJECT field must re-bump the related COT's timestamp."""
        cot_target = self.create_simple_custom_object_type(name='odtarget', slug='od-target')
        cot_source = self.create_simple_custom_object_type(name='odsource', slug='od-source')

        field = self.create_custom_object_type_field(
            cot_source,
            name='ref_target',
            label='Reference Target',
            type='object',
            related_object_type=cot_target.object_type,
            on_delete_behavior=ObjectFieldOnDeleteChoices.SET_NULL,
        )

        cot_target.refresh_from_db()
        ts_after_create = cot_target.cache_timestamp

        # Reload from DB so that from_db() populates _original (required by save()).
        field = CustomObjectTypeField.objects.get(pk=field.pk)
        field.on_delete_behavior = ObjectFieldOnDeleteChoices.PROTECT
        field.save()

        cot_target.refresh_from_db()
        self.assertGreater(
            cot_target.cache_timestamp,
            ts_after_create,
            "Changing on_delete_behavior must re-bump the related COT's cache_timestamp.",
        )

    def test_change_on_delete_behavior_protect_to_set_null(self):
        """Changing on_delete_behavior from PROTECT to SET_NULL on an existing field must update
        the DB-level FK constraint so that deleting the referenced object now sets the field to
        NULL instead of being blocked."""
        device = self._make_device(suffix='-chg-sn')

        cot = self.create_simple_custom_object_type(name='chgsn', slug='chg-sn')
        field = self.create_custom_object_type_field(
            cot,
            name='device',
            label='Device',
            type='object',
            related_object_type=self.get_device_object_type(),
            on_delete_behavior=ObjectFieldOnDeleteChoices.PROTECT,
        )
        model = cot.get_model()
        co = model.objects.create(name='CO Chg SN', device=device)
        device_pk = device.pk

        # Confirm PROTECT is in effect: raw DELETE must be blocked.
        with self.assertRaises(IntegrityError, msg="RESTRICT should block deletion before the change"):
            with connection.cursor() as cursor:
                cursor.execute('DELETE FROM dcim_device WHERE id = %s', [device_pk])

        # Change the field to SET_NULL.
        field = CustomObjectTypeField.objects.get(pk=field.pk)
        field.on_delete_behavior = ObjectFieldOnDeleteChoices.SET_NULL
        field.save()

        # Now deletion must succeed and set the FK to NULL.
        with connection.cursor() as cursor:
            cursor.execute('DELETE FROM dcim_device WHERE id = %s', [device_pk])

        self.assertFalse(Device.objects.filter(pk=device_pk).exists())
        self.assertTrue(
            model.objects.filter(pk=co.pk).exists(),
            "CO must survive after switching to SET_NULL and deleting the Device.",
        )
        co.refresh_from_db()
        self.assertIsNone(co.device_id, "device field must be NULL after Device is deleted.")

    def test_change_on_delete_behavior_protect_to_cascade(self):
        """Changing on_delete_behavior from PROTECT to CASCADE on an existing field must update
        the DB-level FK constraint so that deleting the referenced object now deletes the CO."""
        device = self._make_device(suffix='-chg-casc')

        cot = self.create_simple_custom_object_type(name='chgcasc', slug='chg-casc')
        field = self.create_custom_object_type_field(
            cot,
            name='device',
            label='Device',
            type='object',
            related_object_type=self.get_device_object_type(),
            on_delete_behavior=ObjectFieldOnDeleteChoices.PROTECT,
        )
        model = cot.get_model()
        co = model.objects.create(name='CO Chg Casc', device=device)
        co_pk = co.pk
        device_pk = device.pk

        # Confirm PROTECT is in effect.
        with self.assertRaises(IntegrityError, msg="RESTRICT should block deletion before the change"):
            with connection.cursor() as cursor:
                cursor.execute('DELETE FROM dcim_device WHERE id = %s', [device_pk])

        # Change the field to CASCADE.
        field = CustomObjectTypeField.objects.get(pk=field.pk)
        field.on_delete_behavior = ObjectFieldOnDeleteChoices.CASCADE
        field.save()

        # Now deletion must cascade and remove the CO.
        with connection.cursor() as cursor:
            cursor.execute('DELETE FROM dcim_device WHERE id = %s', [device_pk])

        self.assertFalse(Device.objects.filter(pk=device_pk).exists())
        self.assertFalse(
            model.objects.filter(pk=co_pk).exists(),
            "CO must be deleted after switching to CASCADE and deleting the Device.",
        )

    def test_protect_co_to_co_enforced_at_db_level(self):
        """The DB-level ON DELETE RESTRICT constraint blocks a raw-SQL DELETE that
        bypasses Django's collector for a CO-to-CO PROTECT field.

        Django's deletion collector raises ProtectedError before issuing any SQL, so it
        never exercises the DB constraint directly. This test verifies that the constraint
        itself is wired correctly by using a raw DELETE, mirroring the pattern used by
        test_delete_referenced_core_object_protect for core-model FKs.
        """
        cot_target = self.create_simple_custom_object_type(name='dbtarget', slug='db-target')
        cot_source = self.create_simple_custom_object_type(name='dbsource', slug='db-source')

        self.create_custom_object_type_field(
            cot_source,
            name='ref_target',
            label='Reference Target',
            type='object',
            related_object_type=cot_target.object_type,
            on_delete_behavior=ObjectFieldOnDeleteChoices.PROTECT,
        )

        model_source = cot_source.get_model()
        cot_target.refresh_from_db()
        model_target = cot_target.get_model()

        obj_target = model_target.objects.create(name='Target Object')
        model_source.objects.create(name='Source Object', ref_target=obj_target)

        target_table = cot_target.get_database_table_name()
        with self.assertRaises(IntegrityError,
                               msg="DB-level ON DELETE RESTRICT must block raw-SQL deletion of the target"):
            with connection.cursor() as cursor:
                cursor.execute(f'DELETE FROM {target_table} WHERE id = %s', [obj_target.pk])

        self.assertTrue(
            model_target.objects.filter(pk=obj_target.pk).exists(),
            "Target object must survive the failed deletion.",
        )

    # ------------------------------------------------------------------
    # Cross-COT multiobject (M2M) deletion – issue #483
    # ------------------------------------------------------------------

    def test_delete_source_co_with_cross_cot_multiobject_field(self):
        """#483 – Deleting a CO that is the SOURCE of a cross-COT M2M field
        succeeds and cascade-deletes the through rows."""
        cot_source = self.create_simple_custom_object_type(name='m2msrc', slug='m2m-src')
        cot_target = self.create_simple_custom_object_type(name='m2mtrg', slug='m2m-trg')

        self.create_custom_object_type_field(
            cot_source,
            name='refs',
            label='References',
            type='multiobject',
            related_object_type=cot_target.object_type,
        )

        # Per cross-COT FK convention: generate source first, refresh target, then target.
        model_source = cot_source.get_model()
        cot_target.refresh_from_db()
        model_target = cot_target.get_model()

        obj_target = model_target.objects.create(name='Target 1')
        obj_source = model_source.objects.create(name='Source 1')
        obj_source.refs.add(obj_target)

        m2m_field = model_source._meta.get_field('refs')
        through_model = m2m_field.remote_field.through
        self.assertEqual(through_model.objects.filter(source_id=obj_source.pk).count(), 1)

        # Deleting the source CO must cascade-delete through rows and succeed.
        obj_source.delete()

        self.assertFalse(
            model_source.objects.filter(pk=obj_source.pk).exists(),
            'Source CO should be deleted.',
        )
        self.assertEqual(
            through_model.objects.filter(source_id=obj_source.pk).count(),
            0,
            'Through rows must be deleted when the source CO is deleted.',
        )
        self.assertTrue(
            model_target.objects.filter(pk=obj_target.pk).exists(),
            'Target CO must survive when source CO is deleted.',
        )

    def test_delete_target_co_with_cross_cot_multiobject_field(self):
        """#483 – Deleting a CO that is the TARGET of a cross-COT M2M field
        succeeds and cascade-deletes the through rows."""
        cot_source = self.create_simple_custom_object_type(name='m2msrc2', slug='m2m-src2')
        cot_target = self.create_simple_custom_object_type(name='m2mtrg2', slug='m2m-trg2')

        self.create_custom_object_type_field(
            cot_source,
            name='refs',
            label='References',
            type='multiobject',
            related_object_type=cot_target.object_type,
        )

        model_source = cot_source.get_model()
        cot_target.refresh_from_db()
        model_target = cot_target.get_model()

        obj_target = model_target.objects.create(name='Target 2')
        obj_source = model_source.objects.create(name='Source 2')
        obj_source.refs.add(obj_target)

        m2m_field = model_source._meta.get_field('refs')
        through_model = m2m_field.remote_field.through
        self.assertEqual(through_model.objects.filter(target_id=obj_target.pk).count(), 1)

        # Deleting the target CO must cascade-delete through rows and succeed.
        obj_target.delete()

        self.assertFalse(
            model_target.objects.filter(pk=obj_target.pk).exists(),
            'Target CO should be deleted.',
        )
        self.assertEqual(
            through_model.objects.filter(target_id=obj_target.pk).count(),
            0,
            'Through rows must be deleted when the target CO is deleted.',
        )
        self.assertTrue(
            model_source.objects.filter(pk=obj_source.pk).exists(),
            'Source CO must survive when target CO is deleted.',
        )

    def test_delete_target_co_after_target_model_regeneration(self):
        """#483 – Deletion of the target CO succeeds even after the TARGET COT's
        model is regenerated (cache miss), which leaves the through model's target
        FK pointing at the old class.  The fix repoints the FK so the ORM-level
        cascade wires up correctly and the deletion succeeds."""
        cot_source = self.create_simple_custom_object_type(name='m2msrc3', slug='m2m-src3')
        cot_target = self.create_simple_custom_object_type(name='m2mtrg3', slug='m2m-trg3')

        self.create_custom_object_type_field(
            cot_source,
            name='refs',
            label='References',
            type='multiobject',
            related_object_type=cot_target.object_type,
        )

        model_source = cot_source.get_model()
        cot_target.refresh_from_db()
        model_target = cot_target.get_model()

        obj_target = model_target.objects.create(name='Target 3')
        obj_source = model_source.objects.create(name='Source 3')
        obj_source.refs.add(obj_target)

        m2m_field = model_source._meta.get_field('refs')
        through_model = m2m_field.remote_field.through
        self.assertEqual(through_model.objects.filter(target_id=obj_target.pk).count(), 1)

        # Force model regeneration for cot_target (simulates a cache-miss in production).
        CustomObjectType.clear_model_cache(cot_target.id)
        cot_target.refresh_from_db()
        model_target_v2 = cot_target.get_model()

        # Ensure we actually got a fresh class.
        obj_target_v2 = model_target_v2.objects.get(pk=obj_target.pk)

        # Deletion must succeed — DB-level CASCADE must clean up through rows even
        # if the ORM-level related_objects cache is stale.
        obj_target_v2.delete()

        self.assertFalse(
            model_target_v2.objects.filter(pk=obj_target.pk).exists(),
            'Target CO should be deleted after model regeneration.',
        )
        self.assertEqual(
            through_model.objects.filter(target_id=obj_target.pk).count(),
            0,
            'Through rows must be deleted (DB CASCADE) even after model regeneration.',
        )

    def test_delete_co_in_multi_hop_cross_cot_m2m_chain(self):
        """#483 – Complex cross-COT chain: A.refs→B, B.ports→C.
        Deleting a B instance must cascade-delete both A→B through rows and
        B→C through rows (B is both source and target in different M2M relations)."""
        cot_a = self.create_simple_custom_object_type(name='m2mcha', slug='m2m-ch-a')
        cot_b = self.create_simple_custom_object_type(name='m2mchb', slug='m2m-ch-b')
        cot_c = self.create_simple_custom_object_type(name='m2mchc', slug='m2m-ch-c')

        # A.refs → B (M2M)
        self.create_custom_object_type_field(
            cot_a,
            name='refs',
            label='References',
            type='multiobject',
            related_object_type=cot_b.object_type,
        )
        # B.ports → C (M2M)
        self.create_custom_object_type_field(
            cot_b,
            name='ports',
            label='Ports',
            type='multiobject',
            related_object_type=cot_c.object_type,
        )

        model_a = cot_a.get_model()
        cot_b.refresh_from_db()
        model_b = cot_b.get_model()
        cot_c.refresh_from_db()
        model_c = cot_c.get_model()

        obj_a = model_a.objects.create(name='A1')
        obj_b = model_b.objects.create(name='B1')
        obj_c = model_c.objects.create(name='C1')

        obj_a.refs.add(obj_b)
        obj_b.ports.add(obj_c)

        refs_field = model_a._meta.get_field('refs')
        through_ab = refs_field.remote_field.through
        ports_field = model_b._meta.get_field('ports')
        through_bc = ports_field.remote_field.through

        self.assertEqual(through_ab.objects.filter(target_id=obj_b.pk).count(), 1)
        self.assertEqual(through_bc.objects.filter(source_id=obj_b.pk).count(), 1)

        # Deleting B must cascade-delete both sets of through rows.
        obj_b.delete()

        self.assertFalse(model_b.objects.filter(pk=obj_b.pk).exists())
        self.assertEqual(
            through_ab.objects.filter(target_id=obj_b.pk).count(),
            0,
            'A→B through rows must be deleted when B is deleted.',
        )
        self.assertEqual(
            through_bc.objects.filter(source_id=obj_b.pk).count(),
            0,
            'B→C through rows must be deleted when B is deleted.',
        )
        # A and C must survive.
        self.assertTrue(model_a.objects.filter(pk=obj_a.pk).exists())
        self.assertTrue(model_c.objects.filter(pk=obj_c.pk).exists())

    def test_non_object_field_save_does_not_bump_unrelated_cot_cache_timestamp(self):
        """Saving a non-object field must not affect an unrelated COT's cache_timestamp."""
        cot_target = self.create_simple_custom_object_type(name='notarget', slug='no-target')
        cot_other = self.create_simple_custom_object_type(name='noother', slug='no-other')

        cot_target.refresh_from_db()
        initial_ts = cot_target.cache_timestamp

        self.create_custom_object_type_field(
            cot_other,
            name='extra',
            label='Extra',
            type='text',
        )

        cot_target.refresh_from_db()
        self.assertEqual(
            cot_target.cache_timestamp,
            initial_ts,
            "Saving a text field on an unrelated COT must not bump the target COT's cache_timestamp.",
        )

    def test_production_path_get_model_field_uses_fresh_db_fetch(self):
        """get_model_field() fetches the target COT fresh from DB, so source_cot.get_model()
        works correctly even when the caller's Python target COT object is stale.

        After saving a TYPE_OBJECT field the signal bumps the target COT's cache_timestamp
        in the DB and clears its in-process model cache.  The Python object held by test
        code (or by any code that loaded the target COT before the save) then has a stale
        cache_timestamp.

        The production code in get_model_field() (field_types.py) always issues a fresh
        CustomObjectType.objects.get() for the target COT before calling get_model(), so
        the model it generates is cached under the current (post-bump) timestamp.

        This test verifies that invariant by calling source_cot.get_model() with NO
        refresh_from_db() on cot_target, then using only the model class that the FK
        field itself resolved to (remote_field.model) — which is what the production
        path set — to create and relate objects.  If get_model_field() ever stopped
        fetching the target COT fresh from DB, the FK would resolve to a different model
        class than the one cached under the current timestamp, and the create() call
        would raise ValueError.
        """
        cot_target = self.create_simple_custom_object_type(name='ppttarget', slug='ppt-target')
        cot_source = self.create_simple_custom_object_type(name='pptsource', slug='ppt-source')

        self.create_custom_object_type_field(
            cot_source,
            name='ref_target',
            label='Reference Target',
            type='object',
            related_object_type=cot_target.object_type,
        )

        # No refresh_from_db() on cot_target — its Python object is stale.
        # get_model_field() inside source_cot.get_model() must handle this itself.
        source_model = cot_source.get_model()

        # Retrieve the target model class as the production path resolved it: via the
        # FK's remote_field, not via the stale cot_target Python object.
        target_model = source_model._meta.get_field('ref_target').remote_field.model

        # Create and relate objects using only the production-path model class.
        # A class-identity mismatch (stale vs. current model) would raise ValueError here.
        obj_target = target_model.objects.create(name='Target Object')
        obj_source = source_model.objects.create(name='Source Object', ref_target=obj_target)
        self.assertEqual(obj_source.ref_target, obj_target)
