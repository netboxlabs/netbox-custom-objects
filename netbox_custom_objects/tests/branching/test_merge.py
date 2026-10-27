"""
Merging branch changes beyond the strategy matrix: field attribute changes, tags and
journal entries, choice sets and search weight, and changes made in both main and the
branch before the merge.
"""

import unittest

from django.contrib.contenttypes.models import ContentType
from django.db import connection as main_conn

from extras.models import CustomFieldChoiceSet
from netbox_custom_objects.models import (
    CustomObjectType,
    CustomObjectTypeField,
)

from .base import (
    HAS_BRANCHING,
    BranchingTestBase,
    _make_request,
    _provision_branch,
    _TestBase,
)

if HAS_BRANCHING:
    from netbox_branching.choices import BranchStatusChoices
    from netbox_branching.utilities import activate_branch

    from netbox.context_managers import event_tracking


# ── Field attribute changes & COT update (iterative only) ─────────────────────


@unittest.skipUnless(HAS_BRANCHING, 'netbox-branching is not installed')
class FieldAttributeChangesTestCase(BranchingTestBase, _TestBase):
    """
    Application-layer field attribute changes that the existing tests don't
    cover individually: COT-level updates, field type change, primary swap,
    and required toggle.  Iterative only — these don't hit strategy-specific
    code paths.
    """

    def test_cot_metadata_update_merge(self):
        """COT name / verbose_name / description / version edited inside branch."""
        with event_tracking(self.request):
            cot = CustomObjectType.objects.create(
                name='meta_cot',
                slug='meta-cot',
                verbose_name='Original Name',
                description='original',
                version='1.0.0',
            )

        cot_pk = cot.pk
        branch = _provision_branch('Meta Branch', 'iterative', self.user)
        branch_request = _make_request(self.user)

        with activate_branch(branch), event_tracking(branch_request):
            branch_cot = CustomObjectType.objects.get(pk=cot_pk)
            branch_cot.snapshot()
            branch_cot.verbose_name = 'Updated Name'
            branch_cot.description = 'updated description'
            branch_cot.version = '1.1.0'
            branch_cot.save()

        # Main hasn't seen the change yet.
        cot.refresh_from_db()
        self.assertEqual(cot.verbose_name, 'Original Name')

        branch.merge(user=self.user, commit=True)
        branch.refresh_from_db()
        self.assertEqual(branch.status, BranchStatusChoices.MERGED)

        cot.refresh_from_db()
        self.assertEqual(cot.verbose_name, 'Updated Name')
        self.assertEqual(cot.description, 'updated description')
        self.assertEqual(cot.version, '1.1.0')

    def test_field_type_change_text_to_integer_merge(self):
        """text → integer field type change across merge.

        The CO has a value that's parseable as both; the column type
        change replaces ``VARCHAR`` with ``INTEGER`` via ``alter_field``.
        """
        with event_tracking(self.request):
            cot = CustomObjectType.objects.create(name='retype_cot', slug='retype-cot')
            field = CustomObjectTypeField.objects.create(
                custom_object_type=cot,
                name='value',
                label='Value',
                type='text',
            )
            Model = cot.get_model()
            # Use a value that parses as both text and integer so the
            # USING cast applied by alter_field can succeed.
            co = Model.objects.create(value='42')

        cot_pk, field_pk, co_pk = cot.pk, field.pk, co.pk
        branch = _provision_branch('Retype Branch', 'iterative', self.user)
        branch_request = _make_request(self.user)

        with activate_branch(branch), event_tracking(branch_request):
            f = CustomObjectTypeField.objects.get(pk=field_pk)
            f.snapshot()
            f.type = 'integer'
            f.save()

        branch.merge(user=self.user, commit=True)
        branch.refresh_from_db()
        self.assertEqual(branch.status, BranchStatusChoices.MERGED)

        field_main = CustomObjectTypeField.objects.get(pk=field_pk)
        self.assertEqual(field_main.type, 'integer')

        # PostgreSQL column type must be bigint (CO integer fields use BigIntegerField).
        cot_main = CustomObjectType.objects.get(pk=cot_pk)
        co_table = cot_main.get_database_table_name()
        with main_conn.cursor() as cursor:
            cursor.execute(
                'SELECT data_type FROM information_schema.columns WHERE table_name = %s AND column_name = %s',
                [co_table, 'value'],
            )
            data_type = cursor.fetchone()[0]
        self.assertEqual(data_type, 'bigint')

        # CO value survived the cast.
        co_main = cot_main.get_model().objects.get(pk=co_pk)
        self.assertEqual(co_main.value, 42)

    def test_primary_field_swap_merge(self):
        """Switch which field is ``primary``; __str__ must follow."""
        with event_tracking(self.request):
            cot = CustomObjectType.objects.create(name='primary_cot', slug='primary-cot')
            CustomObjectTypeField.objects.create(
                custom_object_type=cot,
                name='code',
                label='Code',
                type='text',
                primary=True,
            )
            CustomObjectTypeField.objects.create(
                custom_object_type=cot,
                name='title',
                label='Title',
                type='text',
                primary=False,
            )
            Model = cot.get_model()
            co = Model.objects.create(code='ABC', title='Widget')
            self.assertEqual(str(co), 'ABC')

        cot_pk = cot.pk
        co_pk = co.pk
        branch = _provision_branch('Primary Branch', 'iterative', self.user)
        branch_request = _make_request(self.user)

        with activate_branch(branch), event_tracking(branch_request):
            f_code = CustomObjectTypeField.objects.get(custom_object_type=cot, name='code')
            f_code.snapshot()
            f_code.primary = False
            f_code.save()
            f_title = CustomObjectTypeField.objects.get(custom_object_type=cot, name='title')
            f_title.snapshot()
            f_title.primary = True
            f_title.save()

        branch.merge(user=self.user, commit=True)
        branch.refresh_from_db()
        self.assertEqual(branch.status, BranchStatusChoices.MERGED)

        cot_main = CustomObjectType.objects.get(pk=cot_pk)
        co_main = cot_main.get_model().objects.get(pk=co_pk)
        # __str__ now follows the newly-primary 'title' field.
        self.assertEqual(str(co_main), 'Widget')

    def test_field_required_toggle_merge(self):
        """Toggle a field's required flag from False to True across merge.

        ``required`` is enforced at the application layer only (model
        ``blank``, DRF serializer ``required``) — every field constructor in
        ``field_types.py`` keeps ``null=True`` unconditionally, so the DB
        column stays nullable regardless of ``required``.  This test pins
        both halves of that contract: ``required=True`` does survive the
        merge as an ORM attribute (form/serializer validation will reject
        empty values), but the underlying column does NOT become NOT NULL.
        """
        with event_tracking(self.request):
            cot = CustomObjectType.objects.create(name='required_cot', slug='required-cot')
            field = CustomObjectTypeField.objects.create(
                custom_object_type=cot,
                name='note',
                label='Note',
                type='text',
                required=False,
            )

        cot_pk, field_pk = cot.pk, field.pk
        branch = _provision_branch('Required Branch', 'iterative', self.user)
        branch_request = _make_request(self.user)

        with activate_branch(branch), event_tracking(branch_request):
            f = CustomObjectTypeField.objects.get(pk=field_pk)
            f.snapshot()
            f.required = True
            f.save()

        branch.merge(user=self.user, commit=True)
        branch.refresh_from_db()
        self.assertEqual(branch.status, BranchStatusChoices.MERGED)

        self.assertTrue(
            CustomObjectTypeField.objects.get(pk=field_pk).required,
            'required=True ORM flag must survive the merge',
        )

        # DB column stays nullable — required is enforced at the form layer only.
        cot_main = CustomObjectType.objects.get(pk=cot_pk)
        co_table = cot_main.get_database_table_name()
        with main_conn.cursor() as cursor:
            cursor.execute(
                'SELECT is_nullable FROM information_schema.columns WHERE table_name = %s AND column_name = %s',
                [co_table, 'note'],
            )
            is_nullable = cursor.fetchone()[0]
        self.assertEqual(
            is_nullable,
            'YES',
            'required=True must NOT produce NOT NULL — this plugin enforces '
            'required at the form layer only (field_types.py hardcodes null=True).',
        )


