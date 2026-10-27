"""
Tests that run threads. They use TransactionTestCase, because each thread uses its own
database connection and only sees committed data.
"""

import threading
from unittest import mock
from unittest.mock import patch

from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError, ProgrammingError, connection, connections
from django.test import TransactionTestCase
from django.urls import reverse
from psycopg.errors import DuplicateTable

from core.models import ObjectType
from dcim.models import Site
from extras.choices import CustomFieldTypeChoices
from netbox_custom_objects.choices import ObjectFieldOnDeleteChoices
from netbox_custom_objects.constants import APP_LABEL
from netbox_custom_objects.field_types import FIELD_TYPE_CLASS
from netbox_custom_objects.models import CustomObjectType, CustomObjectTypeField
from users.models import ObjectPermission

from .base import CustomObjectsTestCase, TransactionCleanupMixin, _fk_constraints


class PolymorphicMultiObjectConcurrencyTestCase(TransactionCleanupMixin, CustomObjectsTestCase, TransactionTestCase):
    """
    Regression tests: creating a polymorphic multiobject field
    races registering its through-model class against a concurrent
    get_model() call, producing a class-identity mismatch that later surfaces
    as "ValueError: Cannot query 'X': Must be 'TableYModel' instance." or a
    RecursionError (the same symptom class as other stale-model bugs).
    """

    def setUp(self):
        super().setUp()
        self.site_ot = ObjectType.objects.get_for_model(Site)

    def test_field_creation_racing_concurrent_readers_yields_consistent_through_model(self):
        """
        Races field *creation* (real DB I/O) against 12 looping get_model()
        readers -- the shape that reproduced the bug live. Rarely lands inside
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
            reader_errors,
            [],
            'concurrent get_model() calls must not raise while a polymorphic multiobject field is being created',
        )

        # get_model() and the through model's "source" FK must agree on which
        # class is canonical -- a mismatch means a stale model class was used.
        final_model = CustomObjectType.objects.get(pk=cot.pk).get_model()
        through_model = apps.get_model(APP_LABEL, field.through_model_name)
        source_field = through_model._meta.get_field('source')
        self.assertIs(
            source_field.remote_field.model,
            final_model,
            "the registered through model's source FK must point at the model class "
            'get_model() currently returns, not an orphaned duplicate from a losing thread',
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
        _global_lock across that build+register+repoint step, so R can't
        start until W has repointed "source" -- the pause below just times
        out harmlessly. Without the fix, R runs inside the pause and the two
        threads' writes land in different orders, reliably producing the
        mismatch asserted below. The history of this test has the full analysis,
        including why the lock can't simply span the rest of the call too.
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
            # this whole call, so R can't have started yet and this always
            # times out rather than being signalled -- R can't reach
            # reader_done.set() until W releases the lock, which doesn't
            # happen until this wait returns. The duration only bounds how
            # long that unavoidable wait lasts; it has no bearing on
            # correctness (R's ability to run concurrently here is decided
            # by lock state, not by wall-clock timing), so keep it short to
            # avoid taxing every CI run by a fixed 2s.
            reader_may_proceed.set()
            reader_done.wait(timeout=0.5)
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

        # A join() timeout leaves the result dicts empty rather than raising, so without these
        # checks a hung thread could silently make the assertions below vacuously pass -- e.g. a
        # writer that never finished never reaches the mismatch-inducing repoint at all.
        self.assertFalse(t_w.is_alive(), 'writer thread did not complete within the join timeout')
        self.assertFalse(t_r.is_alive(), 'reader thread did not complete within the join timeout')

        self.assertNotIn('error', writer_result, f'writer raised: {writer_result.get("error")!r}')
        self.assertNotIn('error', reader_result, f'reader raised: {reader_result.get("error")!r}')

        # Without the fix, this reliably produces a mismatch: reader's model
        # cached while writer's model is left on the through's "source" FK,
        # or vice versa.
        final_model = CustomObjectType.objects.get(pk=cot.pk).get_model()
        through_model = apps.get_model(APP_LABEL, field.through_model_name)
        source_field = through_model._meta.get_field('source')
        self.assertIs(
            source_field.remote_field.model,
            final_model,
            "the registered through model's source FK must point at the model class "
            'get_model() currently returns -- a mismatch here is the stale-class bug',
        )

        obj = final_model.objects.create(name='Instance 1')
        obj.depends_on.set([Site.objects.create(name='Force Site', slug='force-site')])

        # The delete-confirmation GET is the reported UI path (step 4 of the original report): obj.delete()
        # below realigns each through's "source" FK to type(self) before Django's collector runs
        # (see CustomObject.delete()), which would silently paper over a lingering registry
        # mismatch. A GET here never calls delete() at all, so it exercises the raw, unrepaired
        # state directly -- exactly what crashed with "ValueError: Cannot query ...: Must be ...
        # instance." in the original report, and what the class-identity assertion above cannot
        # by itself confirm is actually reachable through the UI.
        content_type = ContentType.objects.get_for_model(final_model)
        obj_perm = ObjectPermission(name='poly-force-delete-view', actions=['view', 'delete'])
        obj_perm.save()
        obj_perm.users.add(self.user)
        obj_perm.object_types.add(content_type)
        delete_url = reverse(
            'plugins:netbox_custom_objects:customobject_delete',
            kwargs={'custom_object_type': cot.slug, 'pk': obj.pk},
        )
        response = self.client.get(delete_url)
        self.assertEqual(
            response.status_code,
            200,
            f'delete-confirmation GET must render, not crash (got {response.status_code})',
        )

        obj.delete()  # Must not raise ValueError: "Cannot query ...: Must be ... instance."

    def test_concurrent_double_submit_does_not_deadlock(self):
        """
        Two threads independently calling CustomObjectTypeField.objects.create() for the
        identical (custom_object_type, name) at once -- a genuinely reachable scenario (e.g. a
        retried request, or a doubly-clicked "save" button) -- must not deadlock.

        This is a real deadlock, not just a slow race, when CustomObjectType._global_lock spans
        create_polymorphic_m2m_table()'s DDL: both threads build+register a through model for the
        SAME physical table before either knows which one will win the (name, custom_object_type)
        UniqueConstraint, so whichever thread's schema_editor.create_model() runs second blocks at
        the Postgres level waiting for the first thread's uncommitted CREATE TABLE (same table
        name) to resolve. If the first thread still needs the *same* Python lock afterward (its
        own save() calls CustomObjectType.clear_model_cache(), which acquires it) before it can
        commit and release that Postgres-level wait, neither thread can make progress. Confirmed
        empirically: this exact scenario hung a live test run before the lock was narrowed to
        cover only the build+register+repoint step, not the DDL.

        The losing thread fails in one of two ways, depending on timing: at the field row's
        (name, custom_object_type) UniqueConstraint (IntegrityError, also what concurrent CREATE
        TABLEs for one name raise), or, if it checked for the through table before the winner
        committed but creates it after, at its own CREATE TABLE (DuplicateTable).
        """
        cot = self.create_simple_custom_object_type(name='doublesubmit', slug='double-submit')
        self_ot = ObjectType.objects.get_for_model(cot.get_model())

        results = {}

        def create_field(key):
            threading.current_thread().name = key
            try:
                field = CustomObjectTypeField.objects.create(
                    custom_object_type=cot,
                    name='depends_on',
                    label='Depends On',
                    type=CustomFieldTypeChoices.TYPE_MULTIOBJECT,
                    is_polymorphic=True,
                )
                field.related_object_types.set([self_ot, self.site_ot])
                results[key] = {'field': field}
            except IntegrityError as e:
                # Expected for exactly one of the two: the (name, custom_object_type)
                # UniqueConstraint has only one winner.
                results[key] = {'lost': e}
            except ProgrammingError as e:
                # The other way to lose (see the docstring); anything else is a real error.
                results[key] = {'lost': e} if isinstance(e.__cause__, DuplicateTable) else {'error': e}
            except Exception as e:  # noqa: BLE001 - surfaced via the assertion below
                results[key] = {'error': e}
            finally:
                connection.close()

        t_a = threading.Thread(target=create_field, args=('A',), name='A')
        t_b = threading.Thread(target=create_field, args=('B',), name='B')
        t_a.start()
        t_b.start()
        t_a.join(timeout=15)
        t_b.join(timeout=15)

        self.assertFalse(t_a.is_alive(), 'thread A did not complete within the join timeout (deadlocked?)')
        self.assertFalse(t_b.is_alive(), 'thread B did not complete within the join timeout (deadlocked?)')
        for key, result in results.items():
            self.assertNotIn('error', result, f'thread {key} raised an unexpected error: {result.get("error")!r}')

        succeeded = [key for key, result in results.items() if 'field' in result]
        lost = [key for key, result in results.items() if 'lost' in result]
        self.assertEqual(len(succeeded), 1, f'expected exactly one winner: {results!r}')
        self.assertEqual(len(lost), 1, f'expected exactly one loser: {results!r}')

    def test_field_creation_via_public_save_path_with_two_type_setup_yields_consistent_through_model(self):
        """
        Variant of test_field_creation_racing_concurrent_readers_yields_consistent_through_model
        above, using the reported two-type Custom Object setup (a self-reference plus a second,
        genuine Custom Object Type -- not just a single core dcim model) instead of one type, to
        match the exact reported repro. Goes through the public field-save path
        (CustomObjectTypeField.objects.create()) throughout, with no private-helper shortcut.

        A deterministic, forced-interleaving version of *this specific* scenario (two threads
        both calling CustomObjectTypeField.objects.create() for the identical (name,
        custom_object_type) at once, paused via the same mocked apps.register_model() technique
        as test_forced_registration_interleaving_stays_consistent) was attempted and abandoned:
        it can genuinely deadlock rather than just race. Both threads target the same physical
        through table, so the second thread's CREATE TABLE blocks at the Postgres level on the
        first thread's still-open transaction; CustomObjectType._global_lock is held by the first
        thread across that same window (with the fix in place); and if anything downstream in the
        first thread's own save() needs that lock again (e.g. a signal handler calling
        get_model()), neither thread can make progress -- confirmed by hanging an actual test
        run. Real thread-scheduling luck, exercised here instead via 12 looping readers (matching
        the existing single-type test above), cannot deadlock this way: no reader ever holds
        transaction.atomic() open across a paused lock acquisition.
        """
        cot = self.create_simple_custom_object_type(name='polypublic', slug='poly-public')
        other_cot = self.create_simple_custom_object_type(name='polypublicother', slug='poly-public-other')
        self_ot = ObjectType.objects.get_for_model(cot.get_model())
        other_ot = ObjectType.objects.get_for_model(other_cot.get_model())

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
            field = CustomObjectTypeField.objects.create(
                custom_object_type=cot,
                name='depends_on',
                label='Depends On',
                type=CustomFieldTypeChoices.TYPE_MULTIOBJECT,
                is_polymorphic=True,
            )
            field.related_object_types.set([self_ot, other_ot])
        finally:
            stop.set()
            for t in readers:
                t.join()

        self.assertEqual(
            reader_errors,
            [],
            'concurrent get_model() calls must not raise while a polymorphic multiobject field '
            'with the reported two-type setup is being created',
        )
        self.assertEqual(set(field.related_object_types.all()), {self_ot, other_ot})

        # get_model() and the through model's "source" FK must agree on which class is canonical
        # -- a mismatch means a stale model class was used.
        final_model = CustomObjectType.objects.get(pk=cot.pk).get_model()
        through_model = apps.get_model(APP_LABEL, field.through_model_name)
        source_field = through_model._meta.get_field('source')
        self.assertIs(
            source_field.remote_field.model,
            final_model,
            "the registered through model's source FK must point at the model class "
            'get_model() currently returns, not an orphaned duplicate from a losing thread',
        )

        obj = final_model.objects.create(name='Instance 1')
        obj.depends_on.set([other_cot.get_model().objects.create(name='Public Race Target')])
        obj.delete()  # Must not raise ValueError: "Cannot query ...: Must be ... instance."


class FKConstraintHookRaceTestCase(TransactionCleanupMixin, CustomObjectsTestCase, TransactionTestCase):
    """Two requests saving the same Object field race through the FK-constraint hook."""

    def test_delayed_hook_applies_latest_on_delete(self):
        """Request 1's hook is delayed before it takes the lock; request 2 saves and
        its hook finishes first.  Request 1's hook must still apply the latest value."""
        cot = self.create_simple_custom_object_type(name='race', slug='races')
        field = self.create_custom_object_type_field(
            cot,
            name='site',
            type='object',
            related_object_type=self.get_site_object_type(),
            on_delete_behavior=ObjectFieldOnDeleteChoices.SET_NULL,
        )
        original = CustomObjectType._lock_database_table
        paused, resume = threading.Event(), threading.Event()

        def delayed(self_cot, schema_conn):
            if threading.current_thread().name == 'request-1' and not paused.is_set():
                paused.set()
                resume.wait(10)
            return original(self_cot, schema_conn)

        def request_1():
            try:
                self._set(field, on_delete_behavior=ObjectFieldOnDeleteChoices.CASCADE)
            finally:
                connections.close_all()

        with mock.patch.object(CustomObjectType, '_lock_database_table', delayed):
            thread = threading.Thread(target=request_1, name='request-1')
            thread.start()
            self.assertTrue(paused.wait(10))
            self._set(field, on_delete_behavior=ObjectFieldOnDeleteChoices.PROTECT)
            resume.set()
            thread.join(10)

        self.assertEqual(CustomObjectTypeField.objects.get(pk=field.pk).on_delete_behavior, 'protect')
        self.assertEqual(_fk_constraints(cot.get_database_table_name(), 'site_id'), [('dcim_site', 'r')])
