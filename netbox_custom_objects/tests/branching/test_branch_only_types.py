"""
A Custom Object Type created in a branch that hasn't been merged.

Its object type is created in main, but main has no row or table for the type.  A process
that has built the type's model in the branch must not resolve it in main, or
netbox-branching tries to copy its table into new branches.
"""

import unittest
from unittest import mock

from django.db import ProgrammingError

from core.models import ObjectType
from dcim.models import Site
from netbox_custom_objects import branching
from netbox_custom_objects.constants import APP_LABEL
from netbox_custom_objects.models import CustomObjectType, CustomObjectTypeField
from netbox_custom_objects.tests.base import _purge_stale_generated_models

from .base import HAS_BRANCHING, BranchingTestBase, _make_request, _provision_branch, _TestBase

if HAS_BRANCHING:
    from netbox_branching.choices import BranchStatusChoices
    from netbox_branching.models import Branch
    from netbox_branching.utilities import activate_branch

    from netbox.context_managers import event_tracking


@unittest.skipUnless(HAS_BRANCHING, 'netbox-branching is not installed')
class BranchOnlyTypeTestCase(BranchingTestBase, _TestBase):
    def setUp(self):
        super().setUp()
        self.addCleanup(CustomObjectType.clear_model_cache)

    def _create_type_in(self, branch, name):
        """Create a type with one object in *branch*, building its model in this process."""
        with activate_branch(branch), event_tracking(_make_request(self.user)):
            cot = CustomObjectType.objects.create(name=name, slug=name)
            CustomObjectTypeField.objects.create(custom_object_type=cot, name='name', type='text', primary=True)
            cot.get_model().objects.create(name=f'{name}-1')
        return cot

    def _object_type(self, cot):
        return ObjectType.objects.filter(app_label=APP_LABEL, model=f'table{cot.pk}model').first()

    def test_branch_only_type_has_no_model_in_main(self):
        branch = _provision_branch('Branch A', 'iterative', self.user)
        cot = self._create_type_in(branch, 'widget')
        object_type = self._object_type(cot)

        self.assertIsNone(object_type.model_class())
        with activate_branch(branch):
            self.assertEqual(object_type.model_class().objects.count(), 1)

    def test_branch_only_type_has_no_model_in_other_branch(self):
        branch_a = _provision_branch('Branch A', 'iterative', self.user)
        branch_b = _provision_branch('Branch B', 'iterative', self.user)
        cot = self._create_type_in(branch_a, 'widget')

        with activate_branch(branch_b):
            self.assertIsNone(self._object_type(cot).model_class())

    def test_main_builds_type_whose_through_model_was_built_in_branch(self):
        with event_tracking(_make_request(self.user)):
            cot = CustomObjectType.objects.create(name='holder', slug='holders')
            CustomObjectTypeField.objects.create(custom_object_type=cot, name='name', type='text', primary=True)
            field = CustomObjectTypeField.objects.create(
                custom_object_type=cot, name='targets', type='multiobject', is_polymorphic=True
            )
            field.related_object_types.set([ObjectType.objects.get_for_model(Site)])
        branch = _provision_branch('Branch A', 'iterative', self.user)
        _purge_stale_generated_models()
        CustomObjectType.clear_model_cache()
        with activate_branch(branch):
            CustomObjectType.objects.get(pk=cot.pk).get_model()

        model = CustomObjectType.objects.get(pk=cot.pk).get_model()

        self.assertIsNone(model._context_branch_id)

    def test_branch_provisions_after_branch_only_type_is_built(self):
        branch = _provision_branch('Branch A', 'iterative', self.user)
        self._create_type_in(branch, 'widget')

        _provision_branch('Branch B', user=self.user)

    def test_merged_type_resolves_to_main_model(self):
        branch = _provision_branch('Branch A', 'iterative', self.user)
        cot = self._create_type_in(branch, 'widget')

        branch.merge(user=self.user, commit=True)

        model = self._object_type(cot).model_class()
        self.assertIsNone(model._context_branch_id)
        self.assertEqual(model.objects.count(), 1)

    def test_squash_merge_in_process_that_never_built_the_model(self):
        branch = _provision_branch('Branch A', 'squash', self.user)
        cot = self._create_type_in(branch, 'widget')
        # As in a worker that hasn't loaded the type, which builds models on lookup
        # (the test runner doesn't).
        _purge_stale_generated_models()
        CustomObjectType.clear_model_cache()

        with mock.patch(
            'netbox_custom_objects.CustomObjectsPluginConfig.should_skip_dynamic_model_creation', return_value=False
        ):
            branch.merge(user=self.user, commit=True)

        self.assertEqual(self._object_type(cot).model_class().objects.count(), 1)

    def test_branch_merged_elsewhere_doesnt_resolve(self):
        branch = _provision_branch('Branch A', 'iterative', self.user)
        cot = self._create_type_in(branch, 'widget')
        # As while another process merges it.
        Branch.objects.filter(pk=branch.pk).update(status=BranchStatusChoices.MERGING)

        self.assertIsNone(self._object_type(cot).model_class())

    def test_failed_merge_doesnt_resolve(self):
        branch = _provision_branch('Branch A', 'iterative', self.user)
        cot = self._create_type_in(branch, 'widget')
        # As left by a failed merge, which netbox-branching sets back to ready.
        self.addCleanup(branching._merging_branch.reset, branching._merging_branch.set(branch))

        self.assertIsNone(self._object_type(cot).model_class())

    def test_merging_branch_lookup_error_is_no_model(self):
        branch = _provision_branch('Branch A', 'iterative', self.user)
        cot = self._create_type_in(branch, 'widget')
        Branch.objects.filter(pk=branch.pk).update(status=BranchStatusChoices.MERGING)
        self.addCleanup(branching._merging_branch.reset, branching._merging_branch.set(branch))

        with mock.patch('netbox_branching.utilities.activate_branch', side_effect=ProgrammingError):
            self.assertIsNone(self._object_type(cot).model_class())

    def test_deleting_branch_removes_object_type_of_unmerged_type(self):
        branch_a = _provision_branch('Branch A', 'iterative', self.user)
        branch_b = _provision_branch('Branch B', 'iterative', self.user)
        widget = self._create_type_in(branch_a, 'widget')
        gadget = self._create_type_in(branch_b, 'gadget')

        with mock.patch('netbox_custom_objects.stale_models.signal_custom_object_type_deleted') as signal:
            branch_a.delete()

        self.assertIsNone(self._object_type(widget))
        # Still in branch B.
        self.assertIsNotNone(self._object_type(gadget))
        signal.assert_called_once_with(widget.pk)

    def test_deleting_branch_from_another_branch_keeps_types_in_main(self):
        branch_a = _provision_branch('Branch A', 'iterative', self.user)
        with event_tracking(_make_request(self.user)):
            cot = CustomObjectType.objects.create(name='widget', slug='widget')
        branch_b = _provision_branch('Branch B', 'iterative', self.user)

        # Branch A doesn't have the type.
        with activate_branch(branch_a):
            branch_b.delete()

        self.assertIsNotNone(self._object_type(cot))

    def test_failed_clean_up_doesnt_fail_branch_deletion(self):
        branch = _provision_branch('Branch A', 'iterative', self.user)
        self._create_type_in(branch, 'widget')

        with (
            mock.patch.object(ObjectType, 'delete', side_effect=RuntimeError('delete failed')),
            self.assertLogs('netbox_custom_objects.branching', 'ERROR'),
        ):
            branch.delete()

        self.assertFalse(Branch.objects.filter(pk=branch.pk).exists())
