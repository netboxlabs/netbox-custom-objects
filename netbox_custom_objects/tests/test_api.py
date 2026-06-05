"""
Tests for API code paths.
"""
from django.test import TestCase
from django.urls import reverse

from utilities.testing import create_test_user
from rest_framework import status
from rest_framework.test import APIClient

from netbox_custom_objects.models import CustomObjectType, CustomObjectTypeField
from .base import CustomObjectsTestCase
from core.models import ObjectType
from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Rack, Site
from users.models import ObjectPermission, Token
from virtualization.models import Cluster, ClusterType


def create_token(user):
    try:
        # NetBox >= 4.5
        from users.choices import TokenVersionChoices
        token = Token(version=TokenVersionChoices.V1, user=user)
        token.save()
        return token.token
    except ImportError:
        # NetBox < 4.5
        token = Token(user=user)
        token.save()
        return token.key


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
        response = self.client.post(
            self._get_list_url(), self.create_data[0], format='json', **self.header
        )
        self.assertHttpStatus(response, 403)

    def test_update_object_without_permission(self):
        """PATCH a single object without permission returns 403."""
        instance = self._get_queryset().first()
        response = self.client.patch(
            self._get_detail_url(instance), self.update_data, format='json', **self.header
        )
        self.assertHttpStatus(response, 403)

    def test_update_object(self):
        """PATCH a single object returns 200 and persists the changes."""
        self._add_permission('change', 'Update object perm')
        instance = self._get_queryset().first()
        response = self.client.patch(
            self._get_detail_url(instance), self.update_data, format='json', **self.header
        )
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


class CustomObjectTest(CustomObjectsTestCase, CustomObjectAPITestCaseMixin, TestCase):
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
                local_context_data={'A': 1}
            ),
            Device(
                device_type=device_types[0],
                role=roles[0],
                name='Device 2',
                site=sites[0],
                rack=racks[0],
                cluster=clusters[0],
                local_context_data={'B': 2}
            ),
            Device(
                device_type=device_types[0],
                role=roles[0],
                name='Device 3',
                site=sites[0],
                rack=racks[0],
                cluster=clusters[0],
                local_context_data={'C': 3}
            ),
        )
        Device.objects.bulk_create(devices)

        # Create test custom object types
        cls.custom_object_type1 = CustomObjectType.objects.create(
            name="TestObject1",
            description="First test custom object type",
            verbose_name_plural="Test Objects 1",
            slug="test-objects-1",
        )

        cls.custom_object_type2 = CustomObjectType.objects.create(
            name="TestObject2",
            description="Second test custom object type",
            verbose_name_plural="Test Objects 2",
            slug="test-objects-2",
        )

        cls.custom_object_type3 = cls.create_complex_custom_object_type(name="ComplexObject")

        cls.model = cls.custom_object_type1.get_model()
        cls.create_custom_object_type_field(cls.custom_object_type1)

        # Set the model for the test class
        CustomObjectTest.model = cls.model

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

    def test_bulk_create_objects(self):
        """Create multiple objects with required fields via individual POST requests.

        CustomObjectViewSet uses a standard DRF ModelViewSet which does not expose a
        bulk-create endpoint, so we exercise per-object creation to verify required
        field enforcement and response shape.
        """
        self._add_permission('add', 'Bulk create perm')

        initial_count = self._get_queryset().count()
        for data in self.create_data:
            response = self.client.post(
                self._get_list_url(), data, format='json', **self.header
            )
            self.assertHttpStatus(response, 201)

        self.assertEqual(self._get_queryset().count(), initial_count + len(self.create_data))

    def test_bulk_delete_objects(self):
        """Delete multiple objects via individual DELETE requests.

        CustomObjectViewSet uses a standard DRF ModelViewSet which does not expose a
        bulk-delete endpoint, so we exercise per-object deletion.
        """
        self._add_permission('delete', 'Bulk delete perm')

        initial_count = self._get_queryset().count()
        self.assertGreaterEqual(initial_count, 2, "Need at least 2 objects to test bulk delete.")
        instances = list(self._get_queryset()[:2])

        for instance in instances:
            response = self.client.delete(self._get_detail_url(instance), **self.header)
            self.assertHttpStatus(response, 204)

        self.assertEqual(self._get_queryset().count(), initial_count - 2)
        for instance in instances:
            self.assertFalse(
                self._get_queryset().filter(pk=instance.pk).exists(),
                f"Object {instance.pk} should have been deleted.",
            )

    def test_bulk_update_objects(self):
        """Partial-update (PATCH) multiple objects — required fields are not enforced.

        CustomObjectViewSet uses a standard DRF ModelViewSet which does not expose a
        bulk-update endpoint, so we exercise per-object PATCH to verify that required
        fields need not be re-supplied on each update.
        """
        self._add_permission('change', 'Bulk update perm')

        instances = list(self._get_queryset()[:2])
        updates = {
            instances[0].pk: 'Updated 001',
            instances[1].pk: 'Updated 002',
        }

        for instance in instances:
            response = self.client.patch(
                self._get_detail_url(instance),
                {'test_field': updates[instance.pk]},
                format='json',
                **self.header,
            )
            self.assertHttpStatus(response, 200)

        instances[0].refresh_from_db()
        instances[1].refresh_from_db()
        self.assertEqual(instances[0].test_field, 'Updated 001')
        self.assertEqual(instances[1].test_field, 'Updated 002')

    def test_delete_object(self):
        """DELETE a single object returns 204 and removes the record."""
        self._add_permission('delete', 'Delete perm')

        instance = self._get_queryset().first()
        url = self._get_detail_url(instance)

        response = self.client.delete(url, **self.header)

        self.assertHttpStatus(response, 204)
        self.assertFalse(
            self._get_queryset().filter(pk=instance.pk).exists(),
            "Deleted object should no longer exist in the database.",
        )

    def test_create_with_nested_serializers(self):
        """
        POST a single object with a multiobject field's values specified via a list of PKs.
        """
        model = self.custom_object_type3.get_model()

        # Set the model for the test class
        self.model = model

        # Add object-level permission
        obj_perm = ObjectPermission(
            name='Test permission',
            actions=['add']
        )
        obj_perm.save()
        obj_perm.users.add(self.user)
        obj_perm.object_types.add(ObjectType.objects.get_for_model(self.model))

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


