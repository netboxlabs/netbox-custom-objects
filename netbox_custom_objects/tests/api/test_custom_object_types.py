"""
Tests for the Custom Object Type REST API endpoint.
"""

from django.db import connection
from django.test import TestCase
from django.urls import reverse

from utilities.testing import create_test_user
from rest_framework import status
from rest_framework.test import APIClient

from netbox_custom_objects.models import CustomObjectType, CustomObjectTypeField
from ..base import CustomObjectsTestCase, create_token
from core.models import ObjectType
from users.models import ObjectPermission


class CustomObjectTypeAPITest(CustomObjectsTestCase, TestCase):
    """
    Test CustomObjectType API endpoint validation.
    """

    def setUp(self):
        """Set up test data."""
        # Create a user
        self.user = create_test_user('testuser')

        # Create token for API access (compatible with NetBox >= 4.5 and < 4.5)
        token_key = create_token(self.user)
        self.header = {'HTTP_AUTHORIZATION': f'Token {token_key}'}

        # Add object-level permission
        obj_perm = ObjectPermission(
            name='Test permission',
            actions=['add']
        )
        obj_perm.save()
        obj_perm.users.add(self.user)
        obj_perm.object_types.add(ObjectType.objects.get_for_model(CustomObjectType))

    def test_create_custom_object_type_with_blank_slug(self):
        """
        Test that creating a CustomObjectType with a blank slug returns a validation error.
        """
        # Test with empty string slug
        data = {
            'name': 'test_blank_slug',
            'slug': '',
        }

        url = reverse('plugins-api:netbox_custom_objects-api:customobjecttype-list')
        response = self.client.post(url, data, format='json', **self.header)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('slug', response.data)

    def test_create_custom_object_type_without_slug(self):
        """
        Test that creating a CustomObjectType without a slug field returns a validation error.
        """
        # Test without slug field at all
        data = {
            'name': 'test_no_slug',
        }

        url = reverse('plugins-api:netbox_custom_objects-api:customobjecttype-list')
        response = self.client.post(url, data, format='json', **self.header)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('slug', response.data)

    def _add_view_permission(self):
        obj_perm = ObjectPermission(name='View permission', actions=['view'])
        obj_perm.save()
        obj_perm.users.add(self.user)
        obj_perm.object_types.add(ObjectType.objects.get_for_model(CustomObjectType))

    def test_group_name_serialized_in_api_response(self):
        """group_name set on a CustomObjectType is returned in the API detail response."""
        self._add_view_permission()
        cot = CustomObjectType.objects.create(
            name='grouped_type',
            slug='grouped-type',
            group_name='my-group',
        )

        url = reverse(
            'plugins-api:netbox_custom_objects-api:customobjecttype-detail',
            kwargs={'pk': cot.pk},
        )
        response = self.client.get(url, **self.header)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('group_name', response.data)
        self.assertEqual(response.data['group_name'], 'my-group')

    def test_group_name_empty_by_default_in_api_response(self):
        """group_name defaults to an empty string when not set."""
        self._add_view_permission()
        cot = CustomObjectType.objects.create(
            name='ungrouped_type',
            slug='ungrouped-type',
        )

        url = reverse(
            'plugins-api:netbox_custom_objects-api:customobjecttype-detail',
            kwargs={'pk': cot.pk},
        )
        response = self.client.get(url, **self.header)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('group_name', response.data)
        self.assertEqual(response.data['group_name'], '')

    def _add_change_permission(self):
        obj_perm = ObjectPermission(name='Change permission', actions=['change'])
        obj_perm.save()
        obj_perm.users.add(self.user)
        obj_perm.object_types.add(ObjectType.objects.get_for_model(CustomObjectType))

    def test_config_context_enabled_set_at_creation(self):
        """config_context_enabled can be set to True when creating a type via the API."""
        data = {'name': 'cc_create', 'slug': 'cc-create', 'config_context_enabled': True}
        url = reverse('plugins-api:netbox_custom_objects-api:customobjecttype-list')
        response = self.client.post(url, data, format='json', **self.header)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertTrue(response.data['config_context_enabled'])
        cot = CustomObjectType.objects.get(pk=response.data['id'])
        self.assertTrue(cot.config_context_enabled)

    def test_config_context_enabled_in_detail_response(self):
        """The current value is readable via the API detail endpoint."""
        self._add_view_permission()
        cot = CustomObjectType.objects.create(
            name='cc_detail', slug='cc-detail', config_context_enabled=True,
        )
        url = reverse(
            'plugins-api:netbox_custom_objects-api:customobjecttype-detail',
            kwargs={'pk': cot.pk},
        )
        response = self.client.get(url, **self.header)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('config_context_enabled', response.data)
        self.assertTrue(response.data['config_context_enabled'])

    def test_config_context_enabled_immutable_after_creation(self):
        """Changing config_context_enabled via PATCH is rejected (create-only)."""
        self._add_change_permission()
        cot = CustomObjectType.objects.create(
            name='cc_immut', slug='cc-immut', config_context_enabled=False,
        )
        url = reverse(
            'plugins-api:netbox_custom_objects-api:customobjecttype-detail',
            kwargs={'pk': cot.pk},
        )
        # APIClient honours format='json' on PATCH (the plain Django client sends
        # octet-stream → 415).
        response = APIClient().patch(
            url, {'config_context_enabled': True}, format='json', **self.header,
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('config_context_enabled', response.data)
        cot.refresh_from_db()
        self.assertFalse(cot.config_context_enabled)

    def test_create_with_invalid_display_expression_returns_400(self):
        data = {'name': 'bad_expr', 'slug': 'bad-expr', 'display_expression': '{% if name %}{{ name }}'}
        url = reverse('plugins-api:netbox_custom_objects-api:customobjecttype-list')
        response = self.client.post(url, data, format='json', **self.header)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('display_expression', response.data)
        self.assertFalse(CustomObjectType.objects.filter(slug='bad-expr').exists())

    def test_patch_with_invalid_display_expression_returns_400(self):
        self._add_change_permission()
        cot = CustomObjectType.objects.create(name='expr_patch', slug='expr-patch', display_expression='{{ name }}')
        url = reverse(
            'plugins-api:netbox_custom_objects-api:customobjecttype-detail',
            kwargs={'pk': cot.pk},
        )
        response = APIClient().patch(url, {'display_expression': '{{ name'}, format='json', **self.header)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('display_expression', response.data)
        cot.refresh_from_db()
        self.assertEqual(cot.display_expression, '{{ name }}')


class CustomObjectTypeAndFieldViewSetPermissionTest(CustomObjectsTestCase, TestCase):
    """CustomObjectTypeViewSet/CustomObjectTypeFieldViewSet must enforce object-level permissions on LIST."""

    def setUp(self):
        self.user = create_test_user('unprivileged_user')
        token_key = create_token(self.user)
        self.header = {'HTTP_AUTHORIZATION': f'Token {token_key}'}

    def test_custom_object_type_list_without_permission_returns_403(self):
        url = reverse('plugins-api:netbox_custom_objects-api:customobjecttype-list')
        response = self.client.get(url, **self.header)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_custom_object_type_field_list_without_permission_returns_403(self):
        url = reverse('plugins-api:netbox_custom_objects-api:customobjecttypefield-list')
        response = self.client.get(url, **self.header)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class CustomObjectTypeAndFieldFilteringBulkAPITest(CustomObjectsTestCase, TestCase):
    """
    List filters on the type and field endpoints must narrow the result set, and bulk
    writes must touch only the listed ids. pynetbox's ``filter(...).update()`` and
    ``filter(...).delete()`` send a filtered GET and then a bulk write of the returned
    ids, so an ignored filter would turn them into writes against every row (#584).
    """

    type_list_url = 'plugins-api:netbox_custom_objects-api:customobjecttype-list'
    field_list_url = 'plugins-api:netbox_custom_objects-api:customobjecttypefield-list'

    def setUp(self):
        self.user = create_test_user('bulk_filter_user')
        # APIClient honours format='json' on PATCH/DELETE (the plain Django client does not).
        self.client = APIClient()
        token_key = create_token(self.user)
        self.header = {'HTTP_AUTHORIZATION': f'Token {token_key}'}

        obj_perm = ObjectPermission(name='Bulk filter perm', actions=['view', 'change', 'delete'])
        obj_perm.save()
        obj_perm.users.add(self.user)
        obj_perm.object_types.add(
            ObjectType.objects.get_for_model(CustomObjectType),
            ObjectType.objects.get_for_model(CustomObjectTypeField),
        )

        self.types = [
            CustomObjectType.objects.create(name=f'bulk_type_{i}', slug=f'bulk-type-{i}', description=f'desc {i}')
            for i in range(3)
        ]
        self.fields = [
            CustomObjectTypeField.objects.create(
                custom_object_type=cot,
                name='label_text',
                type='text',
            )
            for cot in self.types
        ]
        CustomObjectTypeField.objects.create(
            custom_object_type=self.types[0],
            name='count',
            type='integer',
        )

    def _list(self, url_name, params):
        response = self.client.get(reverse(url_name), params, **self.header)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        return sorted(row['id'] for row in response.data['results'])

    # Types: filtering

    def test_type_list_filter_by_slug(self):
        self.assertEqual(self._list(self.type_list_url, {'slug': 'bulk-type-1'}), [self.types[1].pk])

    def test_type_list_filter_by_unknown_slug_returns_nothing(self):
        self.assertEqual(self._list(self.type_list_url, {'slug': 'no-such-type'}), [])

    def test_type_list_filter_by_multiple_ids(self):
        ids = [self.types[0].pk, self.types[2].pk]
        self.assertEqual(self._list(self.type_list_url, {'id': ids}), sorted(ids))

    def test_type_list_search(self):
        self.assertEqual(self._list(self.type_list_url, {'q': 'desc 2'}), [self.types[2].pk])

    # Fields: filtering

    def test_field_list_filter_by_custom_object_type_id(self):
        expected = sorted(f.pk for f in CustomObjectTypeField.objects.filter(custom_object_type=self.types[0]))
        self.assertEqual(
            self._list(self.field_list_url, {'custom_object_type_id': self.types[0].pk}),
            expected,
        )

    def test_field_list_filter_by_name(self):
        self.assertEqual(
            self._list(self.field_list_url, {'name': 'label_text'}),
            sorted(f.pk for f in self.fields),
        )

    def test_field_list_filter_by_type(self):
        expected = [CustomObjectTypeField.objects.get(name='count').pk]
        self.assertEqual(self._list(self.field_list_url, {'type': 'integer'}), expected)

    def test_field_list_filter_by_invalid_custom_object_type_id_is_rejected(self):
        response = self.client.get(reverse(self.field_list_url), {'custom_object_type_id': 999999}, **self.header)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    # Types: bulk writes

    def test_type_bulk_update_touches_only_listed_ids(self):
        data = [{'id': cot.pk, 'description': 'bulk updated'} for cot in self.types[:2]]
        response = self.client.patch(reverse(self.type_list_url), data, format='json', **self.header)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        for cot in self.types:
            cot.refresh_from_db()
        self.assertEqual([cot.description for cot in self.types], ['bulk updated', 'bulk updated', 'desc 2'])

    def test_type_bulk_delete_touches_only_listed_ids(self):
        survivor_table = self.types[2].get_model()._meta.db_table
        data = [{'id': cot.pk} for cot in self.types[:2]]
        response = self.client.delete(reverse(self.type_list_url), data, format='json', **self.header)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(list(CustomObjectType.objects.values_list('pk', flat=True)), [self.types[2].pk])
        self.assertIn(survivor_table, connection.introspection.table_names())

    def test_type_filtered_bulk_delete_spares_non_matching_types(self):
        """Mirror pynetbox ``custom_object_types.filter(slug=...).delete()``."""
        ids = self._list(self.type_list_url, {'slug': 'bulk-type-0'})
        response = self.client.delete(
            reverse(self.type_list_url), [{'id': pk} for pk in ids], format='json', **self.header
        )
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(
            sorted(CustomObjectType.objects.values_list('pk', flat=True)),
            [self.types[1].pk, self.types[2].pk],
        )

    # Fields: bulk writes

    def test_field_bulk_update_touches_only_listed_ids(self):
        data = [{'id': field.pk, 'label': 'Bulk Label'} for field in self.fields[:2]]
        response = self.client.patch(reverse(self.field_list_url), data, format='json', **self.header)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        for field in self.fields:
            field.refresh_from_db()
        self.assertEqual([field.label for field in self.fields], ['Bulk Label', 'Bulk Label', ''])

    def test_field_bulk_delete_touches_only_listed_ids(self):
        data = [{'id': field.pk} for field in self.fields[:2]]
        response = self.client.delete(reverse(self.field_list_url), data, format='json', **self.header)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        remaining = set(CustomObjectTypeField.objects.values_list('pk', flat=True))
        self.assertNotIn(self.fields[0].pk, remaining)
        self.assertNotIn(self.fields[1].pk, remaining)
        self.assertIn(self.fields[2].pk, remaining)

    def test_field_filtered_bulk_delete_spares_non_matching_fields(self):
        """Mirror pynetbox ``custom_object_type_fields.filter(type='integer').delete()``."""
        ids = self._list(self.field_list_url, {'type': 'integer'})
        response = self.client.delete(
            reverse(self.field_list_url), [{'id': pk} for pk in ids], format='json', **self.header
        )
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(
            sorted(CustomObjectTypeField.objects.values_list('pk', flat=True)),
            sorted(f.pk for f in self.fields),
        )


# ---------------------------------------------------------------------------
# PEP 440 version string validation — API layer (issue #392)
# ---------------------------------------------------------------------------

class Pep440APIValidationTestCase(CustomObjectsTestCase, TestCase):
    """
    Verify that ``validate_pep440`` surfaces as a 400 at the API layer for
    ``CustomObjectType.version`` and ``CustomObjectTypeField.deprecated_since``
    / ``scheduled_removal``.

    DRF's ModelSerializer copies model-field validators into the serializer
    field, so these should be enforced during deserialization without any
    extra serializer code.
    """

    def setUp(self):
        super().setUp()
        token_key = create_token(self.user)
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {token_key}')

        # Permission to create/change CustomObjectType records.
        add_cot_perm = ObjectPermission(name='pep440_add_cot', actions=['add', 'change'])
        add_cot_perm.save()
        add_cot_perm.users.add(self.user)
        add_cot_perm.object_types.add(ObjectType.objects.get_for_model(CustomObjectType))

        # Permission to change CustomObjectTypeField records.
        from netbox_custom_objects.models import CustomObjectTypeField  # noqa: PLC0415
        change_field_perm = ObjectPermission(name='pep440_change_field', actions=['add', 'change'])
        change_field_perm.save()
        change_field_perm.users.add(self.user)
        change_field_perm.object_types.add(ObjectType.objects.get_for_model(CustomObjectTypeField))

    # ------------------------------------------------------------------
    # CustomObjectType.version
    # ------------------------------------------------------------------

    def test_create_cot_invalid_version_returns_400(self):
        url = reverse('plugins-api:netbox_custom_objects-api:customobjecttype-list')
        data = {'name': 'vertest', 'slug': 'ver-test', 'version': 'not-a-version'}
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('version', response.data)

    def test_create_cot_valid_version_accepted(self):
        url = reverse('plugins-api:netbox_custom_objects-api:customobjecttype-list')
        data = {'name': 'vertest2', 'slug': 'ver-test-2', 'version': '1.2.3'}
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    # ------------------------------------------------------------------
    # CustomObjectTypeField.deprecated_since / scheduled_removal
    # ------------------------------------------------------------------

    def test_patch_field_invalid_deprecated_since_returns_400(self):
        cot = self.create_custom_object_type(name='pep440cot', slug='pep440-cot')
        field = self.create_custom_object_type_field(cot, name='alpha', type='text')
        url = reverse(
            'plugins-api:netbox_custom_objects-api:customobjecttypefield-detail',
            kwargs={'pk': field.pk},
        )
        response = self.client.patch(url, {'deprecated_since': 'latest'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('deprecated_since', response.data)

    def test_patch_field_invalid_scheduled_removal_returns_400(self):
        cot = self.create_custom_object_type(name='pep440cot2', slug='pep440-cot-2')
        field = self.create_custom_object_type_field(cot, name='beta', type='text')
        url = reverse(
            'plugins-api:netbox_custom_objects-api:customobjecttypefield-detail',
            kwargs={'pk': field.pk},
        )
        response = self.client.patch(url, {'scheduled_removal': '1.x.0'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('scheduled_removal', response.data)

    def test_patch_cot_valid_version_accepted(self):
        # PATCH CustomObjectType.version (no DDL on COT update) verifies the
        # validator doesn't reject a valid PEP 440 string.
        cot = self.create_custom_object_type(name='pep440cot3', slug='pep440-cot-3')
        url = reverse(
            'plugins-api:netbox_custom_objects-api:customobjecttype-detail',
            kwargs={'pk': cot.pk},
        )
        response = self.client.patch(url, {'version': '2.0.0'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
