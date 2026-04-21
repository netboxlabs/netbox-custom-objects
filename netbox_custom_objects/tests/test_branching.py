"""
Branching integration tests for netbox-custom-objects.

Requires netbox-branching to be installed alongside this plugin.  All tests
are skipped when netbox-branching is absent so the suite remains clean in
environments that don't use branching.

These tests use TransactionTestCase (not TestCase) because branch schemas live
in separate PostgreSQL schemas backed by distinct database connections that
cannot be rolled back inside a single SAVEPOINT-based transaction.
"""
import datetime
import decimal
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
from netbox_custom_objects.tests.base import TransactionCleanupMixin, _recreate_contenttypes

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

    def setUp(self):
        super().setUp()  # → TransactionCleanupMixin.setUp() → _purge_stale_generated_models()
        _recreate_contenttypes()
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
        text        — plain VARCHAR column
        integer     — INTEGER column
        decimal     — DECIMAL column (exercises numeric precision handling)
        boolean     — BOOLEAN column
        datetime    — TIMESTAMPTZ column (exercises timezone-aware handling)
        select      — VARCHAR column with a ChoiceSet
        object      — ForeignKey column (to dcim.Site)
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

        test_dt = datetime.datetime(2024, 6, 15, 12, 0, 0, tzinfo=datetime.timezone.utc)
        test_decimal = decimal.Decimal('3.14')

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
                ('dec_field', {'type': 'decimal'}),
                ('bool_field', {'type': 'boolean'}),
                ('dt_field', {'type': 'datetime'}),
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
                dec_field=test_decimal,
                bool_field=True,
                dt_field=test_dt,
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
        self.assertEqual(co_main.dec_field, test_decimal)
        self.assertTrue(co_main.bool_field)
        self.assertEqual(co_main.dt_field, test_dt)
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

    # ── object modified inside branch ─────────────────────────────────────

    def test_object_modified_merge_and_revert(self):
        """
        CO that exists in main is modified inside a branch.  Merge brings the
        new value to main; revert restores the original.

        The COT and field are created in main before provisioning so the branch
        has a full schema copy.  Only the CO data changes inside the branch.
        """
        request = _make_request(self.user)

        with event_tracking(request):
            cot = CustomObjectType.objects.create(name='modify_cot', slug='modify-cot')
            CustomObjectTypeField.objects.create(
                custom_object_type=cot,
                name='notes',
                label='Notes',
                type='text',
            )
            Model = cot.get_model()
            co = Model.objects.create(notes='original value')

        co_pk = co.pk
        branch = _provision_branch('Modify Branch', self.MERGE_STRATEGY, self.user)
        branch_request = _make_request(self.user)

        # Modify CO inside the branch.
        with activate_branch(branch), event_tracking(branch_request):
            branch_co = cot.get_model().objects.get(pk=co_pk)
            branch_co.snapshot()  # captures pre-change state for ObjectChange.diff()['pre'] during revert
            branch_co.notes = 'modified in branch'
            branch_co.save()

        # Main must not see the modification yet.
        co.refresh_from_db()
        self.assertEqual(co.notes, 'original value', 'Main must not see branch modification before merge')

        # ── merge ─────────────────────────────────────────────────────────
        branch.merge(user=self.user, commit=True)
        branch.refresh_from_db()
        self.assertEqual(branch.status, BranchStatusChoices.MERGED)

        co.refresh_from_db()
        self.assertEqual(co.notes, 'modified in branch', 'Main must see modified value after merge')

        # ── revert ────────────────────────────────────────────────────────
        branch.revert(user=self.user, commit=True)

        co.refresh_from_db()
        self.assertEqual(co.notes, 'original value', 'Main must have original value after revert')

    # ── object deleted inside branch ──────────────────────────────────────

    def test_object_deleted_merge_and_revert(self):
        """
        CO that exists in main is deleted inside a branch.  Merge removes it
        from main; revert restores it.
        """
        request = _make_request(self.user)

        with event_tracking(request):
            cot = CustomObjectType.objects.create(name='delete_cot', slug='delete-cot')
            CustomObjectTypeField.objects.create(
                custom_object_type=cot,
                name='notes',
                label='Notes',
                type='text',
            )
            Model = cot.get_model()
            co = Model.objects.create(notes='will be deleted')

        co_pk = co.pk
        branch = _provision_branch('Delete Branch', self.MERGE_STRATEGY, self.user)
        branch_request = _make_request(self.user)

        with activate_branch(branch), event_tracking(branch_request):
            cot.get_model().objects.get(pk=co_pk).delete()

        # CO must still exist in main before merge.
        self.assertTrue(
            cot.get_model().objects.filter(pk=co_pk).exists(),
            'CO must still exist in main before merge',
        )

        # ── merge ─────────────────────────────────────────────────────────
        branch.merge(user=self.user, commit=True)
        branch.refresh_from_db()
        self.assertEqual(branch.status, BranchStatusChoices.MERGED)

        self.assertFalse(
            cot.get_model().objects.filter(pk=co_pk).exists(),
            'CO must be deleted from main after merge',
        )

        # ── revert ────────────────────────────────────────────────────────
        branch.revert(user=self.user, commit=True)

        self.assertTrue(
            cot.get_model().objects.filter(pk=co_pk).exists(),
            'CO must be restored in main after revert',
        )

    # ── field renamed inside branch → merge ───────────────────────────────

    def test_field_rename_merge_and_revert(self):
        """
        Field created and then renamed inside a branch.  Merge brings the COT
        with the renamed column to main; revert removes it.

        Exercises _schema_alter_field via the merge deserialization path using
        PK-based rename detection (same PK, different name values).
        """
        branch = _provision_branch('Rename Branch', self.MERGE_STRATEGY, self.user)
        request = _make_request(self.user)

        with activate_branch(branch), event_tracking(request):
            cot = CustomObjectType.objects.create(name='rename_cot', slug='rename-cot')
            field = CustomObjectTypeField.objects.create(
                custom_object_type=cot,
                name='old_name',
                label='Old Name',
                type='text',
            )
            # Load from DB so _original is set, then rename.
            field = CustomObjectTypeField.objects.get(pk=field.pk)
            field.snapshot()  # captures pre-change state for ObjectChange.diff()['pre'] during revert
            field.name = 'new_name'
            field.label = 'New Name'
            field.save()
            Model = cot.get_model()
            co = Model.objects.create(new_name='value after rename')

        field_pk, cot_pk, co_pk = field.pk, cot.pk, co.pk

        self.assertFalse(
            CustomObjectType.objects.filter(pk=cot_pk).exists(),
            'COT must not exist in main before merge',
        )

        # ── merge ─────────────────────────────────────────────────────────
        branch.merge(user=self.user, commit=True)
        branch.refresh_from_db()
        self.assertEqual(branch.status, BranchStatusChoices.MERGED)

        self.assertTrue(CustomObjectTypeField.objects.filter(pk=field_pk).exists())
        field_main = CustomObjectTypeField.objects.get(pk=field_pk)
        self.assertEqual(field_main.name, 'new_name', 'Field must have new name in main after merge')

        cot_main = CustomObjectType.objects.get(pk=cot_pk)
        co_main = cot_main.get_model().objects.get(pk=co_pk)
        self.assertEqual(co_main.new_name, 'value after rename')

        # ── revert ────────────────────────────────────────────────────────
        branch.revert(user=self.user, commit=True)

        self.assertFalse(CustomObjectType.objects.filter(pk=cot_pk).exists())
        self.assertFalse(CustomObjectTypeField.objects.filter(pk=field_pk).exists())

    # ── unique constraint toggled inside branch → merge ───────────────────

    def test_field_unique_toggle_merge_and_revert(self):
        """
        Field created without a unique constraint inside a branch, then
        toggled to unique=True.  Merge brings the COT with the UNIQUE
        constraint to main; revert removes it.

        Exercises alter_field for constraint-only changes via the merge path.
        """
        from django.db import connection as main_conn

        branch = _provision_branch('Unique Branch', self.MERGE_STRATEGY, self.user)
        request = _make_request(self.user)

        with activate_branch(branch), event_tracking(request):
            cot = CustomObjectType.objects.create(name='unique_cot', slug='unique-cot')
            field = CustomObjectTypeField.objects.create(
                custom_object_type=cot,
                name='code',
                label='Code',
                type='text',
                unique=False,
            )
            # Load from DB so _original is set, then enable unique.
            field = CustomObjectTypeField.objects.get(pk=field.pk)
            field.snapshot()  # captures pre-change state for ObjectChange.diff()['pre'] during revert
            field.unique = True
            field.save()
            Model = cot.get_model()
            co_pk = Model.objects.create(code='ABC').pk

        field_pk, cot_pk = field.pk, cot.pk

        # ── merge ─────────────────────────────────────────────────────────
        branch.merge(user=self.user, commit=True)
        branch.refresh_from_db()
        self.assertEqual(branch.status, BranchStatusChoices.MERGED)

        field_main = CustomObjectTypeField.objects.get(pk=field_pk)
        self.assertTrue(field_main.unique, 'Field must have unique=True in main after merge')

        # Verify the UNIQUE constraint exists in main's physical schema.
        cot_main = CustomObjectType.objects.get(pk=cot_pk)
        table_name = cot_main.get_database_table_name()
        with main_conn.cursor() as cursor:
            constraints = main_conn.introspection.get_constraints(cursor, table_name)
        self.assertTrue(
            any(c['unique'] and c.get('columns') == ['code'] for c in constraints.values()),
            'UNIQUE constraint on "code" must exist in main schema after merge',
        )

        # CO with code='ABC' must have survived the merge.
        cot_main2 = CustomObjectType.objects.get(pk=cot_pk)
        self.assertTrue(cot_main2.get_model().objects.filter(pk=co_pk).exists())

        # ── revert ────────────────────────────────────────────────────────
        branch.revert(user=self.user, commit=True)

        self.assertFalse(CustomObjectType.objects.filter(pk=cot_pk).exists())

    # ── non-schema field attributes inside branch → merge ─────────────────

    def test_field_non_schema_attrs_merge_and_revert(self):
        """
        Field attributes that do not affect the physical schema (label,
        primary, required, description) survive a branch merge and revert
        without causing schema errors or spurious ALTER TABLE calls.

        These attributes are excluded from _field_schema_key and exist only
        at the application layer.  This test confirms that altering them in
        a branch and merging correctly updates the ORM-level field record in
        main without touching the DB column definition.
        """
        branch = _provision_branch('Attrs Branch', self.MERGE_STRATEGY, self.user)
        request = _make_request(self.user)

        with activate_branch(branch), event_tracking(request):
            cot = CustomObjectType.objects.create(name='attrs_cot', slug='attrs-cot')
            field = CustomObjectTypeField.objects.create(
                custom_object_type=cot,
                name='title',
                label='Title',
                type='text',
                primary=False,
                required=False,
                description='original description',
            )
            # Load from DB so _original is set, then mutate non-schema attrs.
            field = CustomObjectTypeField.objects.get(pk=field.pk)
            field.snapshot()  # captures pre-change state for ObjectChange.diff()['pre'] during revert
            field.label = 'Updated Title'
            field.primary = True
            field.required = True
            field.description = 'updated description'
            field.save()

        field_pk, cot_pk = field.pk, cot.pk

        # ── merge ─────────────────────────────────────────────────────────
        branch.merge(user=self.user, commit=True)
        branch.refresh_from_db()
        self.assertEqual(branch.status, BranchStatusChoices.MERGED)

        field_main = CustomObjectTypeField.objects.get(pk=field_pk)
        self.assertEqual(field_main.label, 'Updated Title')
        self.assertTrue(field_main.primary)
        self.assertTrue(field_main.required)
        self.assertEqual(field_main.description, 'updated description')

        # ── revert ────────────────────────────────────────────────────────
        branch.revert(user=self.user, commit=True)

        self.assertFalse(CustomObjectType.objects.filter(pk=cot_pk).exists())
        self.assertFalse(CustomObjectTypeField.objects.filter(pk=field_pk).exists())


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

    def setUp(self):
        super().setUp()  # → TransactionCleanupMixin.setUp() → _purge_stale_generated_models()
        _recreate_contenttypes()
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