# ── Tags + journal entries survive merge ──────────────────────────────────────


@unittest.skipUnless(HAS_BRANCHING, 'netbox-branching is not installed')
class TagsAndJournalTestCase(BranchingTestBase, _TestBase):
    """
    Tags use a separate code path in ``CustomObject.deserialize_object`` via
    the ``is_taggable`` branch.  Journal entries are NetBox infrastructure
    used by NetBoxModel subclasses.  Neither is exercised by the rest of the
    branching suite.
    """

    def test_co_with_tags_survives_merge(self):
        from extras.models import Tag

        with event_tracking(self.request):
            tag_a = Tag.objects.create(name='Branch Tag A', slug='branch-tag-a')
            tag_b = Tag.objects.create(name='Branch Tag B', slug='branch-tag-b')
            cot = CustomObjectType.objects.create(name='tagged_cot', slug='tagged-cot')
            CustomObjectTypeField.objects.create(
                custom_object_type=cot,
                name='label',
                label='Label',
                type='text',
            )

        cot_pk = cot.pk
        branch = _provision_branch('Tagged Branch', 'iterative', self.user)
        branch_request = _make_request(self.user)

        with activate_branch(branch), event_tracking(branch_request):
            Model = cot.get_model()
            co = Model.objects.create(label='tagged')
            co.tags.set([tag_a, tag_b])

        co_pk = co.pk

        branch.merge(user=self.user, commit=True)
        branch.refresh_from_db()
        self.assertEqual(branch.status, BranchStatusChoices.MERGED)

        Model = CustomObjectType.objects.get(pk=cot_pk).get_model()
        co_main = Model.objects.get(pk=co_pk)
        self.assertEqual(
            set(co_main.tags.values_list('name', flat=True)),
            {'Branch Tag A', 'Branch Tag B'},
            'Tags assigned in branch must appear on the merged CO',
        )

    def test_co_with_journal_entry_survives_merge(self):
        """Journal entries created against a CO in branch arrive in main on merge."""
        from extras.models import JournalEntry

        with event_tracking(self.request):
            cot = CustomObjectType.objects.create(name='journal_cot', slug='journal-cot')
            CustomObjectTypeField.objects.create(
                custom_object_type=cot,
                name='label',
                label='Label',
                type='text',
            )

        cot_pk = cot.pk
        branch = _provision_branch('Journal Branch', 'iterative', self.user)
        branch_request = _make_request(self.user)

        with activate_branch(branch), event_tracking(branch_request):
            Model = cot.get_model()
            co = Model.objects.create(label='journaled')
            JournalEntry.objects.create(
                assigned_object=co,
                created_by=self.user,
                kind='info',
                comments='Note added in branch',
            )

        co_pk = co.pk

        branch.merge(user=self.user, commit=True)
        branch.refresh_from_db()
        self.assertEqual(branch.status, BranchStatusChoices.MERGED)

        Model = CustomObjectType.objects.get(pk=cot_pk).get_model()
        co_ct = ContentType.objects.get_for_model(Model)
        entries = JournalEntry.objects.filter(
            assigned_object_type=co_ct,
            assigned_object_id=co_pk,
        )
        self.assertEqual(
            list(entries.values_list('comments', flat=True)),
            ['Note added in branch'],
        )


