"""
A change to an existing custom object's polymorphic multi-object field, merged, synced
and reverted.

netbox-branching applies such a change by assigning the field's serialized rows, which
the field's descriptor stages and the object's save() applies.
"""

import unittest

from django.db import connection as main_conn

from core.models import ObjectChange, ObjectType
from dcim.models import Site
from netbox.plugins import get_plugin_config
from netbox_custom_objects.models import CustomObjectType, CustomObjectTypeField

from .base import HAS_BRANCHING, BranchingTestBase, _make_request, _provision_branch, _TestBase

if HAS_BRANCHING:
    from netbox_branching.choices import BranchStatusChoices
    from netbox_branching.utilities import activate_branch

    from netbox.context_managers import event_tracking


class PolymorphicMultiObjectChangeTestBase(BranchingTestBase):
    MERGE_STRATEGY = None

    def setUp(self):
        super().setUp()
        self.addCleanup(CustomObjectType.clear_model_cache)
        with event_tracking(_make_request(self.user)):
            self.site_a = Site.objects.create(name='Site A', slug='site-a')
            self.site_b = Site.objects.create(name='Site B', slug='site-b')
            self.cot = CustomObjectType.objects.create(name='holder', slug='holders')
            CustomObjectTypeField.objects.create(custom_object_type=self.cot, name='name', type='text', primary=True)
            self.field = CustomObjectTypeField.objects.create(
                custom_object_type=self.cot, name='targets', type='multiobject', is_polymorphic=True
            )
            self.field.related_object_types.set([ObjectType.objects.get_for_model(Site)])
            holder = self._model().objects.create(name='holder-1')
            holder.targets.add(self.site_a, self.site_b)
        self.holder_pk = holder.pk

    def _model(self):
        return CustomObjectType.objects.get(pk=self.cot.pk).get_model()

    def _provision_branch(self):
        branch = _provision_branch('Branch A', self.MERGE_STRATEGY, self.user)
        with main_conn.cursor() as cursor:
            cursor.execute('SELECT to_regclass(%s)', [f'"{branch.schema_name}"."{self.field.through_table_name}"'])
            if cursor.fetchone()[0] is None:
                self.skipTest("netbox-branching before 1.2.4 doesn't copy the field's table into a branch")
        return branch

    def _remove_site_b(self):
        with event_tracking(_make_request(self.user)):
            holder = self._model().objects.get(pk=self.holder_pk)
            holder.snapshot()
            holder.targets.remove(self.site_b)

    def _targets(self, schema=None):
        """The holder's target site ids in *schema*'s copy of the through table (main's by default)."""
        schema = schema or get_plugin_config('netbox_branching', 'main_schema')
        with main_conn.cursor() as cursor:
            cursor.execute(
                f'SELECT object_id FROM "{schema}"."{self.field.through_table_name}" '
                'WHERE source_id = %s ORDER BY object_id',
                [self.holder_pk],
            )
            return [row[0] for row in cursor.fetchall()]

    def test_merge(self):
        branch = self._provision_branch()
        with activate_branch(branch):
            self._remove_site_b()

        branch.merge(user=self.user, commit=True)

        branch.refresh_from_db()
        self.assertEqual(branch.status, BranchStatusChoices.MERGED)
        self.assertEqual(self._targets(), [self.site_a.pk])
        # Main's change record has the new value.
        change = ObjectChange.objects.filter(
            changed_object_type=ObjectType.objects.get_for_model(self._model()), changed_object_id=self.holder_pk
        ).latest('time')
        self.assertEqual(
            change.postchange_data['targets'], [{'content_type': 'dcim.site', 'object_id': self.site_a.pk}]
        )

    def test_revert(self):
        branch = self._provision_branch()
        with activate_branch(branch):
            self._remove_site_b()
        branch.merge(user=self.user, commit=True)

        branch.revert(user=self.user, commit=True)

        self.assertEqual(self._targets(), [self.site_a.pk, self.site_b.pk])

    def test_sync(self):
        branch = self._provision_branch()
        self._remove_site_b()

        branch.sync(user=self.user, commit=True)

        self.assertEqual(self._targets(branch.schema_name), [self.site_a.pk])
        self.assertEqual(self._targets(), [self.site_a.pk])


@unittest.skipUnless(HAS_BRANCHING, 'netbox-branching is not installed')
class IterativePolymorphicMultiObjectChangeTestCase(PolymorphicMultiObjectChangeTestBase, _TestBase):
    MERGE_STRATEGY = 'iterative'


@unittest.skipUnless(HAS_BRANCHING, 'netbox-branching is not installed')
class SquashPolymorphicMultiObjectChangeTestCase(PolymorphicMultiObjectChangeTestBase, _TestBase):
    MERGE_STRATEGY = 'squash'