# ── Drift detection and Branch.migrate() tests ────────────────────────────────

@unittest.skipUnless(HAS_BRANCHING, 'netbox-branching is not installed')
class BranchMigrateTestCase(TransactionCleanupMixin, TransactionTestCase):
    """
    Tests for the drift-detection and schema-reconciliation path.

    Scenario:
    1. A COT and field(s) are created in main and a branch is provisioned
       (branch has a full schema copy at provision time).
    2. A field is then modified in main (added, removed, renamed, or
       unique-toggled).  on_custom_object_field_changed fires and marks the
       branch PENDING_MIGRATIONS.
    3. branch.migrate(user) runs the normal Django migration pass and then
       emits post_migrate, which fires on_branch_migrated.  That handler
       reconciles the branch's physical schema against main's current field
       definitions.
    4. The branch is back to READY and its physical column layout matches main.

    These tests do NOT use merge/revert — they isolate the separate
    on_branch_migrated reconciliation path for branches that pre-date a main
    schema change.  They use the iterative strategy only because the strategy
    affects merge order, not schema reconciliation.
    """

    def setUp(self):
        super().setUp()
        _recreate_contenttypes()
        self.user = User.objects.create_user(username='testuser')
        self.request = _make_request(self.user)

    def tearDown(self):
        _close_branch_connections()
        super().tearDown()

    def _get_branch_columns(self, branch, table_name):
        """Return the set of column names on table_name in the branch schema."""
        conn = connections[branch.connection_name]
        with conn.cursor() as cursor:
            return {col.name for col in conn.introspection.get_table_description(cursor, table_name)}

    def _get_branch_tables(self, branch):
        """Return the set of table names in the branch schema."""
        conn = connections[branch.connection_name]
        with conn.cursor() as cursor:
            return set(conn.introspection.table_names(cursor))

    def _get_branch_constraints(self, branch, table_name):
        """Return the constraints dict for table_name in the branch schema."""
        conn = connections[branch.connection_name]
        with conn.cursor() as cursor:
            return conn.introspection.get_constraints(cursor, table_name)

    # ── field added to main ───────────────────────────────────────────────

    def test_field_added_to_main_triggers_branch_migrate(self):
        """
        Field added to a COT in main marks the branch PENDING_MIGRATIONS.
        branch.migrate() fires on_branch_migrated which calls add_field to
        create the new column in the branch schema.
        """
        cot = CustomObjectType.objects.create(name='drift_add_cot', slug='drift-add-cot')
        CustomObjectTypeField.objects.create(
            custom_object_type=cot,
            name='existing_field',
            label='Existing',
            type='text',
        )

        branch = _provision_branch('Drift Add Branch', 'iterative', self.user)
        table_name = cot.get_database_table_name()

        self.assertEqual(branch.status, BranchStatusChoices.READY)
        self.assertIn('existing_field', self._get_branch_columns(branch, table_name))

        # Add a new field to main — on_custom_object_field_changed marks branch PENDING.
        CustomObjectTypeField.objects.create(
            custom_object_type=cot,
            name='new_field',
            label='New Field',
            type='integer',
        )

        branch.refresh_from_db()
        self.assertEqual(
            branch.status, BranchStatusChoices.PENDING_MIGRATIONS,
            'Branch must be PENDING_MIGRATIONS after field added to main',
        )
        self.assertNotIn(
            'new_field', self._get_branch_columns(branch, table_name),
            'New column must not exist in branch before migrate',
        )

        # migrate() fires on_branch_migrated → add_field runs on the branch schema.
        branch.migrate(user=self.user)
        branch.refresh_from_db()
        self.assertEqual(branch.status, BranchStatusChoices.READY)

        self.assertIn(
            'new_field', self._get_branch_columns(branch, table_name),
            'New column must be present in branch schema after migrate',
        )

    # ── field deleted from main ───────────────────────────────────────────

    def test_field_deleted_from_main_triggers_branch_migrate(self):
        """
        Field deleted from a COT in main marks the branch PENDING_MIGRATIONS.
        branch.migrate() fires on_branch_migrated which calls remove_field to
        drop the column from the branch schema.
        """
        cot = CustomObjectType.objects.create(name='drift_del_cot', slug='drift-del-cot')
        CustomObjectTypeField.objects.create(
            custom_object_type=cot,
            name='keep_field',
            label='Keep',
            type='text',
        )
        drop_field = CustomObjectTypeField.objects.create(
            custom_object_type=cot,
            name='drop_field',
            label='Drop',
            type='text',
        )

        branch = _provision_branch('Drift Del Branch', 'iterative', self.user)
        table_name = cot.get_database_table_name()

        self.assertIn('drop_field', self._get_branch_columns(branch, table_name))

        # Delete field from main.
        drop_field.delete()

        branch.refresh_from_db()
        self.assertEqual(branch.status, BranchStatusChoices.PENDING_MIGRATIONS)

        # Column still present in branch before migrate.
        self.assertIn('drop_field', self._get_branch_columns(branch, table_name))

        branch.migrate(user=self.user)
        branch.refresh_from_db()
        self.assertEqual(branch.status, BranchStatusChoices.READY)

        cols = self._get_branch_columns(branch, table_name)
        self.assertNotIn(
            'drop_field', cols,
            'Deleted column must be absent from branch schema after migrate',
        )
        self.assertIn('keep_field', cols, 'Unmodified column must remain in branch schema')

    # ── field renamed in main ─────────────────────────────────────────────

    def test_field_renamed_in_main_triggers_branch_migrate(self):
        """
        Field renamed in main marks the branch PENDING_MIGRATIONS.
        branch.migrate() fires on_branch_migrated which calls alter_field to
        rename the column in the branch schema using PK-based matching (same
        PK in both main and branch, different name values).
        """
        cot = CustomObjectType.objects.create(name='drift_ren_cot', slug='drift-ren-cot')
        field = CustomObjectTypeField.objects.create(
            custom_object_type=cot,
            name='old_col',
            label='Old',
            type='text',
        )

        branch = _provision_branch('Drift Ren Branch', 'iterative', self.user)
        table_name = cot.get_database_table_name()

        self.assertIn('old_col', self._get_branch_columns(branch, table_name))

        # Rename in main — load from DB so _original is set before modifying.
        field = CustomObjectTypeField.objects.get(pk=field.pk)
        field.name = 'new_col'
        field.label = 'New'
        field.save()

        branch.refresh_from_db()
        self.assertEqual(branch.status, BranchStatusChoices.PENDING_MIGRATIONS)

        branch.migrate(user=self.user)
        branch.refresh_from_db()
        self.assertEqual(branch.status, BranchStatusChoices.READY)

        cols = self._get_branch_columns(branch, table_name)
        self.assertIn('new_col', cols, 'Renamed column must exist in branch schema after migrate')
        self.assertNotIn('old_col', cols, 'Old column name must be absent from branch schema after migrate')

    # ── unique constraint toggled in main ─────────────────────────────────

    def test_unique_toggled_in_main_triggers_branch_migrate(self):
        """
        Field's unique constraint toggled in main marks the branch
        PENDING_MIGRATIONS.  branch.migrate() reconciles the constraint in
        the branch's physical schema.
        """
        cot = CustomObjectType.objects.create(name='drift_uniq_cot', slug='drift-uniq-cot')
        field = CustomObjectTypeField.objects.create(
            custom_object_type=cot,
            name='code',
            label='Code',
            type='text',
            unique=False,
        )

        branch = _provision_branch('Drift Uniq Branch', 'iterative', self.user)
        table_name = cot.get_database_table_name()

        # Branch must not have a unique constraint on 'code' initially.
        constraints_before = self._get_branch_constraints(branch, table_name)
        self.assertFalse(
            any(c['unique'] and c.get('columns') == ['code'] for c in constraints_before.values()),
            'Branch must not have UNIQUE on "code" before toggle',
        )

        # Toggle unique=True in main.
        field = CustomObjectTypeField.objects.get(pk=field.pk)
        field.unique = True
        field.save()

        branch.refresh_from_db()
        self.assertEqual(branch.status, BranchStatusChoices.PENDING_MIGRATIONS)

        branch.migrate(user=self.user)
        branch.refresh_from_db()
        self.assertEqual(branch.status, BranchStatusChoices.READY)

        constraints_after = self._get_branch_constraints(branch, table_name)
        self.assertTrue(
            any(c['unique'] and c.get('columns') == ['code'] for c in constraints_after.values()),
            'Branch must have UNIQUE constraint on "code" after migrate',
        )

    # ── multiobject field renamed in main (through-table rename) ─────────

    def test_multiobject_field_renamed_in_main_triggers_branch_migrate(self):
        """
        MULTIOBJECT field renamed in main marks the branch PENDING_MIGRATIONS.
        branch.migrate() fires on_branch_migrated which renames the through
        table in the branch schema in addition to the column alter_field.

        Exercises the MULTIOBJECT rename branch of _schema_alter_field.
        """
        from core.models import ObjectType

        site_ot = ObjectType.objects.get(app_label='dcim', model='site')

        cot = CustomObjectType.objects.create(name='drift_m2m_cot', slug='drift-m2m-cot')
        field = CustomObjectTypeField.objects.create(
            custom_object_type=cot,
            name='related_sites',
            label='Related Sites',
            type='multiobject',
            related_object_type=site_ot,
        )

        old_through = field.through_table_name

        branch = _provision_branch('Drift M2M Branch', 'iterative', self.user)

        self.assertIn(old_through, self._get_branch_tables(branch))

        # Rename in main.
        field = CustomObjectTypeField.objects.get(pk=field.pk)
        field.name = 'linked_sites'
        field.label = 'Linked Sites'
        field.save()

        new_through = field.through_table_name

        branch.refresh_from_db()
        self.assertEqual(branch.status, BranchStatusChoices.PENDING_MIGRATIONS)

        branch.migrate(user=self.user)
        branch.refresh_from_db()
        self.assertEqual(branch.status, BranchStatusChoices.READY)

        branch_tables = self._get_branch_tables(branch)
        self.assertIn(new_through, branch_tables, 'Renamed through table must exist in branch after migrate')
        self.assertNotIn(old_through, branch_tables, 'Old through table must be absent from branch after migrate')
