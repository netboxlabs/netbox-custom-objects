"""
Branching integration tests for netbox-custom-objects.

Requires netbox-branching to be installed alongside this plugin.  All tests
are skipped when netbox-branching is absent so the suite remains clean in
environments that don't use branching.

These tests use TransactionTestCase (not TestCase) because branch schemas live
in separate PostgreSQL schemas backed by distinct database connections that
cannot be rolled back inside a single SAVEPOINT-based transaction.
"""
import time
import unittest
import uuid

from django.contrib.auth import get_user_model
from django.db import connections
from django.test import RequestFactory, TransactionTestCase
from django.urls import reverse

try:
    from netbox.context_managers import event_tracking
    from netbox_branching.choices import BranchStatusChoices
    from netbox_branching.models import Branch
    from netbox_branching.utilities import activate_branch
    HAS_BRANCHING = True
except ImportError:
    HAS_BRANCHING = False

from netbox_custom_objects.models import CustomObjectType, CustomObjectTypeField
from netbox_custom_objects.tests.base import TransactionCleanupMixin

User = get_user_model()


def _make_request(user):
    """Return a fresh request object suitable for event_tracking."""
    request = RequestFactory().get(reverse('home'))
    request.id = uuid.uuid4()
    request.user = user
    return request


def _provision_branch(name, merge_strategy, user):
    """Create and wait for a branch to reach READY status (up to 30 s)."""
    branch = Branch(name=name, merge_strategy=merge_strategy)
    branch.save(provision=False)
    branch.provision(user=user)
    deadline = time.time() + 30
    while time.time() < deadline:
        branch.refresh_from_db()
        if branch.status == BranchStatusChoices.READY:
            return branch
        time.sleep(0.1)
    raise TimeoutError(
        f'Branch {name!r} did not reach READY within 30 s '
        f'(status={branch.status!r})'
    )


def _close_branch_connections():
    """Close any open branch database connections."""
    for branch in Branch.objects.all():
        if hasattr(connections, branch.connection_name):
            connections[branch.connection_name].close()


# ── Shared merge/revert tests (strategy-agnostic) ────────────────────────────

