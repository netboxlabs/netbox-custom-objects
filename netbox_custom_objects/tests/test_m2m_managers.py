"""
Tests for the write paths (add / remove / set / clear) of CustomManyToManyManager
and PolymorphicManyToManyManager.

TransactionTestCase so the transaction tests run in autocommit mode: in a
TestCase the per-test transaction would roll back partial writes on its own
and hide a manager that doesn't wrap its writes in one.
"""

from django.db import connection
from django.db.models.signals import m2m_changed
from django.test import TransactionTestCase
from django.test.utils import CaptureQueriesContext

from core.models import ObjectType
from dcim.models import Site
from ipam.choices import PrefixStatusChoices
from ipam.models import Prefix
from tenancy.models import Tenant

from netbox_custom_objects.models import CustomObjectType, CustomObjectTypeField
from netbox_custom_objects.tests.base import CustomObjectsTestCase, TransactionCleanupMixin


class _SignalRecorder:
    """Collect (action, pk_set) for every m2m_changed sent by ``sender``."""

    def __init__(self, sender):
        self.sender = sender
        self.calls = []

    def __call__(self, sender, action, pk_set, **kwargs):
        if sender is self.sender:
            self.calls.append((action, set(pk_set) if pk_set is not None else None))

    def __enter__(self):
        m2m_changed.connect(self, weak=False)
        return self

    def __exit__(self, *exc):
        m2m_changed.disconnect(self)


class _FailOn:
    """m2m_changed receiver that raises on one action."""

    def __init__(self, action):
        self.action = action

    def __call__(self, action, **kwargs):
        if action == self.action:
            raise RuntimeError(f'boom on {action}')

    def __enter__(self):
        m2m_changed.connect(self, weak=False)
        return self

    def __exit__(self, *exc):
        m2m_changed.disconnect(self)


def _statements(ctx, verb, table):
    return [q['sql'] for q in ctx.captured_queries if q['sql'].startswith(verb) and f'"{table}"' in q['sql']]


