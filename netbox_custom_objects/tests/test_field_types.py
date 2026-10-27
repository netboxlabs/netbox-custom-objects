"""
Tests for all the different field types supported by Custom Object Type Fields.
"""
import json
from importlib import import_module
from unittest import skipUnless
from unittest.mock import Mock
from datetime import date, datetime
from decimal import Decimal
from django.apps import apps
from django.core.exceptions import FieldDoesNotExist, ValidationError
from django.db import connection, models
from django.forms.fields import JSONString
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from core.models import ObjectType
from dcim.models import Device, DeviceType, ModuleType
from extras.models import CustomFieldChoiceSet
from netbox.tables.columns import ColorColumn
from netbox_custom_objects.field_types import (
    FIELD_TYPE_CLASS,
    ArrayFieldType,
    ColorFieldType,
    MACAddressFieldType,
    CoordinatesFieldType,
    DecimalFieldType,
    MultiObjectFieldType,
    MultiSelectFieldType,
    ObjectFieldType,
    SelectFieldType,
    _csv_import_to_field_name,
)
from netbox.search import get_indexer
from netbox_custom_objects.models import CustomObjectType, CustomObjectTypeField, _deferred_co_field_data
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


class RequiredFieldEnforcementTestCase(FieldTypeTestCase):
    """A required field must produce blank=False at the model layer, regardless of type."""

    SCALAR_TYPES = (
        'text', 'longtext', 'integer', 'decimal', 'boolean', 'date', 'datetime',
        'url', 'json', 'color', 'mac_address',
    )

    def test_scalar_fields_blank_matches_required(self):
        for field_type in self.SCALAR_TYPES:
            with self.subTest(field_type=field_type):
                required = self.create_custom_object_type_field(
                    self.custom_object_type, name=f'{field_type}_req', type=field_type,
                    required=True,
                )
                optional = self.create_custom_object_type_field(
                    self.custom_object_type, name=f'{field_type}_opt', type=field_type,
                    required=False,
                )
                model = self.custom_object_type.get_model()
                self.assertFalse(model._meta.get_field(required.name).blank)
                self.assertTrue(model._meta.get_field(optional.name).blank)
                self.assertTrue(model._meta.get_field(required.name).null)

    def test_select_and_multiselect_blank_matches_required(self):
        choice_set = self.create_choice_set()
        for field_type in ('select', 'multiselect'):
            with self.subTest(field_type=field_type):
                required = self.create_custom_object_type_field(
                    self.custom_object_type, name=f'{field_type}_req', type=field_type,
                    required=True, choice_set=choice_set,
                )
                model = self.custom_object_type.get_model()
                self.assertFalse(model._meta.get_field(required.name).blank)

    def test_coordinates_both_halves_follow_required(self):
        self.create_custom_object_type_field(
            self.custom_object_type, name='coords_req', type='coordinates', required=True,
        )
        model = self.custom_object_type.get_model()
        self.assertFalse(model._meta.get_field('coords_req_latitude').blank)
        self.assertFalse(model._meta.get_field('coords_req_longitude').blank)

    def test_url_title_stays_optional_regardless_of_required(self):
        self.create_custom_object_type_field(
            self.custom_object_type, name='url_req', type='url', required=True,
        )
        model = self.custom_object_type.get_model()
        self.assertFalse(model._meta.get_field('url_req').blank)
        self.assertTrue(model._meta.get_field('url_req_title').blank)

    def test_object_field_blank_matches_required(self):
        """A plain object field's FK follows required, like every scalar type --
        and full_clean() enforces it, since FK fields are validated by
        Django's clean_fields()."""
        site_ot = ObjectType.objects.get(app_label='dcim', model='site')
        required = self.create_custom_object_type_field(
            self.custom_object_type, name='site_req', type='object',
            related_object_type=site_ot, required=True,
        )
        optional = self.create_custom_object_type_field(
            self.custom_object_type, name='site_opt', type='object',
            related_object_type=site_ot, required=False,
        )
        model = self.custom_object_type.get_model()
        self.assertFalse(model._meta.get_field(required.name).blank)
        self.assertTrue(model._meta.get_field(optional.name).blank)
        self.assertTrue(model._meta.get_field(required.name).null)

        instance = model(name='obj')
        with self.assertRaises(ValidationError):
            instance.full_clean()

    def test_multiobject_field_blank_matches_required_but_full_clean_cannot_check_it(self):
        """A plain multiobject field's M2M also follows required (the
        required-toggle pre-flight check relies on it), but full_clean() can't
        enforce it -- Django's clean_fields() excludes M2M fields entirely, so
        required stays enforced at the REST/UI layer for multiobject."""
        site_ot = ObjectType.objects.get(app_label='dcim', model='site')
        required = self.create_custom_object_type_field(
            self.custom_object_type, name='sites_req', type='multiobject',
            related_object_type=site_ot, required=True,
        )
        optional = self.create_custom_object_type_field(
            self.custom_object_type, name='sites_opt', type='multiobject',
            related_object_type=site_ot, required=False,
        )
        model = self.custom_object_type.get_model()
        self.assertFalse(model._meta.get_field(required.name).blank)
        self.assertTrue(model._meta.get_field(optional.name).blank)

        instance = model.objects.create(name='obj')
        instance.full_clean()  # does not raise -- M2M is outside clean_fields()'s reach


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

    def test_validation_regex_enforced_on_model_field(self):
        """
        validation_regex is enforced by the generated model field itself,
        so model validation (used by API writes and CSV import) rejects non-matching values.
        """
        for field_type, regex, valid, invalid in (
            ("text", r"^[A-Z]{3}$", "ABC", "abc"),
            ("longtext", r"^ok", "ok then", "not ok"),
            ("url", r"^https://", "https://example.com", "http://example.com"),
        ):
            with self.subTest(field_type=field_type):
                field_name = f"regex_{field_type}"
                self.create_custom_object_type_field(
                    self.custom_object_type,
                    name=field_name,
                    label=field_name,
                    type=field_type,
                    validation_regex=regex,
                )
                model = self.custom_object_type.get_model()

                model(**{field_name: valid}).clean_fields(exclude=["id", "name"])

                with self.assertRaises(ValidationError) as cm:
                    model(**{field_name: invalid}).clean_fields(exclude=["id", "name"])
                self.assertIn(field_name, cm.exception.error_dict)

    def test_validation_regex_with_percent_renders_error(self):
        """
        A literal % in validation_regex must not break rendering of the validation
        error: Django renders validator messages with ``message % params``.
        """
        regex = r"^\d+%$"
        self.create_custom_object_type_field(
            self.custom_object_type,
            name="percent",
            label="Percent",
            type="text",
            validation_regex=regex,
        )
        model = self.custom_object_type.get_model()

        model(percent="50%").clean_fields(exclude=["id", "name"])

        with self.assertRaises(ValidationError) as cm:
            model(percent="abc").clean_fields(exclude=["id", "name"])
        # Rendering the message is what raised ValueError before the fix.
        self.assertIn(regex, str(cm.exception.message_dict["percent"][0]))

        field = self.custom_object_type.fields.get(name="percent")
        form_field = FIELD_TYPE_CLASS[field.type]().get_form_field(field)
        with self.assertRaises(ValidationError) as cm:
            form_field.clean("abc")
        self.assertIn(regex, str(cm.exception.messages[0]))


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

    def test_integer_field_min_max_enforced_by_full_clean(self):
        """validation_minimum/maximum must be enforced by the model field, not just the UI form."""
        self.create_custom_object_type_field(
            self.custom_object_type, name="count", type="integer",
            validation_minimum=0, validation_maximum=100,
        )
        model = self.custom_object_type.get_model()

        model(name="Test", count=50).full_clean()
        model(name="Test", count=0).full_clean()  # exact minimum
        model(name="Test", count=100).full_clean()  # exact maximum

        with self.assertRaises(ValidationError):
            model(name="Test", count=-1).full_clean()
        with self.assertRaises(ValidationError):
            model(name="Test", count=101).full_clean()

    def test_integer_field_is_64_bit(self):
        """Integer fields use a 64-bit (bigint) column, not 32-bit."""
        self.create_custom_object_type_field(
            self.custom_object_type,
            name="count",
            label="Count",
            type="integer",
        )

        model = self.custom_object_type.get_model()

        # The generated model field must be a BigIntegerField.
        self.assertIsInstance(
            model._meta.get_field("count"), models.BigIntegerField
        )

        # A value beyond the signed 32-bit range must round-trip through the DB.
        big_value = 9_000_000_000  # > 2**31 - 1 (2_147_483_647)
        instance = model.objects.create(name="Test", count=big_value)
        instance.refresh_from_db()
        self.assertEqual(instance.count, big_value)


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

    def test_decimal_field_min_max_enforced_by_full_clean(self):
        """validation_minimum/maximum must be enforced by the model field, not just the UI form."""
        self.create_custom_object_type_field(
            self.custom_object_type, name="price", type="decimal",
            validation_minimum=0, validation_maximum=100,
        )
        model = self.custom_object_type.get_model()

        model(name="Test", price=Decimal("50.00")).full_clean()
        model(name="Test", price=Decimal("0.00")).full_clean()  # exact minimum
        model(name="Test", price=Decimal("100.00")).full_clean()  # exact maximum

        with self.assertRaises(ValidationError):
            model(name="Test", price=Decimal("-1.00")).full_clean()
        with self.assertRaises(ValidationError):
            model(name="Test", price=Decimal("101.00")).full_clean()

    def test_decimal_precision_is_the_same_in_every_layer(self):
        """The model, serializer and form all accept 16 digits with 4 decimal places.

        The column used to be numeric(8,2) while the form accepted 12/4, so the form
        took values the database then rejected.
        """
        field = self.create_custom_object_type_field(
            self.custom_object_type, name="price", label="Price", type="decimal",
        )
        model = self.custom_object_type.get_model(no_cache=True)
        field_type = DecimalFieldType()

        for layer in (
            model._meta.get_field("price"),
            field_type.get_serializer_field(field),
            field_type.get_form_field(field),
        ):
            self.assertEqual((layer.max_digits, layer.decimal_places), (16, 4), layer)

        widest = Decimal("123456789012.3456")
        self.assertEqual(field_type.get_form_field(field).clean(str(widest)), widest)
        instance = model(name="Test", price=widest)
        instance.full_clean()
        instance.save()
        instance.refresh_from_db()
        self.assertEqual(instance.price, widest)

        too_wide = "1234567890123.4567"
        with self.assertRaises(ValidationError):
            field_type.get_form_field(field).clean(too_wide)
        with self.assertRaises(ValidationError):
            model(name="Test", price=Decimal(too_wide)).full_clean()


