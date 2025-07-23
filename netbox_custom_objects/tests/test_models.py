from pprint import pprint
from datetime import date, datetime
from decimal import Decimal
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase
from django.urls import reverse
from extras.models import CustomFieldChoiceSet
from utilities.testing import create_test_user

from netbox_custom_objects.models import CustomObjectType, CustomObjectTypeField
from .base import CustomObjectsTestCase


class CustomObjectTypeTestCase(CustomObjectsTestCase, TestCase):
    """Test cases for CustomObjectType model."""

    def test_custom_object_type_creation(self):
        """Test creating a CustomObjectType."""
        custom_object_type = self.create_custom_object_type(
            name="TestObject",
            description="A test custom object type",
            verbose_name_plural="Test Objects"
        )
        
        self.assertEqual(custom_object_type.name, "TestObject")
        self.assertEqual(custom_object_type.description, "A test custom object type")
        self.assertEqual(custom_object_type.verbose_name_plural, "Test Objects")
        self.assertEqual(str(custom_object_type), "TestObject")

    def test_custom_object_type_unique_name_constraint(self):
        """Test that custom object type names must be unique (case-insensitive)."""
        self.create_custom_object_type(name="TestObject")
        
        # Should not allow duplicate name (case-insensitive)
        with self.assertRaises(Exception):
            self.create_custom_object_type(name="testobject")

    def test_custom_object_type_get_absolute_url(self):
        """Test get_absolute_url method."""
        custom_object_type = self.create_custom_object_type(name="TestObject")
        expected_url = reverse("plugins:netbox_custom_objects:customobjecttype", args=[custom_object_type.pk])
        self.assertEqual(custom_object_type.get_absolute_url(), expected_url)

    def test_custom_object_type_get_list_url(self):
        """Test get_list_url method."""
        custom_object_type = self.create_custom_object_type(name="TestObject")
        expected_url = reverse(
            "plugins:netbox_custom_objects:customobject_list",
            kwargs={"custom_object_type": custom_object_type.name.lower()}
        )
        self.assertEqual(custom_object_type.get_list_url(), expected_url)

    def test_custom_object_type_get_model_without_fields(self):
        """Test get_model method when no fields are defined."""
        custom_object_type = self.create_custom_object_type(name="TestObject")

        # Should raise an error when no primary field is defined
        with self.assertRaises(Exception):
            custom_object_type.get_model()

    def test_custom_object_type_get_model_with_primary_field(self):
        """Test get_model method with a primary field."""
        custom_object_type = self.create_custom_object_type(name="TestObject")
        
        # Add a primary field
        self.create_custom_object_type_field(
            custom_object_type,
            name="name",
            label="Name",
            type="text",
            primary=True,
            required=True
        )
        
        # Get the dynamic model
        model = custom_object_type.get_model()
        
        # Verify the model has the expected fields
        self.assertTrue(hasattr(model, 'name'))
        self.assertTrue(hasattr(model, 'get_absolute_url'))
        self.assertTrue(hasattr(model, '__str__'))

    def test_custom_object_type_get_model_with_multiple_fields(self):
        """Test get_model method with multiple fields of different types."""
        custom_object_type = self.create_custom_object_type(name="TestObject")
        
        # Add various field types
        self.create_custom_object_type_field(
            custom_object_type,
            name="name",
            label="Name",
            type="text",
            primary=True,
            required=True
        )
        
        self.create_custom_object_type_field(
            custom_object_type,
            name="count",
            label="Count",
            type="integer",
            validation_minimum=0,
            validation_maximum=100
        )
        
        self.create_custom_object_type_field(
            custom_object_type,
            name="active",
            label="Active",
            type="boolean",
            default=True
        )
        
        # Get the dynamic model
        model = custom_object_type.get_model()
        
        # Verify all fields exist
        self.assertTrue(hasattr(model, 'name'))
        self.assertTrue(hasattr(model, 'count'))
        self.assertTrue(hasattr(model, 'active'))

    def test_custom_object_type_save_creates_table(self):
        """Test that saving a custom object type creates the database table."""
        custom_object_type = self.create_custom_object_type(name="TestObject")
        
        # Add a primary field
        self.create_custom_object_type_field(
            custom_object_type,
            name="name",
            label="Name",
            type="text",
            primary=True,
            required=True
        )
        
        # Save to trigger table creation
        custom_object_type.save()
        
        # Check if the table exists
        with connection.cursor() as cursor:
            tables = connection.introspection.table_names(cursor)
            expected_table = f"custom_objects_{custom_object_type.id}"
            self.assertIn(expected_table, tables)

    def test_custom_object_type_delete_removes_table(self):
        """Test that deleting a custom object type removes the database table."""
        custom_object_type = self.create_custom_object_type(name="TestObject")
        
        # Add a primary field
        self.create_custom_object_type_field(
            custom_object_type,
            name="name",
            label="Name",
            type="text",
            primary=True,
            required=True
        )
        
        # Save to create table
        custom_object_type.save()
        
        # Get table name
        table_name = custom_object_type.get_database_table_name()
        
        # Delete the custom object type
        custom_object_type.delete()
        
        # Check if the table was removed
        with connection.cursor() as cursor:
            tables = connection.introspection.table_names(cursor)
            self.assertNotIn(table_name, tables)