# ── ChoiceSet lifecycle, search_weight, sync-then-merge ───────────────────────


@unittest.skipUnless(HAS_BRANCHING, 'netbox-branching is not installed')
class ChoiceSetSearchLifecycleTestCase(BranchingTestBase, _TestBase):
    """Misc lifecycle gaps: ChoiceSet mutation, search_weight changes,
    sync→edit→merge chains."""

    def test_choice_set_choices_mutated_in_branch_merge(self):
        """Add a new choice to a ChoiceSet inside a branch; merge to main."""
        with event_tracking(self.request):
            cs = CustomFieldChoiceSet.objects.create(
                name='Mutable Choices',
                extra_choices=[['x', 'X'], ['y', 'Y']],
            )
            cot = CustomObjectType.objects.create(name='cs_cot', slug='cs-cot')
            CustomObjectTypeField.objects.create(
                custom_object_type=cot,
                name='c',
                label='Choice',
                type='select',
                choice_set=cs,
            )

        cs_pk = cs.pk
        branch = _provision_branch('CS Branch', 'iterative', self.user)
        branch_request = _make_request(self.user)

        with activate_branch(branch), event_tracking(branch_request):
            cs_branch = CustomFieldChoiceSet.objects.get(pk=cs_pk)
            cs_branch.snapshot()
            cs_branch.extra_choices = [['x', 'X'], ['y', 'Y'], ['z', 'Z']]
            cs_branch.save()
            Model = cot.get_model()
            Model.objects.create(c='z')  # uses the new choice

        branch.merge(user=self.user, commit=True)
        branch.refresh_from_db()
        self.assertEqual(branch.status, BranchStatusChoices.MERGED)

        cs_main = CustomFieldChoiceSet.objects.get(pk=cs_pk)
        keys = {pair[0] for pair in cs_main.extra_choices}
        self.assertIn('z', keys, 'New choice must be present in main after merge')

    def test_field_search_weight_change_merge(self):
        """Changing ``search_weight`` is a non-DDL field update.  It must
        survive merge as an ORM-level change; reindexing happens via
        ``ReindexCustomObjectTypeJob`` which runs out-of-band."""
        with event_tracking(self.request):
            cot = CustomObjectType.objects.create(name='sw_cot', slug='sw-cot')
            field = CustomObjectTypeField.objects.create(
                custom_object_type=cot,
                name='label',
                label='Label',
                type='text',
                search_weight=10,
            )

        field_pk = field.pk
        branch = _provision_branch('SW Branch', 'iterative', self.user)
        branch_request = _make_request(self.user)

        with activate_branch(branch), event_tracking(branch_request):
            f = CustomObjectTypeField.objects.get(pk=field_pk)
            f.snapshot()
            f.search_weight = 100
            f.save()

        branch.merge(user=self.user, commit=True)
        branch.refresh_from_db()
        self.assertEqual(branch.status, BranchStatusChoices.MERGED)

        self.assertEqual(
            CustomObjectTypeField.objects.get(pk=field_pk).search_weight,
            100,
        )

    def test_sync_then_branch_edit_then_merge_lifecycle(self):
        """
        Full lifecycle: main creates state → sync pulls it into branch →
        branch edits a CO → merge brings the edit back.  No standalone
        test covers state persistence across sync and merge in one branch.
        """
        branch = _provision_branch('Lifecycle Branch', 'iterative', self.user)

        # Main creates a COT + CO *after* the branch was provisioned.
        with event_tracking(self.request):
            cot = CustomObjectType.objects.create(name='lifecycle_cot', slug='lifecycle-cot')
            CustomObjectTypeField.objects.create(
                custom_object_type=cot,
                name='label',
                label='Label',
                type='text',
            )
            Model = cot.get_model()
            co = Model.objects.create(label='initial')

        co_pk = co.pk
        cot_pk = cot.pk

        # Sync the branch so it sees the COT+CO.
        branch.sync(user=self.user, commit=True)
        branch.refresh_from_db()

        # Branch edits the CO.
        branch_request = _make_request(self.user)
        with activate_branch(branch), event_tracking(branch_request):
            BranchModel = CustomObjectType.objects.get(pk=cot_pk).get_model()
            branch_co = BranchModel.objects.get(pk=co_pk)
            branch_co.snapshot()
            branch_co.label = 'edited after sync'
            branch_co.save()

        # Main hasn't seen the edit yet.
        co.refresh_from_db()
        self.assertEqual(co.label, 'initial')

        # Merge.
        branch.merge(user=self.user, commit=True)
        branch.refresh_from_db()
        self.assertEqual(branch.status, BranchStatusChoices.MERGED)

        co.refresh_from_db()
        self.assertEqual(
            co.label,
            'edited after sync',
            'Edit made after sync must propagate to main on merge',
        )


