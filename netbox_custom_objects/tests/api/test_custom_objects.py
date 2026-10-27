"""
Tests for the custom object REST API endpoints.
"""

import uuid
from unittest import skipUnless

from django.conf import settings
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from packaging.version import Version
from rest_framework import status
from rest_framework.test import APIClient

from core.models import Job, ObjectType
from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Rack, Site
from extras.models import ExportTemplate, Tag
from netbox.api.viewsets import mixins as netbox_viewset_mixins
from netbox_custom_objects.models import CustomObjectType
from users.models import ObjectPermission
from utilities.testing import TestCase as NetBoxTestCase
from utilities.testing import create_test_user
from virtualization.models import Cluster, ClusterType

from ..base import CustomObjectsTestCase, create_token


class CustomObjectAPITestCaseMixin:
    """
    Base test class for custom object API endpoints.

    Subclasses must provide:
      - setUp() — sets self.user, self.header, self.client
      - _get_detail_url(instance)
      - _get_list_url()
      - _get_queryset()
      - _add_permission(action, name=None)
      - create_data — list of dicts (at least one element)
      - bulk_update_data — dict of fields to PATCH
    """

    @property
    def update_data(self):
        return getattr(self, '_update_data', self.bulk_update_data)

    def assertHttpStatus(self, response, expected_status):
        self.assertEqual(
            response.status_code,
            expected_status,
            f'Expected HTTP {expected_status}; received {response.status_code}: '
            f'{getattr(response, "data", response.content)}',
        )

    def test_get_object_without_permission(self):
        """GET a single object without permission returns 403."""
        instance = self._get_queryset().first()
        response = self.client.get(self._get_detail_url(instance), **self.header)
        self.assertHttpStatus(response, 403)

    def test_get_object(self):
        """GET a single object with permission returns 200 and the correct record."""
        self._add_permission('view', 'Get object perm')
        instance = self._get_queryset().first()
        response = self.client.get(self._get_detail_url(instance), **self.header)
        self.assertHttpStatus(response, 200)
        self.assertEqual(response.data['id'], instance.pk)

    def test_list_objects_without_permission(self):
        """GET the list endpoint without permission returns 403."""
        response = self.client.get(self._get_list_url(), **self.header)
        self.assertHttpStatus(response, 403)

    def test_create_object_without_permission(self):
        """POST to the list endpoint without permission returns 403."""
        response = self.client.post(self._get_list_url(), self.create_data[0], format='json', **self.header)
        self.assertHttpStatus(response, 403)

    def test_update_object_without_permission(self):
        """PATCH a single object without permission returns 403."""
        instance = self._get_queryset().first()
        response = self.client.patch(self._get_detail_url(instance), self.update_data, format='json', **self.header)
        self.assertHttpStatus(response, 403)

    def test_update_object(self):
        """PATCH a single object returns 200 and persists the changes."""
        self._add_permission('change', 'Update object perm')
        instance = self._get_queryset().first()
        response = self.client.patch(self._get_detail_url(instance), self.update_data, format='json', **self.header)
        self.assertHttpStatus(response, 200)
        instance.refresh_from_db()
        for field, value in self.update_data.items():
            # Note: getattr comparison only works for scalar fields. FK/M2M fields
            # require separate assertions (e.g. comparing PKs or querysets).
            self.assertEqual(getattr(instance, field), value)

    def test_delete_object_without_permission(self):
        """DELETE a single object without permission returns 403."""
        instance = self._get_queryset().first()
        response = self.client.delete(self._get_detail_url(instance), **self.header)
        self.assertHttpStatus(response, 403)