class CustomObjectTypeFieldTestCase(CustomObjectsTestCase, TestCase):
    """Test cases for CustomObjectTypeField model."""

    def setUp(self):
        """Set up test data."""
        super().setUp()
        self.custom_object_type = self.create_custom_object_type(name="TestObject")

    def test_custom_object_type_field_creation(self):
        """Test creating a CustomObjectTypeField."""
        field = self.create_custom_object_type_field(
            self.custom_object_type,
            name="test_field",
            label="Test Field",
            type="text",
            description="A test field",
            required=True,
            unique=True
        )
        
        self.assertEqual(field.name, "test_field")
        self.assertEqual(field.label, "Test Field")
        self.assertEqual(field.type, "text")
        self.assertEqual(field.description, "A test field")
        self.assertTrue(field.required)
        self.assertTrue(field.unique)
        self.assertEqual(str(field), "Test Field")

    def test_custom_object_type_field_name_validation(self):
        """Test field name validation."""
        # Test invalid characters
        with self.assertRaises(ValidationError):
            field = CustomObjectTypeField(
                custom_object_type=self.custom_object_type,
                name="test-field",  # Invalid: contains hyphen
                type="text"
            )
            field.full_clean()
        
        # Test double underscores
        with self.assertRaises(ValidationError):
            field = CustomObjectTypeField(
                custom_object_type=self.custom_object_type,
                name="test__field",  # Invalid: contains double underscore
                type="text"
            )
            field.full_clean()

    def test_custom_object_type_field_unique_name_per_type(self):
        """Test that field names must be unique within a custom object type."""
        self.create_custom_object_type_field(
            self.custom_object_type,
            name="test_field",
            type="text"
        )
        
        # Should not allow duplicate field name within the same type
        with self.assertRaises(Exception):
            self.create_custom_object_type_field(
                self.custom_object_type,
                name="test_field",
                type="integer"
            )

    def test_custom_object_type_field_validation_regex_text_only(self):
        """Test that regex validation can only be set on text fields."""
        # Should work for text field
        field = CustomObjectTypeField(
            custom_object_type=self.custom_object_type,
            name="test_field",
            type="text",
            validation_regex="^[A-Z]+$"
        )
        field.full_clean()
        
        # Should fail for integer field
        with self.assertRaises(ValidationError):
            field = CustomObjectTypeField(
                custom_object_type=self.custom_object_type,
                name="test_field2",
                type="integer",
                validation_regex="^[A-Z]+$"
            )
            field.full_clean()

    def test_custom_object_type_field_boolean_unique_validation(self):
        """Test that boolean fields cannot be unique."""
        with self.assertRaises(ValidationError):
            field = CustomObjectTypeField(
                custom_object_type=self.custom_object_type,
                name="test_field",
                type="boolean",
                unique=True
            )
            field.full_clean()

    def test_custom_object_type_field_choice_set_validation(self):
        """Test choice set validation for select fields."""
        choice_set = self.create_choice_set()
        
        # Should require choice set for select field
        with self.assertRaises(ValidationError):
            field = CustomObjectTypeField(
                custom_object_type=self.custom_object_type,
                name="test_field",
                type="select"
            )
            field.full_clean()
        
        # Should not allow choice set for non-select field
        with self.assertRaises(ValidationError):
            field = CustomObjectTypeField(
                custom_object_type=self.custom_object_type,
                name="test_field",
                type="text",
                choice_set=choice_set
            )
            field.full_clean()

    def test_custom_object_type_field_object_type_validation(self):
        """Test object type validation for object/multiobject fields."""
        # Should require related_object_type for object field
        with self.assertRaises(ValidationError):
            field = CustomObjectTypeField(
                custom_object_type=self.custom_object_type,
                name="test_field",
                type="object"
            )
            field.full_clean()
        
        # Should not allow related_object_type for non-object field
        device_ct = self.get_device_content_type()
        with self.assertRaises(ValidationError):
            field = CustomObjectTypeField(
                custom_object_type=self.custom_object_type,
                name="test_field",
                type="text",
                related_object_type=device_ct
            )
            field.full_clean()

    def test_custom_object_type_field_get_absolute_url(self):
        """Test get_absolute_url method."""
        field = self.create_custom_object_type_field(
            self.custom_object_type,
            name="test_field",
            type="text"
        )
        expected_url = reverse("plugins:netbox_custom_objects:customobjecttypefield", args=[field.pk])
        self.assertEqual(field.get_absolute_url(), expected_url)

    def test_custom_object_type_field_validation_methods(self):
        """Test field validation methods."""
        field = self.create_custom_object_type_field(
            self.custom_object_type,
            name="test_field",
            type="integer",
            validation_minimum=0,
            validation_maximum=100
        )
        
        # Test valid value
        field.validate(50)
        
        # Test value below minimum
        with self.assertRaises(ValidationError):
            field.validate(-1)
        
        # Test value above maximum
        with self.assertRaises(ValidationError):
            field.validate(101)

    def test_custom_object_type_field_serialization(self):
        """Test field serialization and deserialization."""
        field = self.create_custom_object_type_field(
            self.custom_object_type,
            name="test_field",
            type="text"
        )
        
        test_value = "test value"
        serialized = field.serialize(test_value)
        deserialized = field.deserialize(serialized)
        
        self.assertEqual(deserialized, test_value)


