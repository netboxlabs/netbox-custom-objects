"""
Tests for all the different field types supported by Custom Object Type Fields.
"""
from unittest import skip
from unittest.mock import Mock
from datetime import date, datetime, timezone
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.test import TestCase

from core.models import ObjectType
from netbox_custom_objects.field_types import (
    MultiObjectFieldType,
    MultiSelectFieldType,
    ObjectFieldType,
    SelectFieldType,
)
from netbox_custom_objects.models import CustomObjectType, CustomObjectTypeField
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
        self.create_custom_object_type_field(
            self.custom_object_type,
            name="description",
            label="Description",
            type="text",
            required=True
        )

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
        self.create_custom_object_type_field(
            self.custom_object_type,
            name="content",
            label="Content",
            type="longtext"
        )

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
        self.create_custom_object_type_field(
            self.custom_object_type,
            name="count",
            label="Count",
            type="integer",
            default=10
        )

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
        self.create_custom_object_type_field(
            self.custom_object_type,
            name="price",
            label="Price",
            type="decimal",
            default=10.50
        )

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
        self.create_custom_object_type_field(
            self.custom_object_type,
            name="active",
            label="Active",
            type="boolean",
            default=True
        )

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
        self.create_custom_object_type_field(
            self.custom_object_type,
            name="created_date",
            label="Created Date",
            type="date"
        )

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
            default="2023-01-01T12:00:00+00:00"
        )

        self.assertEqual(field.type, "datetime")
        self.assertEqual(field.default, "2023-01-01T12:00:00+00:00")

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
        self.create_custom_object_type_field(
            self.custom_object_type,
            name="created_datetime",
            label="Created DateTime",
            type="datetime"
        )

        model = self.custom_object_type.get_model()
        test_datetime = datetime(2023, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
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
        self.create_custom_object_type_field(
            self.custom_object_type,
            name="website",
            label="Website",
            type="url"
        )

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
        self.create_custom_object_type_field(
            self.custom_object_type,
            name="metadata",
            label="Metadata",
            type="json"
        )

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
        self.create_custom_object_type_field(
            self.custom_object_type,
            name="status",
            label="Status",
            type="select",
            choice_set=self.choice_set,
            default="choice1"
        )

        model = self.custom_object_type.get_model()
        instance = model.objects.create(name="Test", status="choice2")

        self.assertEqual(instance.status, "choice2")

    def test_select_field_display_value_returns_label(self):
        """get_display_value() must return the human-readable label, not the raw key."""
        self.create_custom_object_type_field(
            self.custom_object_type,
            name="status",
            label="Status",
            type="select",
            choice_set=self.choice_set,
        )
        model = self.custom_object_type.get_model(no_cache=True)
        instance = model.objects.create(name="Test", status="choice1")
        field_type = SelectFieldType()
        self.assertEqual(field_type.get_display_value(instance, "status"), "Choice 1")

    def test_select_primary_field_str_uses_label(self):
        """When a Selection field is the primary field, __str__ must show the label."""
        cot = self.create_custom_object_type(
            name="StrSelectObject",
            slug="str-select-object",
            verbose_name_plural="StrSelectObjects",
        )
        self.create_custom_object_type_field(
            cot,
            name="status",
            label="Status",
            type="select",
            choice_set=self.choice_set,
            primary=True,
        )
        model = cot.get_model()
        instance = model.objects.create(status="choice2")
        self.assertEqual(str(instance), "Choice 2")

    def test_select_column_render_returns_label(self):
        """get_table_column_field() render() translates a raw key to its human-readable label."""
        field = Mock()
        field.choices = [('choice1', 'Choice 1'), ('choice2', 'Choice 2')]
        column = SelectFieldType().get_table_column_field(field)
        self.assertEqual(column.render(value='choice1'), 'Choice 1')
        self.assertEqual(column.render(value='choice2'), 'Choice 2')

    def test_select_column_render_unknown_key_falls_back_to_raw_value(self):
        """get_table_column_field() render() returns the raw key when it is not in choices."""
        field = Mock()
        field.choices = [('choice1', 'Choice 1')]
        column = SelectFieldType().get_table_column_field(field)
        self.assertEqual(column.render(value='unknown'), 'unknown')

    def test_get_field_value_returns_label_for_select(self):
        """get_field_value template filter returns the human-readable label for select fields."""
        from netbox_custom_objects.templatetags.custom_object_utils import get_field_value
        cotf = self.create_custom_object_type_field(
            self.custom_object_type,
            name="status",
            label="Status",
            type="select",
            choice_set=self.choice_set,
        )
        model = self.custom_object_type.get_model(no_cache=True)
        instance = model.objects.create(name="Test", status="choice1")
        self.assertEqual(get_field_value(instance, cotf), "Choice 1")

    def test_get_field_value_returns_raw_value_when_select_is_none(self):
        """get_field_value returns None (falsy) when the select field is unset."""
        from netbox_custom_objects.templatetags.custom_object_utils import get_field_value
        cotf = self.create_custom_object_type_field(
            self.custom_object_type,
            name="status",
            label="Status",
            type="select",
            choice_set=self.choice_set,
        )
        model = self.custom_object_type.get_model(no_cache=True)
        instance = model.objects.create(name="Test")
        self.assertIsNone(get_field_value(instance, cotf))


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
        self.create_custom_object_type_field(
            self.custom_object_type,
            name="tags",
            label="Tags",
            type="multiselect",
            choice_set=self.choice_set
        )

        model = self.custom_object_type.get_model()
        instance = model.objects.create(name="Test", tags=["choice1", "choice3"])

        self.assertEqual(instance.tags, ["choice1", "choice3"])

    def test_multiselect_field_display_value_returns_labels(self):
        """get_display_value() must return comma-joined human-readable labels, not raw keys."""
        self.create_custom_object_type_field(
            self.custom_object_type,
            name="tags",
            label="Tags",
            type="multiselect",
            choice_set=self.choice_set,
        )
        model = self.custom_object_type.get_model(no_cache=True)
        instance = model.objects.create(name="Test", tags=["choice1", "choice3"])
        field_type = MultiSelectFieldType()
        self.assertEqual(
            field_type.get_display_value(instance, "tags"),
            "Choice 1, Choice 3",
        )

    def test_multiselect_column_render_returns_labels(self):
        """get_table_column_field() render() translates raw keys to comma-joined labels."""
        field = Mock()
        field.choices = [('choice1', 'Choice 1'), ('choice2', 'Choice 2'), ('choice3', 'Choice 3')]
        column = MultiSelectFieldType().get_table_column_field(field)
        self.assertEqual(column.render(value=['choice1', 'choice3']), 'Choice 1, Choice 3')

    def test_multiselect_column_render_unknown_key_falls_back_to_raw_value(self):
        """get_table_column_field() render() preserves unknown keys in the joined output."""
        field = Mock()
        field.choices = [('choice1', 'Choice 1')]
        column = MultiSelectFieldType().get_table_column_field(field)
        self.assertEqual(column.render(value=['choice1', 'unknown']), 'Choice 1, unknown')

    def test_get_field_value_returns_label_list_for_multiselect(self):
        """get_field_value template filter returns a list of labels for multiselect fields."""
        from netbox_custom_objects.templatetags.custom_object_utils import get_field_value
        cotf = self.create_custom_object_type_field(
            self.custom_object_type,
            name="tags",
            label="Tags",
            type="multiselect",
            choice_set=self.choice_set,
        )
        model = self.custom_object_type.get_model(no_cache=True)
        instance = model.objects.create(name="Test", tags=["choice1", "choice3"])
        self.assertEqual(get_field_value(instance, cotf), ["Choice 1", "Choice 3"])

    def test_get_field_value_returns_empty_list_when_multiselect_is_none(self):
        """get_field_value returns None (falsy) when the multiselect field is unset."""
        from netbox_custom_objects.templatetags.custom_object_utils import get_field_value
        cotf = self.create_custom_object_type_field(
            self.custom_object_type,
            name="tags",
            label="Tags",
            type="multiselect",
            choice_set=self.choice_set,
        )
        model = self.custom_object_type.get_model(no_cache=True)
        instance = model.objects.create(name="Test")
        self.assertIsNone(get_field_value(instance, cotf))


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

    def test_get_model_field_raises_not_implemented_error_for_null_related_object_type(self):
        """get_model_field() must raise NotImplementedError (not ContentType.DoesNotExist)
        when related_object_type_id is NULL.  All callers catch NotImplementedError to
        skip broken fields; an unexpected ContentType.DoesNotExist would propagate up
        and crash model generation or the serializer."""
        field = self.create_custom_object_type_field(
            self.custom_object_type,
            name="broken_obj",
            label="Broken",
            type="object",
            related_object_type=self.device_object_type,
        )
        CustomObjectTypeField.objects.filter(pk=field.pk).update(related_object_type=None)
        field.refresh_from_db()
        with self.assertRaises(NotImplementedError):
            ObjectFieldType().get_model_field(field)

    def test_object_field_model_generation(self):
        """Test object field model generation."""
        self.create_custom_object_type_field(
            self.custom_object_type,
            name="device",
            label="Device",
            type="object",
            related_object_type=self.device_object_type
        )

        from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site

        model = self.custom_object_type.get_model()

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

    def test_get_model_field_raises_not_implemented_error_for_null_related_object_type(self):
        """get_model_field() must raise NotImplementedError (not ContentType.DoesNotExist)
        when related_object_type_id is NULL so callers handle it consistently."""
        field = self.create_custom_object_type_field(
            self.custom_object_type,
            name="broken_multi",
            label="Broken Multi",
            type="multiobject",
            related_object_type=self.device_object_type,
        )
        CustomObjectTypeField.objects.filter(pk=field.pk).update(related_object_type=None)
        field.refresh_from_db()
        with self.assertRaises(NotImplementedError):
            MultiObjectFieldType().get_model_field(field)

    def test_multiobject_field_model_generation(self):
        """Test multiobject field model generation."""
        self.create_custom_object_type_field(
            self.custom_object_type,
            name="devices",
            label="Devices",
            type="multiobject",
            related_object_type=self.device_object_type
        )

        from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site

        model = self.custom_object_type.get_model()

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


class SelfReferentialFieldTestCase(FieldTypeTestCase):
    """Test cases for self-referential object fields.

    The recursion guard in CustomObjectTypeField._check_recursion() explicitly
    permits self-referential fields (#263), so these tests should pass without
    any skip decorator.
    """

    def test_self_referential_object_field(self):
        """#263 – A COT may have an FK object field pointing to itself."""
        self.create_custom_object_type_field(
            self.custom_object_type,
            name="parent",
            label="Parent",
            type="object",
            related_object_type=self.custom_object_type.object_type,
        )

        model = self.custom_object_type.get_model()

        parent = model.objects.create(name="Parent Instance")
        child = model.objects.create(name="Child Instance", parent=parent)

        self.assertEqual(child.parent, parent)

    def test_self_referential_multiobject_field(self):
        """#263 – A COT may have a M2M multiobject field pointing to itself."""
        self.create_custom_object_type_field(
            self.custom_object_type,
            name="children",
            label="Children",
            type="multiobject",
            related_object_type=self.custom_object_type.object_type,
        )

        model = self.custom_object_type.get_model()

        parent = model.objects.create(name="Parent Instance")
        child1 = model.objects.create(name="Child 1")
        child2 = model.objects.create(name="Child 2")

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
        self.create_custom_object_type_field(
            self.custom_object_type,
            name="related_object",
            label="Related Object",
            type="object",
            related_object_type=second_type.object_type
        )

        model1 = self.custom_object_type.get_model()
        # Refresh second_type so cache_timestamp matches the DB value bumped by the signal.
        second_type.refresh_from_db()
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
        self.create_custom_object_type_field(
            self.custom_object_type,
            name="related_objects",
            label="Related Objects",
            type="multiobject",
            related_object_type=second_type.object_type
        )

        model1 = self.custom_object_type.get_model()
        model2 = second_type.get_model()

        # Create instances
        obj1 = model1.objects.create(name="First Object")
        obj2_1 = model2.objects.create(name="Second Object 1")
        obj2_2 = model2.objects.create(name="Second Object 2")

        # Add related objects
        obj1.related_objects.add(obj2_1, obj2_2)

        self.assertEqual(obj1.related_objects.count(), 2)
        self.assertIn(obj2_1, obj1.related_objects.all())
        self.assertIn(obj2_2, obj1.related_objects.all())


class MultipleObjectFieldsToSameCOTTestCase(FieldTypeTestCase):
    """Test cases for multiple object/multiobject fields pointing to the same COT.

    Issue #237: multiple FK/M2M fields targeting the same related COT should all
    be created without name collisions and must remain independently queryable.
    """

    def test_two_fk_fields_to_same_cot(self):
        """#237 – Two FK object fields on the same COT can point to the same target COT."""
        # Create a second COT that will be the shared target
        target = self.create_custom_object_type(name="SharedTarget", slug="shared-target")
        self.create_custom_object_type_field(
            target,
            name="name",
            label="Name",
            type="text",
            primary=True,
            required=True,
        )

        # Add two independent FK fields on the existing COT pointing to target
        self.create_custom_object_type_field(
            self.custom_object_type,
            name="primary_ref",
            label="Primary Reference",
            type="object",
            related_object_type=target.object_type,
        )
        self.create_custom_object_type_field(
            self.custom_object_type,
            name="secondary_ref",
            label="Secondary Reference",
            type="object",
            related_object_type=target.object_type,
        )

        # Refresh target so its cache_timestamp matches the DB value bumped by the signal
        # when both TYPE_OBJECT fields above were saved.
        target.refresh_from_db()
        target_model = target.get_model()
        model = self.custom_object_type.get_model()

        t1 = target_model.objects.create(name="Target 1")
        t2 = target_model.objects.create(name="Target 2")

        obj = model.objects.create(name="Source Object", primary_ref=t1, secondary_ref=t2)

        self.assertEqual(obj.primary_ref, t1)
        self.assertEqual(obj.secondary_ref, t2)

    def test_two_m2m_fields_to_same_cot(self):
        """#237 – Two M2M multiobject fields on the same COT can point to the same target COT."""
        target = self.create_custom_object_type(name="M2MTarget", slug="m2m-target")
        self.create_custom_object_type_field(
            target,
            name="name",
            label="Name",
            type="text",
            primary=True,
            required=True,
        )

        self.create_custom_object_type_field(
            self.custom_object_type,
            name="primary_refs",
            label="Primary References",
            type="multiobject",
            related_object_type=target.object_type,
        )
        self.create_custom_object_type_field(
            self.custom_object_type,
            name="secondary_refs",
            label="Secondary References",
            type="multiobject",
            related_object_type=target.object_type,
        )

        target_model = target.get_model()
        model = self.custom_object_type.get_model()

        t1 = target_model.objects.create(name="Target 1")
        t2 = target_model.objects.create(name="Target 2")
        t3 = target_model.objects.create(name="Target 3")

        obj = model.objects.create(name="Source Object")
        obj.primary_refs.add(t1, t2)
        obj.secondary_refs.add(t2, t3)

        # The two M2M relations are independent
        self.assertEqual(obj.primary_refs.count(), 2)
        self.assertEqual(obj.secondary_refs.count(), 2)
        self.assertIn(t1, obj.primary_refs.all())
        self.assertIn(t3, obj.secondary_refs.all())
        # t2 appears in both without conflict
        self.assertIn(t2, obj.primary_refs.all())
        self.assertIn(t2, obj.secondary_refs.all())

    def test_fk_and_m2m_fields_to_same_cot(self):
        """#237 – One FK and one M2M field pointing to the same target COT coexist."""
        target = self.create_custom_object_type(name="MixedTarget", slug="mixed-target")
        self.create_custom_object_type_field(
            target,
            name="name",
            label="Name",
            type="text",
            primary=True,
            required=True,
        )

        self.create_custom_object_type_field(
            self.custom_object_type,
            name="single_ref",
            label="Single Reference",
            type="object",
            related_object_type=target.object_type,
        )
        self.create_custom_object_type_field(
            self.custom_object_type,
            name="multi_refs",
            label="Multi References",
            type="multiobject",
            related_object_type=target.object_type,
        )

        # Refresh target so cache_timestamp matches the DB value bumped by the signal
        # when the TYPE_OBJECT 'single_ref' field was saved above.
        target.refresh_from_db()
        target_model = target.get_model()
        model = self.custom_object_type.get_model()

        t1 = target_model.objects.create(name="Target 1")
        t2 = target_model.objects.create(name="Target 2")

        obj = model.objects.create(name="Source Object", single_ref=t1)
        obj.multi_refs.add(t1, t2)

        self.assertEqual(obj.single_ref, t1)
        self.assertEqual(obj.multi_refs.count(), 2)


class PrimaryFieldChangeTestCase(FieldTypeTestCase):
    """Test changing which field is designated as the primary (display) field.

    Issue #348: switching the primary flag to a different field while object
    references exist must not break __str__ or queryset access.
    """

    def test_change_primary_field_updates_str(self):
        """#348 – __str__ on existing instances reflects the new primary field after change."""
        # The base FieldTypeTestCase already has a primary 'name' text field.
        # Add a second text field and then switch primary to it.
        second_field = self.create_custom_object_type_field(
            self.custom_object_type,
            name="label",
            label="Label",
            type="text",
        )
        model = self.custom_object_type.get_model()
        instance = model.objects.create(name="OldPrimary", label="NewPrimary")

        # Before the switch, __str__ uses 'name'
        self.assertIn("OldPrimary", str(instance))

        # Promote 'label' to primary, demote 'name'.
        # Re-fetch from DB so that _original is populated (required by save()).
        existing_primary = CustomObjectTypeField.objects.filter(
            custom_object_type=self.custom_object_type, primary=True
        ).first()
        self.assertIsNotNone(existing_primary, "Expected a primary field to exist on the COT.")
        existing_primary.primary = False
        existing_primary.save()

        fresh_second_field = CustomObjectTypeField.objects.get(pk=second_field.pk)
        fresh_second_field.primary = True
        fresh_second_field.save()

        # Refresh instance after cache invalidation
        self.custom_object_type.clear_model_cache(self.custom_object_type.id)
        new_model = self.custom_object_type.get_model()
        refreshed = new_model.objects.get(pk=instance.pk)

        # __str__ should now reflect the 'label' field value
        self.assertIn("NewPrimary", str(refreshed))

    def test_change_primary_field_with_object_references(self):
        """#348 – Changing the primary field does not break object fields that reference the COT."""
        # Build a second COT that holds a reference to self.custom_object_type
        ref_cot = self.create_custom_object_type(name="RefHolder", slug="ref-holder")
        self.create_custom_object_type_field(
            ref_cot,
            name="name",
            label="Name",
            type="text",
            primary=True,
        )
        self.create_custom_object_type_field(
            ref_cot,
            name="ref",
            label="Reference",
            type="object",
            related_object_type=self.custom_object_type.object_type,
        )

        # Refresh so cache_timestamp matches the DB value bumped by the signal when
        # the TYPE_OBJECT 'ref' field was saved pointing to custom_object_type.
        self.custom_object_type.refresh_from_db()
        model_target = self.custom_object_type.get_model()
        model_ref = ref_cot.get_model()

        target_instance = model_target.objects.create(name="Target")
        ref_instance = model_ref.objects.create(name="Holder", ref=target_instance)

        # Change the primary field on the target COT.
        # Re-fetch from DB so that _original is populated (required by save()).
        second_field = self.create_custom_object_type_field(
            self.custom_object_type,
            name="code",
            label="Code",
            type="text",
        )

        # Give the target instance a 'code' value now that the column exists,
        # so the __str__ assertion below is meaningful after the primary swap.
        self.custom_object_type.clear_model_cache(self.custom_object_type.id)
        model_target_v2 = self.custom_object_type.get_model()
        model_target_v2.objects.filter(pk=target_instance.pk).update(code="T-001")

        existing_primary = CustomObjectTypeField.objects.filter(
            custom_object_type=self.custom_object_type, primary=True
        ).first()
        self.assertIsNotNone(existing_primary, "Expected a primary field to exist on the COT.")
        existing_primary.primary = False
        existing_primary.save()

        fresh_second_field = CustomObjectTypeField.objects.get(pk=second_field.pk)
        fresh_second_field.primary = True
        fresh_second_field.save()

        # The FK relationship must still be intact after the primary field swap
        self.custom_object_type.clear_model_cache(self.custom_object_type.id)
        ref_cot.clear_model_cache(ref_cot.id)

        new_target_model = self.custom_object_type.get_model()
        refreshed_ref = model_ref.objects.get(pk=ref_instance.pk)
        self.assertEqual(refreshed_ref.ref_id, target_instance.pk)

        # __str__ on the referenced target must reflect the new primary field ('code')
        refreshed_target = new_target_model.objects.get(pk=target_instance.pk)
        self.assertIn("T-001", str(refreshed_target))


# ---------------------------------------------------------------------------
# Context field — ts-parent-field widget attribute on get_form_field()
# ---------------------------------------------------------------------------


class ContextFieldWidgetTestCase(CustomObjectsTestCase, TestCase):
    """
    ObjectFieldType.get_form_field() and MultiObjectFieldType.get_form_field()
    must set ts-parent-field="_context" on the widget whenever the target COT
    has at least one field marked context=True, and must NOT set it otherwise.

    Two target scenarios are exercised:
      • target has a primary field  → display uses the field value
      • target has no primary field → display falls back to "{COT name} {id}"
    Both scenarios still have a context field, so ts-parent-field must appear.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()

        # Target A: primary field + context field
        cls.target_with_primary = cls.create_custom_object_type(
            name="ctxwidgetprimary", slug="ctx-widget-primary"
        )
        cls.create_custom_object_type_field(
            cls.target_with_primary, name="name", type="text", primary=True
        )
        cls.create_custom_object_type_field(
            cls.target_with_primary, name="owner", type="text", context=True
        )

        # Target B: no primary field, has a context field (fallback display)
        cls.target_no_primary = cls.create_custom_object_type(
            name="ctxwidgetnoprimary", slug="ctx-widget-no-primary"
        )
        cls.create_custom_object_type_field(
            cls.target_no_primary, name="owner", type="text", context=True
        )

        # Target C: no context fields at all
        cls.target_no_context = cls.create_custom_object_type(
            name="ctxwidgetnocontext", slug="ctx-widget-no-context"
        )
        cls.create_custom_object_type_field(
            cls.target_no_context, name="name", type="text", primary=True
        )

        # Target D: multiple context fields
        cls.target_multi_ctx = cls.create_custom_object_type(
            name="ctxwidgetmultictx", slug="ctx-widget-multi-ctx"
        )
        cls.create_custom_object_type_field(
            cls.target_multi_ctx, name="name", type="text", primary=True
        )
        cls.create_custom_object_type_field(
            cls.target_multi_ctx, name="owner", type="text", context=True
        )
        cls.create_custom_object_type_field(
            cls.target_multi_ctx, name="region", type="text", context=True
        )

        # Source COT with object/multiobject fields pointing at each target
        cls.source_cot = cls.create_custom_object_type(
            name="ctxwidgetsource", slug="ctx-widget-source"
        )
        cls.field_obj_with_ctx = cls.create_custom_object_type_field(
            cls.source_cot,
            name="ref_obj_with_ctx",
            type="object",
            related_object_type=ObjectType.objects.get_for_model(
                cls.target_with_primary.get_model()
            ),
        )
        cls.field_obj_no_primary = cls.create_custom_object_type_field(
            cls.source_cot,
            name="ref_obj_no_primary",
            type="object",
            related_object_type=ObjectType.objects.get_for_model(
                cls.target_no_primary.get_model()
            ),
        )
        cls.field_obj_no_ctx = cls.create_custom_object_type_field(
            cls.source_cot,
            name="ref_obj_no_ctx",
            type="object",
            related_object_type=ObjectType.objects.get_for_model(
                cls.target_no_context.get_model()
            ),
        )
        cls.field_multi_with_ctx = cls.create_custom_object_type_field(
            cls.source_cot,
            name="ref_multi_with_ctx",
            type="multiobject",
            related_object_type=ObjectType.objects.get_for_model(
                cls.target_with_primary.get_model()
            ),
        )
        cls.field_multi_no_ctx = cls.create_custom_object_type_field(
            cls.source_cot,
            name="ref_multi_no_ctx",
            type="multiobject",
            related_object_type=ObjectType.objects.get_for_model(
                cls.target_no_context.get_model()
            ),
        )
        cls.field_obj_multi_ctx = cls.create_custom_object_type_field(
            cls.source_cot,
            name="ref_obj_multi_ctx",
            type="object",
            related_object_type=ObjectType.objects.get_for_model(
                cls.target_multi_ctx.get_model()
            ),
        )

    @classmethod
    def tearDownClass(cls):
        CustomObjectType.clear_model_cache()
        super().tearDownClass()

    # --- ObjectFieldType ---

    def test_object_field_with_context_sets_ts_parent_field(self):
        """Widget must carry ts-parent-field="_context" when target has a context field."""
        form_field = ObjectFieldType().get_form_field(self.field_obj_with_ctx)
        self.assertEqual(form_field.widget.attrs.get("ts-parent-field"), "_context")

    def test_object_field_fallback_display_still_sets_ts_parent_field(self):
        """ts-parent-field must be set even when the target uses fallback display
        (i.e. has no primary field)."""
        form_field = ObjectFieldType().get_form_field(self.field_obj_no_primary)
        self.assertEqual(form_field.widget.attrs.get("ts-parent-field"), "_context")

    def test_object_field_without_context_does_not_set_ts_parent_field(self):
        """Widget must NOT have ts-parent-field when the target has no context fields."""
        form_field = ObjectFieldType().get_form_field(self.field_obj_no_ctx)
        self.assertNotIn("ts-parent-field", form_field.widget.attrs)

    # --- MultiObjectFieldType ---

    def test_multiobject_field_with_context_sets_ts_parent_field(self):
        """Same behaviour applies to multi-object fields."""
        form_field = MultiObjectFieldType().get_form_field(self.field_multi_with_ctx)
        self.assertEqual(form_field.widget.attrs.get("ts-parent-field"), "_context")

    def test_multiobject_field_without_context_does_not_set_ts_parent_field(self):
        """Multi-object widget must NOT have ts-parent-field without context fields."""
        form_field = MultiObjectFieldType().get_form_field(self.field_multi_no_ctx)
        self.assertNotIn("ts-parent-field", form_field.widget.attrs)

    def test_object_field_with_multiple_context_fields_sets_ts_parent_field(self):
        """ts-parent-field must be set when the target has more than one context field."""
        form_field = ObjectFieldType().get_form_field(self.field_obj_multi_ctx)
        self.assertEqual(form_field.widget.attrs.get("ts-parent-field"), "_context")


# ---------------------------------------------------------------------------
# Context field — model validation
# ---------------------------------------------------------------------------


class ContextFieldValidationTestCase(CustomObjectsTestCase, TestCase):
    """A field cannot be simultaneously marked as primary and context."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.cot = cls.create_custom_object_type(
            name="ctxvalidationcot", slug="ctx-validation-cot"
        )

    def test_primary_and_context_raises_validation_error(self):
        """clean() must reject a field with both primary=True and context=True."""
        from netbox_custom_objects.models import CustomObjectTypeField
        field = CustomObjectTypeField(
            custom_object_type=self.cot,
            name="dual",
            type="text",
            primary=True,
            context=True,
        )
        with self.assertRaises(ValidationError):
            field.full_clean()

    def test_primary_only_is_valid(self):
        """primary=True without context=True must pass validation."""
        from netbox_custom_objects.models import CustomObjectTypeField
        field = CustomObjectTypeField(
            custom_object_type=self.cot,
            name="primaryonly",
            type="text",
            primary=True,
            context=False,
        )
        # Should not raise
        field.full_clean()

    def test_context_only_is_valid(self):
        """context=True without primary=True must pass validation."""
        from netbox_custom_objects.models import CustomObjectTypeField
        field = CustomObjectTypeField(
            custom_object_type=self.cot,
            name="contextonly",
            type="text",
            primary=False,
            context=True,
        )
        # Should not raise
        field.full_clean()
