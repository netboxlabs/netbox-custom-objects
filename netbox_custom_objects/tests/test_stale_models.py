"""
Models of Custom Object Types deleted in another worker process are evicted from
this process's app registry (see netbox_custom_objects.stale_models).
"""

from django.apps import apps
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from extras.models import Tag
from rest_framework import status
from rest_framework.test import APIClient

from netbox_custom_objects import stale_models
from netbox_custom_objects.constants import APP_LABEL

from .base import CustomObjectsTestCase, create_token


class StaleModelEvictionTest(CustomObjectsTestCase, TestCase):

    def setUp(self):
        super().setUp()
        self.user.is_superuser = True
        self.user.save()
        self.client = APIClient()
        self.header = {"HTTP_AUTHORIZATION": f"Token {create_token(self.user)}"}

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
        cot.delete()
        registry = apps.all_models[APP_LABEL]
        registry[model._meta.model_name] = model
        for through_model in getattr(model, "_through_models", []):
            registry[through_model._meta.model_name] = through_model
        apps.clear_cache()
        stale_models.signal_custom_object_type_deleted()
        return model

    def test_tag_delete_succeeds_after_type_deleted_in_another_worker(self):
        cot = self.create_simple_custom_object_type(name="stale", slug="stale")
        stale_model = self._delete_type_in_another_worker(cot)
        tag = Tag.objects.create(name="Test Tag", slug="test-tag")

        response = self.client.delete(
            reverse("extras-api:tag-detail", kwargs={"pk": tag.pk}), **self.header,
        )
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Tag.objects.filter(pk=tag.pk).exists())
        self.assertFalse(self._registered(stale_model))

    def test_eviction_keeps_existing_types(self):
        kept = self.create_simple_custom_object_type(name="kept", slug="kept")
        kept_model = kept.get_model()
        deleted = self.create_simple_custom_object_type(name="gone", slug="gone")
        stale_model = self._delete_type_in_another_worker(deleted)

        stale_models.evict_deleted_custom_object_types()

        self.assertFalse(self._registered(stale_model))
        self.assertTrue(self._registered(kept_model))

    def test_deleting_a_type_signals_other_workers(self):
        cot = self.create_simple_custom_object_type(name="signal", slug="signal")
        cot.get_model()
        before = cache.get(stale_models._TOKEN_CACHE_KEY)

        with self.captureOnCommitCallbacks(execute=True):
            cot.delete()

        after = cache.get(stale_models._TOKEN_CACHE_KEY)
        self.assertIsNotNone(after)
        self.assertNotEqual(after, before)

    def test_check_is_free_when_nothing_was_deleted(self):
        stale_models.signal_custom_object_type_deleted()
        stale_models.evict_deleted_custom_object_types()  # acts on the new token

        with self.assertNumQueries(0):
            stale_models.evict_deleted_custom_object_types()