class CustomObjectAPITestCase(CustomObjectsTestCase, CustomObjectAPITestCaseMixin, NetBoxTestCase):
    model = None  # Will be set in setUpTestData
    bulk_update_data = {
        'test_field': 'Updated test field',
    }

    def setUp(self):
        """Set up test data."""
        # Create a user
        self.user = create_test_user('testuser')

        # Use DRF's APIClient so that format='json' is honoured on all HTTP methods
        # (Django's plain Client defaults PATCH/PUT to application/octet-stream).
        self.client = APIClient()

        # Create token for API access
        token_key = create_token(self.user)
        self.header = {'HTTP_AUTHORIZATION': f'Token {token_key}'}

        # Ensure we have the model reference
        if self.model is None:
            self.model = self.custom_object_type1.get_model()

        # Make custom object type accessible as instance variable
        self.custom_object_type1 = self.__class__.custom_object_type1

    @classmethod
    def setUpTestData(cls):
        # Set up some devices to be used in object/multiobject fields
        sites = (
            Site(name='Site 1', slug='site-1'),
            Site(name='Site 2', slug='site-2'),
        )
        Site.objects.bulk_create(sites)

        racks = (
            Rack(name='Rack 1', site=sites[0]),
            Rack(name='Rack 2', site=sites[1]),
        )
        Rack.objects.bulk_create(racks)

        manufacturer = Manufacturer.objects.create(name='Manufacturer 1', slug='manufacturer-1')

        device_types = (
            DeviceType(manufacturer=manufacturer, model='Device Type 1', slug='device-type-1'),
            DeviceType(manufacturer=manufacturer, model='Device Type 2', slug='device-type-2', u_height=2),
        )
        DeviceType.objects.bulk_create(device_types)

        roles = (
            DeviceRole(name='Device Role 1', slug='device-role-1', color='ff0000'),
            DeviceRole(name='Device Role 2', slug='device-role-2', color='00ff00'),
        )
        for role in roles:
            role.save()

        cluster_type = ClusterType.objects.create(name='Cluster Type 1', slug='cluster-type-1')

        clusters = (
            Cluster(name='Cluster 1', type=cluster_type),
            Cluster(name='Cluster 2', type=cluster_type),
        )
        Cluster.objects.bulk_create(clusters)

        devices = (
            Device(
                device_type=device_types[0],
                role=roles[0],
                name='Device 1',
                site=sites[0],
                rack=racks[0],
                cluster=clusters[0],
                local_context_data={'A': 1},
            ),
            Device(
                device_type=device_types[0],
                role=roles[0],
                name='Device 2',
                site=sites[0],
                rack=racks[0],
                cluster=clusters[0],
                local_context_data={'B': 2},
            ),
            Device(
                device_type=device_types[0],
                role=roles[0],
                name='Device 3',
                site=sites[0],
                rack=racks[0],
                cluster=clusters[0],
                local_context_data={'C': 3},
            ),
        )
        Device.objects.bulk_create(devices)

        # Create test custom object types
        cls.custom_object_type1 = CustomObjectType.objects.create(
            name='TestObject1',
            description='First test custom object type',
            verbose_name_plural='Test Objects 1',
            slug='test-objects-1',
        )

        cls.custom_object_type2 = CustomObjectType.objects.create(
            name='TestObject2',
            description='Second test custom object type',
            verbose_name_plural='Test Objects 2',
            slug='test-objects-2',
        )

        cls.custom_object_type3 = cls.create_complex_custom_object_type(name='ComplexObject')

        cls.create_custom_object_type_field(cls.custom_object_type1)
        cls.model = cls.custom_object_type1.get_model()

        # Set the model for the test class
        CustomObjectAPITestCase.model = cls.model

        # Create test custom objects
        custom_objects = (
            cls.model(test_field='Test 001'),
            cls.model(test_field='Test 002'),
            cls.model(test_field='Test 003'),
        )
        cls.model.objects.bulk_create(custom_objects)

        cls.create_data = [
            {
                'test_field': 'Test 004',
            },
            {
                'test_field': 'Test 005',
            },
            {
                'test_field': 'Test 006',
            },
        ]

    def _get_queryset(self):
        """Get the queryset for the custom object model."""
        return self.model.objects.all()

    def _get_detail_url(self, instance):
        viewname = 'plugins-api:netbox_custom_objects-api:customobject-detail'
        return reverse(viewname, kwargs={'pk': instance.pk, 'custom_object_type': instance.custom_object_type.slug})

    def _get_list_url(self):
        viewname = 'plugins-api:netbox_custom_objects-api:customobject-list'
        return reverse(viewname, kwargs={'custom_object_type': self.custom_object_type1.slug})

    def _add_permission(self, action, name=None):
        """Grant the test user a permission for the current model."""
        perm = ObjectPermission(
            name=name or f'Test {action} permission',
            actions=[action],
        )
        perm.save()
        perm.users.add(self.user)
        perm.object_types.add(ObjectType.objects.get_for_model(self.model))
        return perm

    def test_list_objects(self):
        """GET the list URL returns only objects for the requested COT."""
        self._add_permission('view', 'List view perm')

        response = self.client.get(self._get_list_url(), **self.header)

        self.assertHttpStatus(response, 200)
        # The three objects created in setUpTestData must be present.
        self.assertGreaterEqual(response.data['count'], 3)

    def test_unauthenticated_request_does_not_leak_slug_existence(self):
        """An unauthenticated request must get the same status for a real and a bogus COT slug."""
        invalid_url = reverse(
            'plugins-api:netbox_custom_objects-api:customobject-list',
            kwargs={'custom_object_type': 'does-not-exist'},
        )

        valid_response = self.client.get(self._get_list_url())
        invalid_response = self.client.get(invalid_url)

        self.assertEqual(valid_response.status_code, invalid_response.status_code)
        self.assertIn(valid_response.status_code, (401, 403))

    def test_list_objects_respects_object_level_permission_constraints(self):
        """The list endpoint must only return objects matching the user's constrained permission."""
        instance = self._get_queryset().first()
        perm = ObjectPermission(
            name='Constrained list perm',
            actions=['view'],
            constraints={'pk': instance.pk},
        )
        perm.save()
        perm.users.add(self.user)
        perm.object_types.add(ObjectType.objects.get_for_model(self.model))

        response = self.client.get(self._get_list_url(), **self.header)

        self.assertHttpStatus(response, 200)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['id'], instance.pk)

    def _add_export_template(self, *, viewable=True, model=None):
        """An export template listing each object's test_field, assigned to ``model`` (default: this type)."""
        template = ExportTemplate.objects.create(
            name='Values',
            template_code='{% for obj in queryset %}{{ obj.test_field }};{% endfor %}',
            mime_type='text/plain',
        )
        template.object_types.set([ObjectType.objects.get_for_model(model or self.model)])
        if viewable:
            perm = ObjectPermission.objects.create(name='Export template view perm', actions=['view'])
            perm.users.add(self.user)
            perm.object_types.add(ObjectType.objects.get_for_model(ExportTemplate))
        return template

    def _export(self, query=''):
        return self.client.get(f'{self._get_list_url()}?export=Values{query}', **self.header)

    @staticmethod
    def _exported_values(response):
        return sorted(value for value in response.content.decode().split(';') if value)

    def test_export_renders_template(self):
        self._add_permission('view', 'Export list perm')
        self._add_export_template()

        response = self._export()
        self.assertHttpStatus(response, 200)
        self.assertEqual(response['Content-Type'], 'text/plain')
        self.assertEqual(self._exported_values(response), ['Test 001', 'Test 002', 'Test 003'])

    def test_export_applies_filters_but_not_pagination(self):
        self._add_permission('view', 'Export list perm')
        self._add_export_template()

        self.assertEqual(self._exported_values(self._export('&test_field=002')), ['Test 002'])
        # limit/offset don't apply: the export renders every matching object.
        self.assertEqual(
            self._exported_values(self._export('&limit=1&offset=1')),
            ['Test 001', 'Test 002', 'Test 003'],
        )

    def test_export_respects_object_level_permission_constraints(self):
        instance = self._get_queryset().get(test_field='Test 001')
        perm = ObjectPermission(name='Constrained export perm', actions=['view'], constraints={'pk': instance.pk})
        perm.save()
        perm.users.add(self.user)
        perm.object_types.add(ObjectType.objects.get_for_model(self.model))
        self._add_export_template()

        self.assertEqual(self._exported_values(self._export()), ['Test 001'])

    def test_export_unknown_template_returns_404(self):
        self._add_permission('view', 'Export list perm')
        self._add_export_template()

        response = self.client.get(f'{self._get_list_url()}?export=Missing', **self.header)
        self.assertHttpStatus(response, 404)

    def test_export_template_for_another_type_returns_404(self):
        self._add_permission('view', 'Export list perm')
        self._add_export_template(model=self.custom_object_type2.get_model())

        self.assertHttpStatus(self._export(), 404)

    # COMPAT(netbox<4.6.1): core's ExportTemplatesMixin doesn't check view permission on the template.
    @skipUnless(
        Version(settings.RELEASE.version) >= Version('4.6.1'),
        'NetBox < 4.6.1 renders export templates regardless of view permission',
    )
    def test_export_unviewable_template_returns_404(self):
        self._add_permission('view', 'Export list perm')
        self._add_export_template(viewable=False)

        self.assertHttpStatus(self._export(), 404)

    def test_list_objects_brief(self):
        """?brief=true trims the response to the brief field set."""
        self._add_permission('view', 'Brief list perm')

        response = self.client.get(f'{self._get_list_url()}?brief=true', **self.header)

        self.assertHttpStatus(response, 200)
        result = response.data['results'][0]
        self.assertIn('id', result)
        self.assertIn('url', result)
        self.assertNotIn('test_field', result)

    def test_list_objects_fields(self):
        """?fields= restricts the response to the requested fields."""
        self._add_permission('view', 'Fields list perm')

        response = self.client.get(f'{self._get_list_url()}?fields=id,test_field', **self.header)

        self.assertHttpStatus(response, 200)
        result = response.data['results'][0]
        self.assertEqual(set(result.keys()), {'id', 'test_field'})

    def test_list_objects_omit(self):
        """?omit= excludes the requested fields from the response."""
        self._add_permission('view', 'Omit list perm')

        response = self.client.get(f'{self._get_list_url()}?omit=test_field', **self.header)

        self.assertHttpStatus(response, 200)
        result = response.data['results'][0]
        self.assertNotIn('test_field', result)
        self.assertIn('id', result)

    def test_bulk_create_objects(self):
        """POST a list of objects to the list endpoint in a single request."""
        self._add_permission('add', 'Bulk create perm')

        initial_count = self._get_queryset().count()
        response = self.client.post(self._get_list_url(), self.create_data, format='json', **self.header)

        self.assertHttpStatus(response, 201)
        self.assertEqual(len(response.data), len(self.create_data))
        self.assertEqual(self._get_queryset().count(), initial_count + len(self.create_data))
        for i, obj in enumerate(response.data):
            self.assertEqual(obj['test_field'], self.create_data[i]['test_field'])

    def test_bulk_delete_objects(self):
        """DELETE a list of {"id": ...} objects to the list endpoint in a single request."""
        self._add_permission('delete', 'Bulk delete perm')

        initial_count = self._get_queryset().count()
        self.assertGreaterEqual(initial_count, 2, 'Need at least 2 objects to test bulk delete.')
        instances = list(self._get_queryset()[:2])
        data = [{'id': instance.pk} for instance in instances]

        response = self.client.delete(self._get_list_url(), data, format='json', **self.header)

        self.assertHttpStatus(response, 204)
        self.assertEqual(self._get_queryset().count(), initial_count - 2)
        for instance in instances:
            self.assertFalse(
                self._get_queryset().filter(pk=instance.pk).exists(),
                f'Object {instance.pk} should have been deleted.',
            )

    def test_bulk_update_objects(self):
        """PATCH a list of {"id": ..., ...} objects to the list endpoint in a single request."""
        self._add_permission('change', 'Bulk update perm')

        instances = list(self._get_queryset()[:2])
        updates = {
            instances[0].pk: 'Updated 001',
            instances[1].pk: 'Updated 002',
        }
        data = [{'id': pk, 'test_field': value} for pk, value in updates.items()]

        response = self.client.patch(self._get_list_url(), data, format='json', **self.header)

        self.assertHttpStatus(response, 200)
        instances[0].refresh_from_db()
        instances[1].refresh_from_db()
        self.assertEqual(instances[0].test_field, 'Updated 001')
        self.assertEqual(instances[1].test_field, 'Updated 002')

    # COMPAT(netbox<4.7): ?background=true is ignored, so the update applies normally.
    @skipUnless(
        hasattr(netbox_viewset_mixins, 'BackgroundOperationMixin'), 'NetBox < 4.7 has no background bulk writes'
    )
    def test_background_bulk_update_rejected(self):
        """?background=true on a bulk write must be rejected rather than silently broken."""
        self._add_permission('change', 'Background bulk update perm')
        instance = self._get_queryset().first()
        data = [{'id': instance.pk, 'test_field': 'Should not apply'}]
        initial_job_count = Job.objects.count()

        response = self.client.patch(f'{self._get_list_url()}?background=true', data, format='json', **self.header)

        self.assertHttpStatus(response, 400)
        self.assertEqual(Job.objects.count(), initial_job_count)
        instance.refresh_from_db()
        self.assertNotEqual(instance.test_field, 'Should not apply')

    def test_delete_object(self):
        """DELETE a single object returns 204 and removes the record."""
        self._add_permission('delete', 'Delete perm')

        instance = self._get_queryset().first()
        url = self._get_detail_url(instance)

        response = self.client.delete(url, **self.header)

        self.assertHttpStatus(response, 204)
        self.assertFalse(
            self._get_queryset().filter(pk=instance.pk).exists(),
            'Deleted object should no longer exist in the database.',
        )

    def test_create_with_nested_serializers(self):
        """
        POST a single object with a multiobject field's values specified via a list of PKs.
        """
        model = self.custom_object_type3.get_model()

        # Set the model for the test class
        self.model = model

        # Add object-level permission
        obj_perm = ObjectPermission(name='Test permission', actions=['add'])
        obj_perm.save()
        obj_perm.users.add(self.user)
        obj_perm.object_types.add(ObjectType.objects.get_for_model(self.model))
        view_device_perm = ObjectPermission(name='view-device-for-nested', actions=['view'])
        view_device_perm.save()
        view_device_perm.users.add(self.user)
        view_device_perm.object_types.add(ObjectType.objects.get_for_model(Device))

        devices = Device.objects.all()

        # custom_object_type3 (ComplexObject) uses 'name' as its primary text field
        data = {
            'name': 'Test Nested 001',
            'device': devices[0].id,
            'devices': [devices[1].id, devices[2].id],
        }

        initial_count = self._get_queryset().count()

        viewname = 'plugins-api:netbox_custom_objects-api:customobject-list'
        list_url = reverse(viewname, kwargs={'custom_object_type': self.custom_object_type3.slug})

        response = self.client.post(list_url, data, format='json', **self.header)
        self.assertHttpStatus(response, status.HTTP_201_CREATED)
        self.assertEqual(self._get_queryset().count(), initial_count + 1)
        instance = self._get_queryset().get(pk=response.data['id'])
        # Assert all fields sent in data, including the nested FK and M2M object fields
        self.assertEqual(instance.name, data['name'])
        self.assertEqual(instance.device_id, data['device'])
        self.assertSetEqual(
            set(instance.devices.values_list('id', flat=True)),
            set(data['devices']),
        )

    def test_create_with_tags_persists_to_db(self):
        """Tags submitted on POST must be saved to the DB, not just echoed."""
        self._add_permission('add', 'Create with tags perm')
        self.add_permissions('extras.view_tag')
        tag = Tag.objects.get_or_create(name='api-create-tag', slug='api-create-tag')[0]

        data = {
            'test_field': 'Tagged Object',
            'tags': [{'id': tag.id, 'name': tag.name, 'slug': tag.slug, 'color': tag.color}],
        }
        response = self.client.post(self._get_list_url(), data, format='json', **self.header)
        self.assertHttpStatus(response, status.HTTP_201_CREATED)
        self.assertIn('tags', response.data)
        self.assertTrue(len(response.data['tags']) > 0, 'Response should include the submitted tag')

        # Fetch fresh from the DB — the critical assertion that caught the bug
        instance = self._get_queryset().get(pk=response.data['id'])
        self.assertIn(tag.name, list(instance.tags.names()), 'Tag must be persisted to the DB')

    def test_patch_with_tags_persists_to_db(self):
        """Tags submitted on PATCH must be saved to the DB, not just echoed."""
        self._add_permission('view', 'View perm')
        self._add_permission('change', 'Patch with tags perm')
        self.add_permissions('extras.view_tag')
        tag = Tag.objects.get_or_create(name='api-patch-tag', slug='api-patch-tag')[0]

        instance = self._get_queryset().first()
        self.assertEqual(list(instance.tags.names()), [], 'Instance should start with no tags')

        data = {'tags': [{'id': tag.id, 'name': tag.name, 'slug': tag.slug, 'color': tag.color}]}
        response = self.client.patch(self._get_detail_url(instance), data, format='json', **self.header)
        self.assertHttpStatus(response, status.HTTP_200_OK)

        instance.refresh_from_db()
        self.assertIn(tag.name, list(instance.tags.names()), 'Tag must be persisted to the DB after PATCH')

    def test_patch_with_empty_tags_clears_existing(self):
        """PATCH with tags=[] must remove all existing tags from the DB."""
        self._add_permission('view', 'View perm')
        self._add_permission('change', 'Patch clear tags perm')
        tag = Tag.objects.get_or_create(name='api-clear-tag', slug='api-clear-tag')[0]

        instance = self._get_queryset().first()
        instance.tags.add(tag.name)
        self.assertIn(tag.name, list(instance.tags.names()), 'Pre-condition: tag should be set')

        response = self.client.patch(self._get_detail_url(instance), {'tags': []}, format='json', **self.header)
        self.assertHttpStatus(response, status.HTTP_200_OK)

        instance.refresh_from_db()
        self.assertEqual(list(instance.tags.names()), [], 'All tags must be cleared after PATCH with tags=[]')

    def test_patch_without_tags_preserves_existing(self):
        """PATCH that omits the tags key entirely must leave existing tags unchanged."""
        self._add_permission('view', 'View perm')
        self._add_permission('change', 'Patch preserve tags perm')
        tag = Tag.objects.get_or_create(name='api-preserve-tag', slug='api-preserve-tag')[0]

        instance = self._get_queryset().first()
        instance.tags.add(tag.name)
        self.assertIn(tag.name, list(instance.tags.names()), 'Pre-condition: tag should be set')

        response = self.client.patch(
            self._get_detail_url(instance), {'test_field': 'updated'}, format='json', **self.header
        )
        self.assertHttpStatus(response, status.HTTP_200_OK)

        instance.refresh_from_db()
        self.assertIn(
            tag.name,
            list(instance.tags.names()),
            'Existing tags must be preserved when tags not in PATCH payload',
        )


