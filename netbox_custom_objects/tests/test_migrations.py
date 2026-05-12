"""
Migration regression tests.

Each migration with complex data or schema operations gets its own section here.
The shared infrastructure (scratch-table base case, FK helpers) is defined once
and reused across migration-specific groups.

Adding tests for a new migration
---------------------------------
1. Load the migration with ``_load_migration('NNNN_migration_name')``.
2. Subclass ``_MigrationTestCase`` (and optionally a thin migration-specific
   intermediate) to get scratch-table creation and teardown for free.
3. Add a clearly-delimited section below.
"""

import importlib

from django.db import connection
from django.test import TransactionTestCase

from .base import TransactionCleanupMixin


def _load_migration(name):
    """Import a migration module by its migration name (handles digit-prefixed names)."""
    return importlib.import_module(f'netbox_custom_objects.migrations.{name}')


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_REF_TABLE = 'django_content_type'  # always present; provides a real FK target


class _FakeSchemaEditor:
    """Minimal stand-in for the schema_editor argument in RunPython functions."""
    connection = connection


def _get_fk_constraints(table_name):
    """Return {constraint_name: is_deferrable} for all FK constraints on *table_name*."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT tc.constraint_name, tc.is_deferrable
            FROM information_schema.table_constraints AS tc
            WHERE tc.constraint_type = 'FOREIGN KEY'
              AND tc.table_name = %s
              AND tc.table_schema = current_schema()
            """,
            [table_name],
        )
        return {row[0]: row[1] for row in cursor.fetchall()}


def _add_deferrable_fk(table_name, constraint_name, column_name, ref_table=_REF_TABLE):
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            ALTER TABLE "{table_name}"
            ADD CONSTRAINT "{constraint_name}"
            FOREIGN KEY ("{column_name}")
            REFERENCES "{ref_table}" ("id")
            ON DELETE CASCADE
            DEFERRABLE INITIALLY DEFERRED
            """
        )


def _add_nondeferrable_fk(table_name, constraint_name, column_name, ref_table=_REF_TABLE):
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            ALTER TABLE "{table_name}"
            ADD CONSTRAINT "{constraint_name}"
            FOREIGN KEY ("{column_name}")
            REFERENCES "{ref_table}" ("id")
            ON DELETE CASCADE
            """
        )


# ---------------------------------------------------------------------------
# Base TestCase
# ---------------------------------------------------------------------------

class _MigrationTestCase(TransactionCleanupMixin, TransactionTestCase):
    """
    Base for migration tests that need ephemeral custom_objects_* tables.

    Subclasses call ``_create_scratch_table()`` in setUp; tables are dropped
    automatically in tearDown.  Use IDs in the 99900+ range to avoid colliding
    with any real CustomObjectType rows created during the test run.
    """

    def setUp(self):
        super().setUp()
        self._created_tables: list[str] = []

    def tearDown(self):
        with connection.cursor() as cursor:
            for table in reversed(self._created_tables):
                cursor.execute(f'DROP TABLE IF EXISTS "{table}" CASCADE')
        super().tearDown()

    def _create_scratch_table(self, table_name: str, columns: list[str]):
        """CREATE TABLE with nullable integer columns; register for tearDown."""
        col_defs = ', '.join(f'"{c}" INTEGER' for c in columns)
        with connection.cursor() as cursor:
            cursor.execute(
                f'CREATE TABLE "{table_name}" (id SERIAL PRIMARY KEY, {col_defs})'
            )
        self._created_tables.append(table_name)


# ===========================================================================
# Migration 0011 — fix_deferrable_fk_constraints
# ===========================================================================
#
# Covers three failure modes reported in #507:
#   1. Truncation collision — long through-table names combined with column names
#      can exceed PostgreSQL's 63-char identifier limit; two columns sharing the
#      same truncated prefix would collide on ADD CONSTRAINT.
#   2. DROP without IF EXISTS — a re-run after a partial failure would error when
#      the old constraint name was already gone.
#   3. No pre-drop of new name — if a prior run left a non-DEFERRABLE _fk_cascade
#      constraint behind, the ADD CONSTRAINT would raise DuplicateObject.

_m0011 = _load_migration('0011_non_deferrable_fk_constraints')


class _Migration0011TestCase(_MigrationTestCase):
    def _run_migration(self):
        _m0011.fix_deferrable_fk_constraints(None, _FakeSchemaEditor())


class BasicConversionTestCase(_Migration0011TestCase):
    """DEFERRABLE constraints on a short-named table are converted to non-DEFERRABLE."""

    TABLE = 'custom_objects_99901'

    def setUp(self):
        super().setUp()
        self._create_scratch_table(self.TABLE, ['site_id', 'device_id'])
        _add_deferrable_fk(self.TABLE, f'{self.TABLE}_site_id_old', 'site_id')
        _add_deferrable_fk(self.TABLE, f'{self.TABLE}_device_id_old', 'device_id')

    def test_constraints_become_non_deferrable(self):
        self._run_migration()
        constraints = _get_fk_constraints(self.TABLE)
        self.assertTrue(constraints, 'Expected at least one FK constraint after migration')
        for name, deferrable in constraints.items():
            self.assertEqual(
                deferrable, 'NO',
                f'Constraint {name!r} should be non-DEFERRABLE but is_deferrable={deferrable!r}',
            )

    def test_new_constraint_names_have_fk_cascade_suffix(self):
        self._run_migration()
        for name in _get_fk_constraints(self.TABLE):
            self.assertTrue(
                name.endswith('_fk_cascade'),
                f'Expected _fk_cascade suffix on {name!r}',
            )

    def test_one_constraint_per_column(self):
        self._run_migration()
        constraints = _get_fk_constraints(self.TABLE)
        self.assertEqual(len(constraints), 2, f'Expected 2 constraints, got: {list(constraints)}')


