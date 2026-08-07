"""
Tests for database schema operations and model-cache behaviour.

Uses TransactionTestCase so DDL and on_commit callbacks behave exactly as they
do in production (no wrapping savepoint prevents commits).
"""
import threading
from io import StringIO
from unittest.mock import patch

from django.apps import apps
from django.core.management import call_command
from django.db import connection
from django.test import TransactionTestCase

from core.models import ObjectType
from dcim.models import Site
from extras.choices import CustomFieldTypeChoices
from netbox_custom_objects.constants import APP_LABEL
from netbox_custom_objects.field_types import FIELD_TYPE_CLASS
from netbox_custom_objects.models import CustomObjectType, CustomObjectTypeField

from .base import CustomObjectsTestCase, TransactionCleanupMixin


class SchemaOperationsTestCase(TransactionCleanupMixin, CustomObjectsTestCase, TransactionTestCase):
    """Test database schema operations and related cache/registry behaviour."""

    # ------------------------------------------------------------------
    # Cache invalidation
    # ------------------------------------------------------------------

    def test_cache_invalidation_on_cotf_save(self):
        """#340 – cache_timestamp on the parent COT is updated when a field is saved."""
        cot = self.create_custom_object_type(name='cachetest', slug='cache-test')
        # Capture the initial timestamp
        initial_timestamp = cot.cache_timestamp

        # Adding a field triggers CustomObjectTypeField.save(), which calls
        # cot.save(update_fields=['cache_timestamp']).
        self.create_custom_object_type_field(
            cot,
            name='myfield',
            label='My Field',
            type='text',
        )

        cot.refresh_from_db()
        self.assertNotEqual(
            cot.cache_timestamp,
            initial_timestamp,
            "cache_timestamp must be updated after a field is saved.",
        )

    def test_cache_invalidation_on_cotf_delete(self):
        """cache_timestamp is updated when a field is deleted."""
        cot = self.create_custom_object_type(name='cachedel', slug='cache-del')
        field = self.create_custom_object_type_field(
            cot,
            name='tempfield',
            label='Temp Field',
            type='text',
        )
        cot.refresh_from_db()
        timestamp_after_add = cot.cache_timestamp

        field.delete()

        cot.refresh_from_db()
        self.assertNotEqual(
            cot.cache_timestamp,
            timestamp_after_add,
            "cache_timestamp must be updated after a field is deleted.",
        )

    # ------------------------------------------------------------------
    # Model registry
    # ------------------------------------------------------------------

    def test_model_registered_in_apps_after_cotf_save(self):
        """#335 – The regenerated model is present in apps.get_models() after a field change."""
        cot = self.create_custom_object_type(name='regtest', slug='reg-test')
        self.create_custom_object_type_field(
            cot,
            name='fieldone',
            label='Field One',
            type='text',
            primary=True,
        )

        # Force model generation and registration
        model = cot.get_model()
        model_name = model.__name__.lower()

        # The model must appear in the app registry
        self.assertIn(
            model_name,
            apps.all_models.get(APP_LABEL, {}),
            "Generated model should be registered in Django's app registry.",
        )
        self.assertIn(
            model,
            apps.get_models(),
            "Generated model should be returned by apps.get_models().",
        )

    def test_model_regenerated_after_field_added(self):
        """Adding a field clears the model cache so get_model() reflects the new schema."""
        cot = self.create_custom_object_type(name='regentest', slug='regen-test')
        self.create_custom_object_type_field(
            cot,
            name='name',
            label='Name',
            type='text',
            primary=True,
        )
        old_model = cot.get_model()
        self.assertFalse(
            'extra' in {f.name for f in old_model._meta.get_fields()},
            "Field 'extra' should not exist before it is added.",
        )

        # Add a new field — this invalidates the cache
        self.create_custom_object_type_field(
            cot,
            name='extra',
            label='Extra',
            type='text',
        )

        new_model = cot.get_model()
        self.assertIn(
            'extra',
            {f.name for f in new_model._meta.get_fields()},
            "Field 'extra' should be present on the regenerated model.",
        )

    def test_model_not_in_registry_after_cot_deleted(self):
        """Deleting a COT removes its generated model from Django's app registry."""
        cot = self.create_custom_object_type(name='delregtest', slug='del-reg-test')
        self.create_custom_object_type_field(
            cot,
            name='name',
            label='Name',
            type='text',
            primary=True,
        )
        model = cot.get_model()
        model_name = model.__name__.lower()

        self.assertIn(
            model_name,
            apps.all_models.get(APP_LABEL, {}),
            "Model should be in registry before deletion.",
        )

        cot.delete()

        self.assertNotIn(
            model_name,
            apps.all_models.get(APP_LABEL, {}),
            "Deleted COT's model must be removed from the app registry.",
        )

    def test_delete_cot_with_netbox_custom_field_referencing_object_type(self):
        """#523 – Deleting a COT must not raise ProtectedError when a NetBox CustomField
        has related_object_type pointing to the COT's underlying ObjectType."""
        from core.models import ObjectType
        from extras.choices import CustomFieldTypeChoices
        from extras.models import CustomField

        cot = self.create_custom_object_type(name='protectedtest', slug='protected-test')
        object_type = ObjectType.objects.get_for_model(cot.get_model())

        # Create a regular NetBox CustomField of type "object" whose related_object_type
        # points at the COT's ContentType. This is the scenario that triggered the
        # ProtectedError because CustomField.related_object_type uses on_delete=PROTECT.
        cf = CustomField.objects.create(
            name='cat_ref',
            type=CustomFieldTypeChoices.TYPE_OBJECT,
            related_object_type=object_type,
        )

        # Should delete cleanly without raising ProtectedError.
        cot.delete()

        self.assertFalse(
            CustomField.objects.filter(pk=cf.pk).exists(),
            "The referencing CustomField must be deleted along with the COT.",
        )

    # ------------------------------------------------------------------
    # Management commands
    # ------------------------------------------------------------------

    def test_migration_with_call_command(self):
        """#326 – Running migrate via call_command() should not raise."""
        out = StringIO()
        # --check exits with code 1 if unapplied migrations exist; any other
        # error (e.g. the plugin crashing during the migrate run) would raise.
        try:
            call_command('migrate', '--check', verbosity=0, stdout=out, stderr=out)
        except SystemExit as exc:
            # If we reach here, migrate --check found unapplied migrations or the plugin crashed.
            self.fail(f"migrate --check exited with code {exc.code}: {out.getvalue()}")

    def test_collectstatic_without_database(self):
        """#347 – collectstatic should complete without requiring database access."""
        out = StringIO()
        err = StringIO()
        # --dry-run does not write files; --no-input skips confirmation prompts.
        # The important assertion is that no exception (especially no database
        # error originating from the plugin's AppConfig) is raised.
        call_command(
            'collectstatic',
            '--dry-run',
            '--no-input',
            verbosity=0,
            stdout=out,
            stderr=err,
        )
        # No uncaught exceptions reaching here means success.

    # ------------------------------------------------------------------
    # Coordinates fields expand into two backing columns; verify the schema
    # editor manages both on rename and delete.
    # ------------------------------------------------------------------

    def _db_columns(self, model):
        """Return the set of actual DB column names for a generated model's table."""
        with connection.cursor() as cursor:
            return {
                col.name
                for col in connection.introspection.get_table_description(
                    cursor, model._meta.db_table
                )
            }

    def test_coordinates_field_rename_renames_both_columns(self):
        """Renaming a coordinates field renames both backing DB columns."""
        cot = self.create_custom_object_type(name='coordrename', slug='coord-rename')
        self.create_custom_object_type_field(
            cot, name='name', label='Name', type='text', primary=True,
        )
        field = self.create_custom_object_type_field(
            cot, name='location', label='Location', type='coordinates',
        )

        columns = self._db_columns(cot.get_model())
        self.assertIn('location_latitude', columns)
        self.assertIn('location_longitude', columns)

        # Reload from DB so the rename path has the original snapshot (set in
        # from_db) — this mirrors how the edit view loads the field before saving.
        field = CustomObjectTypeField.objects.get(pk=field.pk)
        field.name = 'geo'
        field.save()

        columns = self._db_columns(cot.get_model())
        self.assertNotIn('location_latitude', columns)
        self.assertNotIn('location_longitude', columns)
        self.assertIn('geo_latitude', columns)
        self.assertIn('geo_longitude', columns)

    def test_coordinates_field_delete_drops_both_columns(self):
        """Deleting a coordinates field drops both backing DB columns."""
        cot = self.create_custom_object_type(name='coorddelete', slug='coord-delete')
        self.create_custom_object_type_field(
            cot, name='name', label='Name', type='text', primary=True,
        )
        field = self.create_custom_object_type_field(
            cot, name='location', label='Location', type='coordinates',
        )

        columns = self._db_columns(cot.get_model())
        self.assertIn('location_latitude', columns)
        self.assertIn('location_longitude', columns)

        field.delete()

        columns = self._db_columns(cot.get_model())
        self.assertNotIn('location_latitude', columns)
        self.assertNotIn('location_longitude', columns)