class PolymorphicMultiObjectPrefetchTestCase(CustomObjectsTestCase, TestCase):
    """Polymorphic multi-object fields must be prefetched (flat query count) and
    must serialize each instance's own members correctly once prefetched."""

    def setUp(self):
        self.user = create_test_user('polym2mprefetchuser')
        token_key = create_token(self.user)
        self.header = {'HTTP_AUTHORIZATION': f'Token {token_key}'}

        self.cot = CustomObjectsTestCase.create_custom_object_type(
            name='PolyM2MPrefetchTest',
            slug='poly-m2m-prefetch-test',
        )
        CustomObjectsTestCase.create_custom_object_type_field(
            self.cot,
            name='name',
            label='Name',
            type='text',
            primary=True,
            required=True,
        )
        CustomObjectsTestCase.create_polymorphic_field(
            self.cot,
            related_object_types=[
                CustomObjectsTestCase.get_device_object_type(),
                CustomObjectsTestCase.get_site_object_type(),
            ],
            name='targets',
            label='Targets',
            type='multiobject',
        )
        self.model = self.cot.get_model()

        manufacturer = Manufacturer.objects.create(name='PM2M Mfr', slug='pm2m-mfr')
        device_type = DeviceType.objects.create(
            manufacturer=manufacturer,
            model='PM2M Type',
            slug='pm2m-type',
        )
        role = DeviceRole.objects.create(name='PM2M Role', slug='pm2m-role', color='ffffff')
        self.site_a = Site.objects.create(name='PM2M Site A', slug='pm2m-site-a')
        self.site_b = Site.objects.create(name='PM2M Site B', slug='pm2m-site-b')
        self.devices = Device.objects.bulk_create(
            [Device(device_type=device_type, role=role, name=f'PM2M Device {i}', site=self.site_a) for i in range(3)]
        )

        perm = ObjectPermission(name='PM2M view perm', actions=['view'])
        perm.save()
        perm.users.add(self.user)
        perm.object_types.add(ObjectType.objects.get_for_model(self.model))

    def _url(self):
        viewname = 'plugins-api:netbox_custom_objects-api:customobject-list'
        return reverse(viewname, kwargs={'custom_object_type': self.cot.slug})

    def test_omit_excludes_field_and_skips_its_prefetch(self):
        """?omit=targets must drop the field from the response and skip prefetching it."""
        for i in range(6):
            obj = self.model.objects.create(name=f'PM2M Obj {i}')
            obj.targets.set([self.devices[0], self.devices[1], self.site_a])

        list_url = f'{self._url()}?limit=100'
        omit_url = f'{list_url}&omit=targets'
        # Warm the per-COT dynamic model/serializer class cache for both shapes.
        self.client.get(list_url, **self.header)
        self.client.get(omit_url, **self.header)

        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(list_url, **self.header)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('targets', response.data['results'][0])
        with_field_query_count = len(ctx.captured_queries)

        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(omit_url, **self.header)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertNotIn('targets', response.data['results'][0])
        self.assertLess(
            len(ctx.captured_queries),
            with_field_query_count,
            "?omit=targets must skip the polymorphic field's prefetch entirely.",
        )

    def test_prefetched_results_are_scoped_per_instance(self):
        """Each instance's serialized targets must reflect only its own memberships,
        not another instance's, even though results share a single prefetch pass."""
        obj_a = self.model.objects.create(name='PM2M Obj A')
        obj_a.targets.set([self.devices[0], self.devices[1], self.site_a])
        obj_b = self.model.objects.create(name='PM2M Obj B')
        obj_b.targets.set([self.devices[0], self.site_b])
        self.model.objects.create(name='PM2M Obj C')
        # 'PM2M Obj C' intentionally left with no targets.

        response = self.client.get(f'{self._url()}?limit=100', **self.header)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        by_name = {row['name']: row for row in response.data['results']}

        def target_ids(row):
            return sorted(t['id'] for t in row['targets'])

        self.assertEqual(
            target_ids(by_name['PM2M Obj A']),
            sorted([self.devices[0].pk, self.devices[1].pk, self.site_a.pk]),
        )
        self.assertEqual(
            target_ids(by_name['PM2M Obj B']),
            sorted([self.devices[0].pk, self.site_b.pk]),
        )
        self.assertEqual(target_ids(by_name['PM2M Obj C']), [])