@unittest.skipUnless(HAS_BRANCHING, 'netbox-branching is not installed')
class BaseBranchingTests(TransactionCleanupMixin):
    """
    Merge and revert tests that run against every merge strategy.

    Subclasses must:
    - set ``MERGE_STRATEGY`` to an iterative or squash strategy string
    - also inherit from ``TransactionTestCase``

    Example::

        class IterativeBranchingTestCase(BaseBranchingTests, TransactionTestCase):
            MERGE_STRATEGY = 'iterative'
    """

    MERGE_STRATEGY = None
    serialized_rollback = True

    def setUp(self):
        self.user = User.objects.create_user(username='testuser')
        self.request = _make_request(self.user)

    def tearDown(self):
        _close_branch_connections()
        super().tearDown()  # → TransactionCleanupMixin → TransactionTestCase

    # ── simple: one COT, one text field, one CO ───────────────────────────

    def test_simple_merge_and_revert(self):
        """
        Create a COT with a single text field and one custom object instance
        inside a branch.  Merge to main, then revert.

        Assertions
        ----------
        Before merge
            - COT is absent from main
            - field is absent from main

        After merge
            - COT is present in main
            - field is present in main
            - get() on the CO in main returns the correct field value

        After revert
            - COT is absent from main
            - field is absent from main
        """
        branch = _provision_branch('Simple Branch', self.MERGE_STRATEGY, self.user)
        request = _make_request(self.user)

        # ── create inside branch ──────────────────────────────────────────
        with activate_branch(branch), event_tracking(request):
            cot = CustomObjectType.objects.create(name='simple_cot', slug='simple-cot')
            field = CustomObjectTypeField.objects.create(
                custom_object_type=cot,
                name='notes',
                label='Notes',
                type='text',
            )
            Model = cot.get_model()
            co = Model.objects.create(notes='hello from branch')

        cot_pk, field_pk, co_pk = cot.pk, field.pk, co.pk

        # ── before merge: nothing in main ─────────────────────────────────
        self.assertFalse(
            CustomObjectType.objects.filter(pk=cot_pk).exists(),
            'COT must not be visible in main before merge',
        )
        self.assertFalse(
            CustomObjectTypeField.objects.filter(pk=field_pk).exists(),
            'Field must not be visible in main before merge',
        )

        # ── merge ─────────────────────────────────────────────────────────
        branch.merge(user=self.user, commit=True)
        branch.refresh_from_db()
        self.assertEqual(branch.status, BranchStatusChoices.MERGED)

        # ── after merge: present in main ──────────────────────────────────
        self.assertTrue(
            CustomObjectType.objects.filter(pk=cot_pk).exists(),
            'COT must be in main after merge',
        )
        self.assertTrue(
            CustomObjectTypeField.objects.filter(pk=field_pk).exists(),
            'Field must be in main after merge',
        )

        # get() the CO and verify its value
        cot_main = CustomObjectType.objects.get(pk=cot_pk)
        co_main = cot_main.get_model().objects.get(pk=co_pk)
        self.assertEqual(co_main.notes, 'hello from branch')

        # ── revert ────────────────────────────────────────────────────────
        branch.revert(user=self.user, commit=True)
        branch.refresh_from_db()

        # ── after revert: gone from main ──────────────────────────────────
        self.assertFalse(
            CustomObjectType.objects.filter(pk=cot_pk).exists(),
            'COT must not be in main after revert',
        )
        self.assertFalse(
            CustomObjectTypeField.objects.filter(pk=field_pk).exists(),
            'Field must not be in main after revert',
        )

    # ── comprehensive: one of every field type ────────────────────────────

    def test_comprehensive_merge_and_revert(self):
        """
        Create a COT with one field of each supported type, plus a CO instance,
        inside a branch.  Merge to main, then revert.

        Field types
        -----------
        text       — plain VARCHAR column
        integer    — INTEGER column
        boolean    — BOOLEAN column
        select     — VARCHAR column with a ChoiceSet
        object     — ForeignKey column (to dcim.Site)
        multiobject — M2M through-table (to dcim.Site)

        This exercises every distinct schema-editor operation
        (add_field, add_FK, create_through_table) across a merge cycle and
        verifies that all field values survive the round-trip.

        Assertions mirror test_simple_merge_and_revert but for every field type.
        """
        from core.models import ObjectType
        from dcim.models import Site
        from extras.models import CustomFieldChoiceSet

        # The Site is created in main before provisioning so it exists in both
        # main and the branch schema and is valid as an FK target during merge.
        with event_tracking(self.request):
            site = Site.objects.create(name='Reference Site', slug='reference-site')

        branch = _provision_branch('Comprehensive Branch', self.MERGE_STRATEGY, self.user)
        request = _make_request(self.user)

        site_ot = ObjectType.objects.get(app_label='dcim', model='site')
        cot_pk = None
        field_pks = {}
        co_pk = None

        # ── create inside branch ──────────────────────────────────────────
        with activate_branch(branch), event_tracking(request):
            choice_set = CustomFieldChoiceSet.objects.create(
                name='Statuses',
                extra_choices=[['active', 'Active'], ['inactive', 'Inactive']],
            )
            cot = CustomObjectType.objects.create(name='full_cot', slug='full-cot')
            cot_pk = cot.pk

            field_specs = [
                ('text_field', {'type': 'text'}),
                ('int_field', {'type': 'integer'}),
                ('bool_field', {'type': 'boolean'}),
                ('select_field', {'type': 'select', 'choice_set': choice_set}),
                ('obj_field', {'type': 'object', 'related_object_type': site_ot}),
                ('multi_field', {'type': 'multiobject', 'related_object_type': site_ot}),
            ]
            for name, kwargs in field_specs:
                f = CustomObjectTypeField.objects.create(
                    custom_object_type=cot,
                    name=name,
                    label=name.replace('_', ' ').title(),
                    **kwargs,
                )
                field_pks[name] = f.pk

            Model = cot.get_model()
            co = Model.objects.create(
                text_field='hello',
                int_field=42,
                bool_field=True,
                select_field='active',
                obj_field_id=site.pk,
                # multi_field left empty — through-table creation is still exercised
            )
            co_pk = co.pk

        # ── before merge: nothing in main ─────────────────────────────────
        self.assertFalse(
            CustomObjectType.objects.filter(pk=cot_pk).exists(),
            'COT must not be in main before merge',
        )
        for name, pk in field_pks.items():
            self.assertFalse(
                CustomObjectTypeField.objects.filter(pk=pk).exists(),
                f'Field {name!r} must not be in main before merge',
            )

        # ── merge ─────────────────────────────────────────────────────────
        branch.merge(user=self.user, commit=True)
        branch.refresh_from_db()
        self.assertEqual(branch.status, BranchStatusChoices.MERGED)

        # ── after merge: present in main, values intact ───────────────────
        self.assertTrue(
            CustomObjectType.objects.filter(pk=cot_pk).exists(),
            'COT must be in main after merge',
        )
        for name, pk in field_pks.items():
            self.assertTrue(
                CustomObjectTypeField.objects.filter(pk=pk).exists(),
                f'Field {name!r} must be in main after merge',
            )

        cot_main = CustomObjectType.objects.get(pk=cot_pk)
        co_main = cot_main.get_model().objects.get(pk=co_pk)
        self.assertEqual(co_main.text_field, 'hello')
        self.assertEqual(co_main.int_field, 42)
        self.assertTrue(co_main.bool_field)
        self.assertEqual(co_main.select_field, 'active')
        self.assertEqual(co_main.obj_field_id, site.pk)

        # ── revert ────────────────────────────────────────────────────────
        branch.revert(user=self.user, commit=True)
        branch.refresh_from_db()

        # ── after revert: gone from main ──────────────────────────────────
        self.assertFalse(
            CustomObjectType.objects.filter(pk=cot_pk).exists(),
            'COT must not be in main after revert',
        )
        for name, pk in field_pks.items():
            self.assertFalse(
                CustomObjectTypeField.objects.filter(pk=pk).exists(),
                f'Field {name!r} must not be in main after revert',
            )


