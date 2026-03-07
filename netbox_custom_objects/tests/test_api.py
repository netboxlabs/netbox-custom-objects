from django.test import TestCase
from django.urls import reverse

from utilities.testing import APIViewTestCases, create_test_user
from rest_framework import status

from netbox_custom_objects.models import CustomObjectType
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


class CustomObjectTest(CustomObjectsTestCase, APIViewTestCases.APIViewTestCase):
    model = None  # Will be set in setUpTestData
    brief_fields = ['created', 'display', 'id', 'last_updated', 'tags', 'test_field', 'url']
    bulk_update_data = {
        'test_field': 'Updated test field',
    }

    def setUp(self):
        """Set up test data."""
        # Create a user
        self.user = create_test_user('testuser')

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

    def test_list_objects(self):
        # TODO: Needs filtering by pk to work
        ...

    def test_bulk_create_objects(self):
        ...

    def test_bulk_delete_objects(self):
        ...

    def test_bulk_update_objects(self):
        ...

    def test_delete_object(self):
        # TODO: ObjectChange causes failure
        ...

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

        data = {
            'test_field': 'Test 004',
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
        self.assertInstanceEqual(
            instance,
            self.create_data[0],
            exclude=self.validation_excluded_fields,
            api=True
        )

    # TODO: GraphQL
    def test_graphql_list_objects(self):
        ...

    def test_graphql_get_object(self):
        ...


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
            custom_object_type=self.cot,
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
            custom_object_type=self.cot,
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

    def test_response_shape(self):
        """Each result contains custom_object_type, field_name, and object keys."""
        self.model.objects.create(
            custom_object_type=self.cot,
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


class CustomObjectTypeAPITest(CustomObjectsTestCase):
    """
    Test CustomObjectType API endpoint validation.
    """

    def setUp(self):
        """Set up test data."""
        # Create a user
        self.user = create_test_user('testuser')

        # Create token for API access
        self.token = Token.objects.create(user=self.user)
        self.header = {'HTTP_AUTHORIZATION': f'Token {self.token.key}'}

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
