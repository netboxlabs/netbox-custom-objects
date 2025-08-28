import json

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db import connections
from django.test import Client, TransactionTestCase
from django.urls import reverse
from rest_framework import status

from dcim.models import Site
from users.models import Token
from utilities.testing import APITestCase, APIViewTestCases, create_test_user

from netbox_custom_objects.models import CustomObjectType, CustomObjectTypeField
from .base import CustomObjectsTestCase

# from netbox_branching.constants import COOKIE_NAME
# from netbox_branching.models import Branch


class CustomObjectsAPITestCase(TransactionTestCase):
    serialized_rollback = True

    def setUp(self):
        self.client = Client()
        user = get_user_model().objects.create_user(username='testuser')
        token = Token(user=user)
        token.save()
        self.header = {
            'HTTP_AUTHORIZATION': f'Token {token.key}',
            'HTTP_ACCEPT': 'application/json',
            'HTTP_CONTENT_TYPE': 'application/json',
        }

        ContentType.objects.get_for_model(CustomObjectType)

        # Create test custom object types
        self.custom_object_type1 = CustomObjectType.objects.create(
            name="TestObject1",
            description="First test custom object type",
            verbose_name_plural="Test Objects 1",
            slug="test-objects-1",
        )

        self.custom_object_type2 = CustomObjectType.objects.create(
            name="TestObject2",
            description="Second test custom object type",
            verbose_name_plural="Test Objects 2",
            slug="test-objects-2",
        )

        # # Create sites
        # Site.objects.create(name='Site 1', slug='site-1')
        # Site.objects.using(branch.connection_name).create(name='Site 2', slug='site-2')

    # def tearDown(self):
    #     # Manually tear down the dynamic connection created for the Branch to
    #     # ensure the test exits cleanly.
    #     branch = Branch.objects.first()
    #     connections[branch.connection_name].close()

    def get_results(self, response):
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        if 'results' not in data:
            raise ValueError("Response content does not contain API results")
        return data['results']

    def test_create_custom_object(self):
        model = self.custom_object_type1.get_model()


    # def test_without_branch(self):
    #     url = reverse('dcim-api:site-list')
    #     response = self.client.get(url, **self.header)
    #     results = self.get_results(response)
    #
    #     self.assertEqual(len(results), 1)
    #     self.assertEqual(results[0]['name'], 'Site 1')
    #
    # def test_with_branch_header(self):
    #     url = reverse('dcim-api:site-list')
    #     branch = Branch.objects.first()
    #     self.assertIsNotNone(branch, "Branch was not created")
    #
    #     # Regular API query
    #     response = self.client.get(url, **self.header)
    #     results = self.get_results(response)
    #     self.assertEqual(len(results), 1)
    #     self.assertEqual(results[0]['name'], 'Site 1')
    #
    #     # Branch-aware API query
    #     header = {
    #         **self.header,
    #         'HTTP_X_NETBOX_BRANCH': branch.schema_id,
    #     }
    #     response = self.client.get(url, **header)
    #     results = self.get_results(response)
    #     self.assertEqual(len(results), 1)
    #     self.assertEqual(results[0]['name'], 'Site 2')
    #
    # def test_with_branch_cookie(self):
    #     url = reverse('dcim-api:site-list')
    #     branch = Branch.objects.first()
    #     self.assertIsNotNone(branch, "Branch was not created")
    #
    #     # Regular API query
    #     response = self.client.get(url, **self.header)
    #     results = self.get_results(response)
    #     self.assertEqual(len(results), 1)
    #     self.assertEqual(results[0]['name'], 'Site 1')
    #
    #     # Branch-aware API query
    #     self.client.cookies.load({
    #         COOKIE_NAME: branch.schema_id,
    #     })
    #     response = self.client.get(url, **self.header)
    #     results = self.get_results(response)
    #     self.assertEqual(len(results), 1)
    #     self.assertEqual(results[0]['name'], 'Site 2')


class CustomObjectTest(CustomObjectsTestCase, APIViewTestCases.APIViewTestCase):
    model = None  # Will be set in setUpTestData
    brief_fields = ['id', 'test_field']
    bulk_update_data = {
        'test_field': 'Updated test field',
    }
    user_permissions = ('netbox_custom_objects.view_customobjecttype', 'netbox_custom_objects.view_customobject')

    # def setUp(self):
    #     super().setUp()

    def setUp(self):
        """Set up test data."""
        # Create a superuser to avoid permission issues
        self.user = create_test_user('testuser')
        self.client = Client()
        self.client.force_login(self.user)
        
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

    def test_get_object(self):
        """
        GET a single object as an authenticated user with permission to view the object.
        """
        self.assertGreaterEqual(self._get_queryset().count(), 2,
                                f"Test requires the creation of at least two {self.model} instances")
        instance1, instance2 = self._get_queryset()[:2]

        # Add object-level permission
        # obj_perm = ObjectPermission(
        #     name='Test permission',
        #     constraints={'pk': instance1.pk},
        #     actions=['view']
        # )
        # obj_perm.save()
        # obj_perm.users.add(self.user)
        # obj_perm.object_types.add(ObjectType.objects.get_for_model(self.model))

        # Try GET to permitted object
        url = self._get_detail_url(instance1)
        print('url:', url)
        self.assertHttpStatus(self.client.get(url, **self.header), status.HTTP_200_OK)

        # # Try GET to non-permitted object
        # url = self._get_detail_url(instance2)
        # self.assertHttpStatus(self.client.get(url, **self.header), status.HTTP_404_NOT_FOUND)