class SerializerFieldCoverageTestCase(CustomObjectsTestCase, TestCase):
    """
    Verify that the dynamic serializer produced by get_serializer_class() exposes
    the expected fields in API responses.
    """

    def setUp(self):
        self.user = create_test_user('serluser')
        token_key = create_token(self.user)
        self.header = {'HTTP_AUTHORIZATION': f'Token {token_key}'}

        self.cot = CustomObjectsTestCase.create_custom_object_type(name='SerializerTest', slug='serializer-test')
        # Primary text field
        CustomObjectsTestCase.create_custom_object_type_field(
            self.cot,
            name='label',
            label='Label',
            type='text',
            primary=True,
            required=True,
        )
        # Integer field
        CustomObjectsTestCase.create_custom_object_type_field(
            self.cot,
            name='count',
            label='Count',
            type='integer',
        )
        # Object field (device)
        device_ct = CustomObjectsTestCase.get_device_object_type()
        CustomObjectsTestCase.create_custom_object_type_field(
            self.cot,
            name='device',
            label='Device',
            type='object',
            related_object_type=device_ct,
        )

        self.model = self.cot.get_model()

        obj_perm = ObjectPermission(name='Serializer view perm', actions=['view'])
        obj_perm.save()
        obj_perm.users.add(self.user)
        obj_perm.object_types.add(ObjectType.objects.get_for_model(self.model))

    def tearDown(self):
        CustomObjectType.clear_model_cache()
        super().tearDown()

    def _list_url(self):
        return reverse(
            'plugins-api:netbox_custom_objects-api:customobject-list',
            kwargs={'custom_object_type': self.cot.slug},
        )

    def _detail_url(self, instance):
        return reverse(
            'plugins-api:netbox_custom_objects-api:customobject-detail',
            kwargs={'pk': instance.pk, 'custom_object_type': self.cot.slug},
        )

    def test_detail_response_contains_netbox_standard_fields(self):
        """Detail response must include the standard NetBox envelope fields."""
        instance = self.model.objects.create(label='Test Instance', count=5)
        response = self.client.get(self._detail_url(instance), **self.header)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        for field in ('id', 'url', 'display', 'owner', 'created', 'last_updated', 'tags'):
            self.assertIn(field, response.data, f"Standard field '{field}' missing from response")

    def test_detail_response_contains_custom_fields(self):
        """Detail response includes fields defined on the COT."""
        instance = self.model.objects.create(label='My Label', count=42)
        response = self.client.get(self._detail_url(instance), **self.header)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('label', response.data)
        self.assertEqual(response.data['label'], 'My Label')
        self.assertIn('count', response.data)
        self.assertEqual(response.data['count'], 42)

    def test_detail_response_contains_object_field(self):
        """Object fields (FK) are serialised as nested representations."""
        instance = self.model.objects.create(label='Device Holder')
        response = self.client.get(self._detail_url(instance), **self.header)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('device', response.data)
        # When no device is set the field must be present but null
        self.assertIsNone(response.data['device'])

    def test_list_response_shape(self):
        """List response wraps results in count/next/previous/results envelope."""
        self.model.objects.create(label='A')
        self.model.objects.create(label='B')
        response = self.client.get(self._list_url(), **self.header)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        for key in ('count', 'next', 'previous', 'results'):
            self.assertIn(key, response.data, f"Envelope key '{key}' missing from list response")
        self.assertGreaterEqual(response.data['count'], 2)

    def test_url_field_is_absolute(self):
        """The 'url' field in the response must be an absolute HTTP URL."""
        instance = self.model.objects.create(label='URL Test')
        response = self.client.get(self._detail_url(instance), **self.header)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        url_value = response.data.get('url', '')
        self.assertRegex(
            url_value,
            r'^https?://',
            f"'url' field should be an absolute HTTP(S) URL, got: {url_value!r}",
        )


