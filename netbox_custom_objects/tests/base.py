# Test utilities for netbox_custom_objects plugin
import logging

from django.apps import apps as django_apps
from django.contrib.contenttypes.management import create_contenttypes
from django.db import connection
from django.test import Client
from core.models import ObjectChange, ObjectType
from extras.models import CustomFieldChoiceSet
from users.models import Token
from utilities.testing import create_test_user

from netbox_custom_objects.constants import APP_LABEL
from netbox_custom_objects.models import (
    CustomObjectType,
    CustomObjectTypeField,
    _deferred_co_field_data,
)

logger = logging.getLogger(__name__)


def create_token(user):
    """Create an API token for ``user`` and return its plaintext key.

    Handles the NetBox 4.5 token-versioning change (V1 tokens expose ``.token``;
    older NetBox exposes ``.key``).
    """
    try:
        # NetBox >= 4.5
        from users.choices import TokenVersionChoices
        token = Token(version=TokenVersionChoices.V1, user=user)
        token.save()
        return token.token
    except ImportError:
        # NetBox < 4.5
        token = Token(user=user)
        token.save()
        return token.key


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
        # Expire reverse-relation caches so any other app whose
        # ``related_objects`` already pointed at the now-removed dynamic
        # models gets rebuilt on next access.  ``apps.clear_cache()`` alone
        # clears the apps registry's own cache, but per-model
        # ``_meta._relation_tree`` snapshots taken by Django outside this
        # plugin survive — they need explicit expiry.
        from django.contrib.contenttypes.models import ContentType  # noqa: PLC0415
        ContentType._meta._expire_cache(forward=False)
        for app_models in django_apps.all_models.values():
            for model in app_models.values():
                model._meta._expire_cache(forward=False)
        django_apps.clear_cache()


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
        quote = connection.ops.quote_name
        with connection.cursor() as cursor:
            for table in dynamic:
                cursor.execute(f'DROP TABLE IF EXISTS {quote(table)} CASCADE')

    # Step 5 — rebuild the app registry cache now that both the stale model
    # entries (step 2) and the stale COT rows (step 3) are gone.  get_models()
    # finds no CustomObjectType rows so nothing is re-registered, and
    # Site._meta.related_objects (etc.) is rebuilt without phantom FK pointers.
    if stale_names:
        django_apps.clear_cache()