class LinkedObjectsAPITest(CustomObjectsTestCase, TestCase):
    """
    Tests for the GET /api/plugins/custom-objects/linked-objects/ endpoint.
    """

    def setUp(self):
        self.user = create_test_user('linkedobjectstestuser')
        token_key = create_token(self.user)
        self.header = {'HTTP_AUTHORIZATION': f'Token {token_key}'}

        # Build a custom object type with both an FK and a M2M device field
        self.cot = CustomObjectsTestCase.create_complex_custom_object_type(
            name='LinkedTest',
            slug='linked-test',
        )
        self.model = self.cot.get_model()

        # Create a device to link against
        manufacturer = Manufacturer.objects.create(
            name='LO Manufacturer', slug='lo-manufacturer'
        )
        device_type = DeviceType.objects.create(
            manufacturer=manufacturer, model='LO Type', slug='lo-type'
        )
        role = DeviceRole.objects.create(
            name='LO Role', slug='lo-role', color='ffffff'
        )
        site = Site.objects.create(name='LO Site', slug='lo-site')
        self.device = Device.objects.create(
            device_type=device_type, role=role, name='LO Device', site=site
        )

    def tearDown(self):
        CustomObjectType.clear_model_cache()
        super().tearDown()

    def _url(self, **params):
        base = reverse('plugins-api:netbox_custom_objects-api:linked-objects')
        if params:
            base += '?' + '&'.join(f'{k}={v}' for k, v in params.items())
        return base

    def test_fk_field_linked_object_appears(self):
        """An object linked via a FK field is returned in the results."""
        linked = self.model.objects.create(
            name='linked-fk',
            device=self.device,
        )
        url = self._url(object_type='dcim.device', object_id=self.device.pk)
        response = self.client.get(url, **self.header)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data['results']
        fk_results = [r for r in results if r['field_name'] == 'device']
        self.assertTrue(
            any(r['object']['id'] == linked.pk for r in fk_results),
            "Linked FK object not found in results"
        )

    def test_m2m_field_linked_object_appears(self):
        """An object linked via a M2M field is returned in the results."""
        linked = self.model.objects.create(
            name='linked-m2m',
        )
        # Attach the device via the M2M field
        linked.devices.add(self.device)

        url = self._url(object_type='dcim.device', object_id=self.device.pk)
        response = self.client.get(url, **self.header)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data['results']
        m2m_results = [r for r in results if r['field_name'] == 'devices']
        self.assertTrue(
            any(r['object']['id'] == linked.pk for r in m2m_results),
            "Linked M2M object not found in results"
        )

    def test_no_linked_objects_returns_empty(self):
        """An object with no linked custom objects returns count=0 and empty results."""
        url = self._url(object_type='dcim.device', object_id=self.device.pk)
        response = self.client.get(url, **self.header)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 0)
        self.assertEqual(response.data['results'], [])

    def test_missing_params_returns_400(self):
        """Omitting required query params returns 400."""
        url = reverse('plugins-api:netbox_custom_objects-api:linked-objects')
        response = self.client.get(url, **self.header)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_invalid_object_type_returns_400(self):
        """A non-existent object_type returns 400."""
        url = self._url(object_type='nonexistent.model', object_id=1)
        response = self.client.get(url, **self.header)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_nonexistent_object_id_returns_404(self):
        """A valid object_type but non-existent object_id returns 404."""
        url = self._url(object_type='dcim.device', object_id=999999)
        response = self.client.get(url, **self.header)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_polymorphic_gfk_field_linked_object_appears(self):
        """An object linked via a polymorphic GFK object field appears in the results."""
        cot = CustomObjectsTestCase.create_custom_object_type(
            name='PolyLOTest', slug='poly-lo-test'
        )
        CustomObjectsTestCase.create_custom_object_type_field(
            cot, name='name', label='Name', type='text', primary=True, required=True
        )
        CustomObjectsTestCase.create_polymorphic_field(
            cot,
            related_object_types=[
                CustomObjectsTestCase.get_device_object_type(),
                CustomObjectsTestCase.get_site_object_type(),
            ],
            name='target',
            label='Target',
            type='object',
        )
        model = cot.get_model()
        linked = model.objects.create(name='poly-gfk-linked', target=self.device)

        url = self._url(object_type='dcim.device', object_id=self.device.pk)
        response = self.client.get(url, **self.header)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data['results']
        self.assertTrue(
            any(r['field_name'] == 'target' and r['object']['id'] == linked.pk for r in results),
            "Polymorphic GFK-linked object not found in linked-objects results"
        )

    def test_polymorphic_m2m_field_linked_object_appears(self):
        """An object linked via a polymorphic multiobject field appears in the results."""
        cot = CustomObjectsTestCase.create_custom_object_type(
            name='PolyM2MLOTest', slug='poly-m2m-lo-test'
        )
        CustomObjectsTestCase.create_custom_object_type_field(
            cot, name='name', label='Name', type='text', primary=True, required=True
        )
        CustomObjectsTestCase.create_polymorphic_field(
            cot,
            related_object_types=[
                CustomObjectsTestCase.get_device_object_type(),
                CustomObjectsTestCase.get_site_object_type(),
            ],
            name='targets',
            label='Targets',
            type='multiobject',
        )
        model = cot.get_model()
        linked = model.objects.create(name='poly-m2m-linked')
        linked.targets.add(self.device)

        url = self._url(object_type='dcim.device', object_id=self.device.pk)
        response = self.client.get(url, **self.header)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data['results']
        self.assertTrue(
            any(r['field_name'] == 'targets' and r['object']['id'] == linked.pk for r in results),
            "Polymorphic M2M-linked object not found in linked-objects results"
        )

    def test_response_shape(self):
        """Each result contains custom_object_type, field_name, and object keys."""
        self.model.objects.create(
            name='shape-test',
            device=self.device,
        )
        url = self._url(object_type='dcim.device', object_id=self.device.pk)
        response = self.client.get(url, **self.header)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        result = response.data['results'][0]
        self.assertIn('custom_object_type', result)
        self.assertIn('field_name', result)
        self.assertIn('object', result)


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


