"""
Syncing changes made in main into a branch, including changes made on both sides.
"""

import unittest

from django.db import connections

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
    from netbox_branching.utilities import activate_branch

    from netbox.context_managers import event_tracking


# ── Sync test ─────────────────────────────────────────────────────────────────


@unittest.skipUnless(HAS_BRANCHING, 'netbox-branching is not installed')
class BranchSyncTestCase(BranchingTestBase, _TestBase):
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
class ConcurrentEditSyncTestCase(BranchingTestBase, _TestBase):
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
                custom_object_type=cot,
                name='notes',
                label='Notes',
                type='text',
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
            self.assertTrue(
                SM.objects.filter(pk=main_new_pk).exists(), 'CO created in main must appear in branch after sync'
            )
            # CO created in branch was not deleted by sync.
            self.assertTrue(
                SM.objects.filter(pk=branch_new_pk).exists(), 'CO created in branch must still exist after sync'
            )

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
                custom_object_type=cot,
                name='alpha',
                label='Alpha',
                type='text',
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
            self.assertEqual(field_b.name, 'branch_alpha', 'Branch field rename must be preserved after sync')
            BM = cot_b.get_model()
            self.assertTrue(BM.objects.filter(pk=branch_new_pk).exists(), 'CO created in branch must survive sync')

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
                custom_object_type=cot,
                name='alpha',
                label='Alpha',
                type='text',
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
                    cursor,
                    cot.get_database_table_name(),
                )
            }
        self.assertIn('main_alpha', branch_cols, 'Branch column must converge to main_alpha after sync')
        self.assertNotIn('branch_alpha', branch_cols, 'branch_alpha column must be gone after sync')
        self.assertNotIn('alpha', branch_cols, 'alpha column must be gone after sync')
