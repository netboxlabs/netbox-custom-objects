from django.urls import reverse

from utilities.testing import APIViewTestCases, create_test_user
from rest_framework import status

from netbox_custom_objects.models import CustomObjectType
from .base import CustomObjectsTestCase
from core.models import ObjectType
from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Rack, Site
from users.models import ObjectPermission, Token
from virtualization.models import Cluster, ClusterType


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
        self.token = Token.objects.create(user=self.user)
        self.header = {'HTTP_AUTHORIZATION': f'Token {self.token.key}'}

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
