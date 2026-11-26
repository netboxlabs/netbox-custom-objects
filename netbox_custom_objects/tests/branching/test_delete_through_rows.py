"""Tests for deleting a custom object in a branch, with values in its Multi-object fields."""

import unittest

from django.apps import apps
from django.db import connection as main_conn
from django.utils import timezone

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

    def _referrer_type(self, **kwargs):
        other = CustomObjectType.objects.create(name='other', slug='others', **kwargs)
        CustomObjectTypeField.objects.create(custom_object_type=other, name='name', type='text', primary=True)
        CustomObjectTypeField.objects.create(
            custom_object_type=other,
            name='holders',
            type='multiobject',
            related_object_type=self._cot().object_type,
        )
        referrer = self._model(other).objects.create(name='other-1')
        referrer.holders.add(self._model().objects.get(pk=self.holder.pk))
        return other, referrer

    def test_target_of_field_on_type_with_longer_id_queries_in_main_after_branch_delete(self):
        # The referring type's table name starts with the holder's: custom_objects_<id>2.
        with event_tracking(_make_request(self.user)):
            other, referrer = self._referrer_type(pk=int(f'{self.cot.pk}2'))
        self._provision_branch()

        self._delete_holder_in_branch()

        holder = self._model().objects.get(pk=self.holder.pk)
        self.assertEqual(list(self._model(other).objects.filter(holders=holder)), [referrer])

    def test_own_field_queries_in_main_after_branch_delete(self):
        with event_tracking(_make_request(self.user)):
            self._add_sites_field()
        self._provision_branch()

        self._delete_holder_in_branch()

        self.assertEqual(list(self._model().objects.filter(sites=self.site)), [self._model().objects.get()])

    def test_main_throughs_unchanged_by_branch_delete(self):
        with event_tracking(_make_request(self.user)):
            self._add_sites_field()
            self._referrer_type()
        self._provision_branch()

        def remote_models():
            return {
                (name, fk.name): fk.remote_field.model
                for name, through in apps.all_models['netbox_custom_objects'].items()
                if name.startswith('through_')
                for fk in through._meta.fields
                if fk.name in ('source', 'target')
            }

        before = remote_models()
        self._delete_holder_in_branch()

        # Concurrent deletes in other branches can't then leave their classes on main's throughs.
        self.assertEqual(remote_models(), before)

    def test_object_loaded_before_type_rebuilt_in_branch(self):
        with event_tracking(_make_request(self.user)):
            other, referrer = self._referrer_type()
            holder_2 = self._model().objects.create(name='holder-2')
            referrer.holders.add(holder_2)
        self._provision_branch()
        branch, request = self._in_branch()
        with branch, request:
            stale = self._model().objects.get(pk=self.holder.pk)
            # Rebuilds the type's model in the branch.
            CustomObjectTypeField.objects.create(custom_object_type=self._cot(), name='notes', type='text')
            stale.delete()

            self.assertFalse(self._model().objects.filter(pk=self.holder.pk).exists())
            self.assertIsNone(stale.pk)
            holder_2 = self._model().objects.get(pk=holder_2.pk)
            referrers = self._model(other).objects.filter(holders=holder_2)
            self.assertEqual(list(referrers.values_list('pk', flat=True)), [referrer.pk])

    def test_object_loaded_before_type_changed_in_another_worker(self):
        with event_tracking(_make_request(self.user)):
            other, referrer = self._referrer_type()
            holder_2 = self._model().objects.create(name='holder-2')
            referrer.holders.add(holder_2)
        self._provision_branch()
        branch, request = self._in_branch()
        with branch, request:
            stale = self._model().objects.get(pk=self.holder.pk)
            # As when another worker changes the type: its cached model is now out of date.
            CustomObjectType.objects.filter(pk=self.cot.pk).update(cache_timestamp=timezone.now())
            stale.delete()

            holder_2 = self._model().objects.get(pk=holder_2.pk)
            referrers = self._model(other).objects.filter(holders=holder_2)
            self.assertEqual(list(referrers.values_list('pk', flat=True)), [referrer.pk])

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