# ---------------------------------------------------------------------------
# Context field — serializer and API response
# ---------------------------------------------------------------------------


class ContextFieldApiTestCase(CustomObjectsTestCase, TestCase):
    """
    Verify the _context field in API responses when a COT has a context field.

    Two display scenarios are exercised:
      • COT with a primary field  → display = primary field value
      • COT with no primary field → display = fallback "{COT display_name} {id}"
    In both cases _context.display must equal the context field value.
    """

    def setUp(self):
        self.user = create_test_user('ctxapiuser')
        token_key = create_token(self.user)
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {token_key}')

        # --- COT A: primary field + context field ---
        self.cot_with_primary = CustomObjectsTestCase.create_custom_object_type(
            name='ctxapiprimary', slug='ctx-api-primary'
        )
        CustomObjectsTestCase.create_custom_object_type_field(
            self.cot_with_primary, name='name', type='text', primary=True
        )
        CustomObjectsTestCase.create_custom_object_type_field(
            self.cot_with_primary, name='owner', type='text', context=True
        )
        self.model_with_primary = self.cot_with_primary.get_model()

        perm_a = ObjectPermission(name='ctx-api-perm-a', actions=['view'])
        perm_a.save()
        perm_a.users.add(self.user)
        perm_a.object_types.add(ObjectType.objects.get_for_model(self.model_with_primary))

        # --- COT B: no primary field + context field (fallback display) ---
        self.cot_no_primary = CustomObjectsTestCase.create_custom_object_type(
            name='ctxapinoprimary', slug='ctx-api-no-primary'
        )
        CustomObjectsTestCase.create_custom_object_type_field(
            self.cot_no_primary, name='owner', type='text', context=True
        )
        self.model_no_primary = self.cot_no_primary.get_model()

        perm_b = ObjectPermission(name='ctx-api-perm-b', actions=['view'])
        perm_b.save()
        perm_b.users.add(self.user)
        perm_b.object_types.add(ObjectType.objects.get_for_model(self.model_no_primary))

        # --- COT C: primary field + two context fields ---
        self.cot_multi_ctx = CustomObjectsTestCase.create_custom_object_type(
            name='ctxapimultictx', slug='ctx-api-multi-ctx'
        )
        CustomObjectsTestCase.create_custom_object_type_field(
            self.cot_multi_ctx, name='name', type='text', primary=True
        )
        CustomObjectsTestCase.create_custom_object_type_field(
            self.cot_multi_ctx, name='owner', type='text', context=True
        )
        CustomObjectsTestCase.create_custom_object_type_field(
            self.cot_multi_ctx, name='region', type='text', context=True
        )
        self.model_multi_ctx = self.cot_multi_ctx.get_model()

        perm_c = ObjectPermission(name='ctx-api-perm-c', actions=['view'])
        perm_c.save()
        perm_c.users.add(self.user)
        perm_c.object_types.add(ObjectType.objects.get_for_model(self.model_multi_ctx))

    def tearDown(self):
        CustomObjectType.clear_model_cache()
        super().tearDown()

    def _detail_url(self, cot, instance):
        return reverse(
            'plugins-api:netbox_custom_objects-api:customobject-detail',
            kwargs={'pk': instance.pk, 'custom_object_type': cot.slug},
        )

    # --- Serializer class structure ---

    def test_serializer_meta_includes_context_field(self):
        """get_serializer_class() must add _context to Meta.fields and brief_fields."""
        from netbox_custom_objects.api.serializers import get_serializer_class

        cls = get_serializer_class(self.model_with_primary)
        self.assertIn('_context', cls.Meta.fields)
        self.assertIn('_context', cls.Meta.brief_fields)

    def test_serializer_meta_excludes_context_when_no_context_fields(self):
        """_context must not appear when the COT has no context fields."""
        from netbox_custom_objects.api.serializers import get_serializer_class

        cot = CustomObjectsTestCase.create_custom_object_type(name='ctxapinoctx', slug='ctx-api-no-ctx')
        CustomObjectsTestCase.create_custom_object_type_field(cot, name='name', type='text', primary=True)
        model = cot.get_model()
        cls = get_serializer_class(model)
        self.assertNotIn('_context', cls.Meta.fields)
        self.assertNotIn('_context', cls.Meta.brief_fields)

    # --- Primary field present ---

    def test_display_equals_primary_field_value(self):
        """display must be the primary field value, not the fallback."""
        instance = self.model_with_primary.objects.create(name='Route-A', owner='Alice')
        response = self.client.get(self._detail_url(self.cot_with_primary, instance))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['display'], 'Route-A')

    def test_context_display_value_with_primary_field(self):
        """_context.display must equal the context field value when primary is set."""
        instance = self.model_with_primary.objects.create(name='Route-A', owner='Alice')
        response = self.client.get(self._detail_url(self.cot_with_primary, instance))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNotNone(response.data['_context'])
        self.assertEqual(response.data['_context']['display'], 'Alice')

    def test_context_null_when_context_field_has_no_value(self):
        """_context must be null when the context field carries no value."""
        instance = self.model_with_primary.objects.create(name='Route-B')
        response = self.client.get(self._detail_url(self.cot_with_primary, instance))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data['_context'])

    # --- No primary field (fallback display) ---

    def test_display_uses_fallback_when_no_primary_field(self):
        """display must use the fallback format when no primary field is configured."""
        instance = self.model_no_primary.objects.create(owner='Bob')
        expected = f'{self.cot_no_primary.display_name} {instance.id}'
        response = self.client.get(self._detail_url(self.cot_no_primary, instance))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['display'], expected)

    def test_context_display_value_with_fallback_display(self):
        """_context.display must work correctly even when display uses the fallback name."""
        instance = self.model_no_primary.objects.create(owner='Bob')
        response = self.client.get(self._detail_url(self.cot_no_primary, instance))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNotNone(response.data['_context'])
        self.assertEqual(response.data['_context']['display'], 'Bob')

    # --- Multiple context fields ---

    def test_multiple_context_fields_joined_in_display(self):
        """_context.display must join all context field values with ', '."""
        instance = self.model_multi_ctx.objects.create(name='Route-C', owner='Carol', region='EU')
        response = self.client.get(self._detail_url(self.cot_multi_ctx, instance))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNotNone(response.data['_context'])
        self.assertEqual(response.data['_context']['display'], 'Carol, EU')

    def test_multiple_context_fields_omits_empty_values(self):
        """_context.display must only include context fields that have a value."""
        instance = self.model_multi_ctx.objects.create(name='Route-D', owner='Dave')
        # region (second context field) is not set
        response = self.client.get(self._detail_url(self.cot_multi_ctx, instance))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNotNone(response.data['_context'])
        self.assertEqual(response.data['_context']['display'], 'Dave')