def _column_type(table_name, column_name):
    """The column's type as PostgreSQL formats it, e.g. ``numeric(16,4)``, or None if absent."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT pg_catalog.format_type(a.atttypid, a.atttypmod)
            FROM pg_catalog.pg_attribute a
            JOIN pg_catalog.pg_class c ON c.oid = a.attrelid
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = current_schema() AND c.relname = %s AND a.attname = %s
              AND a.attnum > 0 AND NOT a.attisdropped
            """,
            [table_name, column_name],
        )
        row = cursor.fetchone()
        return row[0] if row else None


def _alter_column_type(table_name, column_name, column_type):
    with connection.cursor() as cursor:
        cursor.execute(f'ALTER TABLE "{table_name}" ALTER COLUMN "{column_name}" TYPE {column_type}')


class WidenDecimalAndMultiSelectColumnsTestCase(FieldTypeTestCase):
    """The 0019 migration widens older, narrower decimal and multi-select columns in place.

    New tables get numeric(16,4) and varchar(100)[] columns directly, but tables
    created earlier are managed=False and keep numeric(8,2) / varchar(50)[] until
    the migration alters them.
    """

    def setUp(self):
        super().setUp()
        self.choice_set = self.create_choice_set()
        self.table_name = f"custom_objects_{self.custom_object_type.pk}"

    def _run_migration(self):
        """Run the migration's data function and return the ALTER TABLE statements it executed.

        It only reads stable CustomObjectTypeField columns (type, custom_object_type_id,
        name), so the current app registry stands in for the historical one.
        """
        migration = import_module("netbox_custom_objects.migrations.0019_widen_decimal_and_multiselect_columns")
        with CaptureQueriesContext(connection) as queries, connection.schema_editor() as schema_editor:
            migration.widen_decimal_and_multiselect_columns(apps, schema_editor)
        return [q["sql"] for q in queries.captured_queries if q["sql"].startswith("ALTER TABLE")]

    def _create_legacy_fields(self):
        """Create a decimal and a multi-select field, then narrow their columns to the old types."""
        self.create_custom_object_type_field(self.custom_object_type, name="price", label="Price", type="decimal")
        self.create_custom_object_type_field(
            self.custom_object_type, name="tags", label="Tags", type="multiselect", choice_set=self.choice_set,
        )
        # Realize the backing table and columns.
        model = self.custom_object_type.get_model(no_cache=True)
        _alter_column_type(self.table_name, "price", "numeric(8,2)")
        _alter_column_type(self.table_name, "tags", "varchar(50)[]")
        self.assertEqual(_column_type(self.table_name, "price"), "numeric(8,2)")
        self.assertEqual(_column_type(self.table_name, "tags"), "character varying(50)[]")
        return model

    def test_legacy_columns_are_widened_in_one_statement_per_table(self):
        """Both columns of a table are widened by a single ALTER TABLE (one rewrite per table)."""
        model = self._create_legacy_fields()

        statements = self._run_migration()

        self.assertEqual(len(statements), 1, statements)
        self.assertEqual(_column_type(self.table_name, "price"), "numeric(16,4)")
        self.assertEqual(_column_type(self.table_name, "tags"), "character varying(100)[]")

        # Values that only fit the widened types round-trip.
        instance = model.objects.create(name="Test", price=Decimal("123456789012.3456"), tags=["c" * 100])
        instance.refresh_from_db()
        self.assertEqual(instance.price, Decimal("123456789012.3456"))
        self.assertEqual(instance.tags, ["c" * 100])

    def test_rerun_and_fresh_install_are_no_ops(self):
        """Columns already at the new types are skipped, so the migration is idempotent."""
        self._create_legacy_fields()
        self._run_migration()

        self.assertEqual(self._run_migration(), [])

    def test_columns_already_wider_than_the_target_are_not_narrowed(self):
        """Only the exact legacy types are altered, so a manually widened column is left alone."""
        self._create_legacy_fields()
        _alter_column_type(self.table_name, "price", "numeric(20,6)")
        _alter_column_type(self.table_name, "tags", "varchar(200)[]")

        self.assertEqual(self._run_migration(), [])
        self.assertEqual(_column_type(self.table_name, "price"), "numeric(20,6)")
        self.assertEqual(_column_type(self.table_name, "tags"), "character varying(200)[]")

    def test_other_field_types_and_missing_columns_are_untouched(self):
        """Only decimal and multi-select columns are altered; a field without a column is skipped."""
        self.create_custom_object_type_field(self.custom_object_type, name="count", label="Count", type="integer")
        self.create_custom_object_type_field(
            self.custom_object_type, name="color", label="Color", type="select", choice_set=self.choice_set,
        )
        self.custom_object_type.get_model(no_cache=True)
        CustomObjectTypeField.objects.create(
            custom_object_type=self.custom_object_type, name="ghost", label="Ghost", type="decimal",
        )
        with connection.cursor() as cursor:
            cursor.execute(f'ALTER TABLE "{self.table_name}" DROP COLUMN "ghost"')

        self.assertEqual(self._run_migration(), [])
        self.assertEqual(_column_type(self.table_name, "count"), "bigint")
        self.assertEqual(_column_type(self.table_name, "color"), "character varying(100)")