class PolymorphicMultiObjectConcurrencyTestCase(TransactionCleanupMixin, CustomObjectsTestCase, TransactionTestCase):
    """
    Regression tests for issue #640: creating a polymorphic multiobject field
    races registering its through-model class against a concurrent
    get_model() call, producing a class-identity mismatch that later surfaces
    as "ValueError: Cannot query 'X': Must be 'TableYModel' instance." or a
    RecursionError (same symptom class as #477/#483).
    """

    def setUp(self):
        super().setUp()
        self.site_ot = ObjectType.objects.get_for_model(Site)

    def test_field_creation_racing_concurrent_readers_yields_consistent_through_model(self):
        """
        Races field *creation* (real DB I/O) against 12 looping get_model()
        readers -- the shape that reproduced #640 live. Rarely lands inside
        the actual race window in-process (see the deterministic version
        below), but exercises the same code path under real concurrency.
        """
        cot = self.create_simple_custom_object_type(name='polyrace', slug='poly-race')

        stop = threading.Event()
        reader_errors = []
        reader_errors_lock = threading.Lock()

        def reader():
            while not stop.is_set():
                try:
                    CustomObjectType.objects.get(pk=cot.pk).get_model(no_cache=True)
                except Exception as e:  # noqa: BLE001 - captured for the assertion below
                    with reader_errors_lock:
                        reader_errors.append(e)
                finally:
                    connection.close()

        n_readers = 12
        readers = [threading.Thread(target=reader) for _ in range(n_readers)]
        for t in readers:
            t.start()

        try:
            field = self.create_custom_object_type_field(
                cot,
                name='depends_on',
                label='Depends On',
                type='multiobject',
                is_polymorphic=True,
            )
            field.related_object_types.set([self.site_ot])
        finally:
            stop.set()
            for t in readers:
                t.join()

        self.assertEqual(
            reader_errors, [],
            "concurrent get_model() calls must not raise while a polymorphic "
            "multiobject field is being created",
        )

        # get_model() and the through model's "source" FK must agree on which
        # class is canonical -- a mismatch is the #477/#483-class staleness.
        final_model = CustomObjectType.objects.get(pk=cot.pk).get_model()
        through_model = apps.get_model(APP_LABEL, field.through_model_name)
        source_field = through_model._meta.get_field('source')
        self.assertIs(
            source_field.remote_field.model, final_model,
            "the registered through model's source FK must point at the model class "
            "get_model() currently returns, not an orphaned duplicate from a losing thread",
        )

        obj = final_model.objects.create(name='Instance 1')
        obj.depends_on.set([Site.objects.create(name='Race Site', slug='race-site')])
        obj.delete()  # Must not raise ValueError: "Cannot query ...: Must be ... instance."

    def test_forced_registration_interleaving_stays_consistent(self):
        """
        Deterministic version of the same race, forced via mocking instead of
        relying on thread-scheduling luck.

        get_model() already wraps _after_model_generation() in
        CustomObjectType._global_lock, so two concurrent readers can't race
        each other there. The actual gap is the *writer*:
        create_polymorphic_m2m_table() (called once, from
        CustomObjectTypeField.save(), when a polymorphic multiobject field is
        first created) registers its through-model class via Django's
        metaclass, then repoints its "source" FK -- all without that lock. A
        concurrent reader can land in between: it finds the through model
        already registered and repoints "source" at its own model instead,
        so the through's FK and get_model()'s cache can end up pointing at
        two different classes.

        Thread "W" plays the writer (create_polymorphic_m2m_table()
        directly), thread "R" the reader (get_model()). A mocked
        register_model() pauses W right after registration but before it
        repoints "source", giving R a window to run. With the fix, W holds
        _global_lock for that whole call, so R can't even start until W is
        done -- the pause below just times out harmlessly. Without the fix,
        R runs inside the pause and the two threads' writes land in
        different orders, reliably producing the mismatch asserted below.
        """
        cot = self.create_simple_custom_object_type(name='polyforce', slug='poly-force')
        field = self.create_custom_object_type_field(
            cot,
            name='depends_on',
            label='Depends On',
            type='multiobject',
            is_polymorphic=True,
        )
        field.related_object_types.set([self.site_ot])

        # The table/through model already exist (created for real above via
        # the normal save() path). Force the through model back to
        # "unregistered" so a direct create_polymorphic_m2m_table() call
        # takes the same build-register-repoint path a brand-new field's
        # first save would; create_polymorphic_m2m_table()'s own idempotency
        # check will see the physical table already exists and skip the DDL.
        writer_model = CustomObjectType.objects.get(pk=cot.pk).get_model()
        CustomObjectType.clear_model_cache()
        model_name_lower = field.through_model_name.lower()
        del apps.all_models[APP_LABEL][model_name_lower]
        apps.clear_cache()

        real_register_model = apps.register_model
        reader_may_proceed = threading.Event()
        reader_done = threading.Event()
        gated = set()

        def ordered_register_model(app_label, model):
            # Only intercept the through model under test.
            if app_label != APP_LABEL or model.__name__ != field.through_model_name:
                return real_register_model(app_label, model)
            # Only the first call matters (Django's metaclass registers the
            # model as soon as it's built; a harmless explicit re-registration
            # follows immediately after in the real code).
            if 'seen' in gated:
                return real_register_model(app_label, model)
            gated.add('seen')

            result = real_register_model(app_label, model)
            # Registered, but "source" isn't repointed at writer_model yet --
            # give R a window here. With the fix, W holds _global_lock for
            # this whole call, so R can't have started yet and this times out.
            reader_may_proceed.set()
            reader_done.wait(timeout=2)
            return result

        writer_result = {}

        def run_writer():
            threading.current_thread().name = 'W'
            field_type = FIELD_TYPE_CLASS[CustomFieldTypeChoices.TYPE_MULTIOBJECT]()
            try:
                with connection.schema_editor() as schema_editor:
                    field_type.create_polymorphic_m2m_table(field, writer_model, schema_editor)
            except Exception as e:  # noqa: BLE001 - surfaced via the assertion below
                writer_result['error'] = e
            finally:
                connection.close()

        reader_result = {}

        def run_reader():
            threading.current_thread().name = 'R'
            reader_may_proceed.wait(timeout=5)
            try:
                reader_result['model'] = CustomObjectType.objects.get(pk=cot.pk).get_model(no_cache=True)
            except Exception as e:  # noqa: BLE001 - surfaced via the assertion below
                reader_result['error'] = e
            finally:
                reader_done.set()
                connection.close()

        with patch.object(apps, 'register_model', side_effect=ordered_register_model):
            t_w = threading.Thread(target=run_writer, name='W')
            t_r = threading.Thread(target=run_reader, name='R')
            t_w.start()
            t_r.start()
            t_w.join(timeout=10)
            t_r.join(timeout=10)

        self.assertNotIn('error', writer_result, f"writer raised: {writer_result.get('error')!r}")
        self.assertNotIn('error', reader_result, f"reader raised: {reader_result.get('error')!r}")

        # Without the fix, this reliably produces a mismatch: reader's model
        # cached while writer's model is left on the through's "source" FK,
        # or vice versa.
        final_model = CustomObjectType.objects.get(pk=cot.pk).get_model()
        through_model = apps.get_model(APP_LABEL, field.through_model_name)
        source_field = through_model._meta.get_field('source')
        self.assertIs(
            source_field.remote_field.model, final_model,
            "the registered through model's source FK must point at the model class "
            "get_model() currently returns -- a mismatch here is issue #640",
        )

        obj = final_model.objects.create(name='Instance 1')
        obj.depends_on.set([Site.objects.create(name='Force Site', slug='force-site')])
        obj.delete()  # Must not raise ValueError: "Cannot query ...: Must be ... instance."