class CustomObjectTestCase(CustomObjectsTestCase, TestCase):
    """Test cases for dynamic CustomObject instances."""

    @classmethod
    def setUpTestData(cls):
        """Set up test data that should be created once for the entire test class."""
        super().setUpTestData()
        cls.custom_object_type = cls.create_custom_object_type(name="TestObject")
        
        # Add a primary field
        cls.create_custom_object_type_field(
            cls.custom_object_type,
            name="name",
            label="Name",
            type="text",
            primary=True,
            required=True
        )
        
        # Add additional fields
        cls.create_custom_object_type_field(
            cls.custom_object_type,
            name="description",
            label="Description",
            type="text",
            required=False
        )
        
        cls.create_custom_object_type_field(
            cls.custom_object_type,
            name="count",
            label="Count",
            type="integer",
            validation_minimum=0,
            validation_maximum=100
        )

        cls.create_custom_object_type_field(
            cls.custom_object_type,
            name="price",
            label="Price",
            type="decimal",
            validation_minimum=0,
            validation_maximum=100
        )

        cls.create_custom_object_type_field(
            cls.custom_object_type,
            name="is_active",
            label="Is active",
            type="boolean",
        )

        cls.create_custom_object_type_field(
            cls.custom_object_type,
            name="created_on",
            label="Created on (date)",
            type="date",
        )

        cls.create_custom_object_type_field(
            cls.custom_object_type,
            name="created_at",
            label="Created at (datetime)",
            type="datetime",
        )

        cls.create_custom_object_type_field(
            cls.custom_object_type,
            name="url",
            label="URL",
            type="url",
        )

        cls.create_custom_object_type_field(
            cls.custom_object_type,
            name="data",
            label="JSON data",
            type="json",
        )

        choice_set = cls.create_choice_set()
        cls.create_custom_object_type_field(
            cls.custom_object_type,
            name="country",
            label="Single country",
            type="select",
            choice_set=choice_set,
        )

        cls.create_custom_object_type_field(
            cls.custom_object_type,
            name="countries",
            label="Countries",
            type="multiselect",
            choice_set=choice_set,
        )

        site_ct = cls.get_site_content_type()
        cls.create_custom_object_type_field(
            cls.custom_object_type,
            name="site",
            label="Single site",
            type="object",
            related_object_type=site_ct,
        )

        site_ct = cls.get_site_content_type()
        cls.create_custom_object_type_field(
            cls.custom_object_type,
            name="sites",
            label="Sites",
            type="multiobject",
            related_object_type=site_ct,
        )

        # Get the dynamic model
        cls.model = cls.custom_object_type.get_model()

    def setUp(self):
        """Set up test data that should be reset between tests."""
        super().setUp()
        # Any test-specific setup can go here

    def test_custom_object_creation(self):
        """Test creating a custom object instance."""
        instance = self.model.objects.create(
            name="Test Instance",
            description="A test instance",
            count=50
        )
        site_ct = self.get_site_content_type()
        site = site_ct.model_class().objects.create()
        instance.sites.add(site)
        
        self.assertEqual(instance.name, "Test Instance")
        self.assertEqual(instance.description, "A test instance")
        self.assertEqual(instance.count, 50)
        self.assertEqual(instance.sites.all().count(), 1)
        self.assertEqual(str(instance), "Test Instance")

    def test_custom_object_get_absolute_url(self):
        """Test get_absolute_url method for custom objects."""
        instance = self.model.objects.create(name="Test Instance")
        expected_url = reverse(
            "plugins:netbox_custom_objects:customobject",
            kwargs={
                "custom_object_type": self.custom_object_type.name.lower(),
                "pk": instance.pk
            }
        )
        self.assertEqual(instance.get_absolute_url(), expected_url)

    def test_custom_object_queryset_operations(self):
        """Test queryset operations on custom objects."""
        # Create multiple instances
        self.model.objects.create(name="Instance 1", count=10)
        self.model.objects.create(name="Instance 2", count=20)
        self.model.objects.create(name="Instance 3", count=30)
        
        # Test filtering
        filtered = self.model.objects.filter(count__gte=20)
        self.assertEqual(filtered.count(), 2)
        
        # Test ordering
        ordered = self.model.objects.order_by('count')
        self.assertEqual(ordered.first().name, "Instance 1")
        self.assertEqual(ordered.last().name, "Instance 3")

    def test_custom_object_update(self):
        """Test updating custom object instances."""
        instance = self.model.objects.create(name="Test Instance", count=10)
        
        # Update the instance
        instance.name = "Updated Instance"
        instance.count = 25
        instance.save()
        
        # Refresh from database
        instance.refresh_from_db()
        
        self.assertEqual(instance.name, "Updated Instance")
        self.assertEqual(instance.count, 25)

    def test_custom_object_delete(self):
        """Test deleting custom object instances."""
        instance = self.model.objects.create(name="Test Instance")
        
        # Delete the instance
        instance.delete()
        
        # Verify it's gone
        self.assertEqual(self.model.objects.count(), 0) 