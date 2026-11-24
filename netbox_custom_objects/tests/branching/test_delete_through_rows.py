"""Tests for deleting a custom object in a branch, with values in its Multi-object fields."""

import unittest

from django.apps import apps
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
class DeleteThroughRowsInBranchTestCase(BranchingTestBase, _TestBase):
    def setUp(self):
        super().setUp()
        self.addCleanup(CustomObjectType.clear_model_cache)
        with event_tracking(self.request):
            self.site = Site.objects.create(name='Site A', slug='site-a')
            self.cot = CustomObjectType.objects.create(name='holder', slug='holders')
            CustomObjectTypeField.objects.create(custom_object_type=self.cot, name='name', type='text', primary=True)
            self.holder = self._model().objects.create(name='holder-1')
        self.main_schema = get_plugin_config('netbox_branching', 'main_schema')

    def _cot(self, cot=None):
        return CustomObjectType.objects.get(pk=(cot or self.cot).pk)

    def _model(self, cot=None):
        return self._cot(cot).get_model()

    def _provision_branch(self):
        self.branch = _provision_branch('Branch A', 'iterative', self.user)

    def _in_branch(self):
        return activate_branch(self.branch), event_tracking(_make_request(self.user))

    def _add_sites_field(self, **kwargs):
        field = CustomObjectTypeField.objects.create(
            custom_object_type=self._cot(),
            name='sites',
            type='multiobject',
            related_object_type=ObjectType.objects.get_for_model(Site),
            **kwargs,
        )
        self._model().objects.get(pk=self.holder.pk).sites.add(self.site)
        return field

    def _rows_in(self, schema, table, column='source_id', pk=None):
        with main_conn.cursor() as cursor:
            cursor.execute(f'SELECT count(*) FROM "{schema}"."{table}" WHERE {column} = %s', [pk or self.holder.pk])
            return cursor.fetchone()[0]

    def _delete_holder_in_branch(self):
        branch, request = self._in_branch()
        with branch, request:
            self._model().objects.get(pk=self.holder.pk).delete()
            self.assertFalse(self._model().objects.filter(pk=self.holder.pk).exists())

    def test_field_created_in_branch(self):
        self._provision_branch()
        branch, request = self._in_branch()
        with branch, request:
            field = self._add_sites_field()

        self._delete_holder_in_branch()

        self.assertEqual(self._rows_in(self.branch.schema_name, field.through_table_name), 0)

    def test_field_existing_before_branch(self):
        with event_tracking(_make_request(self.user)):
            field = self._add_sites_field()
        self._provision_branch()

        self._delete_holder_in_branch()

        self.assertEqual(self._rows_in(self.branch.schema_name, field.through_table_name), 0)
        self.assertEqual(self._rows_in(self.main_schema, field.through_table_name), 1)

    def test_polymorphic_field_created_in_branch(self):
        self._provision_branch()
        branch, request = self._in_branch()
        with branch, request:
            field = CustomObjectTypeField.objects.create(
                custom_object_type=self._cot(), name='targets', type='multiobject', is_polymorphic=True
            )
            field.related_object_types.set([ObjectType.objects.get_for_model(Site)])
            self._model().objects.get(pk=self.holder.pk).targets.add(self.site)

        self._delete_holder_in_branch()

        self.assertEqual(self._rows_in(self.branch.schema_name, field.through_table_name), 0)

    def test_target_of_field_existing_before_branch(self):
        with event_tracking(_make_request(self.user)):
            other = CustomObjectType.objects.create(name='other', slug='others')
            CustomObjectTypeField.objects.create(custom_object_type=other, name='name', type='text', primary=True)
            field = CustomObjectTypeField.objects.create(
                custom_object_type=other,
                name='holders',
                type='multiobject',
                related_object_type=self._cot().object_type,
            )
            referrer = self._model(other).objects.create(name='other-1')
            referrer.holders.add(self._model().objects.get(pk=self.holder.pk))
        self._provision_branch()

        self._delete_holder_in_branch()

        self.assertEqual(self._rows_in(self.branch.schema_name, field.through_table_name, 'target_id'), 0)
        self.assertEqual(self._rows_in(self.main_schema, field.through_table_name, 'target_id'), 1)

    def test_target_of_field_queries_in_main_after_branch_delete(self):
        with event_tracking(_make_request(self.user)):
            other = CustomObjectType.objects.create(name='other', slug='others')
            CustomObjectTypeField.objects.create(custom_object_type=other, name='name', type='text', primary=True)
            CustomObjectTypeField.objects.create(
                custom_object_type=other,
                name='holders',
                type='multiobject',
                related_object_type=self._cot().object_type,
            )
            referrer = self._model(other).objects.create(name='other-1')
            referrer.holders.add(self._model().objects.get(pk=self.holder.pk))
        self._provision_branch()

        self._delete_holder_in_branch()

        holder = self._model().objects.get(pk=self.holder.pk)
        self.assertEqual(list(self._model(other).objects.filter(holders=holder)), [referrer])

    def test_field_deleted_in_branch_keeps_main_rows(self):
        with event_tracking(_make_request(self.user)):
            field = self._add_sites_field()
        self._provision_branch()
        branch, request = self._in_branch()
        with branch, request:
            CustomObjectTypeField.objects.get(pk=field.pk).delete()

        self._delete_holder_in_branch()

        self.assertEqual(self._rows_in(self.main_schema, field.through_table_name), 1)

    def test_branch_class_registered_keeps_main_rows(self):
        with event_tracking(_make_request(self.user)):
            field = self._add_sites_field()
        self._provision_branch()
        # As in a branch provisioned before netbox-branching 1.2.4: no copy of the table.
        with main_conn.cursor() as cursor:
            cursor.execute(f'DROP TABLE "{self.branch.schema_name}"."{field.through_table_name}"')
        # As after a restart whose first request is in the branch: the branch's class is registered.
        CustomObjectType.clear_model_cache()
        branch, request = self._in_branch()
        with branch, request:
            model = self._model()
            self.assertIs(apps.get_model('netbox_custom_objects', model._meta.model_name), model)
            model.objects.get(pk=self.holder.pk).delete()

        self.assertEqual(self._rows_in(self.main_schema, field.through_table_name), 1)