def _reset_netbox_request_context():
    """Clear ``netbox.context.current_request`` and ``events_queue``.

    netbox.context_managers.event_tracking() yields without try/finally, so an
    exception inside the ``with`` block leaves current_request set to the
    previous request.  In test runs that means the next test's pre_save signal
    handler attributes ObjectChange to the previous test's user (whose row has
    since been truncated by _fixture_teardown), producing
    ``IntegrityError: insert or update on table "core_objectchange" violates
    foreign key constraint`` on user_id.
    """
    try:
        from netbox.context import current_request, events_queue, query_cache
    except ImportError:
        return
    current_request.set(None)
    events_queue.set({})
    # ``query_cache`` is a ContextVar in current NetBox; in older releases it
    # was a thread-local without ``.set()``.  Catch AttributeError narrowly so
    # an unrelated bug surfaces instead of being swallowed silently.
    try:
        query_cache.set(None)
    except AttributeError:
        logger.debug('netbox.context.query_cache has no .set(); skipping reset')


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

    def setUp(self):
        # Purge stale in-memory model registrations left by earlier TestCase
        # classes whose rolled-back transactions dropped the backing tables.
        # Must run before any code that iterates the model registry (e.g.
        # netbox-branching's get_tables_to_replicate() during provisioning).
        _purge_stale_generated_models()
        # Clear netbox's request-scoped ContextVars.  netbox.context_managers
        # event_tracking() sets current_request on entry but only clears it
        # *after* yield — if a test raises inside the with block, the cleanup
        # never runs and the next test's branch.save() (which reads
        # current_request to attribute ObjectChange) still sees the previous
        # test's user, whose row was truncated by _fixture_teardown → FK
        # violation on core_objectchange.user_id.
        _reset_netbox_request_context()
        super().setUp()

    def tearDown(self):
        # Reset deferred CO field data so it doesn't bleed into the next test.
        _deferred_co_field_data.set(None)
        # Defensive reset — see setUp for rationale.  Belt-and-braces in case a
        # test enters event_tracking but raises before super().tearDown() runs.
        _reset_netbox_request_context()
        # Delete COTs and their backing tables before the DB flush.  Cleanup
        # is best-effort — if a previous test left the schema in a weird
        # state, log and continue rather than failing tearDown (which would
        # mask the real failure that put us here).
        for cot in CustomObjectType.objects.all():
            try:
                cot.delete()
            except Exception:
                logger.warning(
                    'tearDown could not delete COT %s', cot.pk, exc_info=True,
                )
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
        # Drop dynamic tables before Django's flush; without this, the flush
        # command's TRUNCATE of django_content_type fails because our through
        # tables have FK references to it.
        _drop_dynamic_tables()
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
        from django.apps import apps as django_apps
        from netbox_custom_objects.constants import APP_LABEL
        self.user = create_test_user('testuser')
        self.client = Client()
        self.client.force_login(self.user)
        # Snapshot the current plugin model registry before this test method
        # runs.  tearDown uses this to identify models (and fields on existing
        # models) that were added during the test so they can be deregistered
        # before the transaction savepoint rolls back.  After the rollback the
        # DB columns / tables are gone, but Django's in-process app registry is
        # not transactional — stale entries cause cascade-collector errors in
        # later tests.
        app_models = django_apps.all_models.get(APP_LABEL, {})
        self._plugin_model_snapshot = {
            name: frozenset(f.column for f in model._meta.local_fields)
            for name, model in app_models.items()
        }

    def tearDown(self):
        """Clean up after each test."""
        from django.apps import apps as django_apps
        from django.contrib.contenttypes.models import ContentType
        from netbox_custom_objects.constants import APP_LABEL
        CustomObjectType.clear_model_cache()
        # Identify plugin models that were added or mutated during this test.
        # Both through-models (new entries) and COT main models that gained new
        # FK columns must be removed so that ContentType._meta.related_objects
        # doesn't reference columns/tables that no longer exist after the
        # transaction savepoint rolls back.
        before = getattr(self, '_plugin_model_snapshot', None)
        if before is None:
            # setUp() was not invoked via super() for this test class —
            # skip per-test model cleanup to avoid deleting static models.
            super().tearDown()
            return
        app_models = django_apps.all_models.get(APP_LABEL, {})
        to_remove = []
        for name, model in list(app_models.items()):
            pre_cols = before.get(name)
            if pre_cols is None:
                # Entirely new model registered during this test.
                to_remove.append(name)
            else:
                # Existing model — check whether it gained new FK columns.
                cur_cols = frozenset(f.column for f in model._meta.local_fields)
                if cur_cols != pre_cols:
                    to_remove.append(name)
        if to_remove:
            for name in to_remove:
                del app_models[name]
            # Expire reverse-relation caches so related_objects is rebuilt
            # from the pruned registry on next access.
            ContentType._meta._expire_cache(forward=False)
            for model in list(app_models.values()):
                model._meta._expire_cache(forward=False)
        super().tearDown()

    @classmethod
    def tearDownClass(cls):
        """Remove class-level dynamic model registrations after all tests run."""
        from django.apps import apps as django_apps
        from django.contrib.contenttypes.models import ContentType
        from netbox_custom_objects.constants import APP_LABEL
        # After tearDownClass the class-level savepoint rolls back, dropping
        # all COT tables created by setUpTestData.  Remove dynamic models from
        # the registry first so the cascade collector never tries to query those
        # dropped tables.  Static plugin models (CustomObjectType, etc.) must
        # NOT be removed.
        app_models = django_apps.all_models.get(APP_LABEL, {})
        dynamic = [
            name for name, model in list(app_models.items())
            if hasattr(model, '_meta')
            and model._meta.db_table.startswith(_DYNAMIC_TABLE_PREFIX)
        ]
        if dynamic:
            for name in dynamic:
                del app_models[name]
            ContentType._meta._expire_cache(forward=False)
        super().tearDownClass()

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

    @classmethod
    def get_prefix_object_type(cls):
        """Get the prefix content type for object field testing."""
        return ObjectType.objects.get(app_label='ipam', model='prefix')

    @classmethod
    def create_polymorphic_field(cls, custom_object_type, related_object_types, **kwargs):
        """Create a polymorphic object/multiobject field with a list of allowed ObjectTypes."""
        defaults = {
            'custom_object_type': custom_object_type,
            'name': 'poly_field',
            'type': 'object',
            'is_polymorphic': True,
        }
        defaults.update(kwargs)
        rot_list = defaults.pop('related_object_types', None) or related_object_types
        field = CustomObjectTypeField.objects.create(**defaults)
        field.related_object_types.set(rot_list)
        return field

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