class CoordinatesFieldTypeTestCase(FieldTypeTestCase):
    """Test cases for the coordinates field type."""

    def test_coordinates_field_creation(self):
        """A coordinates field is created with the expected type."""
        field = self.create_custom_object_type_field(
            self.custom_object_type,
            name="location",
            label="Location",
            type="coordinates",
        )
        self.assertEqual(field.type, "coordinates")

    def test_coordinates_field_model_generation(self):
        """One coordinates field expands into two backing decimal columns."""
        self.create_custom_object_type_field(
            self.custom_object_type,
            name="location",
            label="Location",
            type="coordinates",
        )

        model = self.custom_object_type.get_model()
        column_names = {f.name for f in model._meta.local_fields}
        self.assertIn("location_latitude", column_names)
        self.assertIn("location_longitude", column_names)
        # The logical field name itself is not a real column.
        self.assertNotIn("location", column_names)

        # Verify precision/validators mirror NetBox core's Site coordinates.
        latitude = model._meta.get_field("location_latitude")
        longitude = model._meta.get_field("location_longitude")
        self.assertEqual((latitude.max_digits, latitude.decimal_places), (8, 6))
        self.assertEqual((longitude.max_digits, longitude.decimal_places), (9, 6))

        instance = model.objects.create(
            name="Test",
            location_latitude=Decimal("40.712800"),
            location_longitude=Decimal("-74.006000"),
        )
        self.assertEqual(instance.location_latitude, Decimal("40.712800"))
        self.assertEqual(instance.location_longitude, Decimal("-74.006000"))

    def test_coordinates_display_value(self):
        """get_display_value combines both columns, or returns None when unset."""
        self.create_custom_object_type_field(
            self.custom_object_type,
            name="location",
            label="Location",
            type="coordinates",
        )
        model = self.custom_object_type.get_model()
        field_type = CoordinatesFieldType()

        populated = model.objects.create(
            name="A",
            location_latitude=Decimal("40.712800"),
            location_longitude=Decimal("-74.006000"),
        )
        self.assertEqual(
            field_type.get_display_value(populated, "location"),
            "40.712800, -74.006000",
        )

        empty = model.objects.create(name="B")
        self.assertIsNone(field_type.get_display_value(empty, "location"))

    def test_coordinates_validate_pair(self):
        """Latitude and longitude must both be set or both be empty."""
        field_type = CoordinatesFieldType()
        # Both set / both empty are valid.
        field_type.validate_pair(Decimal("1.0"), Decimal("2.0"))
        field_type.validate_pair(None, None)
        # Half-populated pairs are rejected.
        with self.assertRaises(ValidationError):
            field_type.validate_pair(Decimal("1.0"), None)
        with self.assertRaises(ValidationError):
            field_type.validate_pair(None, Decimal("2.0"))

    def test_coordinates_field_rejects_unique(self):
        """A coordinates field cannot be marked unique."""
        field = CustomObjectTypeField(
            custom_object_type=self.custom_object_type,
            name="location",
            label="Location",
            type="coordinates",
            unique=True,
        )
        with self.assertRaises(ValidationError):
            field.full_clean()

    def test_coordinates_field_search_weight_forced_to_zero(self):
        """
        A coordinates field has no column matching its name, so it can never be
        indexed for search; clean() forces search_weight to 0 rather than honour a
        non-zero value that would silently have no effect.
        """
        field = CustomObjectTypeField(
            custom_object_type=self.custom_object_type,
            name="location",
            label="Location",
            type="coordinates",
            search_weight=500,
        )
        field.full_clean()
        self.assertEqual(field.search_weight, 0)

    def test_change_existing_field_to_coordinates_rejected(self):
        """An existing non-coordinates field cannot be converted to coordinates."""
        field = self.create_custom_object_type_field(
            self.custom_object_type, name="location", label="Location", type="text",
        )
        field.type = "coordinates"
        with self.assertRaises(ValidationError):
            field.full_clean()

    def test_change_coordinates_field_to_other_type_rejected(self):
        """An existing coordinates field cannot be converted to another type."""
        field = self.create_custom_object_type_field(
            self.custom_object_type, name="location", label="Location", type="coordinates",
        )
        field.type = "text"
        with self.assertRaises(ValidationError):
            field.full_clean()

    def test_coordinates_backing_column_collision_rejected(self):
        """
        Adding a coordinates field whose backing column collides with an existing
        field's column raises a ValidationError rather than a DB error.
        """
        self.create_custom_object_type_field(
            self.custom_object_type, name="location_latitude", label="Lat", type="text",
        )
        field = CustomObjectTypeField(
            custom_object_type=self.custom_object_type,
            name="location",
            label="Location",
            type="coordinates",
        )
        with self.assertRaises(ValidationError):
            field.full_clean()

    def test_field_colliding_with_coordinates_backing_column_rejected(self):
        """
        The reverse collision: a plain field named "<coord>_longitude" cannot be
        added when a coordinates field "<coord>" already exists.
        """
        self.create_custom_object_type_field(
            self.custom_object_type, name="location", label="Location", type="coordinates",
        )
        field = CustomObjectTypeField(
            custom_object_type=self.custom_object_type,
            name="location_longitude",
            label="Lon",
            type="text",
        )
        with self.assertRaises(ValidationError):
            field.full_clean()


