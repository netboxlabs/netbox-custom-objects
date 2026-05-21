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
import logging
import os
import time
import unittest
import uuid

from core.models import ObjectType
from dcim.models import Site
from django.contrib.auth import get_user_model
from django.db import connection as main_conn, connections
from django.test import RequestFactory, TransactionTestCase
from django.urls import reverse
from extras.models import CustomFieldChoiceSet

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

logger = logging.getLogger(__name__)
User = get_user_model()


def _make_request(user):
    """Return a fresh request object suitable for event_tracking."""
    request = RequestFactory().get(reverse('home'))
    request.id = uuid.uuid4()
    request.user = user
    return request


# Provisioning timeout for branch tests. Override via the
# ``NETBOX_CO_BRANCH_PROVISION_TIMEOUT`` env var (seconds) when CI flakes.
BRANCH_PROVISION_TIMEOUT = float(
    os.environ.get('NETBOX_CO_BRANCH_PROVISION_TIMEOUT', '30')
)


def _provision_branch(name, merge_strategy, user, timeout=None):
    """Create and wait for a branch to reach READY status."""
    if timeout is None:
        timeout = BRANCH_PROVISION_TIMEOUT
    branch = Branch(name=name, merge_strategy=merge_strategy)
    branch.save(provision=False)
    branch.provision(user=user)
    deadline = time.time() + timeout
    while time.time() < deadline:
        branch.refresh_from_db()
        if branch.status == BranchStatusChoices.READY:
            return branch
        time.sleep(0.1)
    raise TimeoutError(
        f'Branch {name!r} did not reach READY within {timeout:.0f} s '
        f'(status={branch.status!r})'
    )


def _close_branch_connections():
    """Close any open branch database connections.

    Best-effort cleanup between tests.  A ``DatabaseError`` here typically
    just means the connection was already closed by a previous teardown
    pass; we log at DEBUG so a genuine bug isn't silently hidden but normal
    multi-pass teardown stays quiet.
    """
    from django.db.utils import DatabaseError
    for branch in Branch.objects.all():
        try:
            connections[branch.connection_name].close()
        except DatabaseError:
            logger.debug(
                'failed to close branch connection %r',
                branch.connection_name, exc_info=True,
            )


class BranchingTestBase(TransactionCleanupMixin):
    """
    Common per-test lifecycle for branching-aware test classes.

    Centralises the ``_recreate_contenttypes`` / ``_make_request`` /
    ``_close_branch_connections`` boilerplate that was repeated across every
    branch test class so a new test class doesn't accidentally skip a step.

    Subclasses still need to inherit from ``TransactionTestCase`` (directly,
    not via this mixin) because branch schemas live in separate PostgreSQL
    schemas backed by distinct DB connections that can't be rolled back
    inside a single SAVEPOINT-based transaction.
    """

    def setUp(self):
        # → TransactionCleanupMixin.setUp() → _purge_stale_generated_models()
        super().setUp()
        _recreate_contenttypes()
        self.user = User.objects.create_user(username='testuser')
        self.request = _make_request(self.user)

    def tearDown(self):
        _close_branch_connections()
        # → TransactionCleanupMixin.tearDown() → TransactionTestCase
        super().tearDown()


# ── Shared merge/revert tests (strategy-agnostic) ────────────────────────────

