"""
A Custom Object Type created in a branch that hasn't been merged.

Its object type is created in main, but main has no row or table for the type.  A process
that has built the type's model in the branch must not resolve it in main, or
netbox-branching tries to copy its table into new branches.
"""

import unittest
from unittest import mock

from core.models import ObjectType
from netbox_custom_objects.constants import APP_LABEL
from netbox_custom_objects.models import CustomObjectType, CustomObjectTypeField
from netbox_custom_objects.tests.base import _purge_stale_generated_models

from .base import HAS_BRANCHING, BranchingTestBase, _make_request, _provision_branch, _TestBase

if HAS_BRANCHING:
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

    def test_deleting_branch_removes_object_type_of_unmerged_type(self):
        branch_a = _provision_branch('Branch A', 'iterative', self.user)
        branch_b = _provision_branch('Branch B', 'iterative', self.user)
        widget = self._create_type_in(branch_a, 'widget')
        gadget = self._create_type_in(branch_b, 'gadget')

        branch_a.delete()

        self.assertIsNone(self._object_type(widget))
        # Still in branch B.
        self.assertIsNotNone(self._object_type(gadget))