class ArrayFieldTypeTestCase(FieldTypeTestCase):
    """Test cases for the array field type."""

    def _array_field(self, name="points", **kwargs):
        kwargs = {"array_element_type": "integer", **kwargs}
        return self.create_custom_object_type_field(
            self.custom_object_type, name=name, label=name.replace("_", " ").capitalize(), type="array", **kwargs
        )

    def _model(self):
        self.custom_object_type.refresh_from_db()
        return self.custom_object_type.get_model()

    def _clean(self, model, **values):
        instance = model(name="Test", **values)
        instance.full_clean()
        return instance

    def test_flat_and_tuple_arrays_round_trip(self):
        self._array_field(name="readings")
        self._array_field(name="points", array_tuple_size=3, array_min_length=2)
        self._array_field(name="days", array_element_type="date")
        model = self._model()

        # Form, API and CSV input arrive as lists of strings and numbers; each item is converted.
        instance = self._clean(
            model, readings=[1, "2", 3], points=[[0, 0, 0], [10, 0, 5]], days=["2026-01-01", "2026-02-01"],
        )
        instance.save()
        instance.refresh_from_db()
        self.assertEqual(instance.readings, [1, 2, 3])
        self.assertEqual(instance.points, [[0, 0, 0], [10, 0, 5]])
        self.assertEqual(instance.days, [date(2026, 1, 1), date(2026, 2, 1)])

    def test_serialized_round_trip(self):
        """Serialized values (as netbox-branching applies them from the changelog) load back."""
        self._array_field(name="readings")
        self._array_field(name="points", array_tuple_size=3)
        self._array_field(name="days", array_element_type="date")
        model = self._model()
        instance = model.objects.create(
            name="Test", readings=[1, 2], points=[[0, 0, 0], [10, 0, 5]], days=[date(2026, 1, 1)],
        )
        data = instance.serialize_object()
        self.assertIsInstance(data["points"], str)

        # Update: assigned as serialized, then cleaned.
        instance.points = data["points"]
        instance.full_clean()
        self.assertEqual(instance.points, [[0, 0, 0], [10, 0, 5]])

        # Create.
        pk = instance.pk
        instance.delete()
        deserialized = model.deserialize_object(data, pk=pk)
        deserialized.object.full_clean()
        deserialized.save()
        restored = model.objects.get(pk=pk)
        self.assertEqual(restored.readings, [1, 2])
        self.assertEqual(restored.points, [[0, 0, 0], [10, 0, 5]])
        self.assertEqual(restored.days, [date(2026, 1, 1)])

    def test_deferred_replay(self):
        """Serialized values replay once the field exists (a squash merge applying an object first)."""
        model = self._model()
        instance = model.objects.create(name="Test")
        # As Django serializes them: a tuple array is a list of JSON strings.
        data = {"readings": json.dumps(["1", "2"]), "points": json.dumps([json.dumps(["0", "1"])])}
        _deferred_co_field_data.set({self.custom_object_type.get_database_table_name(): {instance.pk: {"data": data}}})
        self._array_field(name="readings")
        self._array_field(name="points", array_tuple_size=2)
        restored = self._model().objects.get(pk=instance.pk)
        self.assertEqual(restored.readings, [1, 2])
        self.assertEqual(restored.points, [[0, 1]])

    def test_value_validation(self):
        self._array_field(name="points", array_tuple_size=3, array_min_length=2, array_max_length=3)
        self._array_field(name="readings", validation_minimum=0, validation_maximum=100)
        self._array_field(name="codes", array_element_type="text", validation_regex="^[A-Z]+$")
        self._array_field(name="amounts", array_element_type="decimal")
        self._array_field(name="days", array_element_type="date")
        self._array_field(name="labels", array_element_type="text")
        model = self._model()
        valid = {"points": [[0, 0, 0], [1, 1, 1]]}
        self._clean(model, **valid)
        self._clean(model, **valid, readings=[0, 100], codes=["AB"], amounts=["1.50", 2])
        cases = {
            "tuple too short": {"points": [[0, 0, 0], [1, 1]]},
            "tuple too long": {"points": [[0, 0, 0], [1, 1, 1, 1]]},
            "not a tuple": {"points": [[0, 0, 0], 1]},
            "too few items": {"points": [[0, 0, 0]]},
            "too many items": {"points": [[0, 0, 0]] * 4},
            "not a list": {"points": {"x": 1}},
            "not an integer": {**valid, "readings": ["many"]},
            "below the minimum": {**valid, "readings": [-1]},
            "above the maximum": {**valid, "readings": [101]},
            "not matching the regex": {**valid, "codes": ["ab"]},
            "not a decimal": {**valid, "amounts": ["x"]},
            "a JSON string": {**valid, "readings": '"abc"'},
            "not JSON": {**valid, "readings": "abc"},
            "a number on dates": {**valid, "days": [1]},
            # Rejected rather than coerced, as the API does.
            "a fraction on integers": {**valid, "readings": [1.5]},
            "a boolean on integers": {**valid, "readings": [True]},
            "a boolean on decimals": {**valid, "amounts": [True]},
            "a list on text": {**valid, "labels": [[1]]},
        }
        for label, values in cases.items():
            with self.subTest(label):
                with self.assertRaises(ValidationError):
                    self._clean(model, **values)

        # Errors name the item, and the value within a tuple.
        messages = {
            "Item 2 must have exactly 3 values.": {"points": [[0, 0, 0], [1, 1]]},
            "Item 2, value 3: “x” value must be an integer.": {"points": [[0, 0, 0], [1, 1, "x"]]},
            "Item 2: Ensure this value is less than or equal to 100.": {**valid, "readings": [0, 101]},
            "Item 1: “1” value has an invalid date format.": {**valid, "days": [1]},
            "Enter a list of values.": {**valid, "readings": '"abc"'},
        }
        for message, values in messages.items():
            with self.subTest(message):
                with self.assertRaises(ValidationError) as cm:
                    self._clean(model, **values)
                self.assertIn(message, " ".join(cm.exception.messages))

    def test_display_and_table_formatting(self):
        self._array_field(name="readings")
        self._array_field(name="points", array_tuple_size=3)
        model = self._model()
        instance = model.objects.create(name="Test", readings=[1, 2, 3], points=[[0, 0, 0], [10, 0, 5]])
        field_type = ArrayFieldType()
        self.assertEqual(field_type.get_display_value(instance, "readings"), "1, 2, 3")
        self.assertEqual(field_type.get_display_value(instance, "points"), "(0, 0, 0), (10, 0, 5)")
        column = field_type.get_table_column_field(self.custom_object_type.fields.get(name="points"))
        self.assertEqual(column.render([[1, 2, 3]]), "(1, 2, 3)")
        self.assertEqual(column.render([]), column.default)

    def test_form_field(self):
        points = self._array_field(name="points", array_tuple_size=3, array_min_length=2)
        days = self._array_field(name="days", array_element_type="date")
        points_field = ArrayFieldType().get_form_field(points)
        days_field = ArrayFieldType().get_form_field(days)

        # The help text's example matches the element type and tuple size.
        self.assertIn("<code>[[0, 0, 0], [10, 10, 10]]</code>", points_field.help_text)
        self.assertIn('<code>["2026-01-15", "2026-07-01"]</code>', days_field.help_text)

        # Values are shown compactly: one tuple per line, or a flat array on one line.
        self.assertEqual(points_field.prepare_value([[0, 0, 0], [10, 0, 5]]), "[\n    [0, 0, 0],\n    [10, 0, 5]\n]")
        self.assertEqual(points_field.prepare_value("[[0, 0, 0], [10, 0, 5]]"), "[\n    [0, 0, 0],\n    [10, 0, 5]\n]")
        self.assertEqual(days_field.prepare_value([date(2026, 1, 1)]), '["2026-01-01"]')
        self.assertEqual(days_field.prepare_value(None), "")

    def test_graphql_annotation(self):
        flat = self._array_field(name="readings", array_element_type="decimal")
        points = self._array_field(name="points", array_tuple_size=3)
        self.assertEqual(ArrayFieldType().get_graphql_annotation(flat), list[Decimal])
        self.assertEqual(ArrayFieldType().get_graphql_annotation(points), list[list[int]])

    def test_default(self):
        self._array_field(name="readings", default=[1, 2])
        model = self._model()
        first, second = model(name="A"), model(name="B")
        self.assertEqual(first.readings, [1, 2])
        # Each object gets its own copy of the default.
        first.readings.append(3)
        self.assertEqual(second.readings, [1, 2])

        invalid = {
            "not an integer": ("integer", ["x"]),
            "a string": ("integer", "abc"),
            "a number on dates": ("date", [1]),
        }
        for label, (element_type, default) in invalid.items():
            with self.subTest(label):
                field = CustomObjectTypeField(
                    custom_object_type=self.custom_object_type, name="bad", type="array",
                    array_element_type=element_type, default=default,
                )
                with self.assertRaises(ValidationError) as cm:
                    field.full_clean()
                self.assertIn("default", cm.exception.message_dict)

    def test_empty_list_is_no_value(self):
        self._array_field(name="readings", array_min_length=2)
        self._array_field(name="required_readings", required=True)
        model = self._model()
        instance = model.objects.create(name="Test", readings=[], required_readings=[1])
        instance.refresh_from_db()
        self.assertIsNone(instance.readings)

        # The minimum length applies to a non-empty list; a required field rejects an empty one.
        self._clean(model, readings=[], required_readings=[1])
        with self.assertRaises(ValidationError) as cm:
            self._clean(model, required_readings=[])
        self.assertIn("required_readings", cm.exception.message_dict)

    def test_search_values(self):
        self._array_field(name="days", array_element_type="date", search_weight=100)
        self._array_field(name="amounts", array_element_type="decimal", search_weight=100)
        model = self._model()
        instance = model.objects.create(
            name="Test", days=[date(2026, 1, 1), date(2026, 7, 1)], amounts=[Decimal("1.50")],
        )
        # Cached as displayed, not as the list's repr.
        values = {value.name: value.value for value in get_indexer(model).to_cache(instance)}
        self.assertEqual((values["days"], values["amounts"]), ("2026-01-01, 2026-07-01", "1.50"))

    def test_field_definition_validation(self):
        cases = {
            "no element type": ({"type": "array"}, "array_element_type"),
            "tuple size of one": ({"type": "array", "array_element_type": "integer", "array_tuple_size": 1},
                                  "array_tuple_size"),
            "zero minimum length": ({"type": "array", "array_element_type": "integer", "array_min_length": 0},
                                    "array_min_length"),
            "zero maximum length": ({"type": "array", "array_element_type": "integer", "array_max_length": 0},
                                    "array_max_length"),
            "minimum above maximum": ({"type": "array", "array_element_type": "integer", "array_min_length": 3,
                                       "array_max_length": 2}, "array_max_length"),
            "numeric limits on text elements": ({"type": "array", "array_element_type": "text",
                                                 "validation_minimum": 1}, "validation_minimum"),
            "regex on integer elements": ({"type": "array", "array_element_type": "integer",
                                           "validation_regex": "^1$"}, "validation_regex"),
            "array settings on a text field": ({"type": "text", "array_element_type": "integer"},
                                               "array_element_type"),
            "length on an integer field": ({"type": "integer", "array_max_length": 2}, "array_max_length"),
        }
        for label, (kwargs, key) in cases.items():
            with self.subTest(label):
                field = CustomObjectTypeField(custom_object_type=self.custom_object_type, name="values", **kwargs)
                with self.assertRaises(ValidationError) as cm:
                    field.full_clean()
                self.assertIn(key, cm.exception.message_dict)

        # Element validation that fits the element type is allowed.
        CustomObjectTypeField(
            custom_object_type=self.custom_object_type, name="codes", type="array",
            array_element_type="text", validation_regex="^[A-Z]+$",
        ).full_clean()
        CustomObjectTypeField(
            custom_object_type=self.custom_object_type, name="readings", type="array",
            array_element_type="decimal", validation_minimum=0, validation_maximum=10,
        ).full_clean()

    def test_shape_and_type_are_fixed_after_creation(self):
        changes = {
            "element type": ("array_element_type", "text"),
            "tuple size": ("array_tuple_size", 2),
            "type": ("type", "text"),
        }
        for label, (attr, value) in changes.items():
            with self.subTest(label):
                field = self._array_field(name="points", array_tuple_size=3)
                field = CustomObjectTypeField.objects.get(pk=field.pk)
                setattr(field, attr, value)
                if attr == "type":
                    field.array_element_type, field.array_tuple_size = "", None
                with self.assertRaises(ValidationError) as cm:
                    field.full_clean()
                self.assertIn(attr, cm.exception.message_dict)
                field.delete()

        # The length limits can change.
        field = CustomObjectTypeField.objects.get(pk=self._array_field(name="points", array_tuple_size=3).pk)
        field.array_max_length = 10
        field.full_clean()

        text = self.create_custom_object_type_field(self.custom_object_type, name="label", type="text")
        text.type, text.array_element_type = "array", "text"
        with self.assertRaises(ValidationError) as cm:
            text.full_clean()
        self.assertIn("type", cm.exception.message_dict)

    def test_instances_are_picklable(self):
        import pickle

        self._array_field(name="points", array_tuple_size=3)
        model = self._model()
        instance = model.objects.create(name="Test", points=[[1, 2, 3]])
        self.assertEqual(pickle.loads(pickle.dumps(instance)).points, [[1, 2, 3]])


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
        """
        Test URL field model generation. A url field expands into two backing
        columns: the URL itself and an optional link title.
        """
        self.create_custom_object_type_field(
            self.custom_object_type,
            name="website",
            label="Website",
            type="url"
        )

        model = self.custom_object_type.get_model()
        column_names = {f.name for f in model._meta.local_fields}
        self.assertIn("website", column_names)
        self.assertIn("website_title", column_names)

        instance = model.objects.create(
            name="Test", website="https://example.com", website_title="Example Site",
        )
        self.assertEqual(instance.website, "https://example.com")
        self.assertEqual(instance.website_title, "Example Site")

    def test_url_field_title_is_optional(self):
        """
        A url value with no title set is valid (no both-required rule). The title
        column defaults to '' (not None) so "unset" has one canonical representation.
        """
        self.create_custom_object_type_field(
            self.custom_object_type,
            name="website",
            label="Website",
            type="url",
        )
        model = self.custom_object_type.get_model()
        instance = model.objects.create(name="Test", website="https://example.com/")
        self.assertEqual(instance.website, "https://example.com/")
        self.assertEqual(instance.website_title, "")

    def test_url_field_unique_still_enforced(self):
        """
        Unique still applies to the URL column itself for a url field (unlike
        coordinates, which disallows unique entirely) -- guards the fix making the
        clean() uniqueness-conversion probe dict-aware.
        """
        self.create_custom_object_type_field(
            self.custom_object_type,
            name="website",
            label="Website",
            type="url",
            unique=True,
        )
        model = self.custom_object_type.get_model()
        model.objects.create(name="A", website="https://example.com/a")
        with self.assertRaises(ValidationError):
            duplicate = model(name="B", website="https://example.com/a")
            duplicate.full_clean()

    def test_change_existing_field_to_url_rejected(self):
        """An existing non-url field cannot be converted to url."""
        field = self.create_custom_object_type_field(
            self.custom_object_type, name="website2", label="Website", type="text",
        )
        field.type = "url"
        with self.assertRaises(ValidationError):
            field.full_clean()

    def test_change_url_field_to_other_type_rejected(self):
        """An existing url field cannot be converted to another type."""
        field = self.create_custom_object_type_field(
            self.custom_object_type, name="website2", label="Website", type="url",
        )
        field.type = "text"
        with self.assertRaises(ValidationError):
            field.full_clean()

    def test_url_backing_column_collision_rejected(self):
        """
        Adding a url field whose title column collides with an existing field's
        column raises a ValidationError rather than a DB error.
        """
        self.create_custom_object_type_field(
            self.custom_object_type, name="website2_title", label="Title", type="text",
        )
        field = CustomObjectTypeField(
            custom_object_type=self.custom_object_type,
            name="website2",
            label="Website",
            type="url",
        )
        with self.assertRaises(ValidationError):
            field.full_clean()

    def test_field_colliding_with_url_backing_column_rejected(self):
        """
        The reverse collision: a plain field named "<url>_title" cannot be added
        when a url field "<url>" already exists.
        """
        self.create_custom_object_type_field(
            self.custom_object_type, name="website2", label="Website", type="url",
        )
        field = CustomObjectTypeField(
            custom_object_type=self.custom_object_type,
            name="website2_title",
            label="Title",
            type="text",
        )
        with self.assertRaises(ValidationError):
            field.full_clean()

    def test_get_url_field_html_renders_link_with_title(self):
        """get_url_field_html renders <a href> using the title as link text when set."""
        from netbox_custom_objects.templatetags.custom_object_utils import get_url_field_html
        cotf = self.create_custom_object_type_field(
            self.custom_object_type, name="website3", label="Website", type="url",
        )
        model = self.custom_object_type.get_model(no_cache=True)
        instance = model.objects.create(
            name="Test", website3="https://example.com/", website3_title="Example Site",
        )
        html = get_url_field_html(instance, cotf)
        self.assertIn('href="https://example.com/"', html)
        self.assertIn("Example Site", html)

    def test_get_url_field_html_falls_back_to_url_text_without_title(self):
        """get_url_field_html uses the URL itself as link text when no title is set."""
        from netbox_custom_objects.templatetags.custom_object_utils import get_url_field_html
        cotf = self.create_custom_object_type_field(
            self.custom_object_type, name="website4", label="Website", type="url",
        )
        model = self.custom_object_type.get_model(no_cache=True)
        instance = model.objects.create(name="Test", website4="https://example.com/")
        html = get_url_field_html(instance, cotf)
        self.assertIn('href="https://example.com/"', html)
        self.assertIn(">https://example.com/<", html)

    def test_get_url_field_html_rejects_disallowed_scheme(self):
        """
        A disallowed URL scheme (e.g. javascript:) renders as plain text, not a link.
        Guards the security-relevant _url_scheme_is_allowed() check -- the value is
        assigned directly (bypassing form/URLField validation) to simulate data that
        reached the column via any path other than the add/edit form.
        """
        from netbox_custom_objects.templatetags.custom_object_utils import get_url_field_html
        cotf = self.create_custom_object_type_field(
            self.custom_object_type, name="website5", label="Website", type="url",
        )
        model = self.custom_object_type.get_model(no_cache=True)
        instance = model.objects.create(
            name="Test", website5="javascript:alert(1)", website5_title="Click me",
        )
        html = get_url_field_html(instance, cotf)
        self.assertNotIn("<a ", html)
        self.assertIn("Click me", html)

    def test_get_url_field_html_returns_empty_string_when_url_unset(self):
        """get_url_field_html returns '' when the URL itself is unset, regardless of title."""
        from netbox_custom_objects.templatetags.custom_object_utils import get_url_field_html
        cotf = self.create_custom_object_type_field(
            self.custom_object_type, name="website6", label="Website", type="url",
        )
        model = self.custom_object_type.get_model(no_cache=True)
        instance = model.objects.create(name="Test")
        self.assertEqual(get_url_field_html(instance, cotf), "")


