"""
Healing branch schemas after a plugin upgrade (``mixin_migration.heal_branch``).
"""

import unittest
from unittest import mock

from django.db import connection as main_conn
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
    from netbox_branching.choices import BranchStatusChoices
    from netbox_branching.models import Branch

    from netbox.context_managers import event_tracking


# ── Branch upgrade heal (mixin_migration.heal_branch) ──────────────────────────


@unittest.skipUnless(HAS_BRANCHING, 'netbox-branching is not installed')
class BranchUpgradeHealTestCase(BranchingTestBase, _TestBase):
    """
    Regression tests for upgrading a branch that already existed before a
    plugin release added a new base column (here, URLFieldType's
    ``<name>_title`` column).

    A schema change like this ships with no actual Django migration file
    (URLFieldType.get_model_field() just changes what columns a dynamically
    generated model has), so there's nothing for netbox-branching's own
    ``MigrationExecutor`` to detect as "pending" against a branch --
    ``Branch.migrate()`` never runs for it, and neither does its
    ``post_migrate`` signal.  The reliable trigger is ``heal_all_branches()``,
    called unconditionally from the main upgrade path (the post_migrate
    signal handler, and ``manage.py upgrade_custom_objects``) -- see
    ``mixin_migration.heal_branch()``'s docstring.
    """

    def _setup_branch_with_dropped_title_column(self, cot_name, cot_slug, field_name):
        """Shared fixture: a COT+url field in main, a branch with a full schema
        copy, then the title column dropped from *only* the branch's schema to
        simulate a branch provisioned before url fields had a title column.  Returns
        (cot, branch, table_name, branch_conn, _columns) where _columns(conn)
        introspects the live column set.
        """
        request = _make_request(self.user)
        with event_tracking(request):
            cot = CustomObjectType.objects.create(name=cot_name, slug=cot_slug)
            CustomObjectTypeField.objects.create(
                custom_object_type=cot,
                name=field_name,
                label=field_name.title(),
                type='url',
            )

        branch = _provision_branch(f'{cot_name} branch', None, self.user)
        table_name = cot.get_database_table_name()
        branch_conn = connections[branch.connection_name]

        with branch_conn.cursor() as cursor:
            cursor.execute(f'ALTER TABLE "{table_name}" DROP COLUMN "{field_name}_title"')

        def _columns(conn):
            with conn.cursor() as cursor:
                return {col.name for col in conn.introspection.get_table_description(cursor, table_name)}

        self.assertNotIn(
            f'{field_name}_title',
            _columns(branch_conn),
            'Precondition: title column must be absent from the branch schema',
        )
        self.assertIn(
            f'{field_name}_title',
            _columns(main_conn),
            'Precondition: title column must still be present in main',
        )
        return cot, branch, table_name, branch_conn, _columns

    def test_heal_branch_adds_missing_title_column_to_branch_schema_only(self):
        """
        Lower-level unit check of heal_branch() itself, called directly.  See
        test_upgrade_custom_objects_command_heals_existing_branch below for
        the end-to-end trigger this plugin actually relies on in production.
        """
        from netbox_custom_objects.mixin_migration import heal_branch

        _cot, branch, _table_name, branch_conn, _columns = self._setup_branch_with_dropped_title_column(
            'heal_cot', 'heal-cot', 'site_link'
        )

        result = heal_branch(branch)

        self.assertIn(
            'site_link_title',
            _columns(branch_conn),
            'heal_branch() must add the missing title column to the branch schema',
        )
        self.assertIn(
            'site_link_title',
            _columns(main_conn),
            "heal_branch() must not have touched main's schema",
        )
        self.assertEqual(result['healed'], 1)

    def test_upgrade_custom_objects_command_heals_existing_branch(self):
        """
        End-to-end regression test for the reviewer's concern that the only
        prior coverage called heal_branch() directly, bypassing the actual
        trigger a real upgrade uses.  Drives the management command instead
        -- the same entry point an administrator runs after upgrading the
        plugin, and the same code path the post_migrate signal handler uses
        internally (heal_all_branches()) -- and never calls heal_branch()
        directly.
        """
        from django.core.management import call_command

        _cot, _branch, _table_name, branch_conn, _columns = self._setup_branch_with_dropped_title_column(
            'heal_cmd_cot', 'heal-cmd-cot', 'site_link'
        )

        call_command('upgrade_custom_objects', verbosity=0)

        self.assertIn(
            'site_link_title',
            _columns(branch_conn),
            "'manage.py upgrade_custom_objects' must heal the branch's schema too, not just main's",
        )
        self.assertIn(
            'site_link_title',
            _columns(main_conn),
            "the command must not have broken main's already-healthy schema",
        )

    def test_heal_all_branches_skips_branch_with_missing_schema(self):
        """
        A branch's schema can be gone even when its status doesn't obviously say
        so -- e.g. provisioning failed after the schema was created, or it was
        since dropped. Introspecting through that branch's own
        connection wouldn't raise: its search path falls through to main, so it
        would silently report (and risk "healing") main's table instead. Confirm
        the branch is skipped instead -- no exception, main untouched, no COT
        reported healed for it.
        """
        from netbox_custom_objects.mixin_migration import heal_all_branches

        _cot, branch, _table_name, branch_conn, _columns = self._setup_branch_with_dropped_title_column(
            'heal_missing', 'heal-missing', 'site_link'
        )
        branch_conn.close()
        with main_conn.cursor() as cursor:
            cursor.execute(f'DROP SCHEMA IF EXISTS "{branch.schema_name}" CASCADE')
        # Simulates provisioning having failed (or the schema having been dropped)
        # without going through the normal archive/deprovision flow.
        Branch.objects.filter(pk=branch.pk).update(status=BranchStatusChoices.FAILED)

        result = heal_all_branches(verbosity=0)

        self.assertEqual(result['total'], 1)
        self.assertEqual(result['healed'], 0)
        self.assertGreaterEqual(result['warnings'], 1)
        self.assertIn(
            'site_link_title',
            _columns(main_conn),
            "main's own schema must be unaffected by a branch with no schema",
        )

    def test_heal_all_branches_skips_branch_with_pending_migrations(self):
        """
        A branch with pending migrations has an ORM that may not match its
        actual (outdated) schema yet -- heal_cot() could misdiagnose drift
        against columns the branch doesn't have for unrelated reasons. Confirm
        such a branch is skipped rather than healed; it's picked up later by
        the branch's own post_migrate hook once it migrates.
        """
        from netbox_custom_objects.mixin_migration import heal_all_branches

        _cot, _branch, _table_name, branch_conn, _columns = self._setup_branch_with_dropped_title_column(
            'heal_pending', 'heal-pending', 'site_link'
        )

        with mock.patch(
            'netbox_branching.models.Branch.pending_migrations',
            new_callable=mock.PropertyMock,
            return_value=[('dcim', '9999_fake_pending_migration')],
        ):
            result = heal_all_branches(verbosity=0)

        self.assertEqual(result['healed'], 0)
        self.assertNotIn(
            'site_link_title',
            _columns(branch_conn),
            'a branch with pending migrations must not be healed yet',
        )

    def test_heal_all_branches_continues_after_one_branch_fails(self):
        """
        One branch failing unexpectedly during heal_branch() must not abort the
        sweep for every other branch.
        """
        from netbox_custom_objects import mixin_migration
        from netbox_custom_objects.mixin_migration import heal_all_branches

        cot, branch1, _table_name, branch1_conn, _columns = self._setup_branch_with_dropped_title_column(
            'heal_multi_a', 'heal-multi-a', 'site_link'
        )
        branch2 = _provision_branch('heal_multi_b branch', None, self.user)
        table_name2 = cot.get_database_table_name()
        branch2_conn = connections[branch2.connection_name]
        with branch2_conn.cursor() as cursor:
            cursor.execute(f'ALTER TABLE "{table_name2}" DROP COLUMN "site_link_title"')
        self.assertNotIn('site_link_title', _columns(branch2_conn))

        original_heal_branch = mixin_migration.heal_branch

        def _flaky_heal_branch(branch, **kwargs):
            if branch.pk == branch1.pk:
                raise RuntimeError('simulated failure healing branch1')
            return original_heal_branch(branch, **kwargs)

        with mock.patch('netbox_custom_objects.mixin_migration.heal_branch', side_effect=_flaky_heal_branch):
            result = heal_all_branches(verbosity=0)

        self.assertNotIn(
            'site_link_title',
            _columns(branch1_conn),
            'branch1 was never healed (its heal_branch() call raised)',
        )
        self.assertIn(
            'site_link_title',
            _columns(branch2_conn),
            "branch2 must still be healed despite branch1's failure",
        )
        self.assertGreaterEqual(result['warnings'], 1)

    def test_heal_all_branches_closes_branch_connections(self):
        """
        pending_migrations (via MigrationExecutor) and heal_branch() both open a
        connection on branch.connection_name without closing it -- left open, these
        accumulate across every branch in the sweep, the same leak netbox_branching
        fixed in its own per-branch migration sweep. Confirm the
        connection is closed afterward, for both a healed branch and one skipped for
        pending migrations.
        """
        from netbox_custom_objects.mixin_migration import heal_all_branches

        _cot, _branch, _table_name, branch_conn, _columns = self._setup_branch_with_dropped_title_column(
            'heal_close', 'heal-close', 'site_link'
        )
        branch2 = _provision_branch('heal_close_pending branch', None, self.user)
        branch2_conn = connections[branch2.connection_name]

        # A plain PropertyMock has no access to the instance being accessed, so it can't
        # distinguish branch from branch2 -- use a real property bound to the class instead.
        def _pending_migrations(self):
            if self.pk == branch2.pk:
                return [('dcim', '9999_fake_pending_migration')]
            return []

        with mock.patch.object(Branch, 'pending_migrations', property(_pending_migrations)):
            heal_all_branches(verbosity=0)

        self.assertIsNone(branch_conn.connection, "healed branch's connection must be closed")
        self.assertIsNone(branch2_conn.connection, "skipped branch's connection must be closed too")
