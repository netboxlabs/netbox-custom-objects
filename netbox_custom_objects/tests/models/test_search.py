"""
Tests for custom objects in the search index, including the reindex job.
"""

import uuid
from unittest.mock import patch

from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.db.models.signals import post_delete
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

import netbox_custom_objects as nco_package
import netbox_custom_objects.models as nco_models
from core.choices import JobIntervalChoices, JobStatusChoices
from core.models import Job
from dcim.models import Site
from extras.models import CachedValue
from netbox.search import registry
from netbox.search.backends import get_backend
from netbox_custom_objects.constants import DISPLAY_EXPRESSION_SEARCH_WEIGHT
from netbox_custom_objects.filtersets import get_filterset_class
from netbox_custom_objects.jobs import RefreshDisplayExpressionSearchJob, ReindexCustomObjectTypeJob
from netbox_custom_objects.models import CustomObjectType, CustomObjectTypeField

from ..base import CustomObjectsTestCase


class SearchReindexTestCase(CustomObjectsTestCase, TestCase):
    """Test that ReindexCustomObjectTypeJob is triggered when field search weights change."""

    def setUp(self):
        super().setUp()
        self.cot = self.create_custom_object_type(
            name='ReindexTest',
            slug='reindex-test',
        )
        self.field = self.create_custom_object_type_field(
            self.cot,
            name='title',
            label='Title',
            type='text',
            search_weight=500,
        )
        self.model = self.cot.get_model()
        self.instance = self.model.objects.create(title='Hello World')

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
        field.label = 'Modified Title'
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
                    name='subtitle',
                    label='Subtitle',
                    type='text',
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
                    name='notes',
                    label='Notes',
                    type='text',
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
            CachedValue.objects.filter(object_type=ct, object_id=self.instance.pk, field='title', weight=500).count(),
            1,
        )

        # Save the field with a new weight (suppress automatic enqueue)
        field = CustomObjectTypeField.objects.get(pk=self.field.pk)
        field.search_weight = 100
        with patch.object(ReindexCustomObjectTypeJob, 'enqueue'), self.captureOnCommitCallbacks(execute=True):
            field.save()

        # Run the job synchronously via immediate=True
        ReindexCustomObjectTypeJob.enqueue(cot_id=self.cot.pk, immediate=True)

        self.assertEqual(
            CachedValue.objects.filter(object_type=ct, object_id=self.instance.pk, field='title', weight=100).count(),
            1,
        )
        self.assertEqual(
            CachedValue.objects.filter(object_type=ct, object_id=self.instance.pk, field='title', weight=500).count(),
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
        with patch('django_rq.get_queue'):
            first_job = ReindexCustomObjectTypeJob.enqueue(cot_id=self.cot.pk)
        # Simulate the first job still pending
        first_job.status = JobStatusChoices.STATUS_PENDING
        first_job.save(update_fields=['status'])

        with patch('django_rq.get_queue'):
            second_job = ReindexCustomObjectTypeJob.enqueue(cot_id=self.cot.pk)

        self.assertEqual(first_job.pk, second_job.pk)


class DisplayExpressionSearchTestCase(CustomObjectsTestCase, TestCase):
    """The rendered display_expression is cached for search and matched by quick search (q)."""

    def setUp(self):
        super().setUp()
        self.cot = self.create_custom_object_type(
            name='Service',
            slug='display-search',
            display_expression='{{ site.name }} / {{ name }}',
        )
        self.create_custom_object_type_field(self.cot, name='name', type='text', primary=True, required=True)
        self.create_custom_object_type_field(
            self.cot,
            name='site',
            type='object',
            related_object_type=self.get_site_object_type(),
        )
        self.model = self.cot.get_model()
        self.site = Site.objects.create(name='Alpha', slug='alpha')
        self.obj = self.model.objects.create(name='svc1', site=self.site)
        self.object_type = ContentType.objects.get_for_model(self.model)

    def _cached_display(self):
        return list(
            CachedValue.objects.filter(
                object_type=self.object_type, object_id=self.obj.pk, field='_display'
            ).values_list('value', 'weight')
        )

    def _quick_search(self, value):
        filterset = get_filterset_class(self.model)({'q': value}, self.model.objects.all())
        return list(filterset.qs.values_list('pk', flat=True))

    def test_rendered_display_is_cached(self):
        get_backend().cache(self.model.objects.all())
        self.assertEqual(self._cached_display(), [('Alpha / svc1', DISPLAY_EXPRESSION_SEARCH_WEIGHT)])

    def test_nothing_cached_without_display_expression(self):
        self.cot.display_expression = ''
        with patch.object(ReindexCustomObjectTypeJob, 'enqueue'):
            self.cot.save()
        self.cot.refresh_from_db()
        model = self.cot.get_model()
        get_backend().cache(model.objects.all())
        self.assertEqual(self._cached_display(), [])

    def test_quick_search_matches_related_values_in_display(self):
        get_backend().cache(self.model.objects.all())
        # "Alpha" comes only from the related site, not from the object's own columns.
        self.assertEqual(self._quick_search('alpha'), [self.obj.pk])
        self.assertEqual(self._quick_search('svc1'), [self.obj.pk])
        self.assertEqual(self._quick_search('nothing'), [])

    def test_reindex_picks_up_related_changes(self):
        get_backend().cache(self.model.objects.all())
        self.site.name = 'Beta'
        self.site.save()
        # Renaming the site doesn't touch the custom object, so its cached display is stale...
        self.assertEqual(self._quick_search('beta'), [])
        # ...until the type is reindexed, as the hourly job does.
        ReindexCustomObjectTypeJob.enqueue(cot_id=self.cot.pk, immediate=True)
        self.assertEqual(self._quick_search('beta'), [self.obj.pk])

    def test_reindex_enqueued_when_display_expression_changes(self):
        cot = CustomObjectType.objects.get(pk=self.cot.pk)
        cot.display_expression = '{{ name }}'
        with patch.object(ReindexCustomObjectTypeJob, 'enqueue') as mock_enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                cot.save()
        mock_enqueue.assert_called_once_with(cot_id=self.cot.pk)

    def test_reindex_not_enqueued_for_other_changes(self):
        cot = CustomObjectType.objects.get(pk=self.cot.pk)
        cot.description = 'Changed'
        with patch.object(ReindexCustomObjectTypeJob, 'enqueue') as mock_enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                cot.save()
                cot.save(update_fields=['cache_timestamp'])
        mock_enqueue.assert_not_called()

    def _row_ids(self, display):
        rows = CachedValue.objects.filter(object_type=self.object_type)
        rows = rows.filter(field='_display') if display else rows.exclude(field='_display')
        return set(rows.values_list('pk', flat=True))

    def _run_hourly_job(self):
        job = Job.objects.create(name='Refresh', job_id=uuid.uuid4())
        RefreshDisplayExpressionSearchJob(job).run()

    def test_hourly_job_refreshes_only_display_rows(self):
        self.assertEqual(
            registry['system_jobs'][RefreshDisplayExpressionSearchJob]['interval'],
            JobIntervalChoices.INTERVAL_HOURLY,
        )
        get_backend().cache(self.model.objects.all())
        other_rows = self._row_ids(display=False)
        self.assertTrue(other_rows)
        self.site.name = 'Beta'
        self.site.save()

        self._run_hourly_job()

        self.assertEqual(self._cached_display(), [('Beta / svc1', DISPLAY_EXPRESSION_SEARCH_WEIGHT)])
        self.assertEqual(self._quick_search('beta'), [self.obj.pk])
        # The object's other cached values are left in place.
        self.assertEqual(self._row_ids(display=False), other_rows)

        # Unchanged display names aren't rewritten.
        display_rows = self._row_ids(display=True)
        self._run_hourly_job()
        self.assertEqual(self._row_ids(display=True), display_rows)

    def test_hourly_job_renders_only_missing_names_without_related_fields(self):
        self.create_custom_object_type(name='NoExpression', slug='no-expression')
        own_fields_only = self.create_custom_object_type(
            name='OwnFields',
            slug='own-fields',
            display_expression='{{ name }}',
        )
        self.create_custom_object_type_field(own_fields_only, name='name', type='text', primary=True)
        # A related field the expression doesn't use can't make it drift.
        self.create_custom_object_type_field(
            own_fields_only,
            name='site',
            type='object',
            related_object_type=self.get_site_object_type(),
        )
        with patch('netbox_custom_objects.jobs.refresh_display_search_cache') as mock_refresh:
            self._run_hourly_job()
        self.assertEqual(
            sorted((call.args[0].pk, call.kwargs['missing_only']) for call in mock_refresh.call_args_list),
            [(self.cot.pk, False), (own_fields_only.pk, True)],
        )

    def test_hourly_job_backfills_objects_without_a_cached_name(self):
        cot = self.create_custom_object_type(name='OwnFields', slug='own-fields', display_expression='#{{ name }}')
        self.create_custom_object_type_field(cot, name='name', type='text', primary=True)
        model = cot.get_model()
        indexed, unindexed = model.objects.create(name='a'), model.objects.create(name='b')
        object_type = ContentType.objects.get_for_model(model)
        rows = CachedValue.objects.filter(object_type=object_type, field='_display')
        get_backend().cache(model.objects.all())
        # As if "b" was saved before display names were indexed.
        rows.filter(object_id=unindexed.pk).delete()
        indexed_row = rows.get(object_id=indexed.pk).pk

        self._run_hourly_job()

        self.assertEqual(
            sorted(rows.values_list('object_id', 'value')),
            [(indexed.pk, '#a'), (unindexed.pk, '#b')],
        )
        self.assertTrue(rows.filter(pk=indexed_row).exists())

    def test_hourly_job_deletes_stale_names_without_delete_signals(self):
        get_backend().cache(self.model.objects.all())
        self.site.name = 'Beta'
        self.site.save()
        deleted = []

        def receiver(sender, instance, **kwargs):
            deleted.append(instance)

        post_delete.connect(receiver, sender=CachedValue)
        try:
            self._run_hourly_job()
        finally:
            post_delete.disconnect(receiver, sender=CachedValue)
        self.assertEqual(deleted, [])
        self.assertEqual(self._cached_display(), [('Beta / svc1', DISPLAY_EXPRESSION_SEARCH_WEIGHT)])

    def test_rendering_skips_fields_the_expression_does_not_use(self):
        self.create_custom_object_type_field(
            self.cot,
            name='sites',
            type='multiobject',
            related_object_type=self.get_site_object_type(),
        )
        cot = CustomObjectType.objects.get(pk=self.cot.pk)
        cot.display_expression = '{{ name }}'
        with patch.object(ReindexCustomObjectTypeJob, 'enqueue'):
            cot.save()
        obj = cot.get_model().objects.get(pk=self.obj.pk)
        str(obj)  # load the type and its field definitions
        with self.assertNumQueries(0):
            self.assertEqual(str(obj), 'svc1')

    def test_caching_renders_display_once(self):
        with patch.object(
            nco_models.CustomObject,
            '_render_display_expression',
            autospec=True,
            side_effect=nco_models.CustomObject._render_display_expression,
        ) as mock_render:
            get_backend().cache(self.model.objects.get(pk=self.obj.pk))
        self.assertEqual(mock_render.call_count, 1)
        self.assertEqual(self._cached_display(), [('Alpha / svc1', DISPLAY_EXPRESSION_SEARCH_WEIGHT)])


class SearchIndexRegistrationTestCase(CustomObjectsTestCase, TestCase):
    """A cached model's SearchIndex is re-registered without a query, and stays current."""

    def setUp(self):
        super().setUp()
        self.cot = self.create_custom_object_type(name='Indexed', slug='indexed')
        self.create_custom_object_type_field(
            self.cot,
            name='name',
            type='text',
            primary=True,
            required=True,
            search_weight=100,
        )
        self.field = self.create_custom_object_type_field(self.cot, name='serial', type='text', search_weight=500)
        self.cot.refresh_from_db()
        self.model = self.cot.get_model()
        self.label = f'netbox_custom_objects.{self.cot.get_table_model_name(self.cot.id).lower()}'

    def _indexed_fields(self):
        return dict(registry['search'][self.label].fields)

    def test_cached_model_runs_no_query(self):
        with self.assertNumQueries(0):
            self.cot.get_model()

    def test_listing_the_app_models_runs_one_query(self):
        """The app's get_models() override, which apps.clear_cache() runs, only lists the types."""
        other = self.create_custom_object_type(name='Other', slug='other')
        self.create_custom_object_type_field(other, name='name', type='text', primary=True, required=True)
        other.refresh_from_db()
        other.get_model()
        app_config = apps.get_app_config('netbox_custom_objects')
        # Tests skip generated models here; list them as a running instance does.
        with (
            patch.object(nco_package, '_app_ready', True),
            patch.object(app_config, 'should_skip_dynamic_model_creation', return_value=False),
            CaptureQueriesContext(connection) as queries,
        ):
            models = list(app_config.get_models())
        self.assertIn(self.model, models)
        # Inside a test's transaction, atomic() adds a savepoint pair around the query.
        statements = [q['sql'] for q in queries if 'SAVEPOINT' not in q['sql']]
        self.assertEqual(len(statements), 1, statements)

    def test_cached_model_reuses_its_index(self):
        search_index = registry['search'][self.label]
        registry['search'].pop(self.label)
        self.cot.get_model()
        self.assertIs(registry['search'][self.label], search_index)

    def test_index_follows_a_changed_search_weight(self):
        field = CustomObjectTypeField.objects.get(pk=self.field.pk)
        field.search_weight = 0
        with patch.object(ReindexCustomObjectTypeJob, 'enqueue'):
            field.save()
        self.cot.refresh_from_db()
        self.cot.get_model()
        self.assertEqual(self._indexed_fields(), {'name': 100})

    def test_index_follows_a_display_expression_set_elsewhere(self):
        """Another worker's display_expression change doesn't bump cache_timestamp, so the cached model is reused."""
        CustomObjectType.objects.filter(pk=self.cot.pk).update(display_expression='{{ name }}')
        cot = CustomObjectType.objects.get(pk=self.cot.pk)
        self.assertIs(cot.get_model(), self.model)
        self.assertEqual(self._indexed_fields()['_display'], DISPLAY_EXPRESSION_SEARCH_WEIGHT)

        CustomObjectType.objects.filter(pk=self.cot.pk).update(display_expression='')
        cot = CustomObjectType.objects.get(pk=self.cot.pk)
        cot.get_model()
        self.assertNotIn('_display', self._indexed_fields())


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
            name='StubTarget',
            slug='stub-target',
        )
        self.create_custom_object_type_field(
            self.target_cot,
            name='name',
            label='Name',
            type='text',
            primary=True,
            required=True,
            search_weight=500,
        )
        # This object field has search_weight=500 (the default).  When the stub
        # model is generated (skip_object_fields=True), this field is ABSENT.
        # The old code would register it in the search index anyway → crash.
        self.create_custom_object_type_field(
            self.target_cot,
            name='device',
            label='Device',
            type='object',
            related_object_type=self.get_device_object_type(),
            search_weight=500,
        )

        # --- Source COT: has an object field pointing to the target COT ---
        self.source_cot = self.create_custom_object_type(
            name='StubSource',
            slug='stub-source',
        )
        self.create_custom_object_type_field(
            self.source_cot,
            name='name',
            label='Name',
            type='text',
            primary=True,
            required=True,
        )
        # Creating this field calls source_cot.get_model(), which internally
        # calls target_cot.get_model(skip_object_fields=True) to resolve the FK.
        # That caches the stub model for target_cot.
        self.create_custom_object_type_field(
            self.source_cot,
            name='target_ref',
            label='Target Reference',
            type='object',
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
        instance = target_model.objects.create(name='Stub Target Instance')

        # Basic sanity: the instance was persisted.
        self.assertEqual(target_model.objects.filter(pk=instance.pk).count(), 1)

    def test_stub_model_search_index_excludes_absent_fields(self):
        """The search index registered for a stub model must not reference absent fields.

        When the stub is generated with skip_object_fields=True, object/multiobject
        fields are excluded from the model class.  The search index must only contain
        fields that are actually present.
        """
        # Force stub semantics: clear any cached full model and re-register the
        # search index against a fresh stub.  This mirrors the cross-COT scenario
        # the regression covers — B's stub gets cached before any caller asks for
        # the full model, so register_custom_object_search_index sees only the
        # stub fields.  Without this, CustomObjectTypeField.save() would have
        # already cached a full model with the OBJECT field.
        self.target_cot.clear_model_cache(self.target_cot.id)
        stub_model = self.target_cot.get_model(skip_object_fields=True)
        self.target_cot.register_custom_object_search_index(stub_model)

        label = f'netbox_custom_objects.{self.target_cot.get_table_model_name(self.target_cot.id).lower()}'
        search_index = registry['search'].get(label)

        self.assertIsNotNone(search_index, 'Search index should be registered for the target COT model')

        # fields is a list of (name, weight) tuples
        indexed_field_names = {f[0] for f in search_index.fields}

        # 'name' (text) is present on the stub → must be indexed.
        self.assertIn('name', indexed_field_names)

        # 'device' (object) is absent from the stub → must NOT be indexed.
        self.assertNotIn(
            'device',
            indexed_field_names,
            'Object-type fields absent from the stub must be excluded from the search index',
        )