class SerializerFieldCoverageTest(CustomObjectsTestCase, TestCase):
    """
    Verify that the dynamic serializer produced by get_serializer_class() exposes
    the expected fields in API responses.
    """

    def setUp(self):
        self.user = create_test_user('serluser')
        token_key = create_token(self.user)
        self.header = {'HTTP_AUTHORIZATION': f'Token {token_key}'}

        self.cot = CustomObjectsTestCase.create_custom_object_type(
            name='SerializerTest', slug='serializer-test'
        )
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
        for field in ('id', 'url', 'display', 'created', 'last_updated', 'tags'):
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
        perm_a.object_types.add(
            ObjectType.objects.get_for_model(self.model_with_primary)
        )

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
        perm_b.object_types.add(
            ObjectType.objects.get_for_model(self.model_no_primary)
        )

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
        perm_c.object_types.add(
            ObjectType.objects.get_for_model(self.model_multi_ctx)
        )

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
        cot = CustomObjectsTestCase.create_custom_object_type(
            name='ctxapinoctx', slug='ctx-api-no-ctx'
        )
        CustomObjectsTestCase.create_custom_object_type_field(
            cot, name='name', type='text', primary=True
        )
        model = cot.get_model()
        cls = get_serializer_class(model)
        self.assertNotIn('_context', cls.Meta.fields)
        self.assertNotIn('_context', cls.Meta.brief_fields)

    # --- Primary field present ---

    def test_display_equals_primary_field_value(self):
        """display must be the primary field value, not the fallback."""
        instance = self.model_with_primary.objects.create(name='Route-A', owner='Alice')
        response = self.client.get(
            self._detail_url(self.cot_with_primary, instance)
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['display'], 'Route-A')

    def test_context_display_value_with_primary_field(self):
        """_context.display must equal the context field value when primary is set."""
        instance = self.model_with_primary.objects.create(name='Route-A', owner='Alice')
        response = self.client.get(
            self._detail_url(self.cot_with_primary, instance)
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNotNone(response.data['_context'])
        self.assertEqual(response.data['_context']['display'], 'Alice')

    def test_context_null_when_context_field_has_no_value(self):
        """_context must be null when the context field carries no value."""
        instance = self.model_with_primary.objects.create(name='Route-B')
        response = self.client.get(
            self._detail_url(self.cot_with_primary, instance)
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data['_context'])

    # --- No primary field (fallback display) ---

    def test_display_uses_fallback_when_no_primary_field(self):
        """display must use the fallback format when no primary field is configured."""
        instance = self.model_no_primary.objects.create(owner='Bob')
        expected = f"{self.cot_no_primary.display_name} {instance.id}"
        response = self.client.get(
            self._detail_url(self.cot_no_primary, instance)
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['display'], expected)

    def test_context_display_value_with_fallback_display(self):
        """_context.display must work correctly even when display uses the fallback name."""
        instance = self.model_no_primary.objects.create(owner='Bob')
        response = self.client.get(
            self._detail_url(self.cot_no_primary, instance)
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNotNone(response.data['_context'])
        self.assertEqual(response.data['_context']['display'], 'Bob')

    # --- Multiple context fields ---

    def test_multiple_context_fields_joined_in_display(self):
        """_context.display must join all context field values with ', '."""
        instance = self.model_multi_ctx.objects.create(
            name='Route-C', owner='Carol', region='EU'
        )
        response = self.client.get(
            self._detail_url(self.cot_multi_ctx, instance)
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNotNone(response.data['_context'])
        self.assertEqual(response.data['_context']['display'], 'Carol, EU')

    def test_multiple_context_fields_omits_empty_values(self):
        """_context.display must only include context fields that have a value."""
        instance = self.model_multi_ctx.objects.create(name='Route-D', owner='Dave')
        # region (second context field) is not set
        response = self.client.get(
            self._detail_url(self.cot_multi_ctx, instance)
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNotNone(response.data['_context'])
        self.assertEqual(response.data['_context']['display'], 'Dave')


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
        import json
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


class CrossCOTMultiObjectAPITest(CustomObjectsTestCase, TestCase):
    """
    Tests for API PATCH/PUT behaviour when a CO has a cross-COT multiobject
    (non-polymorphic M2M) field pointing to another COT.  Covers issue #443.
    """

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        token_key = create_token(self.user)
        self.header = {'HTTP_AUTHORIZATION': f'Token {token_key}'}

        # COT_SOURCE has a multiobject field → COT_TARGET
        self.cot_target = CustomObjectsTestCase.create_custom_object_type(
            name='CrossTarget', slug='cross-target'
        )
        CustomObjectsTestCase.create_custom_object_type_field(
            self.cot_target, name='name', label='Name', type='text',
            primary=True, required=True,
        )

        self.cot_source = CustomObjectsTestCase.create_custom_object_type(
            name='CrossSource', slug='cross-source'
        )
        CustomObjectsTestCase.create_custom_object_type_field(
            self.cot_source, name='name', label='Name', type='text',
            primary=True, required=True,
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
        """#443 – PATCH with a list of target PKs must update the M2M field."""
        self._add_perm('change', self.model_source)

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
        """#443 – PATCH with an empty list must clear the M2M field."""
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
        """#443 – PATCH a scalar field must not disturb existing M2M values."""
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
        """#443 – PUT must update scalar fields (issue title covers both PATCH and PUT)."""
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
        """#443 – GET must return the M2M field as a nested list."""
        self._add_perm('view', self.model_source)

        response = self.client.get(self._detail_url(self.obj_source), **self.header)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('refs', response.data, 'Response must include the refs M2M field.')
        ref_ids = [r['id'] for r in response.data['refs']]
        self.assertIn(self.obj_target1.pk, ref_ids)
