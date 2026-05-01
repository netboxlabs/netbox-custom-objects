"""
Tests for the concrete and dynamically generated models that are managed by this plugin.
"""
from unittest import skip
from unittest.mock import patch
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone


from django.contrib.contenttypes.models import ContentType
from extras.models import CachedValue
from netbox.search.backends import get_backend
from netbox_custom_objects.jobs import ReindexCustomObjectTypeJob
from netbox_custom_objects.models import CustomObjectTypeField
from core.models import ObjectType
from netbox_custom_objects.utilities import extract_cot_id_from_model_name
from .base import CustomObjectsTestCase


class ExtractCotIdFromModelNameTestCase(TestCase):
    """Unit tests for extract_cot_id_from_model_name()."""

    def test_valid_names_return_id_string(self):
        self.assertEqual(extract_cot_id_from_model_name("table1model"), "1")
        self.assertEqual(extract_cot_id_from_model_name("table42model"), "42")
        self.assertEqual(extract_cot_id_from_model_name("table999model"), "999")

    def test_returns_none_for_missing_prefix(self):
        # No leading "table"
        self.assertIsNone(extract_cot_id_from_model_name("42model"))

    def test_returns_none_for_missing_suffix(self):
        # No trailing "model"
        self.assertIsNone(extract_cot_id_from_model_name("table42"))

    def test_returns_none_for_non_digit_id(self):
        self.assertIsNone(extract_cot_id_from_model_name("tableabcmodel"))

    def test_returns_none_for_substring_match(self):
        # "table" and "model" present as substrings but wrong structure
        self.assertIsNone(extract_cot_id_from_model_name("sometablemodel"))
        self.assertIsNone(extract_cot_id_from_model_name("table_model"))
        self.assertIsNone(extract_cot_id_from_model_name("table42modelextra"))

    def test_returns_none_for_empty_string(self):
        self.assertIsNone(extract_cot_id_from_model_name(""))

    def test_case_sensitive(self):
        # The regex is anchored and lowercase-only; uppercase should not match
        self.assertIsNone(extract_cot_id_from_model_name("Table42Model"))
        self.assertIsNone(extract_cot_id_from_model_name("TABLE42MODEL"))


