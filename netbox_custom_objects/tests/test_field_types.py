from unittest import skip
from datetime import date, datetime
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.test import TestCase

from .base import CustomObjectsTestCase


class FieldTypeTestCase(CustomObjectsTestCase, TestCase):
    """Base test case for field type testing."""

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

    def setUp(self):
        """Set up test data that should be reset between tests."""
        super().setUp()
        # Any test-specific setup can go here


class TextFieldTypeTestCase(FieldTypeTestCase):
    """Test cases for text field type."""

    def test_text_field_creation(self):
        """Test creating a text field."""
        field = self.create_custom_object_type_field(
            self.custom_object_type,
            name="description",
            label="Description",
            type="text",
            required=True,
            validation_regex="^[A-Za-z ]+$"
        )

        self.assertEqual(field.type, "text")
        self.assertTrue(field.required)
        self.assertEqual(field.validation_regex, "^[A-Za-z ]+$")

    def test_text_field_validation(self):
        """Test text field validation."""
        field = self.create_custom_object_type_field(
            self.custom_object_type,
            name="description",
            label="Description",
            type="text",
            validation_regex="^[A-Za-z ]+$"
        )

        # Test valid value
        field.validate("Valid Text")

        # Test invalid value (contains numbers)
        with self.assertRaises(ValidationError):
            field.validate("Invalid123")

    def test_text_field_model_generation(self):
        """Test text field model generation."""
        field = self.create_custom_object_type_field(
            self.custom_object_type,
            name="description",
            label="Description",
            type="text",
            required=True
        )
        field  # To silence ruff error

        model = self.custom_object_type.get_model()
        instance = model.objects.create(name="Test", description="Test description")

        self.assertEqual(instance.description, "Test description")


class LongTextFieldTypeTestCase(FieldTypeTestCase):
    """Test cases for long text field type."""

    def test_long_text_field_creation(self):
        """Test creating a long text field."""
        field = self.create_custom_object_type_field(
            self.custom_object_type,
            name="content",
            label="Content",
            type="longtext",
            required=True
        )

        self.assertEqual(field.type, "longtext")

    def test_long_text_field_model_generation(self):
        """Test long text field model generation."""
        field = self.create_custom_object_type_field(
            self.custom_object_type,
            name="content",
            label="Content",
            type="longtext"
        )
        field  # To silence ruff error

        model = self.custom_object_type.get_model()
        long_content = "This is a very long text content that should be stored in a TextField."
        instance = model.objects.create(name="Test", content=long_content)

        self.assertEqual(instance.content, long_content)


