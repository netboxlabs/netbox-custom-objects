"""
Tests for the Custom Object Type field REST API endpoint.
"""
import json

from django.test import TestCase, tag
from django.urls import reverse

from utilities.testing import create_test_user
from rest_framework import status

from netbox_custom_objects.models import CustomObjectType, CustomObjectTypeField
from ..base import CustomObjectsTestCase, create_token
from core.models import ObjectType
from dcim.models import Site
from users.models import ObjectPermission


class CustomObjectTypeFieldObjectResolutionTest(CustomObjectsTestCase, TestCase):
    """
    Tests for app_label/model resolution in CustomObjectTypeFieldSerializer.validate().

    The serializer must accept both user-friendly identifiers and internal Django values:
      - app_label: "custom-objects" (public URL slug) or "netbox_custom_objects" (internal)
      - model:     CustomObjectType slug (e.g. "cpe") or internal table name (e.g. "table3model")
    """

    field_url = 'plugins-api:netbox_custom_objects-api:customobjecttypefield-list'

    def setUp(self):
        self.user = create_test_user('fieldresolutionuser')
        token_key = create_token(self.user)
        self.header = {'HTTP_AUTHORIZATION': f'Token {token_key}'}

        # Permission to add fields
        obj_perm = ObjectPermission(name='Field resolution add perm', actions=['add'])
        obj_perm.save()
        obj_perm.users.add(self.user)
        from netbox_custom_objects.models import CustomObjectTypeField
        obj_perm.object_types.add(ObjectType.objects.get_for_model(CustomObjectTypeField))

        # The parent COT that owns the new field
        self.parent_cot = CustomObjectType.objects.create(
            name='ParentObject',
            slug='parent-object',
        )

        # A target COT that the object field will point to
        self.target_cot = CustomObjectType.objects.create(
            name='TargetObject',
            slug='target-object',
        )
        # Ensure ObjectType (ContentType) exists for the target COT
        self.target_object_type = ObjectType.objects.get(
            app_label='netbox_custom_objects',
            model=self.target_cot.get_table_model_name(self.target_cot.id).lower(),
        )

    def _post_field(self, app_label, model, field_type='object'):
        data = {
            'name': 'related_field',
            'custom_object_type': self.parent_cot.pk,
            'type': field_type,
            'label': 'Related Field',
            'app_label': app_label,
            'model': model,
        }
        url = reverse(self.field_url)
        return self.client.post(url, data, format='json', **self.header)

    def _internal_model_name(self):
        return self.target_cot.get_table_model_name(self.target_cot.id).lower()

    def test_public_app_label_with_slug_accepted(self):
        """app_label='custom-objects' + model=<cot slug> must succeed."""
        response = self._post_field('custom-objects', self.target_cot.slug)
        self.assertEqual(
            response.status_code, status.HTTP_201_CREATED,
            f"Expected 201, got {response.status_code}: {response.data}",
        )

    def test_public_app_label_with_internal_model_name_accepted(self):
        """app_label='custom-objects' + model=<table{N}model> must succeed."""
        response = self._post_field('custom-objects', self._internal_model_name())
        self.assertEqual(
            response.status_code, status.HTTP_201_CREATED,
            f"Expected 201, got {response.status_code}: {response.data}",
        )

    def test_internal_app_label_with_slug_accepted(self):
        """app_label='netbox_custom_objects' + model=<cot slug> must succeed."""
        response = self._post_field('netbox_custom_objects', self.target_cot.slug)
        self.assertEqual(
            response.status_code, status.HTTP_201_CREATED,
            f"Expected 201, got {response.status_code}: {response.data}",
        )

    def test_internal_app_label_with_internal_model_name_accepted(self):
        """app_label='netbox_custom_objects' + model=<table{N}model> must succeed (original behaviour)."""
        response = self._post_field('netbox_custom_objects', self._internal_model_name())
        self.assertEqual(
            response.status_code, status.HTTP_201_CREATED,
            f"Expected 201, got {response.status_code}: {response.data}",
        )

    def test_multiobject_public_app_label_with_slug_accepted(self):
        """Same resolution must work for multiobject field type."""
        response = self._post_field('custom-objects', self.target_cot.slug, field_type='multiobject')
        self.assertEqual(
            response.status_code, status.HTTP_201_CREATED,
            f"Expected 201, got {response.status_code}: {response.data}",
        )

    def test_nonexistent_slug_returns_400(self):
        """An unknown slug with custom-objects app_label must return 400."""
        response = self._post_field('custom-objects', 'does-not-exist')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_invalid_app_label_returns_400(self):
        """An unknown app_label must still return 400."""
        response = self._post_field('invalid-app', self._internal_model_name())
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class CustomObjectTypeFieldValidationAPITest(CustomObjectsTestCase, TestCase):
    """Field-level validation errors from CustomObjectTypeField.clean() surface as 400s via the API."""

    field_url = 'plugins-api:netbox_custom_objects-api:customobjecttypefield-list'

    def setUp(self):
        self.user = create_test_user('fieldvalidationuser')
        token_key = create_token(self.user)
        self.header = {'HTTP_AUTHORIZATION': f'Token {token_key}'}

        obj_perm = ObjectPermission(name='Field validation add perm', actions=['add'])
        obj_perm.save()
        obj_perm.users.add(self.user)
        obj_perm.object_types.add(ObjectType.objects.get_for_model(CustomObjectTypeField))

        self.parent_cot = CustomObjectType.objects.create(name='ParentObject', slug='parent-object')

    @tag('regression')
    def test_related_name_colliding_with_target_attribute_returns_400(self):
        """A related_name that would overwrite an attribute on the target model must be rejected."""
        original_save = Site.save
        data = {
            'name': 'site',
            'custom_object_type': self.parent_cot.pk,
            'type': 'object',
            'label': 'Site',
            'app_label': 'dcim',
            'model': 'site',
            'related_name': 'save',
        }
        response = self.client.post(reverse(self.field_url), data, format='json', **self.header)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.data)
        self.assertIn('related_name', response.data)
        self.assertIs(Site.save, original_save)