class CrossCOTMultiObjectAPITestCase(CustomObjectsTestCase, TestCase):
    """
    Tests for API PATCH/PUT behaviour when a CO has a cross-COT multiobject
    (non-polymorphic M2M) field pointing to another COT.
    """

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        token_key = create_token(self.user)
        self.header = {'HTTP_AUTHORIZATION': f'Token {token_key}'}

        # COT_SOURCE has a multiobject field → COT_TARGET
        self.cot_target = CustomObjectsTestCase.create_custom_object_type(name='CrossTarget', slug='cross-target')
        CustomObjectsTestCase.create_custom_object_type_field(
            self.cot_target,
            name='name',
            label='Name',
            type='text',
            primary=True,
            required=True,
        )

        self.cot_source = CustomObjectsTestCase.create_custom_object_type(name='CrossSource', slug='cross-source')
        CustomObjectsTestCase.create_custom_object_type_field(
            self.cot_source,
            name='name',
            label='Name',
            type='text',
            primary=True,
            required=True,
        )
        CustomObjectsTestCase.create_custom_object_type_field(
            self.cot_source,
            name='refs',
            label='References',
            type='multiobject',
            related_object_type=self.cot_target.object_type,
        )

        # Per cross-COT FK convention: generate source first, refresh target, then target.
        self.model_source = self.cot_source.get_model()
        self.cot_target.refresh_from_db()
        self.model_target = self.cot_target.get_model()

        self.obj_target1 = self.model_target.objects.create(name='Target-A')
        self.obj_target2 = self.model_target.objects.create(name='Target-B')
        self.obj_target3 = self.model_target.objects.create(name='Target-C')
        self.obj_source = self.model_source.objects.create(name='Source-1')
        self.obj_source.refs.add(self.obj_target1)

    def _detail_url(self, instance):
        return reverse(
            'plugins-api:netbox_custom_objects-api:customobject-detail',
            kwargs={'pk': instance.pk, 'custom_object_type': instance.custom_object_type.slug},
        )

    def _add_perm(self, action, model):
        perm = ObjectPermission(name=f'{action}-{model._meta.model_name}', actions=[action])
        perm.save()
        perm.users.add(self.user)
        perm.object_types.add(ObjectType.objects.get_for_model(model))
        return perm

    def test_patch_updates_cross_cot_m2m_field(self):
        """PATCH with a list of target PKs must update the M2M field."""
        self._add_perm('change', self.model_source)
        self._add_perm('view', self.model_target)

        # Confirm initial state.
        self.assertSetEqual(
            set(self.obj_source.refs.values_list('id', flat=True)),
            {self.obj_target1.pk},
        )

        response = self.client.patch(
            self._detail_url(self.obj_source),
            {'refs': [self.obj_target2.pk, self.obj_target3.pk]},
            format='json',
            **self.header,
        )
        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
            f'Expected 200; got {response.status_code}: {getattr(response, "data", response.content)}',
        )

        self.obj_source.refresh_from_db()
        self.assertSetEqual(
            set(self.obj_source.refs.values_list('id', flat=True)),
            {self.obj_target2.pk, self.obj_target3.pk},
            'PATCH must replace M2M values for a cross-COT multiobject field.',
        )

    def test_patch_clears_cross_cot_m2m_field(self):
        """PATCH with an empty list must clear the M2M field."""
        self._add_perm('change', self.model_source)

        response = self.client.patch(
            self._detail_url(self.obj_source),
            {'refs': []},
            format='json',
            **self.header,
        )
        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
            f'Expected 200; got {response.status_code}: {getattr(response, "data", response.content)}',
        )

        self.obj_source.refresh_from_db()
        self.assertSetEqual(
            set(self.obj_source.refs.values_list('id', flat=True)),
            set(),
            'PATCH with empty list must clear M2M values.',
        )

    def test_patch_scalar_field_preserves_m2m(self):
        """PATCH a scalar field must not disturb existing M2M values."""
        self._add_perm('change', self.model_source)

        response = self.client.patch(
            self._detail_url(self.obj_source),
            {'name': 'Updated Name'},
            format='json',
            **self.header,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.obj_source.refresh_from_db()
        self.assertEqual(self.obj_source.name, 'Updated Name')
        self.assertSetEqual(
            set(self.obj_source.refs.values_list('id', flat=True)),
            {self.obj_target1.pk},
            'PATCH on a scalar field must not clear existing M2M relationships.',
        )

    def test_put_updates_scalar_field(self):
        """PUT must update scalar fields."""
        self._add_perm('change', self.model_source)

        # PUT requires the full object payload; supply name + empty refs.
        response = self.client.put(
            self._detail_url(self.obj_source),
            {'name': 'Put Updated Name', 'refs': []},
            format='json',
            **self.header,
        )
        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
            f'Expected 200; got {response.status_code}: {getattr(response, "data", response.content)}',
        )

        self.obj_source.refresh_from_db()
        self.assertEqual(self.obj_source.name, 'Put Updated Name')

    def test_get_response_includes_cross_cot_m2m_field(self):
        """GET must return the M2M field as a nested list."""
        self._add_perm('view', self.model_source)

        response = self.client.get(self._detail_url(self.obj_source), **self.header)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('refs', response.data, 'Response must include the refs M2M field.')
        ref_ids = [r['id'] for r in response.data['refs']]
        self.assertIn(self.obj_target1.pk, ref_ids)