class ColorFieldTypeTestCase(FieldTypeTestCase):
    """Test cases for color field type."""

    def test_model_field_validates_hex_color(self):
        self.create_custom_object_type_field(self.custom_object_type, name="shade", type="color")
        model = self.custom_object_type.get_model()
        self.assertEqual(model._meta.get_field("shade").max_length, 6)

        model(name="Valid", shade="00ff00").full_clean()
        model(name="Blank").full_clean()
        for value in ("00FF00", "#00ff00", "green", "0f0"):
            with self.subTest(value=value), self.assertRaises(ValidationError) as cm:
                model(name="Invalid", shade=value).full_clean()
            self.assertIn("shade", cm.exception.message_dict)

    def test_validate(self):
        field = self.create_custom_object_type_field(self.custom_object_type, name="shade", type="color")
        field.validate("aa1100")
        for value in ("AA1100", "#aa1100", 123):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                field.validate(value)

    def test_default_must_be_a_color(self):
        # The field form's JSON input yields a str subclass.
        for default in ("red", JSONString("red")):
            field = CustomObjectTypeField(
                custom_object_type=self.custom_object_type, name="shade", type="color", default=default
            )
            with self.subTest(default=type(default)), self.assertRaises(ValidationError) as cm:
                field.full_clean()
            self.assertIn("valid hexadecimal RGB color", cm.exception.message_dict["default"][0])
        field.default = JSONString("ff0000")
        field.full_clean()

        field = self.create_custom_object_type_field(
            self.custom_object_type, name="shade", type="color", default="ff0000"
        )
        model = self.custom_object_type.get_model()
        self.assertEqual(model.objects.create(name="Defaulted").shade, "ff0000")

    def test_unique(self):
        self.create_custom_object_type_field(self.custom_object_type, name="shade", type="color", unique=True)
        model = self.custom_object_type.get_model()
        model.objects.create(name="First", shade="0000ff")
        with self.assertRaises(ValidationError):
            model(name="Second", shade="0000ff").full_clean()

    def test_form_field_keeps_a_custom_color(self):
        field = self.create_custom_object_type_field(self.custom_object_type, name="shade", type="color")
        form_field = ColorFieldType().get_form_field(field)
        named = form_field.widget.render("shade", "00ffff")
        custom = form_field.widget.render("shade", "123456")
        self.assertRegex(named, r'value="00ffff"\s+selected')
        self.assertEqual(named.count('value="00ffff"'), 1)
        self.assertRegex(custom, r'value="123456"\s+selected')

    def test_csv_form_field_accepts_exported_values(self):
        field = self.create_custom_object_type_field(self.custom_object_type, name="shade", type="color")
        form_field = ColorFieldType().get_form_field(field, for_csv_import=True)
        self.assertEqual(form_field.clean("#00FF00"), "00ff00")
        self.assertEqual(form_field.clean("abcdef"), "abcdef")
        with self.assertRaises(ValidationError):
            form_field.clean("#xy")

    def test_table_column(self):
        field = self.create_custom_object_type_field(self.custom_object_type, name="shade", type="color")
        column = ColorFieldType().get_table_column_field(field)
        # NetBox's own ColorColumn: a bare swatch, exported as #rrggbb.
        self.assertIsInstance(column, ColorColumn)
        self.assertEqual(
            column.render("ff9800"), '<span class="color-label" style="background-color: #ff9800">&nbsp;</span>',
        )
        self.assertEqual(column.value("ff8800"), "#ff8800")


