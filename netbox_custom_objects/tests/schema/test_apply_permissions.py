"""
Applying a schema document enforces object-level permissions, as the UI and REST views do.
"""

import unittest
from unittest import mock

from core.models import ObjectType
from django.test import TransactionTestCase
from rest_framework import status
from users.models import ObjectPermission

from netbox_custom_objects.models import CustomObjectType, CustomObjectTypeConstraint, CustomObjectTypeField
from netbox_custom_objects.schema.exporter import export_cot

from ..base import TransactionCleanupMixin
from ..branching.base import HAS_BRANCHING, _close_branch_connections, _provision_branch
from .test_schema_api import _SchemaAPIBase, _SchemaAPIMixin

if HAS_BRANCHING:
    from netbox_branching.utilities import activate_branch


class SchemaApplyPermissionTestCase(_SchemaAPIBase):
    def setUp(self):
        super().setUp()
        self.allowed = self.create_custom_object_type(name='allowed', slug='allowed', description='keep')
        self.other = self.create_custom_object_type(name='other', slug='other', description='keep')

    def _apply(self, *type_defs, allow_destructive=False):
        schema_doc = {'schema_version': '1', 'types': list(type_defs)}
        return self.client.post(
            self.apply_url,
            data=self._apply_body(schema_doc, allow_destructive=allow_destructive),
            format='json',
        )

    def _with_description(self, cot, description):
        type_def = export_cot(cot)
        type_def['description'] = description
        return type_def

    def _field_perms(self, *actions):
        self._grant(CustomObjectTypeField, list(actions))

    def test_change_outside_constraint_refused(self):
        self._grant(CustomObjectType, ['add', 'change'], constraints={'slug': 'allowed'})
        response = self._apply(self._with_description(self.other, 'changed'))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.other.refresh_from_db()
        self.assertEqual(self.other.description, 'keep')

        response = self._apply(self._with_description(self.allowed, 'changed'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.allowed.refresh_from_db()
        self.assertEqual(self.allowed.description, 'changed')

    def test_change_leaving_constraint_rolled_back(self):
        self._grant(CustomObjectType, ['add', 'change'], constraints={'description': 'keep'})
        response = self._apply(self._with_description(self.allowed, 'changed'))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.allowed.refresh_from_db()
        self.assertEqual(self.allowed.description, 'keep')

    def test_new_type_outside_add_constraint_not_created(self):
        self._grant(CustomObjectType, ['add', 'change'], constraints={'slug__startswith': 'ok-'})
        response = self._apply({'name': 'bad_new', 'slug': 'bad-new'})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(CustomObjectType.objects.filter(slug='bad-new').exists())

    def test_adding_field_needs_field_add_permission(self):
        self._grant(CustomObjectType, ['add', 'change'])
        type_def = export_cot(self.allowed)
        type_def.setdefault('fields', []).append(
            {'id': self._next_field_id(self.allowed), 'name': 'added', 'type': 'text'}
        )
        response = self._apply(type_def)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(self.allowed.fields.filter(name='added').exists())

        self._field_perms('add')
        self.assertEqual(self._apply(type_def).status_code, status.HTTP_200_OK)
        self.assertTrue(self.allowed.fields.filter(name='added').exists())

    def test_altering_field_needs_field_change_permission(self):
        self._grant(CustomObjectType, ['add', 'change'])
        self._field_perms('add')
        field = self.create_custom_object_type_field(self.allowed, name='code', label='Code', type='text')
        type_def = export_cot(self.allowed)
        (field_def,) = [f for f in type_def['fields'] if f['name'] == 'code']
        field_def['label'] = 'Renamed'
        response = self._apply(type_def)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn('permission to change one or more of these fields', response.data['detail'])
        field.refresh_from_db()
        self.assertEqual(field.label, 'Code')

    def test_removing_field_needs_field_delete_permission(self):
        self._grant(CustomObjectType, ['add', 'change'])
        self._field_perms('add', 'change')
        field = self.create_custom_object_type_field(self.allowed, name='victim', type='text')
        type_def = export_cot(self.allowed)
        type_def['fields'] = []
        type_def['removed_fields'] = [{'id': field.schema_id, 'name': 'victim', 'type': 'text'}]
        response = self._apply(type_def, allow_destructive=True)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(self.allowed.fields.filter(pk=field.pk).exists())

    def test_rejected_apply_sends_no_events(self):
        self._grant(CustomObjectType, ['add', 'change'], constraints={'description': 'keep'})
        with mock.patch('netbox.context_managers.flush_events') as flush_events:
            response = self._apply(self._with_description(self.allowed, 'changed'))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        flush_events.assert_not_called()

        # An accepted apply does send its events.
        with mock.patch('netbox.context_managers.flush_events') as flush_events:
            response = self._apply({'name': 'fresh', 'slug': 'fresh', 'description': 'keep'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        flush_events.assert_called_once()


class SchemaApplyConstraintPermissionTestCase(_SchemaAPIBase):
    """Constraints the document adds, alters or removes need the matching constraint permission."""

    def setUp(self):
        super().setUp()
        self.cot = self.create_custom_object_type(name='asset', slug='assets')
        self.code = self.create_custom_object_type_field(self.cot, name='code', type='text')
        self.serial = self.create_custom_object_type_field(self.cot, name='serial', type='text')
        self.existing = CustomObjectTypeConstraint.objects.create(
            custom_object_type=self.cot,
            name='code_serial',
            field_schema_ids=[self.code.schema_id, self.serial.schema_id],
        )
        for model, actions in (
            (CustomObjectType, ['add', 'change']),
            (CustomObjectTypeField, ['add', 'change', 'delete']),
        ):
            self._grant(model, actions)

    def _apply(self, edit):
        type_def = export_cot(self.cot)
        edit(type_def)
        schema_doc = {'schema_version': '1', 'types': [type_def]}
        return self.client.post(self.apply_url, data=self._apply_body(schema_doc), format='json')

    def _add(self, name):
        def edit(type_def):
            type_def.setdefault('constraints', []).append(
                {'name': name, 'fields': [self.code.schema_id], 'case_insensitive': True}
            )

        return edit

    def _describe(self, description):
        def edit(type_def):
            (constraint,) = [c for c in type_def['constraints'] if c['name'] == 'code_serial']
            constraint['description'] = description

        return edit

    @staticmethod
    def _remove(type_def):
        type_def['constraints'] = []
        type_def['removed_constraints'] = ['code_serial']

    def test_adding_needs_add_permission(self):
        response = self._apply(self._add('code_only'))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        # Refused up front, before anything is written.
        self.assertIn('permission to add Custom Object Type constraints', response.data['detail'])
        self.assertFalse(self.cot.constraints.filter(name='code_only').exists())

        self._grant(CustomObjectTypeConstraint, ['add'])
        self.assertEqual(self._apply(self._add('code_only')).status_code, status.HTTP_200_OK)
        self.assertTrue(self.cot.constraints.filter(name='code_only').exists())

    def test_adding_outside_add_constraint_refused(self):
        self._grant(CustomObjectTypeConstraint, ['add'], constraints={'name__startswith': 'ok_'})
        self.assertEqual(self._apply(self._add('code_only')).status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(self.cot.constraints.filter(name='code_only').exists())

    def test_altering_needs_change_permission(self):
        response = self._apply(self._describe('changed'))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn('permission to change one or more of these constraints', response.data['detail'])
        self.assertEqual(self.cot.constraints.get(name='code_serial').description, '')

        self._grant(CustomObjectTypeConstraint, ['change'])
        self.assertEqual(self._apply(self._describe('changed')).status_code, status.HTTP_200_OK)
        self.assertEqual(self.cot.constraints.get(name='code_serial').description, 'changed')

    def test_altering_keeps_the_constraint_id(self):
        """A change permission scoped to the constraint's ID covers it after it's altered."""
        self._grant(CustomObjectTypeConstraint, ['change'], constraints={'pk': self.existing.pk})
        self.assertEqual(self._apply(self._describe('changed')).status_code, status.HTTP_200_OK)
        constraint = self.cot.constraints.get(name='code_serial')
        self.assertEqual((constraint.pk, constraint.description), (self.existing.pk, 'changed'))

    def test_altering_out_of_change_constraint_rolled_back(self):
        self._grant(CustomObjectTypeConstraint, ['change'], constraints={'description': ''})
        self.assertEqual(self._apply(self._describe('changed')).status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(self.cot.constraints.get(name='code_serial').description, '')

    def test_removing_needs_delete_permission(self):
        self.assertEqual(self._apply(self._remove).status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(self.cot.constraints.filter(name='code_serial').exists())

        self._grant(CustomObjectTypeConstraint, ['delete'])
        self.assertEqual(self._apply(self._remove).status_code, status.HTTP_200_OK)
        self.assertFalse(self.cot.constraints.filter(name='code_serial').exists())

    def test_removing_outside_delete_constraint_refused(self):
        self._grant(CustomObjectTypeConstraint, ['delete'], constraints={'name': 'other'})
        response = self._apply(self._remove)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn('permission to delete one or more of these constraints', response.data['detail'])
        self.assertTrue(self.cot.constraints.filter(name='code_serial').exists())


@unittest.skipUnless(HAS_BRANCHING, 'netbox-branching is not installed')
class SchemaApplyBranchTestCase(TransactionCleanupMixin, _SchemaAPIMixin, TransactionTestCase):
    """An apply inside a branch changes only the branch, and a rejected one not even that."""

    def setUp(self):
        super().setUp()
        self.cot = self.create_custom_object_type(name='allowed', slug='allowed', description='keep')
        perm = ObjectPermission.objects.create(
            name='keep only',
            actions=['add', 'change'],
            constraints={'description': 'keep'},
        )
        perm.users.add(self.user)
        perm.object_types.add(ObjectType.objects.get_for_model(CustomObjectType))
        self.branch = _provision_branch('Schema apply', user=self.user)

    def tearDown(self):
        _close_branch_connections()
        super().tearDown()

    def _apply_in_branch(self, *type_defs):
        schema_doc = {'schema_version': '1', 'types': list(type_defs)}
        return self.client.post(
            self.apply_url,
            data=self._apply_body(schema_doc),
            format='json',
            HTTP_X_NETBOX_BRANCH=self.branch.schema_id,
        )

    def test_change_leaving_constraint_rolled_back_in_branch(self):
        type_def = export_cot(self.cot)
        type_def['description'] = 'changed'
        response = self._apply_in_branch(type_def)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        with activate_branch(self.branch):
            self.assertEqual(CustomObjectType.objects.get(pk=self.cot.pk).description, 'keep')

    def test_new_type_outside_constraint_not_created_in_branch(self):
        response = self._apply_in_branch({'name': 'rejected', 'slug': 'rejected', 'description': 'other'})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        with activate_branch(self.branch):
            self.assertFalse(CustomObjectType.objects.filter(slug='rejected').exists())

    def test_accepted_apply_changes_only_the_branch(self):
        response = self._apply_in_branch({'name': 'branched', 'slug': 'branched', 'description': 'keep'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        with activate_branch(self.branch):
            self.assertTrue(CustomObjectType.objects.filter(slug='branched').exists())
        self.assertFalse(CustomObjectType.objects.filter(slug='branched').exists())