@unittest.skipUnless(HAS_BRANCHING, 'netbox-branching is not installed')
class BaseBranchingTests(BranchingTestBase):
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

        # Capture the multi_field's physical through-table name *while* it
        # still exists so we can confirm it's gone after revert.
        multi_field_main = CustomObjectTypeField.objects.get(pk=field_pks['multi_field'])
        through_table = multi_field_main.through_table_name

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
        # The multi_field's through-table must also be physically dropped from
        # main's schema — ORM absence isn't enough; without this assertion an
        # orphaned through table could survive the revert and break a later
        # COT that picks up the same id.
        self.assertNotIn(
            through_table,
            main_conn.introspection.table_names(),
            f'Through-table {through_table!r} must be physically dropped after revert',
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

    # ── existing main COT extended with new fields inside a branch ────────

    def test_extend_main_cot_with_new_fields_merge_and_revert(self):
        """
        COT exists in main with text + object + multiobject fields and a CO with all
        three filled.  A branch then *adds* new fields of each type to the same COT
        and inserts a new CO that uses both the original and the new fields.  Merge
        brings the new fields and CO into main; revert removes them and leaves the
        original CO intact.

        Exercises ``_schema_add_field`` (including FK and M2M through-table creation)
        running against a target schema that already has live data and existing
        through-tables — distinct from ``test_comprehensive_merge_and_revert`` which
        creates everything greenfield inside the branch.
        """
        # Two Sites in main so they exist in both schemas as valid FK targets.
        with event_tracking(self.request):
            site_a = Site.objects.create(name='Site A', slug='site-a')
            site_b = Site.objects.create(name='Site B', slug='site-b')

        site_ot = ObjectType.objects.get(app_label='dcim', model='site')

        # ── main: COT with text + object + multiobject; one CO ────────────
        with event_tracking(self.request):
            cot = CustomObjectType.objects.create(name='extend_cot', slug='extend-cot')
            CustomObjectTypeField.objects.create(
                custom_object_type=cot, name='main_text', label='Main Text', type='text',
            )
            CustomObjectTypeField.objects.create(
                custom_object_type=cot, name='main_obj', label='Main Obj',
                type='object', related_object_type=site_ot,
            )
            CustomObjectTypeField.objects.create(
                custom_object_type=cot, name='main_multi', label='Main Multi',
                type='multiobject', related_object_type=site_ot,
            )
            MainModel = cot.get_model()
            co_main = MainModel.objects.create(
                main_text='main co text',
                main_obj_id=site_a.pk,
            )
            co_main.main_multi.set([site_a, site_b])

        cot_pk = cot.pk
        co_main_pk = co_main.pk

        branch = _provision_branch('Extend Branch', self.MERGE_STRATEGY, self.user)
        branch_request = _make_request(self.user)

        # ── branch: add new fields of each type, then create a CO with all ─
        with activate_branch(branch), event_tracking(branch_request):
            CustomObjectTypeField.objects.create(
                custom_object_type=cot, name='branch_text', label='Branch Text', type='text',
            )
            CustomObjectTypeField.objects.create(
                custom_object_type=cot, name='branch_obj', label='Branch Obj',
                type='object', related_object_type=site_ot,
            )
            CustomObjectTypeField.objects.create(
                custom_object_type=cot, name='branch_multi', label='Branch Multi',
                type='multiobject', related_object_type=site_ot,
            )
            BranchModel = cot.get_model()
            co_branch = BranchModel.objects.create(
                main_text='branch co original text',
                main_obj_id=site_b.pk,
                branch_text='branch co new text',
                branch_obj_id=site_a.pk,
            )
            co_branch.main_multi.set([site_b])
            co_branch.branch_multi.set([site_a, site_b])

        co_branch_pk = co_branch.pk

        # ── before merge: branch fields and branch CO not visible in main ─
        for fname in ('branch_text', 'branch_obj', 'branch_multi'):
            self.assertFalse(
                CustomObjectTypeField.objects.filter(custom_object_type=cot, name=fname).exists(),
                f'{fname!r} must not be in main before merge',
            )
        MainModelPre = cot.get_model()
        self.assertFalse(
            MainModelPre.objects.filter(pk=co_branch_pk).exists(),
            'Branch CO must not be in main before merge',
        )

        # ── merge ─────────────────────────────────────────────────────────
        branch.merge(user=self.user, commit=True)
        branch.refresh_from_db()
        self.assertEqual(branch.status, BranchStatusChoices.MERGED)

        # ── after merge: all six fields present, both COs intact ──────────
        cot_main = CustomObjectType.objects.get(pk=cot_pk)
        names = set(cot_main.fields(manager='objects').values_list('name', flat=True))
        self.assertEqual(
            names,
            {'main_text', 'main_obj', 'main_multi', 'branch_text', 'branch_obj', 'branch_multi'},
        )

        MergedModel = cot_main.get_model()

        # Original main CO untouched.
        co_main_after = MergedModel.objects.get(pk=co_main_pk)
        self.assertEqual(co_main_after.main_text, 'main co text')
        self.assertEqual(co_main_after.main_obj_id, site_a.pk)
        self.assertEqual(
            set(co_main_after.main_multi.values_list('pk', flat=True)),
            {site_a.pk, site_b.pk},
        )

        # CO created in branch is in main with all field values.
        co_branch_after = MergedModel.objects.get(pk=co_branch_pk)
        self.assertEqual(co_branch_after.main_text, 'branch co original text')
        self.assertEqual(co_branch_after.main_obj_id, site_b.pk)
        self.assertEqual(co_branch_after.branch_text, 'branch co new text')
        self.assertEqual(co_branch_after.branch_obj_id, site_a.pk)
        self.assertEqual(
            set(co_branch_after.main_multi.values_list('pk', flat=True)),
            {site_b.pk},
        )
        self.assertEqual(
            set(co_branch_after.branch_multi.values_list('pk', flat=True)),
            {site_a.pk, site_b.pk},
        )

        # ── revert ────────────────────────────────────────────────────────
        branch.revert(user=self.user, commit=True)

        # Branch fields gone; main fields still present.
        names_after_revert = set(
            cot_main.fields(manager='objects').values_list('name', flat=True)
        )
        self.assertEqual(names_after_revert, {'main_text', 'main_obj', 'main_multi'})

        RevertedModel = cot_main.get_model()
        # Original main CO survived.
        co_main_reverted = RevertedModel.objects.get(pk=co_main_pk)
        self.assertEqual(co_main_reverted.main_text, 'main co text')
        self.assertEqual(co_main_reverted.main_obj_id, site_a.pk)
        self.assertEqual(
            set(co_main_reverted.main_multi.values_list('pk', flat=True)),
            {site_a.pk, site_b.pk},
        )
        # Branch CO removed.
        self.assertFalse(
            RevertedModel.objects.filter(pk=co_branch_pk).exists(),
            'CO created in branch must be gone after revert',
        )

    # ── COT deleted inside branch → merge / revert ────────────────────────

    def test_cot_deleted_in_branch_merge_and_revert(self):
        """
        Delete a COT (with fields and CO instances in main) inside a branch,
        merge the deletion to main, then revert and verify the schema is
        restored.

        Scenario
        --------
        1. Main: create COT with a text field, an object field, and a
           multiobject field; insert one CO using all three.
        2. Provision branch.
        3. Branch: delete the COT.
        4. Merge: main loses the COT, its fields, the CO instances, the
           main table, and the multi-object through-table.
        5. Revert: COT, fields, the dynamic table, and the multi-object
           through-table must all come back at the *original* PKs — the
           ContentType pk in particular has to survive the round-trip so
           any existing FK references remain valid.

        Both schema directions are exercised:
        - Forward (merge): the squash strategy collapses field-level
          deletes alongside the COT delete, so ``_schema_remove_field``
          must stay idempotent when the COT's own ``delete()`` already
          dropped the through-table.
        - Backward (revert): ``CustomObjectType.delete()`` destroys the
          related ContentType row to satisfy ChangeDiff's PROTECT FK.
          Restoring the COT then requires the original ContentType pk
          to come back too — handled by ``restore_object`` (the DELETE-
          undo counterpart to ``deserialize_object``).

        CO data preservation is **not** asserted here.  The current
        delete path drops the dynamic table via raw DDL
        (``schema_editor.delete_model``) without firing per-row
        ``pre_delete`` signals, so no ObjectChange records exist for the
        CO instances and there is nothing for revert to replay.
        Recovering CO data across a COT-delete cycle would require
        iterating instances and calling ``.delete()`` on each before the
        DROP TABLE — a separate, larger change.
        """
        # FK target lives in main so both schemas share it.
        with event_tracking(self.request):
            site = Site.objects.create(name='COT-delete Site', slug='cot-delete-site')

        site_ot = ObjectType.objects.get(app_label='dcim', model='site')

        # ── main: COT + fields + CO ───────────────────────────────────────
        with event_tracking(self.request):
            cot = CustomObjectType.objects.create(name='doomed_cot', slug='doomed-cot')
            CustomObjectTypeField.objects.create(
                custom_object_type=cot, name='note', label='Note', type='text',
            )
            CustomObjectTypeField.objects.create(
                custom_object_type=cot, name='site_ref', label='Site',
                type='object', related_object_type=site_ot,
            )
            multi_field = CustomObjectTypeField.objects.create(
                custom_object_type=cot, name='sites', label='Sites',
                type='multiobject', related_object_type=site_ot,
            )
            Model = cot.get_model()
            co = Model.objects.create(note='hello', site_ref_id=site.pk)
            co.sites.set([site])

        cot_pk = cot.pk
        co_pk = co.pk
        co_table = cot.get_database_table_name()
        through_table = multi_field.through_table_name

        # Sanity: physical tables exist in main before we touch anything.
        self.assertIn(co_table, main_conn.introspection.table_names())
        self.assertIn(through_table, main_conn.introspection.table_names())

        branch = _provision_branch('COT Delete Branch', self.MERGE_STRATEGY, self.user)
        branch_request = _make_request(self.user)

        # ── branch: delete the COT ────────────────────────────────────────
        with activate_branch(branch), event_tracking(branch_request):
            branch_cot = CustomObjectType.objects.get(pk=cot_pk)
            branch_cot.snapshot()
            branch_cot.delete()

        # Main still has the COT before the merge applies.
        self.assertTrue(CustomObjectType.objects.filter(pk=cot_pk).exists())

        # ── merge ─────────────────────────────────────────────────────────
        branch.merge(user=self.user, commit=True)
        branch.refresh_from_db()
        self.assertEqual(branch.status, BranchStatusChoices.MERGED)

        # COT, fields, and physical tables must all be gone from main.
        self.assertFalse(
            CustomObjectType.objects.filter(pk=cot_pk).exists(),
            'COT must be gone from main after merge of branch deletion',
        )
        main_tables = main_conn.introspection.table_names()
        self.assertNotIn(
            co_table, main_tables,
            f'Main CO table {co_table!r} must be dropped after merge',
        )
        self.assertNotIn(
            through_table, main_tables,
            f'Through-table {through_table!r} must be dropped after merge',
        )

        # ── revert ────────────────────────────────────────────────────────
        branch.revert(user=self.user, commit=True)
        branch.refresh_from_db()

        # COT and fields must come back at their original pks.
        self.assertTrue(
            CustomObjectType.objects.filter(pk=cot_pk).exists(),
            'COT must be restored after revert of branch deletion',
        )
        restored = CustomObjectType.objects.get(pk=cot_pk)
        field_names = set(restored.fields(manager='objects').values_list('name', flat=True))
        self.assertEqual(field_names, {'note', 'site_ref', 'sites'})

        # The COT comes back with a fresh ContentType/ObjectType pair
        # (clean_fields nulls the stale FK; the post_save handler then
        # calls get_or_create which creates a new row).  The pk is
        # intentionally not preserved — cross-branch audit data that
        # referenced the original pk was already invalidated when the
        # COT was deleted, so a fresh pk is the honest representation.
        restored_model = restored.get_model()
        self.assertIsNotNone(restored.object_type_id)
        self.assertTrue(
            ObjectType.objects.filter(pk=restored.object_type_id).exists(),
            'Restored COT must reference a live ObjectType row',
        )

        restored_tables = main_conn.introspection.table_names()
        self.assertIn(co_table, restored_tables, 'CO table must be re-created on revert')
        self.assertIn(through_table, restored_tables, 'Through-table must be re-created on revert')

        # CO data is NOT restored — see docstring.  ``co_pk`` is referenced
        # here only to make the unused-variable warning irrelevant; the row
        # at that pk legitimately does not exist after revert.
        self.assertFalse(
            restored_model.objects.filter(pk=co_pk).exists(),
            'CO instances are not recovered by revert (see test docstring)',
        )

    # ── multi-object field rename across merge ────────────────────────────

    def test_multiobject_field_rename_merge_and_revert(self):
        """
        Rename a multi-object field inside a branch and merge.

        Through-table renames are the most fragile schema operation: the
        physical table name changes (``alter_db_table``) but the integer
        FKs to the parent CO model and to the related object type must
        keep pointing at the same rows.  This test verifies that:

        * The old through-table is gone from main after merge.
        * The new through-table is present and holds the same rows.
        * Reading the M2M via the new accessor returns the original
          values intact.
        * Revert restores the old through-table name and field name.
        """
        site_ot = ObjectType.objects.get(app_label='dcim', model='site')

        with event_tracking(self.request):
            site_a = Site.objects.create(name='M2M Site A', slug='m2m-site-a')
            site_b = Site.objects.create(name='M2M Site B', slug='m2m-site-b')

        # ── main: COT with a multi-object field + a CO with M2M values ────
        with event_tracking(self.request):
            cot = CustomObjectType.objects.create(name='m2m_rename_cot', slug='m2m-rename-cot')
            field = CustomObjectTypeField.objects.create(
                custom_object_type=cot, name='tags_old', label='Tags',
                type='multiobject', related_object_type=site_ot,
            )
            MainModel = cot.get_model()
            co = MainModel.objects.create()
            co.tags_old.set([site_a, site_b])

        cot_pk = cot.pk
        field_pk = field.pk
        co_pk = co.pk
        old_through = field.through_table_name

        branch = _provision_branch('M2M Rename Branch', self.MERGE_STRATEGY, self.user)
        branch_request = _make_request(self.user)

        # ── branch: rename the multi-object field ─────────────────────────
        with activate_branch(branch), event_tracking(branch_request):
            f = CustomObjectTypeField.objects.get(pk=field_pk)
            f.snapshot()
            f.name = 'tags_new'
            f.label = 'Tags (renamed)'
            f.save()

        # Compute the post-rename through-table name from a freshly-loaded
        # field record so we don't depend on the in-memory branch state.
        renamed_field = CustomObjectTypeField.objects.get(pk=field_pk)
        # Field name in main hasn't applied yet (still 'tags_old' there) — we
        # need the *branch's* current name, which is what the rename target is.
        new_through = (
            f"custom_objects_{renamed_field.custom_object_type_id}_tags_new"
        )

        # Before merge: main still sees the old field/through-table.
        self.assertEqual(
            CustomObjectTypeField.objects.get(pk=field_pk).name, 'tags_old',
        )
        main_tables_before = main_conn.introspection.table_names()
        self.assertIn(old_through, main_tables_before)
        self.assertNotIn(new_through, main_tables_before)

        # ── merge ─────────────────────────────────────────────────────────
        branch.merge(user=self.user, commit=True)
        branch.refresh_from_db()
        self.assertEqual(branch.status, BranchStatusChoices.MERGED)

        # After merge: field name is updated; old through-table is gone, new
        # through-table is present, and its rows survived the rename.
        self.assertEqual(
            CustomObjectTypeField.objects.get(pk=field_pk).name, 'tags_new',
        )
        main_tables_after = main_conn.introspection.table_names()
        self.assertNotIn(
            old_through, main_tables_after,
            f'Old through-table {old_through!r} must be gone after rename merge',
        )
        self.assertIn(
            new_through, main_tables_after,
            f'New through-table {new_through!r} must exist after rename merge',
        )

        MergedModel = CustomObjectType.objects.get(pk=cot_pk).get_model()
        co_merged = MergedModel.objects.get(pk=co_pk)
        self.assertEqual(
            set(co_merged.tags_new.values_list('pk', flat=True)),
            {site_a.pk, site_b.pk},
            'M2M values must survive the through-table rename',
        )
        # Old accessor must no longer be accessible on the model.
        self.assertFalse(
            hasattr(co_merged, 'tags_old'),
            'Old field accessor must be gone after rename merge',
        )

        # ── revert ────────────────────────────────────────────────────────
        branch.revert(user=self.user, commit=True)
        branch.refresh_from_db()

        # Field name restored, through-table restored, rows intact.
        self.assertEqual(
            CustomObjectTypeField.objects.get(pk=field_pk).name, 'tags_old',
        )
        main_tables_reverted = main_conn.introspection.table_names()
        self.assertIn(
            old_through, main_tables_reverted,
            f'Old through-table {old_through!r} must be restored after revert',
        )
        self.assertNotIn(
            new_through, main_tables_reverted,
            f'New through-table {new_through!r} must be gone after revert',
        )

        RevertedModel = CustomObjectType.objects.get(pk=cot_pk).get_model()
        co_reverted = RevertedModel.objects.get(pk=co_pk)
        self.assertEqual(
            set(co_reverted.tags_old.values_list('pk', flat=True)),
            {site_a.pk, site_b.pk},
            'M2M values must survive the round-trip rename → revert',
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


# ── Branch deletion (abandon without merge) ───────────────────────────────────

@unittest.skipUnless(HAS_BRANCHING, 'netbox-branching is not installed')
class BranchDeletionTestCase(BranchingTestBase, TransactionTestCase):
    """
    Deleting a branch without merging must drop the branch's PostgreSQL
    schema and must NOT leak any of the branch's COT / field / table state
    into main.

    The branch deletion path (``Branch.delete()`` → ``deprovision()`` →
    ``DROP SCHEMA ... CASCADE``) bypasses the merge/revert ObjectChange
    replay engine.  We exercise it here so that the abandon flow stays
    correct even though it doesn't go through the same code as merge.
    """

    def test_branch_delete_without_merge_does_not_leak_to_main(self):
        site_ot = ObjectType.objects.get(app_label='dcim', model='site')

        with event_tracking(self.request):
            site = Site.objects.create(name='Abandon Site', slug='abandon-site')

        branch = _provision_branch('Abandon Branch', 'iterative', self.user)
        schema_name = branch.schema_name
        branch_request = _make_request(self.user)

        # ── branch: create COT + fields + CO that exist ONLY in the branch ─
        branch_cot_pk = None
        branch_field_pk = None
        branch_co_pk = None
        with activate_branch(branch), event_tracking(branch_request):
            cot = CustomObjectType.objects.create(name='abandon_cot', slug='abandon-cot')
            field = CustomObjectTypeField.objects.create(
                custom_object_type=cot,
                name='label',
                label='Label',
                type='text',
            )
            multi_field = CustomObjectTypeField.objects.create(
                custom_object_type=cot, name='multi', label='Multi',
                type='multiobject', related_object_type=site_ot,
            )
            co = cot.get_model().objects.create(label='only in branch')
            co.multi.set([site])
            branch_cot_pk = cot.pk
            branch_field_pk = field.pk
            branch_co_pk = co.pk
            branch_multi_through = multi_field.through_table_name
            branch_co_table = cot.get_database_table_name()

        # The branch's schema must exist before we delete it.
        with main_conn.cursor() as cursor:
            cursor.execute(
                'SELECT 1 FROM information_schema.schemata WHERE schema_name = %s',
                [schema_name],
            )
            self.assertTrue(cursor.fetchone(), f'Branch schema {schema_name!r} must exist before delete')

        # Main must NOT have any of the branch-only state.
        self.assertFalse(
            CustomObjectType.objects.filter(pk=branch_cot_pk).exists(),
            'COT created in branch must not be visible in main',
        )
        self.assertFalse(
            CustomObjectTypeField.objects.filter(pk=branch_field_pk).exists(),
            'Field created in branch must not be visible in main',
        )
        main_tables_pre = main_conn.introspection.table_names()
        self.assertNotIn(branch_co_table, main_tables_pre)
        self.assertNotIn(branch_multi_through, main_tables_pre)

        # ── delete (abandon) the branch ───────────────────────────────────
        branch.delete()

        # Schema must be gone.
        with main_conn.cursor() as cursor:
            cursor.execute(
                'SELECT 1 FROM information_schema.schemata WHERE schema_name = %s',
                [schema_name],
            )
            self.assertIsNone(
                cursor.fetchone(),
                f'Branch schema {schema_name!r} must be dropped after Branch.delete()',
            )

        # Main is still clean — no branch-only state was promoted.
        self.assertFalse(
            CustomObjectType.objects.filter(pk=branch_cot_pk).exists(),
            'Abandoned-branch COT must not appear in main',
        )
        self.assertFalse(
            CustomObjectTypeField.objects.filter(pk=branch_field_pk).exists(),
            'Abandoned-branch field must not appear in main',
        )
        main_tables_post = main_conn.introspection.table_names()
        self.assertNotIn(
            branch_co_table, main_tables_post,
            'Branch-only CO table must not appear in main after delete',
        )
        self.assertNotIn(
            branch_multi_through, main_tables_post,
            'Branch-only through-table must not appear in main after delete',
        )

        # The Branch row itself must be gone.
        self.assertFalse(
            Branch.objects.filter(pk=branch.pk).exists(),
            'Branch row must be deleted',
        )

        # branch_co_pk is asserted unused but referenced for clarity.
        self.assertIsNotNone(branch_co_pk)


# ── Sync test ─────────────────────────────────────────────────────────────────

@unittest.skipUnless(HAS_BRANCHING, 'netbox-branching is not installed')
class BranchSyncTestCase(BranchingTestBase, TransactionTestCase):
    """
    Test that objects created in main after a branch is provisioned are not
    visible in the branch until the branch is synced, and are correctly
    available in the branch after sync.
    """

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


# ── Concurrent-edit tests (both main and branch modified before sync/merge) ───

@unittest.skipUnless(HAS_BRANCHING, 'netbox-branching is not installed')
class ConcurrentEditSyncTestCase(BranchingTestBase, TransactionTestCase):
    """
    Sync scenarios where both main and branch accumulate changes before sync().

    Mirrors netbox-branching's test_sync_m2m_tags_concurrent_changes pattern:
    after sync(), main's ObjectChanges are applied on top of whatever the branch
    did, so main's post-change state takes precedence for any conflicting record.
    """

    def test_co_values_modified_in_both_sync(self):
        """
        CO field values modified in both main and branch before sync.

        Scenario
        --------
        1. Create COT + field 'notes' + two COs in main.
        2. Provision branch (branch sees same COs).
        3. In branch:  update shared CO to 'modified in branch'.
        4. In main:    update a different CO; create a brand-new CO.
        5. sync() applies main's ObjectChanges to the branch.

        Expected after sync
        -------------------
        - Main's new CO is visible in the branch.
        - CO modified in main has main's value in the branch.
        - Branch's own CO modification is overwritten by main's replay for
          the same PK (main wins on sync, same as tag-conflict behaviour).
        """
        request = _make_request(self.user)

        with event_tracking(request):
            cot = CustomObjectType.objects.create(name='sync_co_cot', slug='sync-co-cot')
            CustomObjectTypeField.objects.create(
                custom_object_type=cot, name='notes', label='Notes', type='text',
            )
            Model = cot.get_model()
            co_shared = Model.objects.create(notes='original shared')
            co_main_only = Model.objects.create(notes='main only original')

        co_shared_pk = co_shared.pk
        co_main_only_pk = co_main_only.pk
        branch = _provision_branch('Sync CO Both', 'iterative', self.user)
        branch_request = _make_request(self.user)

        # ── branch: update the shared CO ─────────────────────────────────
        with activate_branch(branch), event_tracking(branch_request):
            BM = cot.get_model()
            co = BM.objects.get(pk=co_shared_pk)
            co.snapshot()
            co.notes = 'modified in branch'
            co.save()
            branch_new = BM.objects.create(notes='new in branch')
        branch_new_pk = branch_new.pk

        # ── main: update the other CO; add a new CO ───────────────────────
        with event_tracking(request):
            MM = cot.get_model()
            co = MM.objects.get(pk=co_main_only_pk)
            co.snapshot()
            co.notes = 'modified in main'
            co.save()
            main_new = MM.objects.create(notes='new in main')
        main_new_pk = main_new.pk

        # ── sync ──────────────────────────────────────────────────────────
        branch.sync(user=self.user, commit=True)

        with activate_branch(branch):
            SyncedCOT = CustomObjectType.objects.get(pk=cot.pk)
            SM = SyncedCOT.get_model()

            # CO modified in main must reflect main's value.
            self.assertEqual(SM.objects.get(pk=co_main_only_pk).notes, 'modified in main')
            # CO created in main must be visible in branch after sync.
            self.assertTrue(SM.objects.filter(pk=main_new_pk).exists(),
                            'CO created in main must appear in branch after sync')
            # CO created in branch was not deleted by sync.
            self.assertTrue(SM.objects.filter(pk=branch_new_pk).exists(),
                            'CO created in branch must still exist after sync')

    def test_field_rename_in_branch_co_add_in_main_sync(self):
        """
        Field renamed inside a branch; new CO added in main (no rename in main).

        Scenario
        --------
        1. Create COT + field 'alpha' + CO in main.
        2. Provision branch.
        3. In branch:  rename 'alpha' → 'branch_alpha'; create a CO.
        4. In main:    create a new CO using the original field name 'alpha'.
        5. sync() replays main's CO-create on top of the branch.

        Expected after sync
        -------------------
        - Branch retains the renamed field ('branch_alpha').
        - CO created in main is present in branch.
        - CO created in branch still exists.

        This exercises the schema-mismatch path: main's CO ObjectChange carries
        the original field key 'alpha' while the branch schema uses 'branch_alpha'.
        The CO data from main is applied by the branching engine regardless of the
        column name divergence (branching replays at the data layer).
        """
        request = _make_request(self.user)

        with event_tracking(request):
            cot = CustomObjectType.objects.create(name='sync_rename_cot', slug='sync-rename-cot')
            CustomObjectTypeField.objects.create(
                custom_object_type=cot, name='alpha', label='Alpha', type='text',
            )
            cot.get_model().objects.create(alpha='original')

        branch = _provision_branch('Sync Rename Branch', 'iterative', self.user)
        branch_request = _make_request(self.user)

        # ── branch: rename alpha → branch_alpha; create CO ────────────────
        with activate_branch(branch), event_tracking(branch_request):
            field = CustomObjectTypeField.objects.get(custom_object_type=cot, name='alpha')
            field.snapshot()
            field.name = 'branch_alpha'
            field.label = 'Branch Alpha'
            field.save()
            BM = cot.get_model()
            branch_new = BM.objects.create(branch_alpha='new in branch')
        branch_new_pk = branch_new.pk

        # ── main: add a CO (no field rename) ──────────────────────────────
        with event_tracking(request):
            cot.get_model().objects.create(alpha='new in main')

        # ── sync ──────────────────────────────────────────────────────────
        branch.sync(user=self.user, commit=True)

        with activate_branch(branch):
            cot_b = CustomObjectType.objects.get(pk=cot.pk)
            field_b = CustomObjectTypeField.objects.get(custom_object_type=cot_b)
            # Branch field rename must be preserved (main did not rename).
            self.assertEqual(field_b.name, 'branch_alpha',
                             'Branch field rename must be preserved after sync')
            BM = cot_b.get_model()
            self.assertTrue(BM.objects.filter(pk=branch_new_pk).exists(),
                            'CO created in branch must survive sync')

    def test_concurrent_field_rename_sync_no_crash(self):
        """
        Field renamed to different names in both main and branch before sync.

        The same CustomObjectTypeField PK was modified in both schemas.
        _schema_alter_field detects the conflict (neither the original 'alpha'
        column nor the target 'main_alpha' column exists in the branch), looks up
        the live column name in the branch ('branch_alpha'), and renames it to
        'main_alpha' to converge the branch schema on main's post-sync state.

        Scenario
        --------
        1. Create COT + field 'alpha' in main.
        2. Provision branch.
        3. Branch renames 'alpha' → 'branch_alpha'.
        4. Main renames 'alpha' → 'main_alpha'.
        5. sync() applies main's rename ObjectChange to the branch.
           _schema_alter_field resolves the conflict: 'branch_alpha' → 'main_alpha'.

        Expected
        --------
        - sync() completes without raising a DB error.
        - Branch physical column is 'main_alpha' (converged to main's rename).
        - 'branch_alpha' and 'alpha' columns are absent from the branch table.
        """
        request = _make_request(self.user)

        with event_tracking(request):
            cot = CustomObjectType.objects.create(name='confl_sync_cot', slug='confl-sync-cot')
            CustomObjectTypeField.objects.create(
                custom_object_type=cot, name='alpha', label='Alpha', type='text',
            )

        branch = _provision_branch('Conflict Sync Branch', 'iterative', self.user)
        branch_request = _make_request(self.user)

        # ── branch: rename alpha → branch_alpha ───────────────────────────
        with activate_branch(branch), event_tracking(branch_request):
            f = CustomObjectTypeField.objects.get(custom_object_type=cot, name='alpha')
            f.snapshot()
            f.name = 'branch_alpha'
            f.label = 'Branch Alpha'
            f.save()

        # ── main: rename alpha → main_alpha ───────────────────────────────
        with event_tracking(request):
            f = CustomObjectTypeField.objects.get(custom_object_type=cot, name='alpha')
            f.name = 'main_alpha'
            f.label = 'Main Alpha'
            f.save()

        # ── sync — must not raise.  Let any failure propagate with its
        # original traceback rather than catching to ``self.fail`` (which
        # would flatten the stack).
        branch.sync(user=self.user, commit=True)

        branch.refresh_from_db()

        # Branch column should now be 'main_alpha' — the conflict was resolved by
        # renaming the branch's live 'branch_alpha' column to main's target name.
        branch_conn = connections[branch.connection_name]
        with branch_conn.cursor() as cursor:
            branch_cols = {
                col.name
                for col in branch_conn.introspection.get_table_description(
                    cursor, cot.get_database_table_name(),
                )
            }
        self.assertIn('main_alpha', branch_cols, 'Branch column must converge to main_alpha after sync')
        self.assertNotIn('branch_alpha', branch_cols, 'branch_alpha column must be gone after sync')
        self.assertNotIn('alpha', branch_cols, 'alpha column must be gone after sync')


# ── Concurrent-edit merge tests ───────────────────────────────────────────────

@unittest.skipUnless(HAS_BRANCHING, 'netbox-branching is not installed')
class BaseConcurrentEditMergeTests(BranchingTestBase):
    """
    Merge scenarios where both main and branch accumulate changes before merge().

    Subclasses must:
    - set ``MERGE_STRATEGY`` to an iterative or squash strategy string
    - also inherit from ``TransactionTestCase``
    """

    MERGE_STRATEGY = None

    def test_field_rename_in_branch_co_changes_merge(self):
        """
        Field renamed inside a branch; COs added/updated in branch; main adds a CO.
        Merge brings the branch rename and CO changes into main.

        Scenario
        --------
        1. Create COT + field 'alpha' + CO in main.
        2. Provision branch.
        3. In branch:  rename 'alpha' → 'beta'; update the existing CO; create a CO.
        4. In main:    add a CO (field name 'alpha', no rename).
        5. merge() → revert().

        Expected after merge
        --------------------
        - Field name in main is 'beta'.
        - Existing CO has the branch-updated value.
        - CO created in branch is present in main.

        Expected after revert
        ---------------------
        - Field name is 'alpha' again.
        - CO created in branch is gone.
        - Existing CO has its original value.
        """
        request = _make_request(self.user)

        with event_tracking(request):
            cot = CustomObjectType.objects.create(name='merge_rename_cot', slug='merge-rename-cot')
            CustomObjectTypeField.objects.create(
                custom_object_type=cot, name='alpha', label='Alpha', type='text',
            )
            Model = cot.get_model()
            co_existing = Model.objects.create(alpha='original')

        co_existing_pk = co_existing.pk
        branch = _provision_branch('Merge Rename Branch', self.MERGE_STRATEGY, self.user)
        branch_request = _make_request(self.user)

        # ── branch: rename; update CO; create CO ──────────────────────────
        with activate_branch(branch), event_tracking(branch_request):
            field = CustomObjectTypeField.objects.get(custom_object_type=cot, name='alpha')
            field.snapshot()
            field.name = 'beta'
            field.label = 'Beta'
            field.save()
            BM = cot.get_model()
            co = BM.objects.get(pk=co_existing_pk)
            co.snapshot()
            co.beta = 'updated in branch'
            co.save()
            branch_new = BM.objects.create(beta='new in branch')
        branch_new_pk = branch_new.pk

        # ── main: add a CO (no schema change) ─────────────────────────────
        with event_tracking(request):
            main_new = cot.get_model().objects.create(alpha='new in main')
        main_new_pk = main_new.pk

        # ── merge ─────────────────────────────────────────────────────────
        branch.merge(user=self.user, commit=True)
        branch.refresh_from_db()
        self.assertEqual(branch.status, BranchStatusChoices.MERGED)

        MergedModel = cot.get_model()
        field_main = CustomObjectTypeField.objects.get(custom_object_type=cot)
        self.assertEqual(field_main.name, 'beta', 'Field must be "beta" in main after merge')
        self.assertEqual(MergedModel.objects.get(pk=co_existing_pk).beta, 'updated in branch')
        self.assertTrue(MergedModel.objects.filter(pk=branch_new_pk).exists(),
                        'CO created in branch must be in main after merge')
        self.assertTrue(MergedModel.objects.filter(pk=main_new_pk).exists(),
                        'CO added in main must still be present after merge')

        # ── revert ────────────────────────────────────────────────────────
        branch.revert(user=self.user, commit=True)

        field_reverted = CustomObjectTypeField.objects.get(custom_object_type=cot)
        self.assertEqual(field_reverted.name, 'alpha', 'Field must be "alpha" after revert')
        RevertedModel = cot.get_model()
        self.assertEqual(RevertedModel.objects.get(pk=co_existing_pk).alpha, 'original')
        self.assertFalse(RevertedModel.objects.filter(pk=branch_new_pk).exists(),
                         'CO created in branch must be gone after revert')

    def test_co_values_modified_in_both_merge(self):
        """
        CO values modified in both main and branch before merge.
        Branch changes win because merge applies branch ObjectChanges to main.

        Scenario
        --------
        1. Create COT + field 'notes' + shared CO in main.
        2. Provision branch.
        3. Branch updates shared CO to 'modified in branch'; creates a CO.
        4. Main creates a separate CO.
        5. merge() → revert().

        Expected after merge: branch CO changes are in main, main CO preserved.
        Expected after revert: shared CO back to original, branch CO gone.
        """
        request = _make_request(self.user)

        with event_tracking(request):
            cot = CustomObjectType.objects.create(name='merge_co_cot', slug='merge-co-cot')
            CustomObjectTypeField.objects.create(
                custom_object_type=cot, name='notes', label='Notes', type='text',
            )
            Model = cot.get_model()
            co_shared = Model.objects.create(notes='original')

        co_shared_pk = co_shared.pk
        branch = _provision_branch('Merge CO Both', self.MERGE_STRATEGY, self.user)
        branch_request = _make_request(self.user)

        # ── branch ────────────────────────────────────────────────────────
        with activate_branch(branch), event_tracking(branch_request):
            BM = cot.get_model()
            co = BM.objects.get(pk=co_shared_pk)
            co.snapshot()
            co.notes = 'modified in branch'
            co.save()
            branch_new = BM.objects.create(notes='new in branch')
        branch_new_pk = branch_new.pk

        # ── main ──────────────────────────────────────────────────────────
        with event_tracking(request):
            main_new = cot.get_model().objects.create(notes='new in main')
        main_new_pk = main_new.pk

        # ── merge ─────────────────────────────────────────────────────────
        branch.merge(user=self.user, commit=True)
        branch.refresh_from_db()
        self.assertEqual(branch.status, BranchStatusChoices.MERGED)

        MM = cot.get_model()
        self.assertEqual(MM.objects.get(pk=co_shared_pk).notes, 'modified in branch')
        self.assertTrue(MM.objects.filter(pk=branch_new_pk).exists())
        self.assertTrue(MM.objects.filter(pk=main_new_pk).exists(),
                        'CO added to main must survive the merge')

        # ── revert ────────────────────────────────────────────────────────
        branch.revert(user=self.user, commit=True)

        RM = cot.get_model()
        self.assertEqual(RM.objects.get(pk=co_shared_pk).notes, 'original')
        self.assertFalse(RM.objects.filter(pk=branch_new_pk).exists())


@unittest.skipUnless(HAS_BRANCHING, 'netbox-branching is not installed')
class IterativeConcurrentEditMergeTestCase(BaseConcurrentEditMergeTests, TransactionTestCase):
    """Run BaseConcurrentEditMergeTests with the iterative merge strategy."""
    MERGE_STRATEGY = 'iterative'


@unittest.skipUnless(HAS_BRANCHING, 'netbox-branching is not installed')
class SquashConcurrentEditMergeTestCase(BaseConcurrentEditMergeTests, TransactionTestCase):
    """Run BaseConcurrentEditMergeTests with the squash merge strategy."""
    MERGE_STRATEGY = 'squash'


# ── Sequential multi-rename tests ─────────────────────────────────────────────

@unittest.skipUnless(HAS_BRANCHING, 'netbox-branching is not installed')
class SequentialRenameTestCase(BranchingTestBase, TransactionTestCase):
    """
    Tests for sequential field renames (A→B→C) in a branch with CO changes at
    each step, plus independent changes in main.

    Exercises the iterative ObjectChange replay order: the rename chain must be
    applied in the right sequence so that each CO update sees the correct column
    name at merge time.

    Run with both iterative and squash strategies to verify that squash correctly
    collapses the A→B→C chain to a single A→C alter.
    """

    MERGE_STRATEGY = 'iterative'

    def _run_sequential_rename_merge(self, cot_name, cot_slug):
        """
        Shared implementation for the sequential rename merge test.

        Branch: rename alpha→beta (update+create CO), rename beta→gamma (update+create CO).
        Main:   add a new independent field + CO (no rename of alpha).
        merge() then revert().
        """
        request = _make_request(self.user)

        with event_tracking(request):
            cot = CustomObjectType.objects.create(name=cot_name, slug=cot_slug)
            CustomObjectTypeField.objects.create(
                custom_object_type=cot, name='alpha', label='Alpha', type='text',
            )
            Model = cot.get_model()
            co_original = Model.objects.create(alpha='original value')

        co_original_pk = co_original.pk
        branch = _provision_branch(f'{cot_name} branch', self.MERGE_STRATEGY, self.user)
        branch_request = _make_request(self.user)

        # ── branch: alpha → beta; update CO; create CO ────────────────────
        with activate_branch(branch), event_tracking(branch_request):
            field = CustomObjectTypeField.objects.get(custom_object_type=cot, name='alpha')
            field.snapshot()
            field.name = 'beta'
            field.label = 'Beta'
            field.save()
            BM = cot.get_model()
            co = BM.objects.get(pk=co_original_pk)
            co.snapshot()
            co.beta = 'after rename to beta'
            co.save()
            co_at_beta = BM.objects.create(beta='created at beta')
        co_at_beta_pk = co_at_beta.pk

        # ── branch: beta → gamma; update CO; create CO ────────────────────
        with activate_branch(branch), event_tracking(branch_request):
            field = CustomObjectTypeField.objects.get(custom_object_type=cot, name='beta')
            field.snapshot()
            field.name = 'gamma'
            field.label = 'Gamma'
            field.save()
            BM = cot.get_model()
            co = BM.objects.get(pk=co_original_pk)
            co.snapshot()
            co.gamma = 'after rename to gamma'
            co.save()
            co_at_gamma = BM.objects.create(gamma='created at gamma')
        co_at_gamma_pk = co_at_gamma.pk

        # ── main: add a new independent field + CO ────────────────────────
        with event_tracking(request):
            CustomObjectTypeField.objects.create(
                custom_object_type=cot, name='extra', label='Extra', type='text',
            )
            co_main = cot.get_model().objects.create(alpha='main added', extra='extra val')
        co_main_pk = co_main.pk

        # ── merge ─────────────────────────────────────────────────────────
        branch.merge(user=self.user, commit=True)
        branch.refresh_from_db()
        self.assertEqual(branch.status, BranchStatusChoices.MERGED)

        field_names = {f.name for f in CustomObjectTypeField.objects.filter(custom_object_type=cot)}
        self.assertIn('gamma', field_names, 'Final field name must be "gamma" after merge')
        self.assertNotIn('alpha', field_names, '"alpha" must be absent after merge')
        self.assertNotIn('beta', field_names, '"beta" must be absent after merge')
        self.assertIn('extra', field_names, '"extra" field from main must be present after merge')

        MergedModel = cot.get_model()
        self.assertEqual(MergedModel.objects.get(pk=co_original_pk).gamma, 'after rename to gamma')
        self.assertTrue(MergedModel.objects.filter(pk=co_at_beta_pk).exists(),
                        'CO created at beta step must survive merge')
        self.assertTrue(MergedModel.objects.filter(pk=co_at_gamma_pk).exists(),
                        'CO created at gamma step must survive merge')
        self.assertTrue(MergedModel.objects.filter(pk=co_main_pk).exists(),
                        'CO added in main must survive merge')

        # ── revert ────────────────────────────────────────────────────────
        branch.revert(user=self.user, commit=True)

        field_names_r = {f.name for f in CustomObjectTypeField.objects.filter(custom_object_type=cot)}
        self.assertIn('alpha', field_names_r, '"alpha" must be restored after revert')
        self.assertNotIn('gamma', field_names_r, '"gamma" must be gone after revert')

        RevertedModel = cot.get_model()
        self.assertEqual(RevertedModel.objects.get(pk=co_original_pk).alpha, 'original value',
                         'Original CO value must be restored after revert')
        self.assertFalse(RevertedModel.objects.filter(pk=co_at_beta_pk).exists())
        self.assertFalse(RevertedModel.objects.filter(pk=co_at_gamma_pk).exists())

    def test_sequential_renames_alpha_beta_gamma_merge(self):
        """Field renamed A→B→C in branch with CO changes at each step; merge + revert."""
        self._run_sequential_rename_merge('seq_iter_cot', 'seq-iter-cot')

    def test_sequential_renames_both_sides_sync(self):
        """
        Branch renames A→B→C while main renames A→D.
        Both schemas independently rename the same field to different names.

        After sync(), main's rename (A→D) is applied on top of the branch's
        state.  Because 'alpha' no longer exists in the branch (it was renamed
        to 'gamma' via beta), _schema_alter_field detects the conflict, looks up
        the live column name in the branch ('gamma'), and renames it to 'delta'
        to converge the branch schema on main's post-sync state.

        Scenario
        --------
        1. Create COT + field 'alpha' + CO in main.
        2. Provision branch.
        3. Branch: alpha→beta (update CO), beta→gamma (update CO, add CO).
        4. Main: alpha→delta (update CO, add CO).
        5. sync(): apply main's changes to branch — column converges to 'delta'.
        """
        request = _make_request(self.user)

        with event_tracking(request):
            cot = CustomObjectType.objects.create(name='seq_sync_cot', slug='seq-sync-cot')
            CustomObjectTypeField.objects.create(
                custom_object_type=cot, name='alpha', label='Alpha', type='text',
            )
            co = cot.get_model().objects.create(alpha='original')

        co_pk = co.pk
        branch = _provision_branch('Seq Sync Branch', self.MERGE_STRATEGY, self.user)
        branch_request = _make_request(self.user)

        # ── branch: alpha → beta → gamma ──────────────────────────────────
        with activate_branch(branch), event_tracking(branch_request):
            f = CustomObjectTypeField.objects.get(custom_object_type=cot, name='alpha')
            f.snapshot()
            f.name = 'beta'
            f.label = 'Beta'
            f.save()
            BM = cot.get_model()
            co_b = BM.objects.get(pk=co_pk)
            co_b.snapshot()
            co_b.beta = 'at beta'
            co_b.save()

        with activate_branch(branch), event_tracking(branch_request):
            f = CustomObjectTypeField.objects.get(custom_object_type=cot, name='beta')
            f.snapshot()
            f.name = 'gamma'
            f.label = 'Gamma'
            f.save()
            BM = cot.get_model()
            co_g = BM.objects.get(pk=co_pk)
            co_g.snapshot()
            co_g.gamma = 'at gamma'
            co_g.save()
            BM.objects.create(gamma='new in branch')

        # ── main: alpha → delta ───────────────────────────────────────────
        with event_tracking(request):
            f = CustomObjectTypeField.objects.get(custom_object_type=cot, name='alpha')
            f.name = 'delta'
            f.label = 'Delta'
            f.save()
            MM = cot.get_model()
            co_m = MM.objects.get(pk=co_pk)
            co_m.snapshot()
            co_m.delta = 'updated in main'
            co_m.save()
            MM.objects.create(delta='main new')

        # ── sync — let any failure propagate with its original traceback ───
        branch.sync(user=self.user, commit=True)

        branch.refresh_from_db()

        # _schema_alter_field resolved the conflict by looking up the live column
        # ('gamma') in the branch and renaming it to main's target name ('delta').
        branch_conn = connections[branch.connection_name]
        with branch_conn.cursor() as cursor:
            branch_cols = {
                col.name
                for col in branch_conn.introspection.get_table_description(
                    cursor, cot.get_database_table_name(),
                )
            }
        self.assertIn('delta', branch_cols, 'Branch column must converge to delta after sync')
        self.assertNotIn('gamma', branch_cols, 'gamma column must be gone after sync')
        self.assertNotIn('beta', branch_cols, 'beta column must be gone after sync')
        self.assertNotIn('alpha', branch_cols, 'alpha column must be gone after sync')

    def test_sequential_renames_both_sides_merge(self):
        """
        Branch renames A→B→C; main renames A→D independently.
        merge() applies branch's rename chain to main.

        When merging the first branch rename (alpha→beta) into main, 'alpha' no
        longer exists in main (it was renamed to 'delta') and 'beta' doesn't exist
        either.  _schema_alter_field detects the conflict, looks up the live column
        in main ('delta'), and renames it to 'beta'.  The second branch rename
        (beta→gamma) then finds 'beta' in main and renames it normally to 'gamma'.

        Expected after merge: main's physical column is 'gamma'.
        """
        request = _make_request(self.user)

        with event_tracking(request):
            cot = CustomObjectType.objects.create(name='seq_merge_cot', slug='seq-merge-cot')
            CustomObjectTypeField.objects.create(
                custom_object_type=cot, name='alpha', label='Alpha', type='text',
            )
            co = cot.get_model().objects.create(alpha='original')

        co_pk = co.pk
        branch = _provision_branch('Seq Merge Conflict Branch', self.MERGE_STRATEGY, self.user)
        branch_request = _make_request(self.user)

        # ── branch: alpha → beta → gamma ──────────────────────────────────
        with activate_branch(branch), event_tracking(branch_request):
            f = CustomObjectTypeField.objects.get(custom_object_type=cot, name='alpha')
            f.snapshot()
            f.name = 'beta'
            f.label = 'Beta'
            f.save()
            BM = cot.get_model()
            co_b = BM.objects.get(pk=co_pk)
            co_b.snapshot()
            co_b.beta = 'at beta'
            co_b.save()

        with activate_branch(branch), event_tracking(branch_request):
            f = CustomObjectTypeField.objects.get(custom_object_type=cot, name='beta')
            f.snapshot()
            f.name = 'gamma'
            f.label = 'Gamma'
            f.save()
            BM = cot.get_model()
            co_g = BM.objects.get(pk=co_pk)
            co_g.snapshot()
            co_g.gamma = 'at gamma'
            co_g.save()
            BM.objects.create(gamma='new in branch')

        # ── main: alpha → delta ───────────────────────────────────────────
        with event_tracking(request):
            f = CustomObjectTypeField.objects.get(custom_object_type=cot, name='alpha')
            f.name = 'delta'
            f.label = 'Delta'
            f.save()

        # ── merge — let any failure propagate with its original traceback ──
        branch.merge(user=self.user, commit=True)

        branch.refresh_from_db()

        # _schema_alter_field resolved the conflict for the first branch rename
        # (alpha→beta): it found 'delta' (main's live column) and renamed it to
        # 'beta'.  The second rename (beta→gamma) then proceeded normally.
        # Main's final physical column should be 'gamma'.
        with main_conn.cursor() as cursor:
            main_cols = {
                col.name
                for col in main_conn.introspection.get_table_description(
                    cursor, cot.get_database_table_name(),
                )
            }
        self.assertIn('gamma', main_cols, 'Main column must be gamma after merge')
        self.assertNotIn('delta', main_cols, 'delta column must be gone after merge')
        self.assertNotIn('beta', main_cols, 'beta column must be gone after merge')
        self.assertNotIn('alpha', main_cols, 'alpha column must be gone after merge')


@unittest.skipUnless(HAS_BRANCHING, 'netbox-branching is not installed')
class SequentialRenameSquashTestCase(SequentialRenameTestCase, TransactionTestCase):
    """Run SequentialRenameTestCase with the squash merge strategy."""
    MERGE_STRATEGY = 'squash'

    def test_sequential_renames_alpha_beta_gamma_merge(self):
        self._run_sequential_rename_merge('seq_squash_cot', 'seq-squash-cot')