class CustomManyToManyManagerWriteTestCase(TransactionCleanupMixin, CustomObjectsTestCase, TransactionTestCase):
    def setUp(self):
        super().setUp()
        self.cot = CustomObjectType.objects.create(name='M2MWrite', slug='m2m-write', verbose_name_plural='M2M Writes')
        CustomObjectTypeField.objects.create(
            custom_object_type=self.cot,
            name='name',
            label='Name',
            type='text',
            primary=True,
            required=True,
        )
        CustomObjectTypeField.objects.create(
            custom_object_type=self.cot,
            name='sites',
            label='Sites',
            type='multiobject',
            related_object_type=ObjectType.objects.get(app_label='dcim', model='site'),
        )
        self.model = self.cot.get_model()
        self.through = self.model._meta.get_field('sites').remote_field.through
        self.sites = [Site.objects.create(name=f'Site {i}', slug=f'site-{i}') for i in range(3)]
        self.obj = self.model.objects.create(name='one')

    def _linked(self):
        return set(self.through.objects.filter(source_id=self.obj.pk).values_list('target_id', flat=True))

    def test_add_inserts_in_one_statement(self):
        with CaptureQueriesContext(connection) as ctx:
            self.obj.sites.add(*self.sites)
        self.assertEqual(len(_statements(ctx, 'INSERT', self.through._meta.db_table)), 1)
        self.assertEqual(self._linked(), {s.pk for s in self.sites})

    def test_add_signals_report_only_new_links(self):
        self.obj.sites.add(self.sites[0])
        with _SignalRecorder(self.through) as rec:
            self.obj.sites.add(self.sites[0], self.sites[1])
        new = {self.sites[1].pk}
        self.assertEqual(rec.calls, [('pre_add', new), ('post_add', new)])

    def test_add_already_linked_sends_no_signals(self):
        self.obj.sites.add(self.sites[0])
        with _SignalRecorder(self.through) as rec:
            self.obj.sites.add(self.sites[0])
        self.assertEqual(rec.calls, [])

    def test_add_accepts_raw_pks(self):
        self.obj.sites.add(self.sites[0].pk, str(self.sites[1].pk))
        self.assertEqual(self._linked(), {self.sites[0].pk, self.sites[1].pk})

    def test_add_wrong_model_raises_and_writes_nothing(self):
        # A Tenant whose pk matches a Site's must not link that Site.
        tenant = Tenant.objects.create(name='T', slug='t')
        with self.assertRaises(TypeError):
            self.obj.sites.add(self.sites[0], tenant)
        self.assertEqual(self._linked(), set())

    def test_set_wrong_model_raises_and_writes_nothing(self):
        self.obj.sites.add(self.sites[0])
        tenant = Tenant.objects.create(name='T', slug='t')
        with self.assertRaises(TypeError):
            self.obj.sites.set([self.sites[1], tenant])
        self.assertEqual(self._linked(), {self.sites[0].pk})

    def test_add_unsaved_raises(self):
        with self.assertRaises(ValueError):
            self.obj.sites.add(Site(name='unsaved', slug='unsaved'))

    def test_remove_deletes_in_one_statement_and_reports_linked_only(self):
        self.obj.sites.add(*self.sites)
        stranger = Site.objects.create(name='Stranger', slug='stranger')
        with CaptureQueriesContext(connection) as ctx, _SignalRecorder(self.through) as rec:
            self.obj.sites.remove(self.sites[0], self.sites[1], stranger)
        self.assertEqual(len(_statements(ctx, 'DELETE', self.through._meta.db_table)), 1)
        gone = {self.sites[0].pk, self.sites[1].pk}
        self.assertEqual(rec.calls, [('pre_remove', gone), ('post_remove', gone)])
        self.assertEqual(self._linked(), {self.sites[2].pk})

    def test_writes_invalidate_prefetch_cache(self):
        obj = self.model.objects.prefetch_related('sites').get(pk=self.obj.pk)
        self.assertEqual(list(obj.sites.all()), [])
        obj.sites.add(self.sites[0])
        self.assertEqual(list(obj.sites.all()), [self.sites[0]])

        obj = self.model.objects.prefetch_related('sites').get(pk=self.obj.pk)
        obj.sites.set([self.sites[1]])
        self.assertEqual(list(obj.sites.all()), [self.sites[1]])

        obj = self.model.objects.prefetch_related('sites').get(pk=self.obj.pk)
        obj.sites.remove(self.sites[1])
        self.assertEqual(list(obj.sites.all()), [])

        obj.sites.add(self.sites[2])
        obj = self.model.objects.prefetch_related('sites').get(pk=self.obj.pk)
        obj.sites.clear()
        self.assertEqual(list(obj.sites.all()), [])

    def test_rejected_write_keeps_prefetch_cache(self):
        self.obj.sites.add(self.sites[0])
        obj = self.model.objects.prefetch_related('sites').get(pk=self.obj.pk)
        tenant = Tenant.objects.create(name='T', slug='t')
        for write in (obj.sites.add, obj.sites.remove):
            with self.assertRaises(TypeError):
                write(tenant)
        with self.assertNumQueries(0):
            self.assertEqual(list(obj.sites.all()), [self.sites[0]])

    def test_set_is_atomic(self):
        self.obj.sites.add(self.sites[0])
        with _FailOn('post_add'), self.assertRaises(RuntimeError):
            self.obj.sites.set([self.sites[1]])
        # The remove of sites[0] must have rolled back with the failed add.
        self.assertEqual(self._linked(), {self.sites[0].pk})

    def test_set_clear_is_atomic(self):
        self.obj.sites.add(self.sites[0])
        with _FailOn('post_add'), self.assertRaises(RuntimeError):
            self.obj.sites.set([self.sites[1]], clear=True)
        self.assertEqual(self._linked(), {self.sites[0].pk})

    def test_set_diffs(self):
        self.obj.sites.add(self.sites[0], self.sites[1])
        with _SignalRecorder(self.through) as rec:
            self.obj.sites.set([self.sites[1], self.sites[2]])
        self.assertEqual(
            rec.calls,
            [
                ('pre_remove', {self.sites[0].pk}),
                ('post_remove', {self.sites[0].pk}),
                ('pre_add', {self.sites[2].pk}),
                ('post_add', {self.sites[2].pk}),
            ],
        )
        self.assertEqual(self._linked(), {self.sites[1].pk, self.sites[2].pk})


