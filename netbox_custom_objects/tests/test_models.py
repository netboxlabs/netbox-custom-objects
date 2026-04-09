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
from .base import CustomObjectsTestCase


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
        model_name = f"{cot.pk}tablemodel"

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