class MACAddressFieldTypeTestCase(FieldTypeTestCase):
    """Test cases for MAC address field type."""

    FORMATS = ("00:1a:2b:3c:4d:5e", "00-1A-2B-3C-4D-5E", "001a.2b3c.4d5e", "001a2b3c4d5e")

    def test_model_field_normalizes_formats(self):
        self.create_custom_object_type_field(self.custom_object_type, name="mac", type="mac_address")
        model = self.custom_object_type.get_model()
        for value in self.FORMATS:
            with self.subTest(value=value):
                obj = model.objects.create(name=value, mac=value)
                obj.refresh_from_db()
                self.assertEqual(str(obj.mac), "00:1A:2B:3C:4D:5E")
        model(name="Blank").full_clean()
        for value in ("00:1a:2b:3c:4d", "not a mac", "00:1a:2b:3c:4d:5e:6f:70"):
            with self.subTest(value=value), self.assertRaises(ValidationError) as cm:
                model(name="Invalid", mac=value).full_clean()
            self.assertIn("mac", cm.exception.message_dict)

    def test_validate(self):
        field = self.create_custom_object_type_field(self.custom_object_type, name="mac", type="mac_address")
        field.validate("00-1a-2b-3c-4d-5e")
        for value in ("00:1a:2b:3c:4d", 123):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                field.validate(value)

    def test_default(self):
        field = CustomObjectTypeField(
            custom_object_type=self.custom_object_type, name="mac", type="mac_address", default="nope"
        )
        with self.assertRaises(ValidationError) as cm:
            field.full_clean()
        self.assertIn("default", cm.exception.message_dict)

        field = CustomObjectTypeField(
            custom_object_type=self.custom_object_type, name="mac", type="mac_address", default="001a.2b3c.4d5e"
        )
        field.full_clean()
        self.assertEqual(field.default, "00:1A:2B:3C:4D:5E")
        field.save()
        model = self.custom_object_type.get_model()
        self.assertEqual(str(model.objects.create(name="Defaulted").mac), "00:1A:2B:3C:4D:5E")

    def test_unique_across_formats(self):
        self.create_custom_object_type_field(self.custom_object_type, name="mac", type="mac_address", unique=True)
        model = self.custom_object_type.get_model()
        model.objects.create(name="First", mac="00:1a:2b:3c:4d:5e")
        with self.assertRaises(ValidationError):
            model(name="Second", mac="001A2B3C4D5E").full_clean()

    def test_form_field(self):
        field = self.create_custom_object_type_field(self.custom_object_type, name="mac", type="mac_address")
        form_field = MACAddressFieldType().get_form_field(field)
        self.assertEqual(form_field.clean("001a.2b3c.4d5e"), "00:1A:2B:3C:4D:5E")
        self.assertIsNone(form_field.clean(""))
        with self.assertRaises(ValidationError):
            form_field.clean("00:1a")


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
        field.choice_set = None
        column = SelectFieldType().get_table_column_field(field)
        self.assertEqual(column.render(value='choice1'), 'Choice 1')
        self.assertEqual(column.render(value='choice2'), 'Choice 2')

    def test_select_column_render_unknown_key_falls_back_to_raw_value(self):
        """get_table_column_field() render() returns the raw key when it is not in choices."""
        field = Mock()
        field.choices = [('choice1', 'Choice 1')]
        field.choice_set = None
        column = SelectFieldType().get_table_column_field(field)
        self.assertEqual(column.render(value='unknown'), 'unknown')

    def test_select_column_render_returns_badge_when_color_present(self):
        """get_table_column_field() render() returns an HTML badge when the choice has a color."""
        field = Mock()
        field.choices = [('choice1', 'Choice 1')]
        field.choice_set = Mock()
        field.choice_set.get_choice_color.return_value = 'green'
        column = SelectFieldType().get_table_column_field(field)
        result = column.render(value='choice1')
        self.assertIn('class="badge', result)
        self.assertIn('text-bg-green', result)
        self.assertIn('Choice 1', result)

    def test_get_field_value_returns_raw_value_for_select(self):
        """get_field_value template filter returns the raw stored value for select fields."""
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
        self.assertEqual(get_field_value(instance, cotf), "choice1")

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

    def test_get_choice_label_returns_label_for_known_value(self):
        """get_choice_label() returns the human-readable label for a stored choice value."""
        cotf = self.create_custom_object_type_field(
            self.custom_object_type,
            name="status",
            label="Status",
            type="select",
            choice_set=self.choice_set,
        )
        self.assertEqual(cotf.get_choice_label("choice1"), "Choice 1")

    def test_get_choice_label_falls_back_to_value_when_unknown(self):
        """get_choice_label() returns the raw value when it is not in the choice set."""
        cotf = self.create_custom_object_type_field(
            self.custom_object_type,
            name="status",
            label="Status",
            type="select",
            choice_set=self.choice_set,
        )
        self.assertEqual(cotf.get_choice_label("unknown"), "unknown")

    # COMPAT(netbox<4.6.0): CustomFieldChoiceSet has no choice_colors.
    @skipUnless(hasattr(CustomFieldChoiceSet, "get_choice_color"), "NetBox < 4.6.0 has no choice colors")
    def test_get_choice_color_returns_color_when_set(self):
        """get_choice_color() returns the color configured for a choice value."""
        from extras.models import CustomFieldChoiceSet
        choice_set = CustomFieldChoiceSet.objects.create(
            name="Colored Choices",
            extra_choices=[["active", "Active"], ["inactive", "Inactive"]],
            choice_colors={"active": "green", "inactive": "red"},
        )
        cotf = self.create_custom_object_type_field(
            self.custom_object_type,
            name="state",
            label="State",
            type="select",
            choice_set=choice_set,
        )
        self.assertEqual(cotf.get_choice_color("active"), "green")
        self.assertEqual(cotf.get_choice_color("inactive"), "red")

    def test_get_choice_color_returns_none_when_no_color(self):
        """get_choice_color() returns None for a value with no configured color."""
        cotf = self.create_custom_object_type_field(
            self.custom_object_type,
            name="status",
            label="Status",
            type="select",
            choice_set=self.choice_set,
        )
        self.assertIsNone(cotf.get_choice_color("choice1"))


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

    def test_multiselect_stores_choice_values_as_long_as_select_does(self):
        """A choice value may be 100 characters, like a select column."""
        long_value = "v" * 100
        choice_set = self.create_choice_set(name="Long values", extra_choices=[[long_value, "Long"]])
        self.create_custom_object_type_field(
            self.custom_object_type, name="tags", label="Tags", type="multiselect", choice_set=choice_set,
        )
        model = self.custom_object_type.get_model(no_cache=True)
        self.assertEqual(model._meta.get_field("tags").base_field.max_length, 100)

        instance = model(name="Test", tags=[long_value])
        instance.full_clean()
        instance.save()
        instance.refresh_from_db()
        self.assertEqual(instance.tags, [long_value])

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
        field.choice_set = None
        column = MultiSelectFieldType().get_table_column_field(field)
        self.assertEqual(column.render(value=['choice1', 'choice3']), 'Choice 1, Choice 3')

    def test_multiselect_column_render_unknown_key_falls_back_to_raw_value(self):
        """get_table_column_field() render() preserves unknown keys in the joined output."""
        field = Mock()
        field.choices = [('choice1', 'Choice 1')]
        field.choice_set = None
        column = MultiSelectFieldType().get_table_column_field(field)
        self.assertEqual(column.render(value=['choice1', 'unknown']), 'Choice 1, unknown')

    def test_multiselect_column_render_returns_badges_when_colors_present(self):
        """get_table_column_field() render() returns HTML badges when choices have colors."""
        field = Mock()
        field.choices = [('choice1', 'Choice 1'), ('choice3', 'Choice 3')]
        field.choice_set = Mock()
        field.choice_set.get_choice_color.side_effect = lambda v: 'green' if v == 'choice1' else 'red'
        column = MultiSelectFieldType().get_table_column_field(field)
        result = column.render(value=['choice1', 'choice3'])
        self.assertIn('text-bg-green', result)
        self.assertIn('text-bg-red', result)
        self.assertIn('Choice 1', result)
        self.assertIn('Choice 3', result)

    def test_multiselect_column_render_mixed_colors(self):
        """Colorless values get a plain badge when at least one sibling has a color."""
        field = Mock()
        field.choices = [('choice1', 'Choice 1'), ('choice2', 'Choice 2')]
        field.choice_set = Mock()
        # choice1 has a color; choice2 does not
        field.choice_set.get_choice_color.side_effect = lambda v: 'blue' if v == 'choice1' else None
        column = MultiSelectFieldType().get_table_column_field(field)
        result = column.render(value=['choice1', 'choice2'])
        self.assertIn('text-bg-blue', result)
        self.assertIn('Choice 1', result)
        # choice2 should still be a badge (no color class), not plain text
        self.assertIn('class="badge"', result)
        self.assertIn('Choice 2', result)
        # result is HTML, not a comma-joined string
        self.assertNotIn('Choice 1, Choice 2', result)

    def test_get_field_value_returns_raw_list_for_multiselect(self):
        """get_field_value template filter returns the raw stored list for multiselect fields."""
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
        self.assertEqual(get_field_value(instance, cotf), ["choice1", "choice3"])

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

    def test_csv_import_field_uses_name_for_models_with_name(self):
        """get_form_field(for_csv_import=True) uses 'name' as to_field_name for models
        that have a name field (e.g. Device)."""
        self.assertEqual(_csv_import_to_field_name(Device), 'name')

    def test_csv_import_field_uses_model_for_module_type(self):
        """get_form_field(for_csv_import=True) uses 'model' as to_field_name for
        ModuleType, which has no 'name' field."""
        self.assertEqual(_csv_import_to_field_name(ModuleType), 'model')

    def test_csv_import_field_uses_slug_before_model_for_device_type(self):
        """DeviceType has both 'slug' and 'model'; slug takes precedence per the
        _CSV_IDENTIFIER_FIELD_PRECEDENCE ordering, matching NetBox core behaviour."""
        self.assertEqual(_csv_import_to_field_name(DeviceType), 'slug')

    def test_csv_import_form_field_for_module_type_uses_model_field(self):
        """get_form_field(for_csv_import=True) on an Object field referencing ModuleType
        produces a CSVModelChoiceField with to_field_name='model', not 'name'."""
        module_type_ot = ObjectType.objects.get(app_label='dcim', model='moduletype')
        cotf = self.create_custom_object_type_field(
            self.custom_object_type,
            name="module_type",
            label="Module Type",
            type="object",
            related_object_type=module_type_ot,
        )
        form_field = ObjectFieldType().get_form_field(cotf, for_csv_import=True)
        self.assertEqual(form_field.to_field_name, 'model',
                         "ModuleType CSV import must use 'model', not 'name'")

    def test_csv_import_to_field_name_explicit_returned_when_valid(self):
        """An explicit to_field_name that exists on the model is returned directly."""
        self.assertEqual(_csv_import_to_field_name(Device, explicit='id'), 'id')

    def test_csv_import_to_field_name_explicit_falls_through_when_stale(self):
        """A stale explicit to_field_name (field no longer exists) triggers a warning
        and falls through to the probe loop."""
        import logging
        with self.assertLogs('netbox_custom_objects.field_types', level=logging.WARNING):
            result = _csv_import_to_field_name(Device, explicit='nonexistent_field_xyz')
        # Falls through to probe loop — Device has 'name'
        self.assertEqual(result, 'name')

    def test_csv_import_to_field_name_pk_fallback_when_no_candidate_exists(self):
        """Falls back to 'pk' and emits a warning when none of the candidate fields
        exist on the model."""
        import logging
        from unittest.mock import MagicMock
        mock_model = MagicMock()
        mock_model.__name__ = 'FakeModel'
        mock_model._meta.get_field.side_effect = FieldDoesNotExist
        with self.assertLogs('netbox_custom_objects.field_types', level=logging.WARNING):
            result = _csv_import_to_field_name(mock_model)
        self.assertEqual(result, 'pk')


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

    def test_csv_import_form_field_for_module_type_uses_model_field(self):
        """MultiObjectFieldType.get_form_field(for_csv_import=True) on a field
        referencing ModuleType produces to_field_name='model', not 'name'."""
        module_type_ot = ObjectType.objects.get(app_label='dcim', model='moduletype')
        cotf = self.create_custom_object_type_field(
            self.custom_object_type,
            name="module_types",
            label="Module Types",
            type="multiobject",
            related_object_type=module_type_ot,
        )
        form_field = MultiObjectFieldType().get_form_field(cotf, for_csv_import=True)
        self.assertEqual(form_field.to_field_name, 'model',
                         "ModuleType multi-object CSV import must use 'model', not 'name'")

    def test_get_form_field_for_cot_target_uses_full_cached_model(self):
        """The form field must resolve a COT target with plain
        get_model() so the target's object fields are present and the generated
        model lands in the cache, rather than building an uncached partial model
        on every form render."""
        target = self.create_custom_object_type(name="FormTarget776", slug="form-target-776")
        self.create_custom_object_type_field(
            target, name="name", label="Name", type="text", primary=True,
        )
        self.create_custom_object_type_field(
            target, name="device", label="Device", type="object",
            related_object_type=self.device_object_type,
        )
        cotf = self.create_custom_object_type_field(
            self.custom_object_type, name="targets", label="Targets", type="multiobject",
            related_object_type=target.object_type,
        )

        CustomObjectType.clear_model_cache(target.id)
        self.assertFalse(CustomObjectType.is_model_cached(target.id))

        form_field = MultiObjectFieldType().get_form_field(cotf)
        model = form_field.queryset.model

        self.assertIn("device", {f.name for f in model._meta.local_fields})
        self.assertTrue(CustomObjectType.is_model_cached(target.id))
        self.assertIs(model, target.get_model())

    def test_change_multiobject_field_to_object_rejected(self):
        """An existing multiobject field cannot be converted to a scalar type."""
        field = self.create_custom_object_type_field(
            self.custom_object_type, name="devices", label="Devices",
            type="multiobject", related_object_type=self.device_object_type,
        )
        field = CustomObjectTypeField.objects.get(pk=field.pk)
        field.type = "object"
        with self.assertRaises(ValidationError) as cm:
            field.full_clean()
        self.assertIn("type", cm.exception.message_dict)

    def test_change_object_field_to_multiobject_rejected(self):
        """An existing object field cannot be converted to multiobject."""
        field = self.create_custom_object_type_field(
            self.custom_object_type, name="device", label="Device",
            type="object", related_object_type=self.device_object_type,
        )
        field = CustomObjectTypeField.objects.get(pk=field.pk)
        field.type = "multiobject"
        with self.assertRaises(ValidationError) as cm:
            field.full_clean()
        self.assertIn("type", cm.exception.message_dict)

    def _create_device(self, name):
        from dcim.models import DeviceRole, Manufacturer, Site

        site, _ = Site.objects.get_or_create(name="Test Site", slug="test-site")
        manufacturer, _ = Manufacturer.objects.get_or_create(name="Test Manufacturer", slug="test-manufacturer")
        device_type, _ = DeviceType.objects.get_or_create(
            manufacturer=manufacturer, model="Test Model", slug="test-model",
        )
        role, _ = DeviceRole.objects.get_or_create(name="Test Role", slug="test-role")
        return Device.objects.create(name=name, site=site, device_type=device_type, role=role)

    def test_change_object_field_to_multiobject_save_without_clean_raises(self):
        """save() refuses the conversion even when clean() is bypassed, leaving
        the stored type, the schema and existing data untouched."""
        field = self.create_custom_object_type_field(
            self.custom_object_type, name="device", label="Device",
            type="object", related_object_type=self.device_object_type,
        )
        device = self._create_device("Device 1")
        instance = self.custom_object_type.get_model().objects.create(name="Test", device=device)

        field = CustomObjectTypeField.objects.get(pk=field.pk)
        field.type = "multiobject"
        with self.assertRaises(ValidationError):
            field.save()

        field.refresh_from_db()
        self.assertEqual(field.type, "object")
        # The FK column and its value survive on the unchanged table.
        self.custom_object_type.clear_model_cache(self.custom_object_type.id)
        model = self.custom_object_type.get_model()
        self.assertEqual(model.objects.get(pk=instance.pk).device, device)

    def test_change_multiobject_field_to_object_save_without_clean_raises(self):
        """save() refuses the reverse conversion even when clean() is bypassed,
        leaving the stored type, the through table and existing data untouched."""
        field = self.create_custom_object_type_field(
            self.custom_object_type, name="devices", label="Devices",
            type="multiobject", related_object_type=self.device_object_type,
        )
        device1 = self._create_device("Device 1")
        device2 = self._create_device("Device 2")
        instance = self.custom_object_type.get_model().objects.create(name="Test")
        instance.devices.add(device1, device2)

        field = CustomObjectTypeField.objects.get(pk=field.pk)
        field.type = "object"
        with self.assertRaises(ValidationError):
            field.save()

        field.refresh_from_db()
        self.assertEqual(field.type, "multiobject")
        # The through table and its rows survive.
        self.custom_object_type.clear_model_cache(self.custom_object_type.id)
        model = self.custom_object_type.get_model()
        self.assertEqual(set(model.objects.get(pk=instance.pk).devices.all()), {device1, device2})


class PrimaryFieldChangeTestCase(FieldTypeTestCase):
    """Test changing which field is designated as the primary (display) field.

    Switching the primary flag to a different field while object
    references exist must not break __str__ or queryset access.
    """

    def test_change_primary_field_updates_str(self):
        """__str__ on existing instances reflects the new primary field after change."""
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
        """Changing the primary field does not break object fields that reference the COT."""
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

    def test_str_post_delete_no_primary_field_does_not_contain_none(self):
        """str() on a post-delete instance (pk=None, no primary
        field value) must return the COT display name, not '<type> None'."""
        cot = self.create_custom_object_type(
            name="DeleteStrTest", slug="delete-str-test",
            verbose_name_plural="Delete Str Tests",
        )
        # No primary field — falls through to the pk fallback path in __str__.
        model = cot.get_model()
        instance = model.objects.create()

        instance.delete()  # sets instance.pk = None

        result = str(instance)
        self.assertNotIn("None", result)
        self.assertEqual(result, cot.display_name)


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
