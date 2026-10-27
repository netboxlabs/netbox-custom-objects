"""
Sequential field renames in main and a branch, through sync and merge.
"""

import unittest

from django.db import connection as main_conn
from django.db import connections

from netbox_custom_objects.models import (
    CustomObjectType,
    CustomObjectTypeField,
)

from .base import (
    HAS_BRANCHING,
    BranchingTestBase,
    _close_branch_connections,
    _make_request,
    _provision_branch,
    _TestBase,
)

if HAS_BRANCHING:
    from netbox_branching.choices import BranchStatusChoices
    from netbox_branching.utilities import activate_branch

    from netbox.context_managers import event_tracking


# ── Sequential multi-rename tests ─────────────────────────────────────────────


@unittest.skipUnless(HAS_BRANCHING, 'netbox-branching is not installed')
class SequentialRenameTestCase(BranchingTestBase, _TestBase):
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
                custom_object_type=cot,
                name='alpha',
                label='Alpha',
                type='text',
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
                custom_object_type=cot,
                name='extra',
                label='Extra',
                type='text',
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
        self.assertTrue(
            MergedModel.objects.filter(pk=co_at_beta_pk).exists(), 'CO created at beta step must survive merge'
        )
        self.assertTrue(
            MergedModel.objects.filter(pk=co_at_gamma_pk).exists(), 'CO created at gamma step must survive merge'
        )
        self.assertTrue(MergedModel.objects.filter(pk=co_main_pk).exists(), 'CO added in main must survive merge')

        # ── revert ────────────────────────────────────────────────────────
        branch.revert(user=self.user, commit=True)

        field_names_r = {f.name for f in CustomObjectTypeField.objects.filter(custom_object_type=cot)}
        self.assertIn('alpha', field_names_r, '"alpha" must be restored after revert')
        self.assertNotIn('gamma', field_names_r, '"gamma" must be gone after revert')

        RevertedModel = cot.get_model()
        self.assertEqual(
            RevertedModel.objects.get(pk=co_original_pk).alpha,
            'original value',
            'Original CO value must be restored after revert',
        )
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
                custom_object_type=cot,
                name='alpha',
                label='Alpha',
                type='text',
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

        # Close the idle branch connection before sync so the DDL inside
        # sync() (ALTER TABLE RENAME COLUMN) can acquire ACCESS EXCLUSIVE
        # without being blocked by the CONN_MAX_AGE-alive idle connection
        # left open by the activate_branch blocks above.
        _close_branch_connections()
        branch.sync(user=self.user, commit=True)

        branch.refresh_from_db()

        # _schema_alter_field resolved the conflict by looking up the live column
        # ('gamma') in the branch and renaming it to main's target name ('delta').
        branch_conn = connections[branch.connection_name]
        with branch_conn.cursor() as cursor:
            branch_cols = {
                col.name
                for col in branch_conn.introspection.get_table_description(
                    cursor,
                    cot.get_database_table_name(),
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
                custom_object_type=cot,
                name='alpha',
                label='Alpha',
                type='text',
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
                    cursor,
                    cot.get_database_table_name(),
                )
            }
        self.assertIn('gamma', main_cols, 'Main column must be gamma after merge')
        self.assertNotIn('delta', main_cols, 'delta column must be gone after merge')
        self.assertNotIn('beta', main_cols, 'beta column must be gone after merge')
        self.assertNotIn('alpha', main_cols, 'alpha column must be gone after merge')

    # ── url field: title column rename + replay/revert ─────────────────────

    def test_url_field_rename_replays_title_and_reverts(self):
        """
        Rename a ``url``-type field inside a branch, updating both the URL
        and title values under the new name.  After merge, both backing
        columns (``<name>``, ``<name>_title``) must have been renamed and
        both values must be present under the new name.  After revert, both
        must be restored under the old name.

        Regression test for the naive ``alter_field()`` call on the title
        column (previously assumed the old title column always existed,
        unlike the primary column's conflict-aware ``_schema_alter_field``
        path) and for the title key's audit-data rewrite.
        """
        request = _make_request(self.user)

        with event_tracking(request):
            cot = CustomObjectType.objects.create(name='url_rename_cot', slug='url-rename-cot')
            CustomObjectTypeField.objects.create(
                custom_object_type=cot,
                name='site_link',
                label='Site Link',
                type='url',
            )
            co = cot.get_model().objects.create(
                site_link='https://old.example.com',
                site_link_title='Old Title',
            )

        co_pk = co.pk
        branch = _provision_branch('URL Rename Branch', self.MERGE_STRATEGY, self.user)
        branch_request = _make_request(self.user)

        with activate_branch(branch), event_tracking(branch_request):
            field = CustomObjectTypeField.objects.get(custom_object_type=cot, name='site_link')
            field.snapshot()
            field.name = 'external_link'
            field.label = 'External Link'
            field.save()
            BM = cot.get_model()
            b_co = BM.objects.get(pk=co_pk)
            b_co.snapshot()
            b_co.external_link = 'https://new.example.com'
            b_co.external_link_title = 'New Title'
            b_co.save()

        branch.merge(user=self.user, commit=True)
        branch.refresh_from_db()
        self.assertEqual(branch.status, BranchStatusChoices.MERGED)

        with main_conn.cursor() as cursor:
            main_cols = {
                col.name
                for col in main_conn.introspection.get_table_description(
                    cursor,
                    cot.get_database_table_name(),
                )
            }
        self.assertIn('external_link', main_cols)
        self.assertIn('external_link_title', main_cols)
        self.assertNotIn('site_link', main_cols)
        self.assertNotIn('site_link_title', main_cols)

        MergedModel = cot.get_model()
        co_main = MergedModel.objects.get(pk=co_pk)
        self.assertEqual(co_main.external_link, 'https://new.example.com')
        self.assertEqual(co_main.external_link_title, 'New Title')

        branch.revert(user=self.user, commit=True)

        with main_conn.cursor() as cursor:
            main_cols_r = {
                col.name
                for col in main_conn.introspection.get_table_description(
                    cursor,
                    cot.get_database_table_name(),
                )
            }
        self.assertIn('site_link', main_cols_r)
        self.assertIn('site_link_title', main_cols_r)
        self.assertNotIn('external_link', main_cols_r)
        self.assertNotIn('external_link_title', main_cols_r)

        RevertedModel = cot.get_model()
        co_reverted = RevertedModel.objects.get(pk=co_pk)
        self.assertEqual(co_reverted.site_link, 'https://old.example.com')
        self.assertEqual(co_reverted.site_link_title, 'Old Title')

    def test_url_field_title_column_rename_conflict_merge(self):
        """
        Same independent-rename conflict as ``test_sequential_renames_both_sides_merge``
        (branch renames A→B→C while main independently renames A→D), but for a
        ``url``-type field, asserting the *title* column resolves the conflict
        the same way the primary column does.

        Regression test: the naive ``alter_field()`` call for the title column
        assumed the old title column still existed; when it doesn't (because
        main independently renamed the field first), the title rename must
        resolve via the live field record — exactly like
        ``_schema_alter_field`` already does for the primary column — instead
        of crashing or silently leaving the title column stale.
        """
        request = _make_request(self.user)

        with event_tracking(request):
            cot = CustomObjectType.objects.create(name='url_conflict_cot', slug='url-conflict-cot')
            CustomObjectTypeField.objects.create(
                custom_object_type=cot,
                name='alpha',
                label='Alpha',
                type='url',
            )
            co = cot.get_model().objects.create(alpha='https://a.example.com', alpha_title='A')

        co_pk = co.pk
        branch = _provision_branch('URL Conflict Branch', self.MERGE_STRATEGY, self.user)
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
            co_b.beta = 'https://b.example.com'
            co_b.beta_title = 'B'
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
            co_g.gamma = 'https://g.example.com'
            co_g.gamma_title = 'G'
            co_g.save()

        # ── main: alpha → delta (independent rename) ──────────────────────
        with event_tracking(request):
            f = CustomObjectTypeField.objects.get(custom_object_type=cot, name='alpha')
            f.name = 'delta'
            f.label = 'Delta'
            f.save()

        # ── merge — let any failure propagate with its original traceback ──
        branch.merge(user=self.user, commit=True)
        branch.refresh_from_db()

        with main_conn.cursor() as cursor:
            main_cols = {
                col.name
                for col in main_conn.introspection.get_table_description(
                    cursor,
                    cot.get_database_table_name(),
                )
            }
        self.assertIn('gamma', main_cols, 'Main URL column must be gamma after merge')
        self.assertIn(
            'gamma_title',
            main_cols,
            'Main title column must converge to gamma_title, not be left as delta_title/stale',
        )
        for stale in ('alpha', 'beta', 'delta', 'alpha_title', 'beta_title', 'delta_title'):
            self.assertNotIn(stale, main_cols, f'{stale!r} column must be gone after merge')

        MergedModel = cot.get_model()
        co_main = MergedModel.objects.get(pk=co_pk)
        self.assertEqual(co_main.gamma, 'https://g.example.com')
        self.assertEqual(co_main.gamma_title, 'G')


@unittest.skipUnless(HAS_BRANCHING, 'netbox-branching is not installed')
class SequentialRenameSquashTestCase(SequentialRenameTestCase, _TestBase):
    """Run SequentialRenameTestCase with the squash merge strategy."""

    MERGE_STRATEGY = 'squash'

    def test_sequential_renames_alpha_beta_gamma_merge(self):
        self._run_sequential_rename_merge('seq_squash_cot', 'seq-squash-cot')