class LongTableNameTestCase(_Migration0011TestCase):
    """
    Through-table with a 47-char name + columns whose combined constraint name
    exceeds 63 chars.  The old code would silently truncate and potentially
    collide; _safe_constraint_name must keep names unique and ≤ 63 chars.
    """

    # 47 chars — long enough that any column with name > 5 chars overflows 63.
    TABLE = 'custom_objects_99902_long_through_table_name_abc'

    def setUp(self):
        super().setUp()
        # Two columns whose first 18 chars are identical.  With the old naïve
        # naming they would both truncate to the same 63-char string and the
        # second ADD CONSTRAINT would raise DuplicateObject.
        self._create_scratch_table(
            self.TABLE,
            ['applicant_user_id_first_variant', 'applicant_user_id_secnd_variant'],
        )
        # Use short hash-style names for the original DEFERRABLE constraints,
        # matching how Django's migration system auto-names them in production.
        _add_deferrable_fk(self.TABLE, 'old_deferrable_col1_99902a', 'applicant_user_id_first_variant')
        _add_deferrable_fk(self.TABLE, 'old_deferrable_col2_99902b', 'applicant_user_id_secnd_variant')

    def test_migration_succeeds_without_duplicate_object_error(self):
        """Should not raise DuplicateObject even though naïve names would collide."""
        try:
            self._run_migration()
        except Exception as exc:
            self.fail(f'fix_deferrable_fk_constraints raised unexpectedly: {exc}')

    def test_all_constraints_non_deferrable(self):
        self._run_migration()
        constraints = _get_fk_constraints(self.TABLE)
        self.assertEqual(len(constraints), 2, f'Expected 2 constraints, got: {list(constraints)}')
        for name, deferrable in constraints.items():
            self.assertEqual(deferrable, 'NO', f'{name!r} should be non-DEFERRABLE')

    def test_constraint_names_within_pg_limit(self):
        self._run_migration()
        for name in _get_fk_constraints(self.TABLE):
            self.assertLessEqual(
                len(name), 63,
                f'Constraint name {name!r} ({len(name)} chars) exceeds PostgreSQL 63-char limit',
            )

    def test_constraint_names_are_unique(self):
        self._run_migration()
        names = list(_get_fk_constraints(self.TABLE))
        self.assertEqual(len(names), len(set(names)), f'Duplicate constraint names: {names}')

    def test_safe_constraint_name_unit(self):
        """_safe_constraint_name produces distinct names for the two colliding columns."""
        safe = _m0011._safe_constraint_name
        n1 = safe(self.TABLE, 'applicant_user_id_first_variant')
        n2 = safe(self.TABLE, 'applicant_user_id_secnd_variant')
        self.assertNotEqual(n1, n2)
        self.assertLessEqual(len(n1), 63)
        self.assertLessEqual(len(n2), 63)


class PartialRerunTestCase(_Migration0011TestCase):
    """
    Simulate a database left in partial state: one column still has its original
    DEFERRABLE constraint PLUS the _fk_cascade non-DEFERRABLE constraint already
    present (as if a prior run succeeded for that column but failed to commit, then
    was retried with autocommit on).  The migration must not raise DuplicateObject.
    """

    TABLE = 'custom_objects_99903'

    def setUp(self):
        super().setUp()
        self._create_scratch_table(self.TABLE, ['site_id', 'tenant_id'])

        # site_id: partial state — old DEFERRABLE constraint still present
        # AND the new _fk_cascade non-DEFERRABLE one already exists.
        _add_deferrable_fk(self.TABLE, f'{self.TABLE}_site_id_old_hash', 'site_id')
        new_site_name = _m0011._safe_constraint_name(self.TABLE, 'site_id')
        _add_nondeferrable_fk(self.TABLE, new_site_name, 'site_id')

        # tenant_id: normal state — only old DEFERRABLE constraint.
        _add_deferrable_fk(self.TABLE, f'{self.TABLE}_tenant_id_old_hash', 'tenant_id')

    def test_migration_succeeds_with_pre_existing_fk_cascade_constraint(self):
        try:
            self._run_migration()
        except Exception as exc:
            self.fail(f'fix_deferrable_fk_constraints raised unexpectedly: {exc}')

    def test_all_constraints_non_deferrable_after_rerun(self):
        self._run_migration()
        constraints = _get_fk_constraints(self.TABLE)
        self.assertEqual(len(constraints), 2, f'Expected 2 constraints, got: {list(constraints)}')
        for name, deferrable in constraints.items():
            self.assertEqual(deferrable, 'NO', f'{name!r} should be non-DEFERRABLE')