class CustomObjectTypeTestCase(CustomObjectsTestCase, TestCase):
    """Test cases for CustomObjectType model."""

    def test_custom_object_type_creation(self):
        """Test creating a CustomObjectType."""
        custom_object_type = self.create_custom_object_type(
            name="TestObject",
            description="A test custom object type",
            verbose_name_plural="Test Objects",
            slug="test-objects",
        )

        self.assertEqual(custom_object_type.name, "TestObject")
        self.assertEqual(custom_object_type.description, "A test custom object type")
        self.assertEqual(custom_object_type.verbose_name_plural, "Test Objects")
        self.assertEqual(custom_object_type.slug, "test-objects")
        self.assertEqual(str(custom_object_type), "TestObject")

    def test_custom_object_type_name_validation(self):
        """COT name must match the schema identifier pattern (no leading/trailing/double underscores)."""
        from netbox_custom_objects.models import CustomObjectType
        invalid_names = [
            "test-type",    # hyphen not allowed
            "test__type",   # double underscore not allowed
            "_test_type",   # leading underscore not allowed
            "test_type_",   # trailing underscore not allowed
        ]
        for invalid_name in invalid_names:
            with self.assertRaises(ValidationError, msg=f"Expected ValidationError for name={invalid_name!r}"):
                cot = CustomObjectType(name=invalid_name, slug=f"slug-{invalid_name}")
                cot.full_clean()

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
            kwargs={"custom_object_type": custom_object_type.slug}
        )
        self.assertEqual(custom_object_type.get_list_url(), expected_url)

    def test_custom_object_type_get_model_without_fields(self):
        """Test get_model method when no fields are defined."""
        custom_object_type = self.create_custom_object_type(name="TestObject")

        model = custom_object_type.get_model()
        # Base fields: id, created, last_updated
        self.assertEqual(len(model._meta.fields), 3)

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

    def test_register_search_index_skips_object_field_absent_from_stub_model(self):
        """register_custom_object_search_index() must use local_fields/local_many_to_many
        rather than _meta.get_field() to check field presence.  _meta.get_field() for a
        name not in _forward_fields_map triggers Django's lazy _relation_tree computation,
        which calls apps.get_models() → our override → get_model() for every COT →
        infinite recursion when called during model registration.

        Regression for PR #474: the stub model generated with skip_object_fields=True
        does not have the OBJECT field, but self.fields.filter(search_weight__gt=0)
        still returns it from the database.
        """
        cot = self.create_custom_object_type(name="StubSearchTest", slug="stub-search-test")
        self.create_custom_object_type_field(
            cot, name="name", label="Name", type="text", primary=True, search_weight=1000,
        )
        self.create_custom_object_type_field(
            cot, name="ref_site", label="Site", type="object",
            related_object_type=self.get_site_object_type(),
            search_weight=500,
        )
        # CustomObjectTypeField.save() now caches a full model after each field
        # save (to defend against a rename/post_save race), so the cache holds a
        # full model here.  Clear it to force generation of a fresh stub.
        cot.clear_model_cache(cot.id)
        stub_model = cot.get_model(skip_object_fields=True)
        model_field_names = (
            {f.name for f in stub_model._meta.local_fields}
            | {f.name for f in stub_model._meta.local_many_to_many}
        )
        self.assertNotIn("ref_site", model_field_names,
                         "OBJECT field must be absent from stub model")
        # Must not raise FieldDoesNotExist, RecursionError, or any other exception.
        cot.register_custom_object_search_index(stub_model)

    @skip("Fails in suite but not individually")
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

    def test_delete_unregisters_model_from_app_registry(self):
        """
        Regression test for: deleting a Custom Object then its Custom Object Type
        leaves the dynamically-generated model in Django's app registry.  When a
        related model (e.g. dcim.Device) is subsequently deleted, Django's ORM
        Collector discovers the stale model class, tries to query the dropped table
        and raises "relation '<table>' does not exist".
        """
        from django.apps import apps as django_apps
        from netbox_custom_objects.constants import APP_LABEL

        custom_object_type = self.create_custom_object_type(
            name="RegTestObject", slug="reg-test-object"
        )
        self.create_custom_object_type_field(
            custom_object_type,
            name="name",
            label="Name",
            type="text",
            primary=True,
            required=True,
        )
        model = custom_object_type.get_model()
        model_name = model.__name__.lower()

        # Confirm the model is registered before deletion
        self.assertIn(model_name, django_apps.all_models.get(APP_LABEL, {}))

        custom_object_type.delete()

        # After deletion the model must be removed from the app registry so that
        # Django's cascade-delete collector no longer tries to query the dropped table.
        self.assertNotIn(model_name, django_apps.all_models.get(APP_LABEL, {}))

    def test_stale_registry_entry_causes_relation_error_on_related_object_delete(self):
        """
        Demonstrates the failure mode for issue #429.

        Re-registers the stale model class after deletion (simulating the pre-fix
        state) and confirms that deleting a related NetBox object then raises
        ProgrammingError('relation "custom_objects_<id>" does not exist').

        The test uses a savepoint so the aborted PostgreSQL transaction does not
        poison the surrounding test transaction.
        """
        from django.apps import apps as django_apps
        from django.db import ProgrammingError, transaction
        from dcim.models import Site
        from netbox_custom_objects.constants import APP_LABEL

        site = Site.objects.create(name='Stale Registry Test Site', slug='stale-registry-test-site')

        cot = self.create_custom_object_type(name='SiteLinked', slug='site-linked')
        self.create_custom_object_type_field(
            cot,
            name='linked_site',
            label='Linked Site',
            type='object',
            related_object_type=self.get_site_object_type(),
        )

        stale_model = cot.get_model()
        stale_model_name = stale_model.__name__.lower()

        # Delete the COT; the fix removes the model from apps.all_models.
        cot.delete()
        self.assertNotIn(stale_model_name, django_apps.all_models.get(APP_LABEL, {}))

        # Simulate pre-fix state: put the stale model back in the registry and
        # force Django to rebuild its relation trees from the now-stale registry.
        django_apps.all_models[APP_LABEL][stale_model_name] = stale_model
        django_apps.clear_cache()

        try:
            # Deleting the site now triggers Django's cascade-delete Collector,
            # which finds the stale FK, queries the dropped table, and fails.
            sid = transaction.savepoint()
            try:
                site.delete()
                transaction.savepoint_commit(sid)
                self.fail('Expected ProgrammingError was not raised — the bug is not being reproduced')
            except ProgrammingError as exc:
                transaction.savepoint_rollback(sid)
                self.assertIn('does not exist', str(exc))
        finally:
            # Restore clean registry state so subsequent tests are unaffected.
            django_apps.all_models[APP_LABEL].pop(stale_model_name, None)
            django_apps.clear_cache()


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
        invalid_names = [
            "test-field",   # hyphen not allowed
            "test__field",  # double underscore not allowed
            "_test_field",  # leading underscore not allowed
            "test_field_",  # trailing underscore not allowed
        ]
        for invalid_name in invalid_names:
            with self.assertRaises(ValidationError, msg=f"Expected ValidationError for name={invalid_name!r}"):
                field = CustomObjectTypeField(
                    custom_object_type=self.custom_object_type,
                    name=invalid_name,
                    type="text",
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
        device_ct = self.get_device_object_type()
        with self.assertRaises(ValidationError):
            field = CustomObjectTypeField(
                custom_object_type=self.custom_object_type,
                name="test_field",
                type="text",
                related_object_type=device_ct
            )
            field.full_clean()

    def test_custom_object_type_field_get_absolute_url(self):
        """
        Test get_absolute_url method.
        Note: get_absolute_url for CustomObjectTypeField returns the absolute_url of the COT, because fields
        are not exposed individually in the UI or API.
        """
        field = self.create_custom_object_type_field(
            self.custom_object_type,
            name="test_field",
            type="text"
        )
        expected_url = reverse(
            "plugins:netbox_custom_objects:customobjecttype", args=[field.custom_object_type.pk]
        )
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
        cls.custom_object_type = cls.create_custom_object_type(name="TestObject", slug="test-objects")
        cls.cot_1_model_name = (
            cls.custom_object_type.get_table_model_name(cls.custom_object_type.id).lower()
        )
        first_object_ct = ObjectType.objects.get(app_label='netbox_custom_objects', model=cls.cot_1_model_name)
        cls.second_custom_object_type = cls.create_custom_object_type(name="TestObject2", slug="test-objects2")
        cls.cot_2_model_name = (
            cls.second_custom_object_type.get_table_model_name(cls.second_custom_object_type.id).lower()
        )
        second_object_ct = ObjectType.objects.get(app_label='netbox_custom_objects', model=cls.cot_2_model_name)

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

        site_ct = cls.get_site_object_type()
        cls.create_custom_object_type_field(
            cls.custom_object_type,
            name="site",
            label="Single site",
            type="object",
            related_object_type=site_ct,
        )

        cls.create_custom_object_type_field(
            cls.custom_object_type,
            name="sites",
            label="Sites",
            type="multiobject",
            related_object_type=site_ct,
        )

        # Custom Object single- and multi-object fields

        cls.create_custom_object_type_field(
            cls.custom_object_type,
            name="second_object_single",
            label="Second Object Single",
            type="object",
            related_object_type=second_object_ct,
        )

        cls.create_custom_object_type_field(
            cls.custom_object_type,
            name="second_object_single_2",
            label="Second Object Single 2",
            type="object",
            related_object_type=second_object_ct,
        )

        cls.create_custom_object_type_field(
            cls.custom_object_type,
            name="second_object_multi",
            label="Second Object Multi",
            type="multiobject",
            related_object_type=second_object_ct,
        )

        cls.create_custom_object_type_field(
            cls.custom_object_type,
            name="second_object_multi_2",
            label="Second Object Multi 2",
            type="multiobject",
            related_object_type=second_object_ct,
        )

        # Self-referential

        cls.create_custom_object_type_field(
            cls.custom_object_type,
            name="self_ref_single",
            label="Self Ref Single",
            type="object",
            related_object_type=first_object_ct,
        )

        cls.create_custom_object_type_field(
            cls.custom_object_type,
            name="self_ref_multi",
            label="Self Ref Multi",
            type="multiobject",
            related_object_type=first_object_ct,
        )

        # Get the dynamic model
        cls.model = cls.custom_object_type.get_model()

    def setUp(self):
        """Set up test data that should be reset between tests."""
        super().setUp()
        # Any test-specific setup can go here

    def test_custom_object_creation(self):
        """Test creating a custom object instance."""
        now = timezone.now()
        site_ct = self.get_site_object_type()
        site = site_ct.model_class().objects.create()
        first_object_1 = self.model.objects.create()
        first_object_2 = self.model.objects.create()
        second_object_model = self.second_custom_object_type.get_model()
        second_object_1 = second_object_model.objects.create()
        second_object_2 = second_object_model.objects.create()

        instance = self.model.objects.create(
            name="Test Instance",
            description="A test instance",
            count=50,
            price=Decimal("10.50"),
            is_active=True,
            created_on=now,
            created_at=now,
            url="http://example.com",
            data={"foo": "bar"},
            country="US",
            countries=["US", "AU"],
            site=site,
            second_object_single=second_object_1,
            second_object_single_2=second_object_2,
            self_ref_single=first_object_1,
        )
        instance.sites.add(site)
        instance.second_object_multi.add(second_object_1)
        instance.second_object_multi.add(second_object_2)
        instance.second_object_multi_2.add(second_object_1)
        instance.self_ref_multi.add(first_object_1)
        instance.self_ref_multi.add(first_object_2)

        self.assertEqual(instance.name, "Test Instance")
        self.assertEqual(instance.description, "A test instance")
        self.assertEqual(instance.count, 50)
        self.assertEqual(instance.price, Decimal("10.50"))
        self.assertEqual(instance.is_active, True)
        self.assertEqual(instance.created_on.date(), now.date())
        self.assertEqual(instance.created_at, now)
        self.assertEqual(instance.url, "http://example.com")
        self.assertEqual(instance.data, {"foo": "bar"})
        self.assertEqual(instance.country, "US")
        self.assertEqual(instance.countries, ["US", "AU"])
        self.assertEqual(instance.site, site)
        self.assertEqual(instance.sites.all().count(), 1)
        self.assertIn(site, instance.sites.all())
        self.assertEqual(str(instance), "Test Instance")
        # Object Fields pointing to Custom Objects
        self.assertEqual(instance.second_object_single, second_object_1)
        self.assertEqual(instance.second_object_single_2, second_object_2)
        self.assertEqual(instance.second_object_multi.count(), 2)
        self.assertEqual(instance.second_object_multi_2.count(), 1)
        # Self-referential Object Fields
        self.assertEqual(instance.self_ref_single, first_object_1)
        self.assertEqual(instance.self_ref_multi.count(), 2)

    def test_custom_object_get_absolute_url(self):
        """Test get_absolute_url method for custom objects."""
        instance = self.model.objects.create(name="Test Instance")
        expected_url = reverse(
            "plugins:netbox_custom_objects:customobject",
            kwargs={
                "custom_object_type": self.custom_object_type.slug,
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


class RelatedNameTestCase(CustomObjectsTestCase, TestCase):
    """Tests for the related_name field on Object and MultiObject fields."""

    def setUp(self):
        super().setUp()
        # "SLB" is the target (reverse side); "Certificate" holds the forward relation.
        self.slb_cot = self.create_custom_object_type(name="SLB", slug="slb")
        self.create_custom_object_type_field(
            self.slb_cot, name="name", label="Name", type="text", primary=True
        )
        self.slb_object_type = ObjectType.objects.get(
            app_label="netbox_custom_objects",
            model=self.slb_cot.get_table_model_name(self.slb_cot.id).lower(),
        )

        self.cert_cot = self.create_custom_object_type(name="Certificate", slug="certificate")
        self.create_custom_object_type_field(
            self.cert_cot, name="name", label="Name", type="text", primary=True
        )

    # ------------------------------------------------------------------ #
    # Validation                                                           #
    # ------------------------------------------------------------------ #

    def test_related_name_rejected_on_non_object_field(self):
        """related_name cannot be set on non-object field types."""
        for field_type in ("text", "integer", "boolean", "date"):
            with self.subTest(field_type=field_type):
                field = CustomObjectTypeField(
                    custom_object_type=self.cert_cot,
                    name="some_field",
                    type=field_type,
                    related_name="my_reverse",
                )
                with self.assertRaises(ValidationError) as cm:
                    field.full_clean()
                self.assertIn("related_name", cm.exception.message_dict)

    def test_related_name_invalid_characters_rejected(self):
        """related_name must contain only lowercase alphanumeric characters and underscores."""
        for bad_value in ("My-Name", "has space", "UPPER", "has--double", "has__double"):
            with self.subTest(value=bad_value):
                field = CustomObjectTypeField(
                    custom_object_type=self.cert_cot,
                    name="slb",
                    type="object",
                    related_object_type=self.slb_object_type,
                    related_name=bad_value,
                )
                with self.assertRaises(ValidationError):
                    field.full_clean()

    def test_duplicate_related_name_same_target_rejected(self):
        """Two fields with the same related_name pointing at the same related_object_type raise ValidationError."""
        self.create_custom_object_type_field(
            self.cert_cot,
            name="slb",
            type="object",
            related_object_type=self.slb_object_type,
            related_name="certificates",
        )
        field = CustomObjectTypeField(
            custom_object_type=self.cert_cot,
            name="slb2",
            type="object",
            related_object_type=self.slb_object_type,
            related_name="certificates",
        )
        with self.assertRaises(ValidationError) as cm:
            field.full_clean()
        self.assertIn("related_name", cm.exception.message_dict)

    def test_same_related_name_different_targets_allowed(self):
        """The same related_name is allowed when the related_object_type differs."""
        site_ct = self.get_site_object_type()
        self.create_custom_object_type_field(
            self.cert_cot,
            name="slb",
            type="object",
            related_object_type=self.slb_object_type,
            related_name="certificates",
        )
        # Same related_name but targeting a different model — should not raise.
        field = CustomObjectTypeField(
            custom_object_type=self.cert_cot,
            name="site",
            type="object",
            related_object_type=site_ct,
            related_name="certificates",
        )
        field.full_clean()  # Should not raise.

    def test_blank_related_name_allows_multiple_fields_same_target(self):
        """Multiple fields with no related_name targeting the same object type are allowed."""
        self.create_custom_object_type_field(
            self.cert_cot,
            name="slb",
            type="object",
            related_object_type=self.slb_object_type,
        )
        field = CustomObjectTypeField(
            custom_object_type=self.cert_cot,
            name="slb2",
            type="object",
            related_object_type=self.slb_object_type,
        )
        field.full_clean()  # blank related_name is excluded from the uniqueness constraint.

    # ------------------------------------------------------------------ #
    # Object (FK) reverse accessor                                        #
    # ------------------------------------------------------------------ #

    def test_object_field_with_related_name_creates_reverse_accessor(self):
        """A named reverse accessor is available on the related model after an Object field is saved."""
        self.create_custom_object_type_field(
            self.cert_cot,
            name="slb",
            type="object",
            related_object_type=self.slb_object_type,
            related_name="certificates",
        )
        # Generate Certificate's model so it contributes the FK (and its reverse) to SLB's class.
        self.cert_cot.get_model()
        slb_model = self.slb_cot.get_model()
        self.assertTrue(
            hasattr(slb_model, "certificates"),
            "Expected reverse accessor 'certificates' on SLB model.",
        )

    def test_object_field_reverse_accessor_returns_correct_objects(self):
        """The reverse FK manager returns only the Certificate instances that reference a given SLB."""
        self.create_custom_object_type_field(
            self.cert_cot,
            name="slb",
            type="object",
            related_object_type=self.slb_object_type,
            related_name="certificates",
        )
        cert_model = self.cert_cot.get_model()
        slb_model = self.slb_cot.get_model()

        slb_a = slb_model.objects.create(name="SLB-A")
        slb_b = slb_model.objects.create(name="SLB-B")
        cert_1 = cert_model.objects.create(name="Cert-1", slb=slb_a)
        cert_2 = cert_model.objects.create(name="Cert-2", slb=slb_a)
        cert_model.objects.create(name="Cert-3", slb=slb_b)

        result = list(slb_a.certificates.all())
        self.assertIn(cert_1, result)
        self.assertIn(cert_2, result)
        self.assertEqual(len(result), 2)
        self.assertEqual(slb_b.certificates.count(), 1)

    def test_object_field_without_related_name_uses_auto_generated_name(self):
        """Without related_name, the auto-generated accessor follows the {table}_{field}_set convention."""
        self.create_custom_object_type_field(
            self.cert_cot,
            name="slb",
            type="object",
            related_object_type=self.slb_object_type,
        )
        self.cert_cot.get_model()
        slb_model = self.slb_cot.get_model()

        table_model_name = self.cert_cot.get_table_model_name(self.cert_cot.id).lower()
        expected_accessor = f"{table_model_name}_slb_set"
        self.assertTrue(
            hasattr(slb_model, expected_accessor),
            f"Expected auto-generated reverse accessor '{expected_accessor}' on SLB model.",
        )

    # ------------------------------------------------------------------ #
    # MultiObject (M2M) reverse accessor                                  #
    # ------------------------------------------------------------------ #

    def test_multiobject_field_with_related_name_creates_reverse_manager(self):
        """A named reverse manager is available on the related model after a MultiObject field is saved."""
        self.create_custom_object_type_field(
            self.cert_cot,
            name="slbs",
            type="multiobject",
            related_object_type=self.slb_object_type,
            related_name="certificates",
        )
        self.cert_cot.get_model()
        slb_model = self.slb_cot.get_model()
        self.assertTrue(
            hasattr(slb_model, "certificates"),
            "Expected reverse manager 'certificates' on SLB model.",
        )

    def test_multiobject_field_reverse_manager_returns_correct_objects(self):
        """The reverse M2M manager returns only the Certificate instances linked to a given SLB."""
        self.create_custom_object_type_field(
            self.cert_cot,
            name="slbs",
            type="multiobject",
            related_object_type=self.slb_object_type,
            related_name="certificates",
        )
        cert_model = self.cert_cot.get_model()
        slb_model = self.slb_cot.get_model()

        slb_a = slb_model.objects.create(name="SLB-A")
        slb_b = slb_model.objects.create(name="SLB-B")
        cert_1 = cert_model.objects.create(name="Cert-1")
        cert_2 = cert_model.objects.create(name="Cert-2")
        cert_1.slbs.add(slb_a)
        cert_2.slbs.add(slb_a, slb_b)

        result = list(slb_a.certificates.all())
        self.assertIn(cert_1, result)
        self.assertIn(cert_2, result)
        self.assertEqual(len(result), 2)
        self.assertEqual(slb_b.certificates.count(), 1)
        self.assertIn(cert_2, slb_b.certificates.all())

    def test_multiobject_field_without_related_name_has_no_reverse_accessor(self):
        """Without related_name, a MultiObject field has no reverse accessor on the related model."""
        self.create_custom_object_type_field(
            self.cert_cot,
            name="slbs",
            type="multiobject",
            related_object_type=self.slb_object_type,
        )
        self.cert_cot.get_model()
        slb_model = self.slb_cot.get_model()

        # No user-defined or auto-generated reverse accessor should exist.
        table_model_name = self.cert_cot.get_table_model_name(self.cert_cot.id).lower()
        self.assertFalse(
            hasattr(slb_model, "slbs"),
            "MultiObject field without related_name should not create a reverse accessor.",
        )
        self.assertFalse(
            hasattr(slb_model, f"{table_model_name}_slbs_set"),
            "MultiObject field without related_name should not create an auto-generated reverse accessor.",
        )


class SearchReindexTestCase(CustomObjectsTestCase, TestCase):
    """Test that ReindexCustomObjectTypeJob is triggered when field search weights change."""

    def setUp(self):
        super().setUp()
        self.cot = self.create_custom_object_type(
            name="ReindexTest",
            slug="reindex-test",
        )
        self.field = self.create_custom_object_type_field(
            self.cot,
            name="title",
            label="Title",
            type="text",
            search_weight=500,
        )
        self.model = self.cot.get_model()
        self.instance = self.model.objects.create(title="Hello World")

    def test_job_enqueued_on_field_weight_change(self):
        """Changing search_weight on a field enqueues a reindex job after the transaction commits."""
        field = CustomObjectTypeField.objects.get(pk=self.field.pk)
        field.search_weight = 100
        with patch.object(ReindexCustomObjectTypeJob, 'enqueue') as mock_enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                field.save()
        mock_enqueue.assert_called_once_with(cot_id=self.cot.pk)

    def test_job_enqueued_on_field_weight_zeroed(self):
        """Changing search_weight to 0 enqueues a reindex job after the transaction commits."""
        field = CustomObjectTypeField.objects.get(pk=self.field.pk)
        field.search_weight = 0
        with patch.object(ReindexCustomObjectTypeJob, 'enqueue') as mock_enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                field.save()
        mock_enqueue.assert_called_once_with(cot_id=self.cot.pk)

    def test_job_not_enqueued_when_weight_unchanged(self):
        """Saving a field without changing search_weight does not enqueue a reindex job."""
        field = CustomObjectTypeField.objects.get(pk=self.field.pk)
        field.label = "Modified Title"
        with patch.object(ReindexCustomObjectTypeJob, 'enqueue') as mock_enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                field.save()
        mock_enqueue.assert_not_called()

    def test_job_enqueued_on_searchable_field_creation(self):
        """Adding a new field with search_weight > 0 enqueues a reindex job after the transaction commits."""
        with patch.object(ReindexCustomObjectTypeJob, 'enqueue') as mock_enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                new_field = self.create_custom_object_type_field(
                    self.cot,
                    name="subtitle",
                    label="Subtitle",
                    type="text",
                    search_weight=300,
                )
        self.addCleanup(new_field.delete)
        mock_enqueue.assert_called_once_with(cot_id=self.cot.pk)

    def test_job_not_enqueued_on_non_searchable_field_creation(self):
        """Adding a field with search_weight=0 does not enqueue a reindex job."""
        with patch.object(ReindexCustomObjectTypeJob, 'enqueue') as mock_enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                non_search_field = self.create_custom_object_type_field(
                    self.cot,
                    name="notes",
                    label="Notes",
                    type="text",
                    search_weight=0,
                )
        self.addCleanup(non_search_field.delete)
        mock_enqueue.assert_not_called()

    def test_job_enqueued_on_searchable_field_deletion(self):
        """Deleting a field with search_weight > 0 enqueues a reindex job after the transaction commits."""
        with patch.object(ReindexCustomObjectTypeJob, 'enqueue') as mock_enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                self.field.delete()
        mock_enqueue.assert_called_once_with(cot_id=self.cot.pk)

    def test_job_run_updates_cached_values(self):
        """The job's run() method re-caches all objects using the updated SearchIndex."""
        # Prime the cache with the initial weight
        get_backend().cache(self.model.objects.all())
        ct = ContentType.objects.get_for_model(self.model)
        self.assertEqual(
            CachedValue.objects.filter(object_type=ct, object_id=self.instance.pk, field="title", weight=500).count(),
            1,
        )

        # Save the field with a new weight (suppress automatic enqueue)
        field = CustomObjectTypeField.objects.get(pk=self.field.pk)
        field.search_weight = 100
        with patch.object(ReindexCustomObjectTypeJob, 'enqueue'):
            with self.captureOnCommitCallbacks(execute=True):
                field.save()

        # Run the job synchronously via immediate=True
        ReindexCustomObjectTypeJob.enqueue(cot_id=self.cot.pk, immediate=True)

        self.assertEqual(
            CachedValue.objects.filter(object_type=ct, object_id=self.instance.pk, field="title", weight=100).count(),
            1,
        )
        self.assertEqual(
            CachedValue.objects.filter(object_type=ct, object_id=self.instance.pk, field="title", weight=500).count(),
            0,
        )

    def test_job_name_includes_cot_name(self):
        """Enqueued job name includes the COT name for observability."""
        job = ReindexCustomObjectTypeJob.enqueue(cot_id=self.cot.pk, immediate=True)
        self.assertEqual(job.name, f'Reindex Custom Object Type: {self.cot.name}')

    def test_job_data_contains_cot_id(self):
        """Job.data is populated with cot_id and job_class for UI visibility and deduplication."""
        job = ReindexCustomObjectTypeJob.enqueue(cot_id=self.cot.pk, immediate=True)
        self.assertEqual(job.data['cot_id'], self.cot.pk)
        self.assertEqual(job.data['job_class'], 'ReindexCustomObjectTypeJob')

    def test_duplicate_job_not_enqueued(self):
        """A second enqueue for the same COT returns the existing pending job without creating a new one."""
        from core.choices import JobStatusChoices

        with patch('django_rq.get_queue'):
            first_job = ReindexCustomObjectTypeJob.enqueue(cot_id=self.cot.pk)
        # Simulate the first job still pending
        first_job.status = JobStatusChoices.STATUS_PENDING
        first_job.save(update_fields=['status'])

        with patch('django_rq.get_queue'):
            second_job = ReindexCustomObjectTypeJob.enqueue(cot_id=self.cot.pk)

        self.assertEqual(first_job.pk, second_job.pk)


class PluginConfigGetModelTestCase(CustomObjectsTestCase, TestCase):
    """
    Regression tests for CustomObjectsPluginConfig.get_model().

    Covers the bug where get_model() queried the DB unconditionally, causing
    "column does not exist" errors during `manage.py migrate` when a new
    migration added a column to CustomObjectType but hadn't run yet.
    See: https://github.com/netboxlabs/netbox-custom-objects/issues/456
    """

    def setUp(self):
        super().setUp()
        from django.apps import apps
        self.config = apps.get_app_config('netbox_custom_objects')

    def test_get_model_raises_lookup_error_when_skipping(self):
        """get_model() raises LookupError instead of querying DB when should_skip returns True."""
        cot = self.create_custom_object_type(name="MigrateTest", slug="migrate-test")
        model_name = f"{cot.pk}tablemodel"

        with patch.object(self.config.__class__, 'should_skip_dynamic_model_creation', return_value=True):
            with self.assertRaises(LookupError):
                self.config.get_model(model_name)

    def test_get_model_returns_model_when_not_skipping(self):
        """get_model() successfully returns the dynamic model when migrations are up to date."""
        cot = self.create_custom_object_type(name="MigrateTest2", slug="migrate-test-2")
        model_name = f"table{cot.pk}model"

        with patch.object(self.config.__class__, 'should_skip_dynamic_model_creation', return_value=False):
            model = self.config.get_model(model_name)
        self.assertIsNotNone(model)

    def test_get_model_skips_db_with_migrate_in_argv(self):
        """get_model() raises LookupError when 'migrate' is in sys.argv (pre-migration state)."""
        import sys
        cot = self.create_custom_object_type(name="MigrateTest3", slug="migrate-test-3")
        model_name = f"{cot.pk}tablemodel"

        original_argv = sys.argv[:]
        try:
            sys.argv = ['manage.py', 'migrate']
            # Reset cached migration check so argv is re-evaluated
            import netbox_custom_objects as nco
            nco._migrations_checked = None
            with self.assertRaises(LookupError):
                self.config.get_model(model_name)
        finally:
            sys.argv = original_argv
            nco._migrations_checked = None

    def test_get_model_converts_programming_error_to_lookup_error(self):
        """
        Regression: get_model() must not let ProgrammingError escape when the DB
        schema is incomplete (e.g. a new column was added by a migration that
        hasn't run yet).  Reproduces the failure reported in issue #456 where
        upgrading from v0.4.6 to v0.4.7 aborted `manage.py migrate` with
        "column netbox_custom_objects_customobjecttype.group_name does not exist".

        Use a model_name with a non-existent COT ID so the dynamic model is not
        already in the app registry; this ensures we reach the objects.get() call
        that needs to be guarded.
        """
        from django.db.utils import ProgrammingError
        from netbox_custom_objects.models import CustomObjectType

        model_name = "table99998model"  # no such COT — not in the app registry

        with patch.object(self.config.__class__, 'should_skip_dynamic_model_creation', return_value=False):
            with patch.object(CustomObjectType.objects, 'get',
                              side_effect=ProgrammingError("column does not exist")):
                with self.assertRaises(LookupError):
                    self.config.get_model(model_name)

    def test_get_model_converts_operational_error_to_lookup_error(self):
        """
        get_model() must convert OperationalError (e.g. table missing entirely)
        to LookupError for the same reason as ProgrammingError above.
        """
        from django.db.utils import OperationalError
        from netbox_custom_objects.models import CustomObjectType

        model_name = "table99999model"  # no such COT — not in the app registry

        with patch.object(self.config.__class__, 'should_skip_dynamic_model_creation', return_value=False):
            with patch.object(CustomObjectType.objects, 'get',
                              side_effect=OperationalError("no such table")):
                with self.assertRaises(LookupError):
                    self.config.get_model(model_name)

    def test_ready_survives_programming_error(self):
        """
        ready() must not propagate ProgrammingError from an incomplete DB schema.
        Calling ready() a second time is safe — signals are re-connected idempotently
        and the dynamic model loop is the only part that can raise here.
        """
        from django.db.utils import ProgrammingError
        from netbox_custom_objects.models import CustomObjectType

        with patch.object(self.config.__class__, 'should_skip_dynamic_model_creation', return_value=False):
            with patch.object(CustomObjectType.objects, 'all',
                              side_effect=ProgrammingError("column does not exist")):
                # Must not raise — bad schema should be silently skipped.
                self.config.ready()

    def test_ready_survives_operational_error(self):
        """ready() must not propagate OperationalError from a missing table."""
        from django.db.utils import OperationalError
        from netbox_custom_objects.models import CustomObjectType

        with patch.object(self.config.__class__, 'should_skip_dynamic_model_creation', return_value=False):
            with patch.object(CustomObjectType.objects, 'all',
                              side_effect=OperationalError("no such table")):
                self.config.ready()

    def test_get_models_survives_programming_error(self):
        """
        get_models() must not propagate ProgrammingError when the DB schema is
        incomplete.  The DB-driven portion yields nothing; static models already
        in the app registry are still returned via super().get_models().
        """
        from django.db.utils import ProgrammingError
        from netbox_custom_objects.models import CustomObjectType

        with patch.object(self.config.__class__, 'should_skip_dynamic_model_creation', return_value=False):
            with patch.object(CustomObjectType.objects, 'all',
                              side_effect=ProgrammingError("column does not exist")):
                # Consuming the generator must not raise.
                list(self.config.get_models())

    def test_get_models_survives_operational_error(self):
        """get_models() must not propagate OperationalError from a missing table."""
        from django.db.utils import OperationalError
        from netbox_custom_objects.models import CustomObjectType

        with patch.object(self.config.__class__, 'should_skip_dynamic_model_creation', return_value=False):
            with patch.object(CustomObjectType.objects, 'all',
                              side_effect=OperationalError("no such table")):
                list(self.config.get_models())

    def test_should_skip_returns_true_on_programming_error(self):
        """
        should_skip_dynamic_model_creation() must return True (skip) when the
        migration infrastructure raises ProgrammingError, e.g. on a fresh install
        before the django_migrations table exists.  An uncaught exception here
        would bypass all the guards in ready(), get_model(), and get_models().
        """
        import netbox_custom_objects as nco
        from django.db.utils import ProgrammingError

        original_checked = nco._migrations_checked
        nco._migrations_checked = None  # force the migration-loader path
        try:
            with patch('netbox_custom_objects.MigrationLoader',
                       side_effect=ProgrammingError("relation does not exist")):
                result = self.config.should_skip_dynamic_model_creation()
            self.assertTrue(result)
            # Must not be cached so the next call retries once the DB is ready.
            self.assertIsNone(nco._migrations_checked)
        finally:
            nco._migrations_checked = original_checked

    def test_should_skip_returns_true_on_operational_error(self):
        """
        should_skip_dynamic_model_creation() must return True when the migration
        infrastructure raises OperationalError (e.g. django_migrations missing).
        """
        import netbox_custom_objects as nco
        from django.db.utils import OperationalError

        original_checked = nco._migrations_checked
        nco._migrations_checked = None
        try:
            with patch('netbox_custom_objects.MigrationLoader',
                       side_effect=OperationalError("no such table: django_migrations")):
                result = self.config.should_skip_dynamic_model_creation()
            self.assertTrue(result)
            self.assertIsNone(nco._migrations_checked)
        finally:
            nco._migrations_checked = original_checked


class CrossCOTStubSearchIndexRegressionTestCase(CustomObjectsTestCase, TestCase):
    """Regression tests for the search-index crash on stub models.

    When COT A has an object field pointing to COT B, generating A's model
    internally calls ``B.get_model(skip_object_fields=True)`` to break the FK
    recursion.  That stub model is then cached.  Any subsequent call to
    ``B.get_model()`` returns the stub — which lacks the object/multiobject
    fields.

    Before the fix, ``register_custom_object_search_index()`` unconditionally
    included *all* searchable fields in the index, even fields that were absent
    from the stub.  When a B instance was saved, Django's ``post_save`` search
    handler tried to read those absent attributes and raised::

        AttributeError: 'TableNModel' object has no attribute '<field>'

    The fix guards each field with ``model._meta.get_field(field.name)`` and
    skips any field not present on the generated model.

    Reproduces the scenario reported by the user after the Django 6.0 M2M fix
    made the edit view reachable for the first time.
    """

    def setUp(self):
        super().setUp()
        # --- Target COT: has a text primary field AND a searchable object field ---
        self.target_cot = self.create_custom_object_type(
            name="StubTarget",
            slug="stub-target",
        )
        self.create_custom_object_type_field(
            self.target_cot,
            name="name",
            label="Name",
            type="text",
            primary=True,
            required=True,
            search_weight=500,
        )
        # This object field has search_weight=500 (the default).  When the stub
        # model is generated (skip_object_fields=True), this field is ABSENT.
        # The old code would register it in the search index anyway → crash.
        self.create_custom_object_type_field(
            self.target_cot,
            name="device",
            label="Device",
            type="object",
            related_object_type=self.get_device_object_type(),
            search_weight=500,
        )

        # --- Source COT: has an object field pointing to the target COT ---
        self.source_cot = self.create_custom_object_type(
            name="StubSource",
            slug="stub-source",
        )
        self.create_custom_object_type_field(
            self.source_cot,
            name="name",
            label="Name",
            type="text",
            primary=True,
            required=True,
        )
        # Creating this field calls source_cot.get_model(), which internally
        # calls target_cot.get_model(skip_object_fields=True) to resolve the FK.
        # That caches the stub model for target_cot.
        self.create_custom_object_type_field(
            self.source_cot,
            name="target_ref",
            label="Target Reference",
            type="object",
            related_object_type=self.target_cot.object_type,
            search_weight=0,  # not searchable; only target_cot's fields matter here
        )

    def test_save_does_not_crash_when_stub_cached_before_full_model(self):
        """Saving a CO instance must not raise when its COT's stub was cached first.

        This is the exact scenario from the bug report: a user saves an object
        of the target COT whose model was cached as a stub (because another COT
        referenced it as an FK target).  The search ``post_save`` handler must
        not attempt to read object-type fields that are absent from the stub.
        """
        # target_cot.get_model() returns the stub (cached during setUp above).
        # With the old code the registered search index included 'device', which
        # is absent from the stub → AttributeError on save.
        target_model = self.target_cot.get_model()

        # This must not raise.
        instance = target_model.objects.create(name="Stub Target Instance")

        # Basic sanity: the instance was persisted.
        self.assertEqual(target_model.objects.filter(pk=instance.pk).count(), 1)

    def test_stub_model_search_index_excludes_absent_fields(self):
        """The search index registered for a stub model must not reference absent fields.

        When the stub is generated with skip_object_fields=True, object/multiobject
        fields are excluded from the model class.  The search index must only contain
        fields that are actually present.
        """
        from netbox.search import registry

        # Force stub semantics: clear any cached full model and re-register the
        # search index against a fresh stub.  This mirrors the cross-COT scenario
        # the regression covers — B's stub gets cached before any caller asks for
        # the full model, so register_custom_object_search_index sees only the
        # stub fields.  Without this, CustomObjectTypeField.save() would have
        # already cached a full model with the OBJECT field.
        self.target_cot.clear_model_cache(self.target_cot.id)
        stub_model = self.target_cot.get_model(skip_object_fields=True)
        self.target_cot.register_custom_object_search_index(stub_model)

        label = f"netbox_custom_objects.{self.target_cot.get_table_model_name(self.target_cot.id).lower()}"
        search_index = registry["search"].get(label)

        self.assertIsNotNone(search_index, "Search index should be registered for the target COT model")

        # fields is a list of (name, weight) tuples
        indexed_field_names = {f[0] for f in search_index.fields}

        # 'name' (text) is present on the stub → must be indexed.
        self.assertIn("name", indexed_field_names)

        # 'device' (object) is absent from the stub → must NOT be indexed.
        self.assertNotIn(
            "device",
            indexed_field_names,
            "Object-type fields absent from the stub must be excluded from the search index",
        )


# ---------------------------------------------------------------------------
# Semver / version string validation (issue #392)
# ---------------------------------------------------------------------------

class SemverValidationTestCase(CustomObjectsTestCase, TestCase):
    """Validate that version-string fields reject non-PEP-440 values."""

    # ------------------------------------------------------------------
    # CustomObjectType.version
    # ------------------------------------------------------------------

    def test_cot_version_blank_is_valid(self):
        cot = self.create_custom_object_type(name='semver_cot', slug='semver-cot')
        cot.version = ''
        cot.full_clean()  # must not raise

    def test_cot_version_valid_semver(self):
        cot = self.create_custom_object_type(name='semver_cot2', slug='semver-cot-2')
        for v in ('1.0.0', '2.3.4', '0.0.1', '1.0.0.post1', '1.0.0a1'):
            cot.version = v
            cot.full_clean()  # must not raise

    def test_cot_version_invalid_raises_validation_error(self):
        cot = self.create_custom_object_type(name='semver_cot3', slug='semver-cot-3')
        for bad in ('not-a-version', '1.x.0', 'latest', '!!invalid!!'):
            cot.version = bad
            with self.assertRaises(ValidationError, msg=f"Expected ValidationError for version={bad!r}"):
                cot.full_clean()

    # ------------------------------------------------------------------
    # CustomObjectTypeField.deprecated_since
    # ------------------------------------------------------------------

    def test_field_deprecated_since_blank_is_valid(self):
        cot = self.create_custom_object_type(name='semver_f1', slug='semver-f1')
        field = self.create_custom_object_type_field(cot, name='alpha', type='text')
        field.deprecated_since = ''
        field.full_clean()

    def test_field_deprecated_since_valid_semver(self):
        cot = self.create_custom_object_type(name='semver_f2', slug='semver-f2')
        field = self.create_custom_object_type_field(cot, name='beta', type='text')
        field.deprecated_since = '2.0.0'
        field.full_clean()

    def test_field_deprecated_since_invalid_raises(self):
        cot = self.create_custom_object_type(name='semver_f3', slug='semver-f3')
        field = self.create_custom_object_type_field(cot, name='gamma', type='text')
        for bad in ('not-a-version', '1.x.0', 'latest', '!!invalid!!'):
            field.deprecated_since = bad
            with self.assertRaises(ValidationError, msg=f"Expected ValidationError for deprecated_since={bad!r}"):
                field.full_clean()

    # ------------------------------------------------------------------
    # CustomObjectTypeField.scheduled_removal
    # ------------------------------------------------------------------

    def test_field_scheduled_removal_blank_is_valid(self):
        cot = self.create_custom_object_type(name='semver_f4', slug='semver-f4')
        field = self.create_custom_object_type_field(cot, name='delta', type='text')
        field.scheduled_removal = ''
        field.full_clean()

    def test_field_scheduled_removal_valid_semver(self):
        cot = self.create_custom_object_type(name='semver_f5', slug='semver-f5')
        field = self.create_custom_object_type_field(cot, name='epsilon', type='text')
        field.scheduled_removal = '3.0.0'
        field.full_clean()

    def test_field_scheduled_removal_invalid_raises(self):
        cot = self.create_custom_object_type(name='semver_f6', slug='semver-f6')
        field = self.create_custom_object_type_field(cot, name='zeta', type='text')
        for bad in ('v-bad', '1.x.0', 'latest', '!!invalid!!'):
            field.scheduled_removal = bad
            with self.assertRaises(ValidationError, msg=f"Expected ValidationError for scheduled_removal={bad!r}"):
                field.full_clean()


class NullRelatedObjectTypeTestCase(CustomObjectsTestCase, TestCase):
    """Regression tests for graceful handling of OBJECT/MULTIOBJECT fields whose
    related_object_type_id is NULL or points to a deleted ContentType.

    A NULL FK can occur when a COT field is created via direct DB manipulation or
    when the referenced ContentType is deleted.  All code paths that build the
    dynamic model or serializer must skip such fields rather than crashing.

    Covers the fixes in _fetch_and_generate_field_attrs (ContentType.DoesNotExist →
    NotImplementedError) and get_serializer_class (Meta.fields/attrs mismatch guard).
    """

    def _make_cot_with_null_object_field(self, name, slug, field_name="broken_ref"):
        from netbox_custom_objects.models import CustomObjectType
        cot = self.create_custom_object_type(name=name, slug=slug)
        self.create_custom_object_type_field(
            cot, name="title", label="Title", type="text", primary=True, required=True,
        )
        field = self.create_custom_object_type_field(
            cot, name=field_name, label="Broken Ref", type="object",
            related_object_type=self.get_site_object_type(),
        )
        # Force the FK to NULL to simulate stale/corrupt data (e.g. ContentType deleted)
        CustomObjectTypeField.objects.filter(pk=field.pk).update(related_object_type=None)
        CustomObjectType.clear_model_cache()
        return cot

    def test_get_model_skips_object_field_with_null_related_object_type(self):
        """get_model() must succeed and silently skip an OBJECT field whose
        related_object_type_id is NULL rather than raising ContentType.DoesNotExist."""
        cot = self._make_cot_with_null_object_field("NullRelObj", "null-rel-obj")
        model = cot.get_model()
        self.assertIsNotNone(model)
        model_field_names = (
            {f.name for f in model._meta.local_fields}
            | {f.name for f in model._meta.local_many_to_many}
        )
        self.assertNotIn("broken_ref", model_field_names,
                         "Field with null FK must be absent from the generated model")
        self.assertIn("title", model_field_names,
                      "Normal fields must still be present")

    def test_get_serializer_class_handles_null_related_object_type(self):
        """get_serializer_class() must not raise AttributeError when an OBJECT field
        was skipped during model generation due to a NULL related_object_type_id.
        Regression for the Meta.fields/attrs mismatch that caused DRF to raise a
        validation error at serializer initialization time."""
        from netbox_custom_objects.api.serializers import get_serializer_class

        cot = self._make_cot_with_null_object_field(
            "NullRelSerializer", "null-rel-serializer"
        )
        model = cot.get_model()
        serializer_cls = get_serializer_class(model)
        self.assertIsNotNone(serializer_cls)
        self.assertNotIn("broken_ref", serializer_cls.Meta.fields,
                         "Null-FK field must not appear in serializer Meta.fields")
        self.assertIn("title", serializer_cls.Meta.fields,
                      "Normal fields must still be present in serializer")
