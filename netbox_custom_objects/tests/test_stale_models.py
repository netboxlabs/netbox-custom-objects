"""
Models of Custom Object Types deleted in another worker process are evicted from
this process's app registry (see netbox_custom_objects.stale_models).
"""

from django.apps import apps
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from extras.models import Tag
from netbox_custom_objects import stale_models
from netbox_custom_objects.constants import APP_LABEL
from netbox_custom_objects.models import CustomObjectType
from utilities.testing.utils import create_test_device

from .base import CustomObjectsTestCase, create_token


class StaleModelEvictionTestCase(CustomObjectsTestCase, TestCase):
    def setUp(self):
        super().setUp()
        self.user.is_superuser = True
        self.user.save()
        self.client = APIClient()
        self.header = {'HTTP_AUTHORIZATION': f'Token {create_token(self.user)}'}

    def _registered(self, model):
        return model._meta.model_name in apps.all_models.get(APP_LABEL, {})

    def _delete_type_in_another_worker(self, cot):
        """
        Delete ``cot`` and leave its model registered here, the state a worker that
        didn't handle the delete is left in; then signal the delete, as the
        deleting worker does on commit.
        """
        model = cot.get_model()
        self.assertTrue(self._registered(model))
        cot_id = cot.pk
        cot.delete()
        registry = apps.all_models[APP_LABEL]
        registry[model._meta.model_name] = model
        for through_model in getattr(model, '_through_models', []):
            registry[through_model._meta.model_name] = through_model
        apps.clear_cache()
        stale_models.signal_custom_object_type_deleted(cot_id)
        return model

    def test_tag_delete_succeeds_after_type_deleted_in_another_worker(self):
        cot = self.create_simple_custom_object_type(name='stale', slug='stale')
        stale_model = self._delete_type_in_another_worker(cot)
        tag = Tag.objects.create(name='Test Tag', slug='test-tag')

        response = self.client.delete(
            reverse('extras-api:tag-detail', kwargs={'pk': tag.pk}),
            **self.header,
        )
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Tag.objects.filter(pk=tag.pk).exists())
        self.assertFalse(self._registered(stale_model))

    def test_eviction_keeps_existing_types(self):
        kept = self.create_simple_custom_object_type(name='kept', slug='kept')
        kept_model = kept.get_model()
        # A mark left over from before a database restore, for an ID now in use again.
        stale_models.signal_custom_object_type_deleted(kept.pk)
        self.addCleanup(cache.delete, stale_models._DELETED_TYPE_CACHE_KEY.format(kept.pk))
        deleted = self.create_simple_custom_object_type(name='gone', slug='gone')
        stale_model = self._delete_type_in_another_worker(deleted)

        stale_models.evict_deleted_models()

        self.assertFalse(self._registered(stale_model))
        self.assertTrue(self._registered(kept_model))

    def test_eviction_keeps_unmarked_types_missing_from_the_database(self):
        # A type another thread has created but not yet committed: registered
        # here, but this thread's connection can't see its row.
        pending = self.create_simple_custom_object_type(name='pending', slug='pending')
        pending_model = pending.get_model()
        self.addCleanup(CustomObjectType.unregister_model, pending_model)
        CustomObjectType.objects.filter(pk=pending.pk).delete()
        deleted = self.create_simple_custom_object_type(name='gone', slug='gone')
        stale_model = self._delete_type_in_another_worker(deleted)

        stale_models.evict_deleted_models()

        self.assertFalse(self._registered(stale_model))
        self.assertTrue(self._registered(pending_model))

    def test_deleting_a_type_signals_other_workers(self):
        cot = self.create_simple_custom_object_type(name='signal', slug='signal')
        cot.get_model()
        cot_id = cot.pk
        before = cache.get(stale_models._TOKEN_CACHE_KEY)

        with self.captureOnCommitCallbacks(execute=True):
            cot.delete()

        after = cache.get(stale_models._TOKEN_CACHE_KEY)
        self.assertIsNotNone(after)
        self.assertNotEqual(after, before)
        self.assertTrue(cache.get(stale_models._DELETED_TYPE_CACHE_KEY.format(cot_id)))

    def test_check_is_free_when_nothing_was_deleted(self):
        stale_models.signal_custom_object_type_deleted(0)
        stale_models.evict_deleted_models()  # acts on the new token

        with self.assertNumQueries(0):
            stale_models.evict_deleted_models()


