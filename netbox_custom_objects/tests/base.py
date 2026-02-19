# Test utilities for netbox_custom_objects plugin
from django.test import Client
from core.models import ObjectType
from extras.models import CustomFieldChoiceSet
from utilities.testing import create_test_user

from netbox_custom_objects.models import CustomObjectType, CustomObjectTypeField


class CustomObjectsTestCase:
    """
    Base test case for custom objects tests.
    """

    @classmethod
    def setUpTestData(cls):
        """Set up test data that should be created once for the entire test class."""
        pass

    def setUp(self):
        """Set up test data."""
        self.user = create_test_user('testuser')
        self.client = Client()
        self.client.force_login(self.user)

    def tearDown(self):
        """Clean up after each test."""
        # Clear the model cache to ensure test isolation
        # This prevents cached models with deleted fields from affecting other tests
        CustomObjectType.clear_model_cache()
        super().tearDown()

    @classmethod
    def create_custom_object_type(cls, **kwargs):
        """Helper method to create a custom object type."""
        defaults = {
            'name': 'TestObject',
            'description': 'A test custom object type',
            'verbose_name_plural': 'Test Objects',
            'slug': 'test-objects',
        }
        defaults.update(kwargs)
        return CustomObjectType.objects.create(**defaults)

    @classmethod
    def create_custom_object_type_field(cls, custom_object_type, **kwargs):
        """Helper method to create a custom object type field."""
        defaults = {
            'custom_object_type': custom_object_type,
            'name': 'test_field',
            'label': 'Test Field',
            'type': 'text',
        }
        defaults.update(kwargs)
        return CustomObjectTypeField.objects.create(**defaults)

    @classmethod
    def create_choice_set(cls, **kwargs):
        """Helper method to create a choice set."""
        defaults = {
            'name': 'Test Choice Set',
            'extra_choices': [
                ['choice1', 'Choice 1'],
                ['choice2', 'Choice 2'],
                ['choice3', 'Choice 3'],
            ],
        }
        defaults.update(kwargs)
        return CustomFieldChoiceSet.objects.create(**defaults)

    @classmethod
    def get_device_object_type(cls):
        """Get the device content type for object field testing."""
        return ObjectType.objects.get(app_label='dcim', model='device')

    @classmethod
    def get_site_object_type(cls):
        """Get the site content type for object field testing."""
        return ObjectType.objects.get(app_label='dcim', model='site')

    def create_simple_custom_object_type(self, **kwargs):
        """Create a simple custom object type with basic fields."""
        custom_object_type = CustomObjectsTestCase.create_custom_object_type(**kwargs)

        # Add a text field as primary
        CustomObjectsTestCase.create_custom_object_type_field(
            custom_object_type, name='name', label='Name', type='text', primary=True, required=True
        )

        # Add a description field
        CustomObjectsTestCase.create_custom_object_type_field(
            custom_object_type, name='description', label='Description', type='text', required=False
        )

        return custom_object_type

    @classmethod
    def create_complex_custom_object_type(cls, **kwargs):
        """Create a complex custom object type with various field types."""
        custom_object_type = CustomObjectsTestCase.create_custom_object_type(**kwargs)
        choice_set = CustomObjectsTestCase.create_choice_set()
        device_object_type = CustomObjectsTestCase.get_device_object_type()

        # Primary text field
        CustomObjectsTestCase.create_custom_object_type_field(
            custom_object_type, name='name', label='Name', type='text', primary=True, required=True
        )

        # Integer field
        CustomObjectsTestCase.create_custom_object_type_field(
            custom_object_type,
            name='count',
            label='Count',
            type='integer',
            validation_minimum=0,
            validation_maximum=100,
        )

        # Boolean field
        CustomObjectsTestCase.create_custom_object_type_field(
            custom_object_type, name='active', label='Active', type='boolean', default=True
        )

        # Select field
        CustomObjectsTestCase.create_custom_object_type_field(
            custom_object_type, name='status', label='Status', type='select', choice_set=choice_set
        )

        # Object field (device)
        CustomObjectsTestCase.create_custom_object_type_field(
            custom_object_type, name='device', label='Device', type='object', related_object_type=device_object_type
        )

        # Multi-Object field (devices)
        CustomObjectsTestCase.create_custom_object_type_field(
            custom_object_type,
            name='devices',
            label='Devices',
            type='multiobject',
            related_object_type=device_object_type,
        )

        return custom_object_type

    def create_self_referential_custom_object_type(self, **kwargs):
        """Create a custom object type that can reference itself."""
        custom_object_type = CustomObjectsTestCase.create_custom_object_type(**kwargs)

        # Primary text field
        CustomObjectsTestCase.create_custom_object_type_field(
            custom_object_type, name='name', label='Name', type='text', primary=True, required=True
        )

        return custom_object_type

    def create_multi_object_custom_object_type(self, **kwargs):
        """Create a custom object type with multi-object fields."""
        custom_object_type = CustomObjectsTestCase.create_custom_object_type(**kwargs)
        device_object_type = CustomObjectsTestCase.get_device_object_type()
        site_object_type = CustomObjectsTestCase.get_site_object_type()

        # Primary text field
        CustomObjectsTestCase.create_custom_object_type_field(
            custom_object_type, name='name', label='Name', type='text', primary=True, required=True
        )

        # Multi-object field (devices)
        CustomObjectsTestCase.create_custom_object_type_field(
            custom_object_type,
            name='devices',
            label='Devices',
            type='multiobject',
            related_object_type=device_object_type,
        )

        # Multi-object field (sites)
        CustomObjectsTestCase.create_custom_object_type_field(
            custom_object_type, name='sites', label='Sites', type='multiobject', related_object_type=site_object_type
        )

        return custom_object_type
