"""Tests for a plain Multi-object field added in main after a branch, then synced into it."""

import unittest

from django.db import connection as main_conn

from core.models import ObjectType
from dcim.models import Site
from netbox.plugins import get_plugin_config
from netbox_custom_objects.models import CustomObjectType, CustomObjectTypeField

from .base import HAS_BRANCHING, BranchingTestBase, _make_request, _provision_branch, _TestBase

if HAS_BRANCHING:
    from netbox_branching.utilities import activate_branch

    from netbox.context_managers import event_tracking


@unittest.skipUnless(HAS_BRANCHING, 'netbox-branching is not installed')
class MultiObjectFieldSyncedIntoBranchTestCase(BranchingTestBase, _TestBase):
    def setUp(self):
        super().setUp()
        self.addCleanup(CustomObjectType.clear_model_cache)
        with event_tracking(self.request):
            self.site_a = Site.objects.create(name='Site A', slug='site-a')
            self.site_b = Site.objects.create(name='Site B', slug='site-b')
            self.cot = CustomObjectType.objects.create(name='holder', slug='holders')
            CustomObjectTypeField.objects.create(custom_object_type=self.cot, name='name', type='text', primary=True)
            self.holder = self._model().objects.create(name='holder-1')
        self.branch = _provision_branch('Branch A', 'iterative', self.user)
        # A request of its own, so these changes aren't folded into the holder's creation.
        with event_tracking(_make_request(self.user)):
            self.field = CustomObjectTypeField.objects.create(
                custom_object_type=self._cot(),
                name='sites',
                type='multiobject',
                related_object_type=ObjectType.objects.get_for_model(Site),
            )
            self._model().objects.get(pk=self.holder.pk).sites.add(self.site_a, self.site_b)
        self.table = self.field.through_table_name
        self.main_schema = get_plugin_config('netbox_branching', 'main_schema')

        self.branch.sync(user=self.user, commit=True)

    def _cot(self):
        return CustomObjectType.objects.get(pk=self.cot.pk)

    def _model(self):
        return self._cot().get_model()

    def _table_exists_in(self, schema, table=None):
        with main_conn.cursor() as cursor:
            cursor.execute('SELECT to_regclass(%s)', [f'"{schema}"."{table or self.table}"'])
            return cursor.fetchone()[0] is not None

    def _site_ids_in(self, schema):
        with main_conn.cursor() as cursor:
            cursor.execute(
                f'SELECT target_id FROM "{schema}"."{self.table}" WHERE source_id = %s ORDER BY target_id',
                [self.holder.pk],
            )
            return [row[0] for row in cursor.fetchall()]

    def _drop_branch_copy(self):
        """As in a branch synced before the fix, which has no copy of its own."""
        with main_conn.cursor() as cursor:
            cursor.execute(f'DROP TABLE "{self.branch.schema_name}"."{self.table}"')

    def _in_branch(self):
        return activate_branch(self.branch)

    def test_sync_creates_branch_copy(self):
        self.assertTrue(self._table_exists_in(self.branch.schema_name))
        self.assertEqual(self._site_ids_in(self.branch.schema_name), [self.site_a.pk, self.site_b.pk])

    def test_branch_change_stays_in_branch(self):
        with self._in_branch(), event_tracking(_make_request(self.user)):
            self._model().objects.get(pk=self.holder.pk).sites.remove(self.site_b)

        self.assertEqual(self._site_ids_in(self.branch.schema_name), [self.site_a.pk])
        self.assertEqual(self._site_ids_in(self.main_schema), [self.site_a.pk, self.site_b.pk])

    def test_deleting_object_in_branch_deletes_only_branch_rows(self):
        with self._in_branch(), event_tracking(_make_request(self.user)):
            self._model().objects.get(pk=self.holder.pk).delete()

        self.assertEqual(self._site_ids_in(self.branch.schema_name), [])
        self.assertEqual(self._site_ids_in(self.main_schema), [self.site_a.pk, self.site_b.pk])

    def test_deleting_field_in_branch_without_its_own_copy_keeps_main_table(self):
        self._drop_branch_copy()

        with self._in_branch(), event_tracking(_make_request(self.user)):
            CustomObjectTypeField.objects.get(pk=self.field.pk).delete()

        self.assertTrue(self._table_exists_in(self.main_schema))

    def test_renaming_field_in_branch_without_its_own_copy_keeps_main_table(self):
        self._drop_branch_copy()

        with self._in_branch(), event_tracking(_make_request(self.user)):
            field = CustomObjectTypeField.objects.get(pk=self.field.pk)
            field.snapshot()
            field.name = 'places'
            field.save()

        self.assertTrue(self._table_exists_in(self.main_schema))
        self.assertFalse(self._table_exists_in(self.main_schema, self.table.replace('sites', 'places')))
        self.assertEqual(
            list(self._model().objects.get(pk=self.holder.pk).sites.values_list('pk', flat=True).order_by('pk')),
            [self.site_a.pk, self.site_b.pk],
        )

    def test_deleting_object_in_branch_without_its_own_copy_keeps_main_rows(self):
        self._drop_branch_copy()

        with self._in_branch(), event_tracking(_make_request(self.user)):
            self._model().objects.get(pk=self.holder.pk).delete()

        self.assertEqual(self._site_ids_in(self.main_schema), [self.site_a.pk, self.site_b.pk])
