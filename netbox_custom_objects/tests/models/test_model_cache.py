"""
Tests for the model cache and Django's app registry: invalidation when fields change, registering
generated models, and keeping the cached and registered classes consistent.
"""

from unittest import mock
from unittest.mock import patch

from django.apps import apps
from django.db import transaction
from django.test import TestCase

import netbox_custom_objects as nco_pkg
from core.models import ObjectType
from dcim.models import Site
from netbox_custom_objects.choices import ObjectFieldOnDeleteChoices
from netbox_custom_objects.constants import APP_LABEL
from netbox_custom_objects.models import CustomObjectType, CustomObjectTypeField
from netbox_custom_objects.tests.base import CustomObjectsTestCase
from utilities.testing import create_test_user


class ModelCacheInvalidationTestCase(CustomObjectsTestCase, TestCase):
    """Field changes bump cache_timestamp and evict cached models, on the field's own type and on
    the types its object fields point at."""

    def test_cache_invalidation_on_cotf_save(self):
        """cache_timestamp on the parent COT is updated when a field is saved."""
        cot = self.create_custom_object_type(name='cachetest', slug='cache-test')
        # Capture the initial timestamp
        initial_timestamp = cot.cache_timestamp

        # Adding a field triggers CustomObjectTypeField.save(), which calls
        # cot.save(update_fields=['cache_timestamp']).
        self.create_custom_object_type_field(
            cot,
            name='myfield',
            label='My Field',
            type='text',
        )

        cot.refresh_from_db()
        self.assertNotEqual(
            cot.cache_timestamp,
            initial_timestamp,
            'cache_timestamp must be updated after a field is saved.',
        )

    def test_cache_invalidation_on_cotf_delete(self):
        """cache_timestamp is updated when a field is deleted."""
        cot = self.create_custom_object_type(name='cachedel', slug='cache-del')
        field = self.create_custom_object_type_field(
            cot,
            name='tempfield',
            label='Temp Field',
            type='text',
        )
        cot.refresh_from_db()
        timestamp_after_add = cot.cache_timestamp

        field.delete()

        cot.refresh_from_db()
        self.assertNotEqual(
            cot.cache_timestamp,
            timestamp_after_add,
            'cache_timestamp must be updated after a field is deleted.',
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


class ModelRegistryTestCase(CustomObjectsTestCase, TestCase):
    """Generated models in Django's app registry, and the classes other contexts generate."""

    def test_model_registered_in_apps_after_cotf_save(self):
        """The regenerated model is present in apps.get_models() after a field change."""
        cot = self.create_custom_object_type(name='regtest', slug='reg-test')
        self.create_custom_object_type_field(
            cot,
            name='fieldone',
            label='Field One',
            type='text',
            primary=True,
        )

        # Force model generation and registration
        model = cot.get_model()
        model_name = model.__name__.lower()

        # The model must appear in the app registry
        self.assertIn(
            model_name,
            apps.all_models.get(APP_LABEL, {}),
            "Generated model should be registered in Django's app registry.",
        )
        self.assertIn(
            model,
            apps.get_models(),
            'Generated model should be returned by apps.get_models().',
        )

    def test_model_regenerated_after_field_added(self):
        """Adding a field clears the model cache so get_model() reflects the new schema."""
        cot = self.create_custom_object_type(name='regentest', slug='regen-test')
        self.create_custom_object_type_field(
            cot,
            name='name',
            label='Name',
            type='text',
            primary=True,
        )
        old_model = cot.get_model()
        self.assertFalse(
            'extra' in {f.name for f in old_model._meta.get_fields()},
            "Field 'extra' should not exist before it is added.",
        )

        # Add a new field — this invalidates the cache
        self.create_custom_object_type_field(
            cot,
            name='extra',
            label='Extra',
            type='text',
        )

        new_model = cot.get_model()
        self.assertIn(
            'extra',
            {f.name for f in new_model._meta.get_fields()},
            "Field 'extra' should be present on the regenerated model.",
        )

    def test_model_not_in_registry_after_cot_deleted(self):
        """Deleting a COT removes its generated model from Django's app registry."""
        cot = self.create_custom_object_type(name='delregtest', slug='del-reg-test')
        self.create_custom_object_type_field(
            cot,
            name='name',
            label='Name',
            type='text',
            primary=True,
        )
        model = cot.get_model()
        model_name = model.__name__.lower()

        self.assertIn(
            model_name,
            apps.all_models.get(APP_LABEL, {}),
            'Model should be in registry before deletion.',
        )

        cot.delete()

        self.assertNotIn(
            model_name,
            apps.all_models.get(APP_LABEL, {}),
            "Deleted COT's model must be removed from the app registry.",
        )

    def test_model_generated_in_another_context_leaves_main_classes_alone(self):
        """Generating models for a branch must not rewire main's classes.

        Main's object FK, multi-object through and polymorphic through must keep
        pointing at main's classes, so main can still assign related objects and
        bulk-delete (which skips ``CustomObject.delete()``'s FK realignment).
        """
        cot_source = self.create_simple_custom_object_type(name='rwsrc', slug='rw-src')
        cot_target = self.create_simple_custom_object_type(name='rwtrg', slug='rw-trg')
        self.create_custom_object_type_field(
            cot_source,
            name='ref',
            label='Reference',
            type='object',
            related_object_type=cot_target.object_type,
        )
        self.create_custom_object_type_field(
            cot_source,
            name='refs',
            label='References',
            type='multiobject',
            related_object_type=cot_target.object_type,
        )
        poly_field = self.create_custom_object_type_field(
            cot_source,
            name='poly',
            label='Polymorphic',
            type='multiobject',
            is_polymorphic=True,
        )
        poly_field.related_object_types.set([cot_target.object_type, ObjectType.objects.get_for_model(Site)])

        model_source = cot_source.get_model()
        cot_target.refresh_from_db()
        model_target = cot_target.get_model()
        main_poly_through = apps.get_model(APP_LABEL, poly_field.through_model_name)

        self.addCleanup(CustomObjectType.clear_model_cache)
        with mock.patch.object(CustomObjectType, '_active_branch_id', return_value=-1):
            for cot, main_model in ((cot_target, model_target), (cot_source, model_source)):
                cot.refresh_from_db()
                self.assertIsNot(cot.get_model(), main_model)
            # And the target once more, after the source exists in this context.
            cot_target.refresh_from_db()
            cot_target.get_model(no_cache=True)
        branch_poly_through = CustomObjectType.get_cached_through_model(
            cot_source.pk,
            poly_field.through_model_name,
            -1,
        )
        self.assertIsNotNone(branch_poly_through)
        self.assertIsNot(branch_poly_through, main_poly_through)

        for cot in (cot_source, cot_target):
            cot.refresh_from_db()
        self.assertIs(cot_source.get_model(), model_source)
        self.assertIs(cot_target.get_model(), model_target)

        self.assertIs(model_source._meta.get_field('ref').remote_field.model, model_target)
        refs_through = model_source._meta.get_field('refs').remote_field.through
        self.assertIs(refs_through._meta.get_field('source').remote_field.model, model_source)
        self.assertIs(refs_through._meta.get_field('target').remote_field.model, model_target)
        poly_through = apps.get_model(APP_LABEL, poly_field.through_model_name)
        self.assertIs(poly_through, main_poly_through)
        self.assertIs(poly_through._meta.get_field('source').remote_field.model, model_source)

        # Main still works end to end: assign, then bulk-delete.
        target = model_target.objects.create(name='Target')
        source = model_source.objects.create(name='Source', ref=target)
        source.refs.add(target)
        source.poly.add(target)
        model_source.objects.filter(pk=source.pk).delete()
        self.assertFalse(refs_through.objects.filter(source_id=source.pk).exists())
        self.assertFalse(poly_through.objects.filter(source_id=source.pk).exists())


class PolymorphicThroughModelRegistrationTestCase(CustomObjectsTestCase, TestCase):
    """Verify the through model is re-registered on model regeneration (simulates restart)."""

    def setUp(self):
        super().setUp()
        self.user = create_test_user('poly-reg-user')
        self.client.force_login(self.user)

        self.cot = CustomObjectType.objects.create(
            name='RegTest',
            slug='reg-test',
            verbose_name_plural='Reg Tests',
        )
        CustomObjectTypeField.objects.create(
            custom_object_type=self.cot,
            name='name',
            type='text',
            primary=True,
        )
        self.m2m_field = CustomObjectTypeField.objects.create(
            custom_object_type=self.cot,
            name='links',
            type='multiobject',
            is_polymorphic=True,
        )
        site_ot = ObjectType.objects.get(app_label='dcim', model='site')
        self.m2m_field.related_object_types.set([site_ot])

    def test_through_model_registered_after_get_model(self):
        """After clearing the cache and calling get_model(), the through model is accessible."""
        # Simulate restart by clearing cache
        CustomObjectType.clear_model_cache()

        # Re-generate the model (as a request would)
        self.cot.get_model()

        # The through model must be findable
        through_name = self.m2m_field.through_model_name
        try:
            through = apps.get_model(APP_LABEL, through_name)
        except LookupError:
            self.fail(f"Through model '{through_name}' not in app registry after get_model().")
        self.assertIsNotNone(through)


class FailedFieldChangeTestCase(CustomObjectsTestCase, TestCase):
    """A field change that fails, or whose transaction rolls back, leaves the cached and registered
    models matching the table."""

    def _assert_model_matches_db(self, cot):
        cot.refresh_from_db()
        model = cot.get_model()
        concrete = [f.column for f in model._meta.concrete_fields]
        self.assertEqual(len(concrete), len(set(concrete)), f'duplicate fields on {model._meta.fields}')
        self.assertEqual(set(concrete), self._db_columns(model))
        registered = apps.all_models[APP_LABEL].get(model.__name__.lower())
        if registered is not None:
            self.assertEqual(
                {f.column for f in registered._meta.concrete_fields},
                self._db_columns(model),
            )
        # Selecting every column must not hit a column that doesn't exist.
        list(model.objects.all())

    def test_failed_rename_does_not_mutate_cached_model(self):
        cot = self.create_custom_object_type(name='failrename', slug='fail-rename')
        self.create_custom_object_type_field(cot, name='name', type='text', primary=True)
        field = self.create_custom_object_type_field(cot, name='coordinatescoordinates', type='text')
        cot.get_model()

        field = CustomObjectTypeField.objects.get(pk=field.pk)
        field.name = 'coordinates'
        with (
            patch(
                'netbox_custom_objects.models._rename_objectchange_field_key',
                side_effect=RuntimeError('boom'),
            ),
            self.assertRaises(RuntimeError),
        ):
            field.save()

        self.assertIn('coordinatescoordinates', self._db_columns(cot.get_model()))
        self._assert_model_matches_db(cot)

    def test_failed_add_does_not_mutate_cached_model(self):
        cot = self.create_custom_object_type(name='failadd', slug='fail-add')
        self.create_custom_object_type_field(cot, name='name', type='text', primary=True)
        cot.get_model()

        with (
            patch(
                'netbox_custom_objects.models._apply_deferred_co_field',
                side_effect=RuntimeError('boom'),
            ),
            self.assertRaises(RuntimeError),
        ):
            self.create_custom_object_type_field(cot, name='extra', type='text')

        self.assertNotIn('extra', self._db_columns(cot.get_model()))
        self._assert_model_matches_db(cot)

    def test_failed_delete_does_not_mutate_cached_model(self):
        cot = self.create_custom_object_type(name='faildelete', slug='fail-delete')
        self.create_custom_object_type_field(cot, name='name', type='text', primary=True)
        field = self.create_custom_object_type_field(cot, name='extra', type='text')
        cot.get_model()

        with (
            patch(
                'django.db.backends.postgresql.schema.DatabaseSchemaEditor.remove_field',
                side_effect=RuntimeError('boom'),
            ),
            self.assertRaises(RuntimeError),
        ):
            field.delete()

        self.assertIn('extra', self._db_columns(cot.get_model()))
        self._assert_model_matches_db(cot)

    def test_field_changes_leave_the_class_in_use_alone(self):
        """DDL binds its fields to a throwaway class, so a request using the
        current class mid-change never sees in-flight fields."""
        cot = self.create_custom_object_type(name='notouch', slug='no-touch')
        self.create_custom_object_type_field(cot, name='name', type='text', primary=True)
        field = self.create_custom_object_type_field(cot, name='coordinatescoordinates', type='text')
        model = cot.get_model()
        before = [f.name for f in model._meta.local_fields + model._meta.local_many_to_many]

        extra = self.create_custom_object_type_field(cot, name='extra', type='text')
        field = CustomObjectTypeField.objects.get(pk=field.pk)
        field.name = 'coordinates'
        field.save()
        extra.delete()

        self.assertEqual([f.name for f in model._meta.local_fields + model._meta.local_many_to_many], before)
        self._assert_model_matches_db(cot)

    def test_failed_object_field_add_installs_no_reverse_accessor(self):
        cot = self.create_custom_object_type(name='noaccessor', slug='no-accessor')
        self.create_custom_object_type_field(cot, name='name', type='text', primary=True)
        cot.get_model()
        self.addCleanup(lambda: 'failed_links' in Site.__dict__ and delattr(Site, 'failed_links'))

        with (
            patch(
                'netbox_custom_objects.models._apply_deferred_co_field',
                side_effect=RuntimeError('boom'),
            ),
            self.assertRaises(RuntimeError),
        ):
            self.create_custom_object_type_field(
                cot,
                name='site',
                type='object',
                related_object_type=ObjectType.objects.get_for_model(Site),
                related_name='failed_links',
            )

        self.assertNotIn('failed_links', Site.__dict__)
        self._assert_model_matches_db(cot)

    def _rename_then_roll_back(self):
        """Rename a field inside a transaction that then rolls back, as a view
        or schema apply that fails after the save does.  Returns the type and
        the instance that saved the rename, whose timestamp was bumped in memory
        but not in the database."""
        cot = self.create_custom_object_type(name='rollback', slug='roll-back')
        self.create_custom_object_type_field(cot, name='name', type='text', primary=True)
        field = self.create_custom_object_type_field(cot, name='coordinatescoordinates', type='text')
        cot.get_model()

        field = CustomObjectTypeField.objects.get(pk=field.pk)
        field.name = 'coordinates'
        with self.assertRaises(RuntimeError), transaction.atomic():
            field.save()
            raise RuntimeError('boom')
        return cot, field.custom_object_type

    def _assert_fits_table(self, model):
        columns = self._db_columns(model)
        self.assertIn('coordinatescoordinates', columns)
        self.assertEqual({f.column for f in model._meta.concrete_fields}, columns)
        list(model.objects.all())

    def test_rollback_after_rename_rebuilds_the_cached_model(self):
        """The saving instance's cache entry looks valid, since both carry the rolled-back timestamp."""
        _cot, stale_cot = self._rename_then_roll_back()
        self._assert_fits_table(stale_cot.get_model())

    def test_rollback_after_rename_rebuilds_the_registered_model(self):
        """Registry lookups (e.g. ContentType.model_class()) bypass get_model() and its cache check."""
        cot, _stale_cot = self._rename_then_roll_back()
        self._assert_fits_table(apps.get_model(APP_LABEL, cot.get_table_model_name(cot.pk)))

    def test_rollback_after_rename_is_seen_from_a_later_transaction(self):
        """The connection that rolled back serves the next request too, so a lookup
        from inside that request's transaction must not mistake it for the one the
        class was built in."""
        cot, stale_cot = self._rename_then_roll_back()
        with transaction.atomic():
            self._assert_fits_table(stale_cot.get_model())
            self._assert_fits_table(apps.get_model(APP_LABEL, cot.get_table_model_name(cot.pk)))


class PolymorphicReverseDescriptorRecursionTestCase(CustomObjectsTestCase, TestCase):
    """Regression test: generating a polymorphic field's
    reverse descriptor raised RecursionError."""

    def test_get_model_with_polymorphic_related_name_does_not_recurse(self):
        site_ot = ObjectType.objects.get(app_label='dcim', model='site')
        prefix_ot = ObjectType.objects.get(app_label='ipam', model='prefix')

        cot = CustomObjectType.objects.create(
            name='RecursionRevTest',
            slug='recursion-rev-test',
            verbose_name_plural='Recursion Rev Tests',
        )
        CustomObjectTypeField.objects.create(
            custom_object_type=cot,
            name='name',
            type='text',
            primary=True,
            required=True,
        )
        field = CustomObjectTypeField.objects.create(
            custom_object_type=cot,
            name='target_obj',
            label='Target',
            type='object',
            is_polymorphic=True,
            related_name='rev_recursion_test',
        )
        field.related_object_types.set([site_ot, prefix_ot])

        # Force the model out of cache and the relation-tree cold, so
        # get_model() -> _after_model_generation() -> related_object_types.all()
        # hits the "first access" path that triggers apps.get_models().
        cot.clear_model_cache(cot.id)
        apps.clear_cache()

        # should_skip_dynamic_model_creation() disables get_models()'s
        # CustomObjectType loop under `manage.py test`; bypass it so this test
        # exercises the real vulnerable code path.
        app_config = apps.get_app_config('netbox_custom_objects')
        with (
            mock.patch.object(nco_pkg, '_app_ready', True),
            mock.patch.object(app_config, 'should_skip_dynamic_model_creation', return_value=False),
        ):
            # Must not raise RecursionError.
            model = cot.get_model()
        self.assertFalse(nco_pkg._generating_models.get())
        # Look up the registered class directly rather than trusting `model`:
        # a nested get_models() call triggered mid-generation (as above) can
        # leave get_model()'s return value out of sync with what's actually
        # registered in the app registry.
        registered_model = apps.get_model(APP_LABEL, model.__name__)
        self.assertIn(
            registered_model,
            apps.get_models(),
            'Generated model should be returned by apps.get_models().',
        )
        self.assertTrue(
            hasattr(Site, 'rev_recursion_test'),
            'Reverse descriptor must still be set on Site after get_model()',
        )
        self.assertIsNotNone(model)


class GetModelCacheRegistryConsistencyTestCase(CustomObjectsTestCase, TestCase):
    """Regression test: a re-entrant get_model() for the same COT could leave
    _model_cache and apps.all_models pointing at two different classes."""

    def test_get_model_registers_the_same_class_it_returns_and_caches(self):
        site_ot = ObjectType.objects.get(app_label='dcim', model='site')
        prefix_ot = ObjectType.objects.get(app_label='ipam', model='prefix')

        cot = CustomObjectType.objects.create(
            name='CacheRegistryTest',
            slug='cache-registry-test',
            verbose_name_plural='Cache Registry Tests',
        )
        CustomObjectTypeField.objects.create(
            custom_object_type=cot,
            name='name',
            type='text',
            primary=True,
            required=True,
        )
        field = CustomObjectTypeField.objects.create(
            custom_object_type=cot,
            name='target_obj',
            label='Target',
            type='object',
            is_polymorphic=True,
            related_name='rev_cache_registry_test',
        )
        field.related_object_types.set([site_ot, prefix_ot])

        # Same setup as the sibling recursion test, to force the re-entrant path.
        cot.clear_model_cache(cot.id)
        apps.clear_cache()

        app_config = apps.get_app_config('netbox_custom_objects')
        with (
            mock.patch.object(nco_pkg, '_app_ready', True),
            mock.patch.object(app_config, 'should_skip_dynamic_model_creation', return_value=False),
        ):
            model = cot.get_model()
        self.assertFalse(nco_pkg._generating_models.get())

        cached_model = CustomObjectType.get_cached_model(cot.id)
        registered_model = apps.get_model(APP_LABEL, model.__name__)

        # Must be the exact same class object, not just an equal one.
        self.assertIs(cached_model, model, 'get_cached_model() must return the same class get_model() returned')
        self.assertIs(
            registered_model,
            model,
            'apps.all_models must register the same class get_model() returned/cached, not a stray '
            'class from a re-entrant regeneration',
        )
        self.assertIn(
            registered_model,
            apps.get_models(),
            'Generated model should be returned by apps.get_models().',
        )