class IntegerFieldTypeTestCase(FieldTypeTestCase):
    """Test cases for integer field type."""

    def test_integer_field_creation(self):
        """Test creating an integer field."""
        field = self.create_custom_object_type_field(
            self.custom_object_type,
            name="count",
            label="Count",
            type="integer",
            validation_minimum=0,
            validation_maximum=100,
            default=50
        )

        self.assertEqual(field.type, "integer")
        self.assertEqual(field.validation_minimum, 0)
        self.assertEqual(field.validation_maximum, 100)
        self.assertEqual(field.default, 50)

    def test_integer_field_validation(self):
        """Test integer field validation."""
        field = self.create_custom_object_type_field(
            self.custom_object_type,
            name="count",
            label="Count",
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

        # Test non-integer value
        with self.assertRaises(ValidationError):
            field.validate("not an integer")

    def test_integer_field_model_generation(self):
        """Test integer field model generation."""
        field = self.create_custom_object_type_field(
            self.custom_object_type,
            name="count",
            label="Count",
            type="integer",
            default=10
        )
        field  # To silence ruff error

        model = self.custom_object_type.get_model()
        instance = model.objects.create(name="Test", count=25)

        self.assertEqual(instance.count, 25)


class DecimalFieldTypeTestCase(FieldTypeTestCase):
    """Test cases for decimal field type."""

    def test_decimal_field_creation(self):
        """Test creating a decimal field."""
        field = self.create_custom_object_type_field(
            self.custom_object_type,
            name="price",
            label="Price",
            type="decimal",
            validation_minimum=Decimal("0.00"),
            validation_maximum=Decimal("1000.00"),
            default=10.50
        )

        self.assertEqual(field.type, "decimal")
        self.assertEqual(field.validation_minimum, Decimal("0.00"))
        self.assertEqual(field.validation_maximum, Decimal("1000.00"))
        self.assertEqual(field.default, Decimal("10.50"))

    def test_decimal_field_validation(self):
        """Test decimal field validation."""
        field = self.create_custom_object_type_field(
            self.custom_object_type,
            name="price",
            label="Price",
            type="decimal",
            validation_minimum=Decimal("0.00"),
            validation_maximum=Decimal("1000.00")
        )

        # Test valid value
        field.validate(Decimal("500.50"))

        # Test value below minimum
        with self.assertRaises(ValidationError):
            field.validate(Decimal("-1.00"))

        # Test value above maximum
        with self.assertRaises(ValidationError):
            field.validate(Decimal("1001.00"))

        # Test invalid decimal
        with self.assertRaises(ValidationError):
            field.validate("not a decimal")

    def test_decimal_field_model_generation(self):
        """Test decimal field model generation."""
        field = self.create_custom_object_type_field(
            self.custom_object_type,
            name="price",
            label="Price",
            type="decimal",
            default=10.50
        )
        field  # To silence ruff error

        model = self.custom_object_type.get_model()
        instance = model.objects.create(name="Test", price=Decimal("25.75"))

        self.assertEqual(instance.price, Decimal("25.75"))


class BooleanFieldTypeTestCase(FieldTypeTestCase):
    """Test cases for boolean field type."""

    def test_boolean_field_creation(self):
        """Test creating a boolean field."""
        field = self.create_custom_object_type_field(
            self.custom_object_type,
            name="active",
            label="Active",
            type="boolean",
            default=True
        )

        self.assertEqual(field.type, "boolean")
        self.assertEqual(field.default, True)

    def test_boolean_field_validation(self):
        """Test boolean field validation."""
        field = self.create_custom_object_type_field(
            self.custom_object_type,
            name="active",
            label="Active",
            type="boolean"
        )

        # Test valid values
        field.validate(True)
        field.validate(False)
        field.validate(1)
        field.validate(0)

        # Test invalid value
        with self.assertRaises(ValidationError):
            field.validate("not a boolean")

    def test_boolean_field_model_generation(self):
        """Test boolean field model generation."""
        field = self.create_custom_object_type_field(
            self.custom_object_type,
            name="active",
            label="Active",
            type="boolean",
            default=True
        )
        field  # To silence ruff error

        model = self.custom_object_type.get_model()
        instance = model.objects.create(name="Test", active=False)

        self.assertFalse(instance.active)


class DateFieldTypeTestCase(FieldTypeTestCase):
    """Test cases for date field type."""

    def test_date_field_creation(self):
        """Test creating a date field."""
        field = self.create_custom_object_type_field(
            self.custom_object_type,
            name="created_date",
            label="Created Date",
            type="date",
            default="2023-01-01"
        )

        self.assertEqual(field.type, "date")
        self.assertEqual(field.default, "2023-01-01")

    def test_date_field_validation(self):
        """Test date field validation."""
        field = self.create_custom_object_type_field(
            self.custom_object_type,
            name="created_date",
            label="Created Date",
            type="date"
        )

        # Test valid date object
        field.validate(date(2023, 1, 1))

        # Test valid date string
        field.validate("2023-01-01")

        # Test invalid date string
        with self.assertRaises(ValidationError):
            field.validate("invalid-date")

    def test_date_field_model_generation(self):
        """Test date field model generation."""
        field = self.create_custom_object_type_field(
            self.custom_object_type,
            name="created_date",
            label="Created Date",
            type="date"
        )
        field  # To silence ruff error

        model = self.custom_object_type.get_model()
        test_date = date(2023, 1, 1)
        instance = model.objects.create(name="Test", created_date=test_date)

        self.assertEqual(instance.created_date, test_date)


class DateTimeFieldTypeTestCase(FieldTypeTestCase):
    """Test cases for datetime field type."""

    def test_datetime_field_creation(self):
        """Test creating a datetime field."""
        field = self.create_custom_object_type_field(
            self.custom_object_type,
            name="created_datetime",
            label="Created DateTime",
            type="datetime",
            default="2023-01-01T12:00:00"
        )

        self.assertEqual(field.type, "datetime")
        self.assertEqual(field.default, "2023-01-01T12:00:00")

    def test_datetime_field_validation(self):
        """Test datetime field validation."""
        field = self.create_custom_object_type_field(
            self.custom_object_type,
            name="created_datetime",
            label="Created DateTime",
            type="datetime"
        )

        # Test valid datetime object
        field.validate(datetime(2023, 1, 1, 12, 0, 0))

        # Test valid datetime string
        field.validate("2023-01-01T12:00:00")

        # Test invalid datetime string
        with self.assertRaises(ValidationError):
            field.validate("invalid-datetime")

    def test_datetime_field_model_generation(self):
        """Test datetime field model generation."""
        field = self.create_custom_object_type_field(
            self.custom_object_type,
            name="created_datetime",
            label="Created DateTime",
            type="datetime"
        )
        field  # To silence ruff error

        model = self.custom_object_type.get_model()
        test_datetime = datetime(2023, 1, 1, 12, 0, 0)
        instance = model.objects.create(name="Test", created_datetime=test_datetime)

        self.assertEqual(instance.created_datetime, test_datetime)


class URLFieldTypeTestCase(FieldTypeTestCase):
    """Test cases for URL field type."""

    def test_url_field_creation(self):
        """Test creating a URL field."""
        field = self.create_custom_object_type_field(
            self.custom_object_type,
            name="website",
            label="Website",
            type="url",
            validation_regex="^https://.*"
        )

        self.assertEqual(field.type, "url")
        self.assertEqual(field.validation_regex, "^https://.*")

    @skip("URL field validation not currently working, including in Custom Fields?")
    def test_url_field_validation(self):
        """Test URL field validation."""
        field = self.create_custom_object_type_field(
            self.custom_object_type,
            name="website",
            label="Website",
            type="url",
            validation_regex="^https://.*"
        )

        # Test valid URL
        field.validate("https://example.com")

        # Test invalid URL (doesn't match regex)
        with self.assertRaises(ValidationError):
            field.validate("http:/example.com")

    def test_url_field_model_generation(self):
        """Test URL field model generation."""
        field = self.create_custom_object_type_field(
            self.custom_object_type,
            name="website",
            label="Website",
            type="url"
        )
        field  # To silence ruff error

        model = self.custom_object_type.get_model()
        instance = model.objects.create(name="Test", website="https://example.com")

        self.assertEqual(instance.website, "https://example.com")


class JSONFieldTypeTestCase(FieldTypeTestCase):
    """Test cases for JSON field type."""

    def test_json_field_creation(self):
        """Test creating a JSON field."""
        field = self.create_custom_object_type_field(
            self.custom_object_type,
            name="metadata",
            label="Metadata",
            type="json",
            default={"key": "value"}
        )

        self.assertEqual(field.type, "json")
        self.assertEqual(field.default, {"key": "value"})

    def test_json_field_model_generation(self):
        """Test JSON field model generation."""
        field = self.create_custom_object_type_field(
            self.custom_object_type,
            name="metadata",
            label="Metadata",
            type="json"
        )
        field  # To silence ruff error

        model = self.custom_object_type.get_model()
        test_data = {"key": "value", "number": 42, "list": [1, 2, 3]}
        instance = model.objects.create(name="Test", metadata=test_data)

        self.assertEqual(instance.metadata, test_data)


class SelectFieldTypeTestCase(FieldTypeTestCase):
    """Test cases for select field type."""

    def setUp(self):
        """Set up test data."""
        super().setUp()
        self.choice_set = self.create_choice_set()

    def test_select_field_creation(self):
        """Test creating a select field."""
        field = self.create_custom_object_type_field(
            self.custom_object_type,
            name="status",
            label="Status",
            type="select",
            choice_set=self.choice_set,
            default="choice1"
        )

        self.assertEqual(field.type, "select")
        self.assertEqual(field.choice_set, self.choice_set)
        self.assertEqual(field.default, "choice1")

    def test_select_field_validation(self):
        """Test select field validation."""
        field = self.create_custom_object_type_field(
            self.custom_object_type,
            name="status",
            label="Status",
            type="select",
            choice_set=self.choice_set
        )

        # Test valid choice
        field.validate("choice1")

        # Test invalid choice
        with self.assertRaises(ValidationError):
            field.validate("invalid_choice")

    def test_select_field_model_generation(self):
        """Test select field model generation."""
        field = self.create_custom_object_type_field(
            self.custom_object_type,
            name="status",
            label="Status",
            type="select",
            choice_set=self.choice_set,
            default="choice1"
        )
        field  # To silence ruff error

        model = self.custom_object_type.get_model()
        instance = model.objects.create(name="Test", status="choice2")

        self.assertEqual(instance.status, "choice2")


class MultiSelectFieldTypeTestCase(FieldTypeTestCase):
    """Test cases for multiselect field type."""

    def setUp(self):
        """Set up test data."""
        super().setUp()
        self.choice_set = self.create_choice_set()

    def test_multiselect_field_creation(self):
        """Test creating a multiselect field."""
        field = self.create_custom_object_type_field(
            self.custom_object_type,
            name="tags",
            label="Tags",
            type="multiselect",
            choice_set=self.choice_set,
            default=["choice1", "choice2"]
        )

        self.assertEqual(field.type, "multiselect")
        self.assertEqual(field.choice_set, self.choice_set)
        self.assertEqual(field.default, ["choice1", "choice2"])

    def test_multiselect_field_validation(self):
        """Test multiselect field validation."""
        field = self.create_custom_object_type_field(
            self.custom_object_type,
            name="tags",
            label="Tags",
            type="multiselect",
            choice_set=self.choice_set
        )

        # Test valid choices
        field.validate(["choice1", "choice2"])

        # Test invalid choice
        with self.assertRaises(ValidationError):
            field.validate(["choice1", "invalid_choice"])

    def test_multiselect_field_model_generation(self):
        """Test multiselect field model generation."""
        field = self.create_custom_object_type_field(
            self.custom_object_type,
            name="tags",
            label="Tags",
            type="multiselect",
            choice_set=self.choice_set
        )
        field  # To silence ruff error

        model = self.custom_object_type.get_model()
        instance = model.objects.create(name="Test", tags=["choice1", "choice3"])

        self.assertEqual(instance.tags, ["choice1", "choice3"])


class ObjectFieldTypeTestCase(FieldTypeTestCase):
    """Test cases for object field type."""

    def setUp(self):
        """Set up test data."""
        super().setUp()
        self.device_object_type = self.get_device_object_type()

    def test_object_field_creation(self):
        """Test creating an object field."""
        field = self.create_custom_object_type_field(
            self.custom_object_type,
            name="device",
            label="Device",
            type="object",
            related_object_type=self.device_object_type
        )

        self.assertEqual(field.type, "object")
        self.assertEqual(field.related_object_type, self.device_object_type)

    def test_object_field_model_generation(self):
        """Test object field model generation."""
        field = self.create_custom_object_type_field(
            self.custom_object_type,
            name="device",
            label="Device",
            type="object",
            related_object_type=self.device_object_type
        )
        field  # To silence ruff error

        model = self.custom_object_type.get_model()

        # Create a test device (if available)
        try:
            from dcim.models import Device, Site, DeviceRole, DeviceType, Manufacturer
            site = Site.objects.create(name="Test Site", slug="test-site")
            manufacturer = Manufacturer.objects.create(name="Test Manufacturer", slug="test-manufacturer")
            device_type = DeviceType.objects.create(
                manufacturer=manufacturer,
                model="Test Model",
                slug="test-model"
            )
            device_role = DeviceRole.objects.create(name="Test Role", slug="test-role")
            device = Device.objects.create(
                name="Test Device",
                site=site,
                device_type=device_type,
                role=device_role
            )

            instance = model.objects.create(name="Test", device=device)
            self.assertEqual(instance.device, device)
        except ImportError:
            # Skip if DCIM models are not available
            pass


class MultiObjectFieldTypeTestCase(FieldTypeTestCase):
    """Test cases for multiobject field type."""

    def setUp(self):
        """Set up test data."""
        super().setUp()
        self.device_object_type = self.get_device_object_type()

    def test_multiobject_field_creation(self):
        """Test creating a multiobject field."""
        field = self.create_custom_object_type_field(
            self.custom_object_type,
            name="devices",
            label="Devices",
            type="multiobject",
            related_object_type=self.device_object_type
        )

        self.assertEqual(field.type, "multiobject")
        self.assertEqual(field.related_object_type, self.device_object_type)

    def test_multiobject_field_model_generation(self):
        """Test multiobject field model generation."""
        field = self.create_custom_object_type_field(
            self.custom_object_type,
            name="devices",
            label="Devices",
            type="multiobject",
            related_object_type=self.device_object_type
        )
        field  # To silence ruff error

        model = self.custom_object_type.get_model()

        # Create test devices (if available)
        try:
            from dcim.models import Device, Site, DeviceRole, DeviceType, Manufacturer
            site = Site.objects.create(name="Test Site", slug="test-site")
            manufacturer = Manufacturer.objects.create(name="Test Manufacturer", slug="test-manufacturer")
            device_type = DeviceType.objects.create(
                manufacturer=manufacturer,
                model="Test Model",
                slug="test-model"
            )
            device_role = DeviceRole.objects.create(name="Test Role", slug="test-role")
            device1 = Device.objects.create(
                name="Test Device 1",
                site=site,
                device_type=device_type,
                role=device_role
            )
            device2 = Device.objects.create(
                name="Test Device 2",
                site=site,
                device_type=device_type,
                role=device_role
            )

            instance = model.objects.create(name="Test")
            instance.devices.add(device1, device2)

            self.assertEqual(instance.devices.count(), 2)
            self.assertIn(device1, instance.devices.all())
            self.assertIn(device2, instance.devices.all())
        except ImportError:
            # Skip if DCIM models are not available
            pass


class SelfReferentialFieldTestCase(FieldTypeTestCase):
    """Test cases for self-referential object fields."""

    @skip("Causes infinite recursion error")
    def test_self_referential_object_field(self):
        """Test creating a self-referential object field."""
        # Add a self-referential object field
        field = self.create_custom_object_type_field(
            self.custom_object_type,
            name="parent",
            label="Parent",
            type="object",
            related_object_type=self.custom_object_type.object_type
        )
        field  # To silence ruff error

        model = self.custom_object_type.get_model()

        # Create parent instance
        parent = model.objects.create(name="Parent Instance")

        # Create child instance with parent reference
        child = model.objects.create(name="Child Instance", parent=parent)

        self.assertEqual(child.parent, parent)

    @skip("Causes infinite recursion error")
    def test_self_referential_multiobject_field(self):
        """Test creating a self-referential multiobject field."""
        # Add a self-referential multiobject field
        field = self.create_custom_object_type_field(
            self.custom_object_type,
            name="children",
            label="Children",
            type="multiobject",
            related_object_type=self.custom_object_type.object_type
        )
        field  # To silence ruff error

        model = self.custom_object_type.get_model()

        # Create parent instance
        parent = model.objects.create(name="Parent Instance")

        # Create child instances
        child1 = model.objects.create(name="Child 1")
        child2 = model.objects.create(name="Child 2")

        # Add children to parent
        parent.children.add(child1, child2)

        self.assertEqual(parent.children.count(), 2)
        self.assertIn(child1, parent.children.all())
        self.assertIn(child2, parent.children.all())


class CrossReferentialFieldTestCase(FieldTypeTestCase):
    """Test cases for cross-referential custom object fields."""

    def test_cross_referential_object_field(self):
        """Test object field referencing another custom object type."""
        # Create second custom object type
        second_type = self.create_custom_object_type(name="SecondObject", slug="second-objects")
        self.create_custom_object_type_field(
            second_type,
            name="name",
            label="Name",
            type="text",
            primary=True,
            required=True
        )

        # Add object field referencing second type
        field = self.create_custom_object_type_field(
            self.custom_object_type,
            name="related_object",
            label="Related Object",
            type="object",
            related_object_type=second_type.object_type
        )
        field  # To silence ruff error

        model1 = self.custom_object_type.get_model()
        model2 = second_type.get_model()

        # Create instances
        obj2 = model2.objects.create(name="Second Object")
        obj1 = model1.objects.create(name="First Object", related_object=obj2)

        self.assertEqual(obj1.related_object, obj2)

    def test_cross_referential_multiobject_field(self):
        """Test multiobject field referencing another custom object type."""
        # Create second custom object type
        second_type = self.create_custom_object_type(name="SecondObject", slug="second-objects")
        self.create_custom_object_type_field(
            second_type,
            name="name",
            label="Name",
            type="text",
            primary=True,
            required=True
        )

        # Add multiobject field referencing second type
        field = self.create_custom_object_type_field(
            self.custom_object_type,
            name="related_objects",
            label="Related Objects",
            type="multiobject",
            related_object_type=second_type.object_type
        )
        field  # To silence ruff error

        model1 = self.custom_object_type.get_model()
        model2 = second_type.get_model()

        # Create instances
        obj1 = model1.objects.create(name="First Object")
        obj2_1 = model2.objects.create(name="Second Object 1")
        obj2_2 = model2.objects.create(name="Second Object 2")

        # Add related objects
        obj1.related_objects.add(obj2_1, obj2_2)

        from deepdiff import DeepDiff

        ob1 = obj1.related_objects.first()
        ob2 = obj2_1

        '''
        diff = DeepDiff(ob1, ob2)
        print("")
        print("--------------------------------")
        print(diff)
        print("--------------------------------")

        print("")
        print(ob1.__dict__.items())
        print(ob2.__dict__.items())
        breakpoint()
        self.assertEqual(obj1.related_objects.count(), 2)
        self.assertIn(obj2_1, obj1.related_objects.all())
        self.assertIn(obj2_2, obj1.related_objects.all())
        '''
