# Test utilities for netbox_custom_objects plugin
from django.db import connection
from django.test import Client
from core.models import ObjectType
from extras.models import CustomFieldChoiceSet
from utilities.testing import create_test_user

from netbox_custom_objects.models import CustomObjectType, CustomObjectTypeField

_DYNAMIC_TABLE_PREFIX = "custom_objects_"


def _drop_dynamic_tables():
    """Drop leftover dynamic custom-object tables and purge stale app-registry state.

    Two problems arise from --keepdb runs:

    1. DB tables — Django's ``flush`` command uses ``django_table_names()`` which
       only returns ORM-registered tables.  Dynamic tables created by this plugin
       live outside that registry, so ``flush`` doesn't TRUNCATE them — but they
       DO have foreign keys to ``django_content_type``, causing PostgreSQL to
       reject the TRUNCATE with "cannot truncate a table referenced in a foreign
       key constraint".  Dropping these orphan tables first fixes that.

    2. App-registry models — stale dynamic models from prior runs may still be
       registered in Django's in-process app registry even after their DB tables
       are dropped.  Django's deletion collector walks ``Site._meta.related_objects``
       (and similar) and queries every registered model that has a FK to the object
       being deleted.  If a stale model points to a now-dropped table the query
       raises ``ProgrammingError``.  We must deregister those models AND delete the
       corresponding CustomObjectType rows from the DB before calling
       ``apps.clear_cache()`` so that the next ``get_models()`` invocation rebuilds
       ``_meta.related_objects`` without phantom FK references.
    """
    from django.apps import apps as django_apps
    from netbox_custom_objects.constants import APP_LABEL
    from netbox_custom_objects.models import CustomObjectType

    # Step 1 — clear the plugin's own model cache so get_model() doesn't hand out
    # stale model objects that still reference non-existent through tables.
    CustomObjectType.clear_model_cache()

    # Step 2 — remove stale dynamic models from apps.all_models.
    # We deliberately do NOT call apps.clear_cache() here: clear_cache() triggers
    # get_models() on each AppConfig, and our override in __init__.py calls
    # get_model() for every row in CustomObjectType.objects.all().  Any stale COT
    # rows still in the DB would be immediately re-registered, undoing this cleanup.
    app_models = django_apps.all_models.get(APP_LABEL, {})
    stale_names = [
        name for name, model in list(app_models.items())
        if hasattr(model, '_meta') and model._meta.db_table.startswith(_DYNAMIC_TABLE_PREFIX)
    ]
    for name in stale_names:
        del app_models[name]

    # Step 3 — delete stale CustomObjectType rows via queryset (direct SQL DELETE,
    # not the custom cot.delete() method which tries schema operations on tables
    # that no longer exist).  Wrapped in a broad except so a partially-migrated
    # schema never blocks test startup.
    try:
        CustomObjectType.objects.all().delete()
    except Exception:
        pass

    # Step 4 — drop all dynamic DB tables.
    all_tables = connection.introspection.table_names()
    dynamic = [t for t in all_tables if t.startswith(_DYNAMIC_TABLE_PREFIX)]
    if dynamic:
        with connection.cursor() as cursor:
            for table in dynamic:
                cursor.execute(f'DROP TABLE IF EXISTS "{table}" CASCADE')

    # Step 5 — rebuild the app registry cache now that both the stale model
    # entries (step 2) and the stale COT rows (step 3) are gone.  get_models()
    # finds no CustomObjectType rows so nothing is re-registered, and
    # Site._meta.related_objects (etc.) is rebuilt without phantom FK pointers.
    if stale_names:
        django_apps.clear_cache()


class TransactionCleanupMixin:
    """Mixin for TransactionTestCase subclasses that create CustomObjectType instances.

    Deletes all COTs in tearDown so their backing tables are dropped before the
    database flush that TransactionTestCase performs between tests.  Also drops
    any leftover dynamic tables before the flush so a dirty database from a
    previous (failed) run cannot block the TRUNCATE.

    Django 5.2 notes:
    - _pre_setup / _fixture_setup are classmethods called once per class.
    - _fixture_teardown is an instance method called after *every* test.
    - Overriding _fixture_teardown is the correct place to drop dynamic tables
      because it always runs, even when setUp raised an exception.
    - Overriding _pre_setup (as classmethod) handles leftover tables from a
      previous run before the first test of the current run.
    """

    @classmethod
    def _pre_setup(cls):
        # Drop leftovers from any previous (possibly failed) run first, so the
        # normal fixture setup that follows isn't blocked by orphan tables.
        _drop_dynamic_tables()
        super()._pre_setup()

    def _fixture_teardown(self):
        # Drop dynamic tables before Django's flush; without this, the flush
        # command's TRUNCATE of django_content_type fails because our through
        # tables have FK references to it.
        _drop_dynamic_tables()
        super()._fixture_teardown()

    def tearDown(self):
        for cot in CustomObjectType.objects.all():
            try:
                cot.delete()
            except Exception as exc:
                print(f"WARNING: tearDown could not delete COT {cot.pk}: {exc}")
        super().tearDown()


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
