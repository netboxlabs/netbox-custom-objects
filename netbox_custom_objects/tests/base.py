# Test utilities for netbox_custom_objects plugin
from django.apps import apps as django_apps
from django.contrib.contenttypes.management import create_contenttypes
from django.test import Client
from core.models import ObjectType
from extras.models import CustomFieldChoiceSet
from users.models import Token
from utilities.testing import create_test_user

from netbox_custom_objects.constants import APP_LABEL
from netbox_custom_objects.models import CustomObjectType, CustomObjectTypeField


def _recreate_contenttypes():
    """Recreate ContentType rows for all installed apps using get_or_create.

    Called after a TransactionTestCase flush so that subsequent test classes —
    whether TransactionTestCase or regular TestCase — can look up ContentTypes.
    Using create_contenttypes (get_or_create) avoids the duplicate-key
    violations that serialized_rollback causes in the parallel test runner.
    """
    for app_config in django_apps.get_app_configs():
        create_contenttypes(app_config, verbosity=0)


def _purge_stale_generated_models():
    """Remove dynamically generated CustomObject models from the app registry.

    Regular TestCase subclasses wrap each test in a transaction that is rolled
    back at the end.  Rolling back the transaction drops any tables created by
    DDL inside the test (e.g. CREATE TABLE custom_objects_1), but Django's
    in-memory model registry is NOT rolled back.  The stale model entry then
    causes problems for subsequent TransactionTestCase tests:

    - netbox-branching's get_tables_to_replicate() iterates over registered
      models to build the list of tables to clone into the branch schema.
      A stale model entry makes it try to COPY a table that no longer exists.
    - Django's cascade-delete collector queries non-existent tables.

    Calling this in setUp() of every TransactionTestCase prevents both failure
    modes.
    """
    stale = [
        name
        for name, model in list(django_apps.all_models.get(APP_LABEL, {}).items())
        if getattr(model, '_generated_table_model', False)
    ]
    for name in stale:
        django_apps.all_models[APP_LABEL].pop(name, None)
    if stale:
        django_apps.clear_cache()


def create_api_token(user):
    """Create an API token for *user*, handling the NetBox ≥ 4.5 version field."""
    try:
        from users.choices import TokenVersionChoices  # noqa: PLC0415
        token = Token(version=TokenVersionChoices.V1, user=user)
    except ImportError:
        token = Token(user=user)
    token.save()
    return token


class TransactionCleanupMixin:
    """Mixin for TransactionTestCase subclasses that create CustomObjectType instances.

    Deletes all COTs in tearDown so their backing tables are dropped before the
    database flush that TransactionTestCase performs between tests.
    """

    def setUp(self):
        # Purge stale in-memory model registrations left by earlier TestCase
        # classes whose rolled-back transactions dropped the backing tables.
        # Must run before any code that iterates the model registry (e.g.
        # netbox-branching's get_tables_to_replicate() during provisioning).
        _purge_stale_generated_models()
        super().setUp()

    def tearDown(self):
        from core.models import ObjectChange
        from netbox_custom_objects.models import _deferred_co_field_data
        # Reset deferred CO field data so it doesn't bleed into the next test.
        _deferred_co_field_data.set(None)
        # Delete COTs and their backing tables before the DB flush.
        for cot in CustomObjectType.objects.all():
            try:
                cot.delete()
            except Exception as exc:
                print(f"WARNING: tearDown could not delete COT {cot.pk}: {exc}")
        # Remove any ObjectChange records created during the test (merge/revert creates
        # them in main with the test user's ID).  If left in place, the serialized_rollback
        # snapshot accumulates them and restoring it after the next flush produces FK
        # violations (user referenced by ObjectChange no longer exists).
        ObjectChange.objects.all().delete()
        super().tearDown()

    def _fixture_teardown(self):
        """Flush tables and restore ContentTypes for the next test class.

        TransactionTestCase._fixture_teardown() TRUNCATEs all tables after each
        test.  Any TestCase class that follows on the same worker then finds no
        ContentTypes and fails trying to look up ObjectType rows.  Recreating
        them here (idempotently, via get_or_create) avoids that without the
        duplicate-key violations that serialized_rollback=True causes when the
        parallel runner tries to INSERT rows that already exist.
        """
        super()._fixture_teardown()
        _recreate_contenttypes()


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
            'type': 'text'
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
            ]
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
            custom_object_type,
            name="name",
            label="Name",
            type="text",
            primary=True,
            required=True
        )

        # Add a description field
        CustomObjectsTestCase.create_custom_object_type_field(
            custom_object_type,
            name="description",
            label="Description",
            type="text",
            required=False
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
            custom_object_type,
            name="name",
            label="Name",
            type="text",
            primary=True,
            required=True
        )

        # Integer field
        CustomObjectsTestCase.create_custom_object_type_field(
            custom_object_type,
            name="count",
            label="Count",
            type="integer",
            validation_minimum=0,
            validation_maximum=100
        )

        # Boolean field
        CustomObjectsTestCase.create_custom_object_type_field(
            custom_object_type,
            name="active",
            label="Active",
            type="boolean",
            default=True
        )

        # Select field
        CustomObjectsTestCase.create_custom_object_type_field(
            custom_object_type,
            name="status",
            label="Status",
            type="select",
            choice_set=choice_set
        )

        # Object field (device)
        CustomObjectsTestCase.create_custom_object_type_field(
            custom_object_type,
            name="device",
            label="Device",
            type="object",
            related_object_type=device_object_type
        )

        # Multi-Object field (devices)
        CustomObjectsTestCase.create_custom_object_type_field(
            custom_object_type,
            name="devices",
            label="Devices",
            type="multiobject",
            related_object_type=device_object_type
        )

        return custom_object_type

    def create_self_referential_custom_object_type(self, **kwargs):
        """Create a custom object type that can reference itself."""
        custom_object_type = CustomObjectsTestCase.create_custom_object_type(**kwargs)

        # Primary text field
        CustomObjectsTestCase.create_custom_object_type_field(
            custom_object_type,
            name="name",
            label="Name",
            type="text",
            primary=True,
            required=True
        )

        return custom_object_type

    def create_multi_object_custom_object_type(self, **kwargs):
        """Create a custom object type with multi-object fields."""
        custom_object_type = CustomObjectsTestCase.create_custom_object_type(**kwargs)
        device_object_type = CustomObjectsTestCase.get_device_object_type()
        site_object_type = CustomObjectsTestCase.get_site_object_type()

        # Primary text field
        CustomObjectsTestCase.create_custom_object_type_field(
            custom_object_type,
            name="name",
            label="Name",
            type="text",
            primary=True,
            required=True
        )

        # Multi-object field (devices)
        CustomObjectsTestCase.create_custom_object_type_field(
            custom_object_type,
            name="devices",
            label="Devices",
            type="multiobject",
            related_object_type=device_object_type
        )

        # Multi-object field (sites)
        CustomObjectsTestCase.create_custom_object_type_field(
            custom_object_type,
            name="sites",
            label="Sites",
            type="multiobject",
            related_object_type=site_object_type
        )

        return custom_object_type