class PolymorphicManyToManyManagerWriteTestCase(TransactionCleanupMixin, CustomObjectsTestCase, TransactionTestCase):
    def setUp(self):
        super().setUp()
        self.cot = CustomObjectType.objects.create(
            name='PolyWrite',
            slug='poly-write',
            verbose_name_plural='Poly Writes',
        )
        CustomObjectTypeField.objects.create(
            custom_object_type=self.cot,
            name='name',
            label='Name',
            type='text',
            primary=True,
            required=True,
        )
        field = CustomObjectTypeField.objects.create(
            custom_object_type=self.cot,
            name='targets',
            label='Targets',
            type='multiobject',
            is_polymorphic=True,
        )
        field.related_object_types.set(
            [
                ObjectType.objects.get(app_label='dcim', model='site'),
                ObjectType.objects.get(app_label='ipam', model='prefix'),
            ]
        )
        self.model = self.cot.get_model()
        self.through = self.model().targets._get_through_model()
        self.site = Site.objects.create(name='Site', slug='site')
        self.site2 = Site.objects.create(name='Site 2', slug='site-2')
        self.prefix = Prefix.objects.create(prefix='10.0.0.0/8', status=PrefixStatusChoices.STATUS_ACTIVE)
        self.obj = self.model.objects.create(name='one')

    def _linked(self):
        return set(self.through.objects.filter(source_id=self.obj.pk).values_list('content_type_id', 'object_id'))

    def _key(self, obj):
        return ObjectType.objects.get_for_model(obj).pk, obj.pk

    def test_add_inserts_in_one_statement(self):
        with CaptureQueriesContext(connection) as ctx:
            self.obj.targets.add(self.site, self.site2, self.prefix)
        self.assertEqual(len(_statements(ctx, 'INSERT', self.through._meta.db_table)), 1)
        self.assertEqual(self._linked(), {self._key(self.site), self._key(self.site2), self._key(self.prefix)})

    def test_add_signals_report_only_new_links(self):
        self.obj.targets.add(self.site)
        with _SignalRecorder(self.through) as rec:
            self.obj.targets.add(self.site, self.prefix)
        new = {self.prefix.pk}
        self.assertEqual(rec.calls, [('pre_add', new), ('post_add', new)])

    def test_add_disallowed_type_raises_and_writes_nothing(self):
        tenant = Tenant.objects.create(name='T', slug='t')
        with self.assertRaises(TypeError):
            self.obj.targets.add(self.site, tenant)
        self.assertEqual(self._linked(), set())

    def test_add_raw_pk_raises(self):
        with self.assertRaises(TypeError):
            self.obj.targets.add(self.site.pk)

    def test_remove_deletes_in_one_statement_and_reports_linked_only(self):
        self.obj.targets.add(self.site, self.site2, self.prefix)
        with CaptureQueriesContext(connection) as ctx, _SignalRecorder(self.through) as rec:
            self.obj.targets.remove(self.site, self.prefix)
        self.assertEqual(len(_statements(ctx, 'DELETE', self.through._meta.db_table)), 1)
        gone = {self.site.pk, self.prefix.pk}
        self.assertEqual(rec.calls, [('pre_remove', gone), ('post_remove', gone)])
        self.assertEqual(self._linked(), {self._key(self.site2)})

    def test_writes_invalidate_prefetch_cache(self):
        obj = self.model.objects.prefetch_related('targets').get(pk=self.obj.pk)
        self.assertEqual(list(obj.targets.all()), [])
        obj.targets.add(self.site)
        self.assertEqual(list(obj.targets.all()), [self.site])

        obj = self.model.objects.prefetch_related('targets').get(pk=self.obj.pk)
        obj.targets.set([self.prefix])
        self.assertEqual(list(obj.targets.all()), [self.prefix])

        obj = self.model.objects.prefetch_related('targets').get(pk=self.obj.pk)
        obj.targets.remove(self.prefix)
        self.assertEqual(list(obj.targets.all()), [])

        obj.targets.add(self.site)
        obj = self.model.objects.prefetch_related('targets').get(pk=self.obj.pk)
        obj.targets.clear()
        self.assertEqual(list(obj.targets.all()), [])

    def test_rejected_write_keeps_prefetch_cache(self):
        self.obj.targets.add(self.site)
        obj = self.model.objects.prefetch_related('targets').get(pk=self.obj.pk)
        tenant = Tenant.objects.create(name='T', slug='t')
        for write in (obj.targets.add, obj.targets.remove):
            with self.assertRaises(TypeError):
                write(tenant)
        with self.assertNumQueries(0):
            self.assertEqual(list(obj.targets.all()), [self.site])

    def test_set_is_atomic(self):
        self.obj.targets.add(self.site)
        with _FailOn('post_add'), self.assertRaises(RuntimeError):
            self.obj.targets.set([self.prefix])
        self.assertEqual(self._linked(), {self._key(self.site)})

    def test_set_clear_is_atomic(self):
        self.obj.targets.add(self.site)
        with _FailOn('post_add'), self.assertRaises(RuntimeError):
            self.obj.targets.set([self.prefix], clear=True)
        self.assertEqual(self._linked(), {self._key(self.site)})

    def test_set_diffs(self):
        self.obj.targets.add(self.site, self.site2)
        with _SignalRecorder(self.through) as rec:
            self.obj.targets.set([self.site2, self.prefix])
        self.assertEqual(
            rec.calls,
            [
                ('pre_remove', {self.site.pk}),
                ('post_remove', {self.site.pk}),
                ('pre_add', {self.prefix.pk}),
                ('post_add', {self.prefix.pk}),
            ],
        )
        self.assertEqual(self._linked(), {self._key(self.site2), self._key(self.prefix)})