class StaleThroughModelEvictionTestCase(CustomObjectsTestCase, TestCase):
    """
    Through models of multi-object fields deleted in another worker process are
    evicted from this process's app registry too.
    """

    def setUp(self):
        super().setUp()
        self.user.is_superuser = True
        self.user.save()
        self.client = APIClient()
        self.header = {'HTTP_AUTHORIZATION': f'Token {create_token(self.user)}'}
        self.cot = self.create_custom_object_type(name='widget', slug='widget')
        self.create_custom_object_type_field(self.cot, name='name', type='text', primary=True)

    def _registered(self, model):
        return apps.all_models.get(APP_LABEL, {}).get(model._meta.model_name) is model

    def _delete_field_in_another_worker(self, field):
        """
        Delete ``field`` and put back this process's classes from before the delete,
        the state a worker that didn't handle the delete is left in.
        """
        cot_id = field.custom_object_type_id
        model = self.cot.get_model()
        registry = apps.all_models[APP_LABEL]
        through_model = registry[field.through_model_name.lower()]
        model_cache = CustomObjectType._model_cache[(cot_id, None)]
        through_model_cache = dict(CustomObjectType._through_model_cache[(cot_id, None)])

        with self.captureOnCommitCallbacks(execute=True):
            field.delete()

        registry[model._meta.model_name] = model
        registry[through_model._meta.model_name] = through_model
        CustomObjectType._model_cache[(cot_id, None)] = model_cache
        CustomObjectType._through_model_cache[(cot_id, None)] = through_model_cache
        apps.clear_cache()
        self.addCleanup(CustomObjectType._unregister_model_names, [through_model.__name__])
        self.addCleanup(CustomObjectType.clear_model_cache, cot_id, all_branches=True)
        self.addCleanup(
            cache.delete, stale_models._DELETED_THROUGH_MODEL_CACHE_KEY.format(through_model._meta.model_name)
        )
        return through_model

    def _delete_device(self, device):
        response = self.client.delete(
            reverse('dcim-api:device-detail', kwargs={'pk': device.pk}),
            **self.header,
        )
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

    def test_device_delete_succeeds_after_multi_object_field_deleted_in_another_worker(self):
        field = self.create_custom_object_type_field(
            self.cot, name='devices', type='multiobject', related_object_type=self.get_device_object_type()
        )
        device = create_test_device('device1')
        stale_through_model = self._delete_field_in_another_worker(field)

        self._delete_device(device)

        self.assertFalse(self._registered(stale_through_model))

    def test_device_delete_succeeds_after_polymorphic_field_deleted_in_another_worker(self):
        field = self.create_polymorphic_field(
            self.cot,
            [self.get_device_object_type(), self.get_site_object_type()],
            name='targets',
            type='multiobject',
        )
        device = create_test_device('device1')
        stale_through_model = self._delete_field_in_another_worker(field)

        self._delete_device(device)

        self.assertFalse(self._registered(stale_through_model))

    def test_eviction_keeps_through_model_of_field_added_again(self):
        field = self.create_custom_object_type_field(
            self.cot, name='devices', type='multiobject', related_object_type=self.get_device_object_type()
        )
        self._delete_field_in_another_worker(field)
        self.create_custom_object_type_field(
            self.cot, name='devices', type='multiobject', related_object_type=self.get_device_object_type()
        )
        through_model = self.cot.get_model()._meta.get_field('devices').remote_field.through

        stale_models.evict_deleted_models()

        self.assertTrue(self._registered(through_model))

    def test_deleting_a_multi_object_field_signals_other_workers(self):
        field = self.create_custom_object_type_field(
            self.cot, name='devices', type='multiobject', related_object_type=self.get_device_object_type()
        )
        self.cot.get_model()
        key = stale_models._DELETED_THROUGH_MODEL_CACHE_KEY.format(field.through_model_name.lower())
        self.addCleanup(cache.delete, key)
        before = cache.get(stale_models._TOKEN_CACHE_KEY)

        with self.captureOnCommitCallbacks(execute=True):
            field.delete()

        after = cache.get(stale_models._TOKEN_CACHE_KEY)
        self.assertIsNotNone(after)
        self.assertNotEqual(after, before)
        self.assertEqual(tuple(cache.get(key)), (self.cot.pk, 'devices'))