# ── Concurrent-edit merge tests ───────────────────────────────────────────────


@unittest.skipUnless(HAS_BRANCHING, 'netbox-branching is not installed')
class BaseConcurrentEditMergeTestCase(BranchingTestBase):
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
                custom_object_type=cot,
                name='alpha',
                label='Alpha',
                type='text',
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
        self.assertTrue(
            MergedModel.objects.filter(pk=branch_new_pk).exists(), 'CO created in branch must be in main after merge'
        )
        self.assertTrue(
            MergedModel.objects.filter(pk=main_new_pk).exists(), 'CO added in main must still be present after merge'
        )

        # ── revert ────────────────────────────────────────────────────────
        branch.revert(user=self.user, commit=True)

        field_reverted = CustomObjectTypeField.objects.get(custom_object_type=cot)
        self.assertEqual(field_reverted.name, 'alpha', 'Field must be "alpha" after revert')
        RevertedModel = cot.get_model()
        self.assertEqual(RevertedModel.objects.get(pk=co_existing_pk).alpha, 'original')
        self.assertFalse(
            RevertedModel.objects.filter(pk=branch_new_pk).exists(), 'CO created in branch must be gone after revert'
        )

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
                custom_object_type=cot,
                name='notes',
                label='Notes',
                type='text',
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
        self.assertTrue(MM.objects.filter(pk=main_new_pk).exists(), 'CO added to main must survive the merge')

        # ── revert ────────────────────────────────────────────────────────
        branch.revert(user=self.user, commit=True)

        RM = cot.get_model()
        self.assertEqual(RM.objects.get(pk=co_shared_pk).notes, 'original')
        self.assertFalse(RM.objects.filter(pk=branch_new_pk).exists())


@unittest.skipUnless(HAS_BRANCHING, 'netbox-branching is not installed')
class IterativeConcurrentEditMergeTestCase(BaseConcurrentEditMergeTestCase, _TestBase):
    """Run BaseConcurrentEditMergeTestCase with the iterative merge strategy."""

    MERGE_STRATEGY = 'iterative'


@unittest.skipUnless(HAS_BRANCHING, 'netbox-branching is not installed')
class SquashConcurrentEditMergeTestCase(BaseConcurrentEditMergeTestCase, _TestBase):
    """Run BaseConcurrentEditMergeTestCase with the squash merge strategy."""

    MERGE_STRATEGY = 'squash'