class OwnerAPITestCase(CustomObjectsTestCase, TestCase):
    """Tests for the owner field on custom object instances."""

    def setUp(self):
        super().setUp()
        from netbox_custom_objects.models import CustomObjectType

        self.cot = CustomObjectType.objects.create(name='OwnedThing', slug='owned-thing')
        self.model = self.cot.get_model()
        token = create_token(self.user)
        self.header = {'HTTP_AUTHORIZATION': f'Token {token}'}
        self.client = APIClient()

    def _list_url(self):
        return reverse(
            'plugins-api:netbox_custom_objects-api:customobject-list',
            kwargs={'custom_object_type': self.cot.slug},
        )

    def _detail_url(self, pk):
        return reverse(
            'plugins-api:netbox_custom_objects-api:customobject-detail',
            kwargs={'pk': pk, 'custom_object_type': self.cot.slug},
        )

    def _add_perm(self, action, model):
        perm = ObjectPermission(name=f'{action}-{model._meta.model_name}', actions=[action])
        perm.save()
        perm.users.add(self.user)
        perm.object_types.add(ObjectType.objects.get_for_model(model))
        return perm

    def _grant_owner_view(self, suffix=''):
        from users.models import Owner

        perm = ObjectPermission(name=f'view-owner{suffix}', actions=['view'])
        perm.save()
        perm.users.add(self.user)
        perm.object_types.add(ObjectType.objects.get_for_model(Owner))
        return perm

    def test_owner_null_by_default(self):
        """New CO instances must have owner=None when not explicitly set."""
        obj = self.model.objects.create()
        self.assertIsNone(obj.owner)

    def test_owner_present_in_api_response(self):
        """GET response must contain an 'owner' key (null when unset)."""
        obj = self.model.objects.create()
        self._add_perm('view', self.model)

        response = self.client.get(self._detail_url(obj.pk), **self.header)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('owner', response.data)
        self.assertIsNone(response.data['owner'])

    def test_create_with_owner(self):
        """POST with owner=<pk> must persist the owner FK."""
        from users.models import Owner

        owner = Owner.objects.create(name='create-owner')
        self._add_perm('add', self.model)
        self._grant_owner_view('-create')

        response = self.client.post(
            self._list_url(),
            {'owner': owner.pk},
            format='json',
            **self.header,
        )
        self.assertEqual(
            response.status_code,
            status.HTTP_201_CREATED,
            f'Expected 201; got {response.status_code}: {getattr(response, "data", response.content)}',
        )
        obj = self.model.objects.get(pk=response.data['id'])
        self.assertEqual(obj.owner_id, owner.pk)

    def test_patch_sets_owner(self):
        """PATCH with owner=<pk> must update the owner FK."""
        from users.models import Owner

        obj = self.model.objects.create()
        owner = Owner.objects.create(name='patch-owner')
        self._add_perm('change', self.model)
        self._grant_owner_view('-patch')

        response = self.client.patch(
            self._detail_url(obj.pk),
            {'owner': owner.pk},
            format='json',
            **self.header,
        )
        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
            f'Expected 200; got {response.status_code}: {getattr(response, "data", response.content)}',
        )
        obj.refresh_from_db()
        self.assertEqual(obj.owner_id, owner.pk)

    def test_patch_clears_owner(self):
        """PATCH with owner=null must clear the owner FK."""
        from users.models import Owner

        owner = Owner.objects.create(name='clear-owner')
        obj = self.model.objects.create(owner=owner)
        self._add_perm('change', self.model)

        response = self.client.patch(
            self._detail_url(obj.pk),
            {'owner': None},
            format='json',
            **self.header,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        obj.refresh_from_db()
        self.assertIsNone(obj.owner)

    def test_filter_by_owner_id(self):
        """?owner_id=<pk> must return only instances with that owner."""
        from users.models import Owner

        owner_a = Owner.objects.create(name='filter-owner-a')
        owner_b = Owner.objects.create(name='filter-owner-b')
        obj_a = self.model.objects.create(owner=owner_a)
        obj_b = self.model.objects.create(owner=owner_b)
        self._add_perm('view', self.model)

        response = self.client.get(
            self._list_url(),
            {'owner_id': owner_a.pk},
            **self.header,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = [r['id'] for r in response.data['results']]
        self.assertIn(obj_a.pk, ids)
        self.assertNotIn(obj_b.pk, ids)

    def test_filter_by_owner_group_id(self):
        """?owner_group_id=<pk> must return only instances whose owner belongs to that group."""
        from users.models import Owner, OwnerGroup

        group_x = OwnerGroup.objects.create(name='group-x')
        group_y = OwnerGroup.objects.create(name='group-y')
        owner_x = Owner.objects.create(name='owner-in-x', group=group_x)
        owner_y = Owner.objects.create(name='owner-in-y', group=group_y)
        obj_x = self.model.objects.create(owner=owner_x)
        obj_y = self.model.objects.create(owner=owner_y)
        self._add_perm('view', self.model)

        response = self.client.get(
            self._list_url(),
            {'owner_group_id': group_x.pk},
            **self.header,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = [r['id'] for r in response.data['results']]
        self.assertIn(obj_x.pk, ids)
        self.assertNotIn(obj_y.pk, ids)

    def test_filter_by_id(self):
        """?id=<pk> must return only the matching instance."""
        obj_a = self.model.objects.create()
        obj_b = self.model.objects.create()
        self._add_perm('view', self.model)

        response = self.client.get(
            self._list_url(),
            {'id': obj_a.pk},
            **self.header,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = [r['id'] for r in response.data['results']]
        self.assertIn(obj_a.pk, ids)
        self.assertNotIn(obj_b.pk, ids)


class NullOptionalObjectFieldTestCase(CustomObjectsTestCase, TestCase):
    """
    POST/PATCH with explicit null on a non-required object or multiobject field
    must succeed (201/200).  Before the fix, the serializer lacked allow_null=True
    so null was rejected with 400 'This field may not be null.'
    """

    def setUp(self):
        self.user = create_test_user('nullobjuser')
        token_key = create_token(self.user)
        self.header = {'HTTP_AUTHORIZATION': f'Token {token_key}'}
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {token_key}')

        self.cot = CustomObjectsTestCase.create_custom_object_type(name='NullObjTest', slug='null-obj-test')
        CustomObjectsTestCase.create_custom_object_type_field(
            self.cot,
            name='name',
            type='text',
            primary=True,
            required=True,
        )
        device_ct = CustomObjectsTestCase.get_device_object_type()
        CustomObjectsTestCase.create_custom_object_type_field(
            self.cot,
            name='device',
            type='object',
            related_object_type=device_ct,
            required=False,
        )
        CustomObjectsTestCase.create_custom_object_type_field(
            self.cot,
            name='devices',
            type='multiobject',
            related_object_type=device_ct,
            required=False,
        )
        self.model = self.cot.get_model()

        uid = uuid.uuid4().hex[:8]
        for action in ('add', 'change', 'view'):
            perm = ObjectPermission(name=f'null-obj-{action}-{uid}', actions=[action])
            perm.save()
            perm.users.add(self.user)
            perm.object_types.add(ObjectType.objects.get_for_model(self.model))

    def tearDown(self):
        CustomObjectType.clear_model_cache()
        super().tearDown()

    def _list_url(self):
        return reverse(
            'plugins-api:netbox_custom_objects-api:customobject-list',
            kwargs={'custom_object_type': self.cot.slug},
        )

    def _detail_url(self, pk):
        return reverse(
            'plugins-api:netbox_custom_objects-api:customobject-detail',
            kwargs={'pk': pk, 'custom_object_type': self.cot.slug},
        )

    def test_post_null_object_field_accepted(self):
        """POST with explicit null on an optional object field must return 201."""
        response = self.client.post(
            self._list_url(),
            {'name': 'obj-null-post', 'device': None},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertIsNone(response.data['device'])

    def test_post_null_multiobject_field_accepted(self):
        """POST with explicit null on an optional multiobject field must return 201."""
        response = self.client.post(
            self._list_url(),
            {'name': 'multiobj-null-post', 'devices': None},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data['devices'], [])

    def test_patch_null_object_field_clears_value(self):
        """PATCH with null on an optional object field must clear it and return 200."""
        site = Site.objects.create(name='null-test-site', slug='null-test-site')
        manufacturer = Manufacturer.objects.create(name='null-mfr', slug='null-mfr')
        device_type = DeviceType.objects.create(manufacturer=manufacturer, model='null-dt', slug='null-dt')
        device_role = DeviceRole.objects.create(name='null-role', slug='null-role')
        device = Device.objects.create(name='null-dev', site=site, device_type=device_type, role=device_role)
        obj = self.model.objects.create(name='patch-null-obj', device=device)
        self.assertIsNotNone(obj.device)

        response = self.client.patch(
            self._detail_url(obj.pk),
            {'device': None},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        obj.refresh_from_db()
        self.assertIsNone(obj.device)

    def test_patch_null_multiobject_field_clears_value(self):
        """PATCH with null on an optional multiobject field must clear it and return 200."""
        site = Site.objects.create(name='null-multi-site', slug='null-multi-site')
        manufacturer = Manufacturer.objects.create(name='null-multi-mfr', slug='null-multi-mfr')
        device_type = DeviceType.objects.create(manufacturer=manufacturer, model='null-multi-dt', slug='null-multi-dt')
        device_role = DeviceRole.objects.create(name='null-multi-role', slug='null-multi-role')
        device = Device.objects.create(name='null-multi-dev', site=site, device_type=device_type, role=device_role)
        obj = self.model.objects.create(name='patch-null-multi-obj')
        obj.devices.add(device)
        self.assertEqual(obj.devices.count(), 1)

        response = self.client.patch(
            self._detail_url(obj.pk),
            {'devices': None},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        obj.refresh_from_db()
        self.assertEqual(obj.devices.count(), 0)

    def test_post_null_required_object_field_rejected(self):
        """POST with null on a required object field must still return 400."""
        device_ct = CustomObjectsTestCase.get_device_object_type()
        uid = uuid.uuid4().hex[:8]
        cot = CustomObjectsTestCase.create_custom_object_type(name=f'NullReqTest-{uid}', slug=f'null-req-test-{uid}')
        CustomObjectsTestCase.create_custom_object_type_field(
            cot,
            name='name',
            type='text',
            primary=True,
            required=True,
        )
        CustomObjectsTestCase.create_custom_object_type_field(
            cot,
            name='req_device',
            type='object',
            related_object_type=device_ct,
            required=True,
        )
        model = cot.get_model()
        for action in ('add', 'view'):
            perm = ObjectPermission(name=f'null-req-{action}-{uid}', actions=[action])
            perm.save()
            perm.users.add(self.user)
            perm.object_types.add(ObjectType.objects.get_for_model(model))

        list_url = reverse(
            'plugins-api:netbox_custom_objects-api:customobject-list',
            kwargs={'custom_object_type': cot.slug},
        )
        response = self.client.post(
            list_url,
            {'name': 'req-null-post', 'req_device': None},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.data)