class SchemaIdReadOnlyTest(CustomObjectsTestCase, TestCase):
    """
    schema_id on CustomObjectTypeField is read-only via the API.
    POSTing a value for it must be silently ignored; PATCHing an existing
    field with a new schema_id must also leave the stored value unchanged.
    """

    def setUp(self):
        self.user = create_test_user('schemauser')
        token_key = create_token(self.user)
        self.header = {'HTTP_AUTHORIZATION': f'Token {token_key}'}

        # Add add + change + view permissions on CustomObjectTypeField
        perm = ObjectPermission(name='Schema perm', actions=['add', 'change', 'view'])
        perm.save()
        perm.users.add(self.user)
        perm.object_types.add(ObjectType.objects.get_for_model(CustomObjectTypeField))
        # Also need add on CustomObjectType (for creating the parent)
        cot_perm = ObjectPermission(name='COT perm', actions=['add', 'view'])
        cot_perm.save()
        cot_perm.users.add(self.user)
        cot_perm.object_types.add(ObjectType.objects.get_for_model(CustomObjectType))

        self.cot = self.create_custom_object_type(name='ro_schema', slug='ro-schema')

    def _field_list_url(self):
        return reverse('plugins-api:netbox_custom_objects-api:customobjecttypefield-list')

    def _field_detail_url(self, pk):
        return reverse(
            'plugins-api:netbox_custom_objects-api:customobjecttypefield-detail',
            kwargs={'pk': pk},
        )

    def test_schema_id_in_response(self):
        """schema_id must be present and non-null in the API response."""
        field = self.create_custom_object_type_field(self.cot, name='alpha', type='text')
        response = self.client.get(self._field_detail_url(field.pk), **self.header)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('schema_id', response.data)
        self.assertIsNotNone(response.data['schema_id'])

    def test_schema_id_ignored_on_create(self):
        """Supplying schema_id on POST must be silently ignored; auto-assignment wins."""
        data = {
            'custom_object_type': self.cot.pk,
            'name': 'beta',
            'type': 'text',
            'schema_id': 999,
        }
        response = self.client.post(
            self._field_list_url(), data, format='json', **self.header
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertNotEqual(response.data['schema_id'], 999)

    def test_schema_id_ignored_on_patch(self):
        """PATCHing schema_id must not change the stored value."""
        field = self.create_custom_object_type_field(self.cot, name='gamma', type='text')
        original_id = field.schema_id

        response = self.client.patch(
            self._field_detail_url(field.pk),
            json.dumps({'schema_id': original_id + 100}),
            content_type='application/json',
            **self.header,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        field.refresh_from_db()
        self.assertEqual(field.schema_id, original_id)

    def test_deprecation_fields_writable_on_patch(self):
        """
        deprecated/deprecated_since/scheduled_removal are excluded
        from CustomObjectTypeFieldForm (the web edit form) but must remain writable
        via the REST API -- this is how the portable-schema import mechanism applies
        them. Unlike schema_id, they must NOT be silently ignored here.
        """
        field = self.create_custom_object_type_field(self.cot, name='delta', type='text')
        self.assertFalse(field.deprecated)

        response = self.client.patch(
            self._field_detail_url(field.pk),
            json.dumps({'deprecated': True, 'deprecated_since': '1.0.0', 'scheduled_removal': '2.0.0'}),
            content_type='application/json',
            **self.header,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        field.refresh_from_db()
        self.assertTrue(field.deprecated)
        self.assertEqual(field.deprecated_since, '1.0.0')
        self.assertEqual(field.scheduled_removal, '2.0.0')
