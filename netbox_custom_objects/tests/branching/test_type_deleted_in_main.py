"""
A Custom Object Type deleted in main while branches exist.

Deleting it removes its object type, which a branch's changes to the type's objects refer
to, so the deletion is refused while an open branch has such changes.  A branch without
them syncs the deletion.
"""

import unittest
from unittest import mock

from django.db import connection as main_conn
from django.db.models import ProtectedError
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from core.models import ObjectType
from dcim.models import Site
from netbox_custom_objects import branching
from netbox_custom_objects.constants import APP_LABEL
from netbox_custom_objects.models import CustomObjectType, CustomObjectTypeField, TypeChangedInBranches

from ..base import create_token
from .base import HAS_BRANCHING, BranchingTestBase, _make_request, _provision_branch, _TestBase

if HAS_BRANCHING:
    from netbox_branching.choices import BranchStatusChoices
    from netbox_branching.models import Branch
    from netbox_branching.utilities import activate_branch

    from netbox.context_managers import event_tracking


@unittest.skipUnless(HAS_BRANCHING, 'netbox-branching is not installed')
class TypeDeletedInMainTestCase(BranchingTestBase, _TestBase):
    def setUp(self):
        super().setUp()
        self.addCleanup(CustomObjectType.clear_model_cache)
        with event_tracking(_make_request(self.user)):
            self.cot = CustomObjectType.objects.create(name='widget', slug='widgets')
            CustomObjectTypeField.objects.create(custom_object_type=self.cot, name='name', type='text', primary=True)
            self.qty = CustomObjectTypeField.objects.create(custom_object_type=self.cot, name='qty', type='integer')
            self.widget = self.cot.get_model().objects.create(name='widget-1', qty=1)
        self.table = self.cot.get_database_table_name()

    def _cot(self):
        return CustomObjectType.objects.get(pk=self.cot.pk)

    def _edit_widget_in(self, branch):
        with activate_branch(branch), event_tracking(_make_request(self.user)):
            widget = self._cot().get_model().objects.get(pk=self.widget.pk)
            widget.snapshot()
            widget.qty = 2
            widget.save()

    def _delete_type(self):
        with event_tracking(_make_request(self.user)):
            self._cot().delete()

    def _object_type_exists(self):
        return ObjectType.objects.filter(app_label=APP_LABEL, model=f'table{self.cot.pk}model').exists()

    def _table_exists_in(self, schema):
        with main_conn.cursor() as cursor:
            cursor.execute('SELECT to_regclass(%s)', [f'"{schema}"."{self.table}"'])
            return cursor.fetchone()[0] is not None

    def test_delete_refused_while_branch_has_changes_to_objects(self):
        branch = _provision_branch('Branch A', 'iterative', self.user)
        self._edit_widget_in(branch)

        with self.assertRaisesMessage(TypeChangedInBranches, 'Branch A. Merge or delete those branches first.'):
            self._delete_type()

        self.assertTrue(CustomObjectType.objects.filter(pk=self.cot.pk).exists())
        self.assertTrue(self._object_type_exists())

    def test_delete_refused_while_another_branch_is_merging(self):
        branch = _provision_branch('Branch A', 'iterative', self.user)
        self._edit_widget_in(branch)
        # As while another process merges it.
        Branch.objects.filter(pk=branch.pk).update(status=BranchStatusChoices.MERGING)

        with self.assertRaises(TypeChangedInBranches):
            self._delete_type()

    def test_delete_refused_after_branch_merge_failed(self):
        branch = _provision_branch('Branch A', 'iterative', self.user)
        self._edit_widget_in(branch)
        # As left by a failed merge in this process, which netbox-branching sets back to ready.
        self.addCleanup(branching._applying_branch.reset, branching._applying_branch.set(branch))

        with self.assertRaises(TypeChangedInBranches):
            self._delete_type()

    def test_branch_change_made_after_the_check_protects_object_type(self):
        branch = _provision_branch('Branch A', 'iterative', self.user)
        self._edit_widget_in(branch)

        with (
            mock.patch.object(CustomObjectType, '_refuse_delete_with_branch_changes'),
            self.assertRaises(ProtectedError),
        ):
            self._delete_type()

        self.assertTrue(CustomObjectType.objects.filter(pk=self.cot.pk).exists())
        self.assertTrue(self._object_type_exists())

    def test_rest_api_delete_refused_while_branch_has_changes_to_objects(self):
        branch = _provision_branch('Branch A', 'iterative', self.user)
        self._edit_widget_in(branch)
        self.user.is_superuser = True
        self.user.save()

        response = APIClient().delete(
            reverse('plugins-api:netbox_custom_objects-api:customobjecttype-detail', args=[self.cot.pk]),
            HTTP_AUTHORIZATION=f'Token {create_token(self.user)}',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.content)
        self.assertIn('Branch A', response.json()['detail'])
        self.assertTrue(CustomObjectType.objects.filter(pk=self.cot.pk).exists())

    def test_delete_refused_while_branch_created_an_object(self):
        branch = _provision_branch('Branch A', 'iterative', self.user)
        with activate_branch(branch), event_tracking(_make_request(self.user)):
            self._cot().get_model().objects.create(name='widget-2', qty=3)

        with self.assertRaises(TypeChangedInBranches):
            self._delete_type()

    def test_delete_allowed_once_branch_is_deleted(self):
        branch = _provision_branch('Branch A', 'iterative', self.user)
        self._edit_widget_in(branch)
        branch.delete()

        self._delete_type()

        self.assertFalse(self._object_type_exists())

    def test_delete_allowed_once_branch_is_merged(self):
        branch = _provision_branch('Branch A', 'iterative', self.user)
        self._edit_widget_in(branch)
        branch.merge(user=self.user, commit=True)

        self._delete_type()

        self.assertFalse(self._object_type_exists())

    def test_branch_that_changes_objects_and_deletes_type_merges(self):
        branch = _provision_branch('Branch A', 'iterative', self.user)
        self._edit_widget_in(branch)
        with activate_branch(branch), event_tracking(_make_request(self.user)):
            self._cot().delete()

        branch.merge(user=self.user, commit=True)

        branch.refresh_from_db()
        self.assertEqual(branch.status, BranchStatusChoices.MERGED)
        self.assertFalse(CustomObjectType.objects.filter(pk=self.cot.pk).exists())

    def test_branch_without_changes_syncs_deletion(self):
        branch = _provision_branch('Branch A', 'iterative', self.user)
        with activate_branch(branch), event_tracking(_make_request(self.user)):
            site = Site.objects.create(name='Site A', slug='site-a')
        self._delete_type()

        branch.sync(user=self.user, commit=True)

        with activate_branch(branch):
            self.assertFalse(CustomObjectType.objects.filter(pk=self.cot.pk).exists())
        self.assertFalse(self._table_exists_in(branch.schema_name))
        # Not re-created in main by the replayed deletion.
        self.assertFalse(self._object_type_exists())

        branch.merge(user=self.user, commit=True)

        branch.refresh_from_db()
        self.assertEqual(branch.status, BranchStatusChoices.MERGED)
        self.assertTrue(Site.objects.filter(pk=site.pk).exists())

    def test_branch_syncs_deletion_of_type_with_self_referential_field(self):
        with event_tracking(_make_request(self.user)):
            CustomObjectTypeField.objects.create(
                custom_object_type=self._cot(),
                name='parent',
                type='object',
                related_object_type_id=self.cot.object_type_id,
            )
        branch = _provision_branch('Branch A', 'iterative', self.user)
        self._delete_type()

        branch.sync(user=self.user, commit=True)

        with activate_branch(branch):
            self.assertFalse(CustomObjectType.objects.filter(pk=self.cot.pk).exists())

    def test_branch_syncs_deletion_of_type_another_type_points_at(self):
        with event_tracking(_make_request(self.user)):
            other = CustomObjectType.objects.create(name='holder', slug='holders')
            CustomObjectTypeField.objects.create(custom_object_type=other, name='name', type='text', primary=True)
            CustomObjectTypeField.objects.create(
                custom_object_type=other, name='widget', type='object', related_object_type_id=self.cot.object_type_id
            )
        branch = _provision_branch('Branch A', 'iterative', self.user)
        self._delete_type()

        branch.sync(user=self.user, commit=True)

        with activate_branch(branch):
            self.assertFalse(CustomObjectType.objects.filter(pk=self.cot.pk).exists())
            self.assertFalse(CustomObjectTypeField.objects.filter(custom_object_type=other, name='widget').exists())

    def test_branch_field_pointing_at_type_blocks_deletion(self):
        with event_tracking(_make_request(self.user)):
            other = CustomObjectType.objects.create(name='holder', slug='holders')
            CustomObjectTypeField.objects.create(custom_object_type=other, name='name', type='text', primary=True)
        branch = _provision_branch('Branch A', 'iterative', self.user)
        with activate_branch(branch), event_tracking(_make_request(self.user)):
            CustomObjectTypeField.objects.create(
                custom_object_type_id=other.pk,
                name='widget',
                type='object',
                related_object_type_id=self.cot.object_type_id,
            )

        with self.assertRaisesMessage(TypeChangedInBranches, 'Branch A'):
            self._delete_type()

    def test_branch_polymorphic_field_allowing_type_blocks_deletion(self):
        with event_tracking(_make_request(self.user)):
            other = CustomObjectType.objects.create(name='holder', slug='holders')
            CustomObjectTypeField.objects.create(custom_object_type=other, name='name', type='text', primary=True)
        branch = _provision_branch('Branch A', 'iterative', self.user)
        with activate_branch(branch), event_tracking(_make_request(self.user)):
            field = CustomObjectTypeField.objects.create(
                custom_object_type_id=other.pk, name='targets', type='multiobject', is_polymorphic=True
            )
            field.related_object_types.set([self.cot.object_type_id, ObjectType.objects.get_for_model(Site).pk])

        with self.assertRaisesMessage(TypeChangedInBranches, 'Branch A'):
            self._delete_type()

    def test_field_change_in_branch_doesnt_block_deletion(self):
        branch = _provision_branch('Branch A', 'iterative', self.user)
        with activate_branch(branch), event_tracking(_make_request(self.user)):
            field = CustomObjectTypeField.objects.get(pk=self.qty.pk)
            field.snapshot()
            field.label = 'Quantity'
            field.save()

        self._delete_type()
        branch.sync(user=self.user, commit=True)

        self.assertFalse(self._object_type_exists())
        with activate_branch(branch):
            self.assertFalse(CustomObjectType.objects.filter(pk=self.cot.pk).exists())