# ── Concrete test classes (one per merge strategy) ────────────────────────────

@unittest.skipUnless(HAS_BRANCHING, 'netbox-branching is not installed')
class IterativeBranchingTestCase(BaseBranchingTests, TransactionTestCase):
    """Run BaseBranchingTests with the iterative merge strategy."""
    MERGE_STRATEGY = 'iterative'


@unittest.skipUnless(HAS_BRANCHING, 'netbox-branching is not installed')
class SquashBranchingTestCase(BaseBranchingTests, TransactionTestCase):
    """Run BaseBranchingTests with the squash merge strategy."""
    MERGE_STRATEGY = 'squash'


# ── Sync test ─────────────────────────────────────────────────────────────────

@unittest.skipUnless(HAS_BRANCHING, 'netbox-branching is not installed')
class BranchSyncTestCase(TransactionCleanupMixin, TransactionTestCase):
    """
    Test that objects created in main after a branch is provisioned are not
    visible in the branch until the branch is synced, and are correctly
    available in the branch after sync.
    """

    serialized_rollback = True

    def setUp(self):
        self.user = User.objects.create_user(username='testuser')
        self.request = _make_request(self.user)

    def tearDown(self):
        _close_branch_connections()
        super().tearDown()  # → TransactionCleanupMixin → TransactionTestCase

    def test_main_changes_synced_to_branch(self):
        """
        A COT, field, and CO created in main *after* a branch is provisioned
        must not appear in the branch before sync.  After branch.sync() they
        must be present and the CO must be retrievable.

        Scenario
        --------
        1. Provision branch (no COT exists yet).
        2. Create COT, field, and CO in main.
        3. Assert they are absent from the branch.
        4. sync() the branch.
        5. Assert they are present in the branch and the CO value is correct.
        """
        branch = _provision_branch('Sync Branch', 'iterative', self.user)
        request = _make_request(self.user)

        # ── create COT, field, CO in main ─────────────────────────────────
        with event_tracking(request):
            cot = CustomObjectType.objects.create(name='main_cot', slug='main-cot')
            field = CustomObjectTypeField.objects.create(
                custom_object_type=cot,
                name='title',
                label='Title',
                type='text',
            )
            Model = cot.get_model()
            co = Model.objects.create(title='main object')

        cot_pk, field_pk, co_pk = cot.pk, field.pk, co.pk

        # ── before sync: absent from branch ───────────────────────────────
        with activate_branch(branch):
            self.assertFalse(
                CustomObjectType.objects.filter(pk=cot_pk).exists(),
                'COT must not be in branch before sync',
            )
            self.assertFalse(
                CustomObjectTypeField.objects.filter(pk=field_pk).exists(),
                'Field must not be in branch before sync',
            )

        # ── sync ──────────────────────────────────────────────────────────
        branch.sync(user=self.user, commit=True)
        branch.refresh_from_db()

        # ── after sync: present in branch ─────────────────────────────────
        with activate_branch(branch):
            self.assertTrue(
                CustomObjectType.objects.filter(pk=cot_pk).exists(),
                'COT must be in branch after sync',
            )
            self.assertTrue(
                CustomObjectTypeField.objects.filter(pk=field_pk).exists(),
                'Field must be in branch after sync',
            )
            cot_branch = CustomObjectType.objects.get(pk=cot_pk)
            BranchModel = cot_branch.get_model()
            co_branch = BranchModel.objects.get(pk=co_pk)
            self.assertEqual(co_branch.title, 'main object')
