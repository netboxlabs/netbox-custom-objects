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

from django.apps import apps
from django.db import connection
from django.test import TestCase

from netbox_custom_objects.models import CustomObjectType, CustomObjectTypeField

from ..base import CustomObjectsTestCase


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

class _MigrationTestCase(TestCase):
    """
    Base for migration tests that need ephemeral custom_objects_* tables.

    Subclasses call ``_create_scratch_table()`` in setUp; the test's rollback drops
    them.  Use IDs in the 99900+ range to avoid colliding with any real
    CustomObjectType rows created during the test run.
    """

    def _create_scratch_table(self, table_name: str, columns: list[str]):
        """CREATE TABLE with nullable integer columns."""
        col_defs = ', '.join(f'"{c}" INTEGER' for c in columns)
        with connection.cursor() as cursor:
            cursor.execute(
                f'CREATE TABLE "{table_name}" (id SERIAL PRIMARY KEY, {col_defs})'
            )


# ===========================================================================
# Migration 0008 — backfill_schema_ids
# ===========================================================================

# The backfill functions read models only through apps.get_model(), so the current
# app registry stands in for the historical one RunPython would pass.
_m0008 = _load_migration('0008_backfill_schema_ids')


class SchemaIdBackfillTestCase(CustomObjectsTestCase, TestCase):
    """
    Tests for the 0008_backfill_schema_ids migration logic.

    Rather than exercising the migration runner itself (which would require
    replaying the full migration history), these tests call the same backfill
    function directly and verify its behaviour against real model instances.
    """

    # ------------------------------------------------------------------
    # Core backfill behaviour
    # ------------------------------------------------------------------

    def test_fields_without_schema_id_are_assigned(self):
        cot = self.create_custom_object_type(name='bf1', slug='bf-1')
        f1 = self.create_custom_object_type_field(cot, name='alpha', type='text')
        f2 = self.create_custom_object_type_field(cot, name='beta', type='text')
        # Force both to NULL (simulate pre-feature state)
        CustomObjectTypeField.objects.filter(
            custom_object_type=cot
        ).update(schema_id=None)

        _m0008.assign_schema_ids(apps, None)

        f1.refresh_from_db()
        f2.refresh_from_db()
        self.assertIsNotNone(f1.schema_id)
        self.assertIsNotNone(f2.schema_id)

    def test_assigned_ids_are_sequential_from_one(self):
        cot = self.create_custom_object_type(name='bf2', slug='bf-2')
        [
            self.create_custom_object_type_field(cot, name=f'f{i}', type='text')
            for i in range(1, 4)
        ]
        CustomObjectTypeField.objects.filter(
            custom_object_type=cot
        ).update(schema_id=None)

        _m0008.assign_schema_ids(apps, None)

        ids = sorted(
            CustomObjectTypeField.objects.filter(custom_object_type=cot)
            .values_list('schema_id', flat=True)
        )
        self.assertEqual(ids, [1, 2, 3])

    def test_existing_schema_ids_are_not_changed(self):
        cot = self.create_custom_object_type(name='bf3', slug='bf-3')
        f_existing = self.create_custom_object_type_field(cot, name='kept', type='text')
        existing_id = f_existing.schema_id  # set by auto-assign
        self.assertIsNotNone(existing_id)

        # Add a second field and force it to NULL
        f_new = self.create_custom_object_type_field(cot, name='new_one', type='text')
        CustomObjectTypeField.objects.filter(pk=f_new.pk).update(schema_id=None)

        _m0008.assign_schema_ids(apps, None)

        f_existing.refresh_from_db()
        f_new.refresh_from_db()
        self.assertEqual(f_existing.schema_id, existing_id, "Pre-existing ID must not change")
        self.assertIsNotNone(f_new.schema_id)
        self.assertGreater(f_new.schema_id, existing_id, "New ID must follow existing max")

    def test_backfill_continues_from_existing_max(self):
        """New IDs start after the highest already-assigned ID."""
        cot = self.create_custom_object_type(name='bf4', slug='bf-4')
        # Manually assign schema_id=5 to simulate a gap
        f1 = self.create_custom_object_type_field(cot, name='five', type='text')
        CustomObjectTypeField.objects.filter(pk=f1.pk).update(schema_id=5)

        f2 = self.create_custom_object_type_field(cot, name='null_one', type='text')
        CustomObjectTypeField.objects.filter(pk=f2.pk).update(schema_id=None)

        _m0008.assign_schema_ids(apps, None)

        f2.refresh_from_db()
        self.assertEqual(f2.schema_id, 6)

    def test_next_schema_id_updated_on_cot(self):
        from netbox_custom_objects.models import CustomObjectType
        cot = self.create_custom_object_type(name='bf5', slug='bf-5')
        for i in range(1, 4):
            self.create_custom_object_type_field(cot, name=f'g{i}', type='text')
        # Force all to NULL and reset counter
        CustomObjectTypeField.objects.filter(
            custom_object_type=cot
        ).update(schema_id=None)
        CustomObjectType.objects.filter(pk=cot.pk).update(next_schema_id=0)

        _m0008.assign_schema_ids(apps, None)

        cot.refresh_from_db()
        self.assertEqual(cot.next_schema_id, 3)

    def test_next_schema_id_never_decreases(self):
        """If next_schema_id is already high, the backfill must not lower it."""
        from netbox_custom_objects.models import CustomObjectType
        cot = self.create_custom_object_type(name='bf6', slug='bf-6')
        f = self.create_custom_object_type_field(cot, name='only', type='text')
        CustomObjectTypeField.objects.filter(pk=f.pk).update(schema_id=None)
        # Artificially set next_schema_id to a large value
        CustomObjectType.objects.filter(pk=cot.pk).update(next_schema_id=99)

        _m0008.assign_schema_ids(apps, None)

        cot.refresh_from_db()
        self.assertGreaterEqual(cot.next_schema_id, 99)

    def test_ids_unique_within_cot(self):
        cot = self.create_custom_object_type(name='bf7', slug='bf-7')
        for i in range(1, 6):
            self.create_custom_object_type_field(cot, name=f'h{i}', type='text')
        CustomObjectTypeField.objects.filter(
            custom_object_type=cot
        ).update(schema_id=None)

        _m0008.assign_schema_ids(apps, None)

        ids = list(
            CustomObjectTypeField.objects.filter(custom_object_type=cot)
            .values_list('schema_id', flat=True)
        )
        self.assertEqual(len(ids), len(set(ids)), "All schema_ids must be unique within COT")

    def test_ids_scoped_per_cot(self):
        """Two different COTs both start their IDs from 1."""
        cotA = self.create_custom_object_type(name='bfA', slug='bf-a')
        cotB = self.create_custom_object_type(name='bfB', slug='bf-b')
        fa = self.create_custom_object_type_field(cotA, name='x', type='text')
        fb = self.create_custom_object_type_field(cotB, name='x', type='text')
        CustomObjectTypeField.objects.filter(pk__in=[fa.pk, fb.pk]).update(schema_id=None)
        from netbox_custom_objects.models import CustomObjectType
        CustomObjectType.objects.filter(pk__in=[cotA.pk, cotB.pk]).update(next_schema_id=0)

        _m0008.assign_schema_ids(apps, None)

        fa.refresh_from_db()
        fb.refresh_from_db()
        self.assertEqual(fa.schema_id, 1)
        self.assertEqual(fb.schema_id, 1)

    def test_idempotent(self):
        """Running the backfill twice must not change any already-assigned IDs."""
        cot = self.create_custom_object_type(name='bfIdem', slug='bf-idem')
        for i in range(1, 4):
            self.create_custom_object_type_field(cot, name=f'i{i}', type='text')
        CustomObjectTypeField.objects.filter(
            custom_object_type=cot
        ).update(schema_id=None)

        _m0008.assign_schema_ids(apps, None)
        ids_after_first = list(
            CustomObjectTypeField.objects.filter(custom_object_type=cot)
            .order_by('id').values_list('schema_id', flat=True)
        )

        _m0008.assign_schema_ids(apps, None)
        ids_after_second = list(
            CustomObjectTypeField.objects.filter(custom_object_type=cot)
            .order_by('id').values_list('schema_id', flat=True)
        )

        self.assertEqual(ids_after_first, ids_after_second)


# ===========================================================================
# Migration 0010 — backfill_base_columns
# ===========================================================================

_m0010 = _load_migration('0010_backfill_base_columns')


class BackfillBaseColumnsTestCase(CustomObjectsTestCase, TestCase):
    """
    Tests for the 0010_backfill_base_columns migration function.

    The migration function is called directly (bypassing the migration runner)
    so we can verify its behaviour against real DB state.
    """

    def _clear_base_columns(self, cot):
        """Remove base_columns from schema_document to simulate pre-feature state."""
        doc = cot.schema_document or {}
        doc.pop("base_columns", None)
        # Use None if dict is now empty, to match real pre-feature state
        CustomObjectType.objects.filter(pk=cot.pk).update(
            schema_document=doc if doc else None
        )
        cot.refresh_from_db()

    def test_backfill_populates_base_columns(self):
        """COTs without base_columns must get them after backfill."""
        cot = self.create_custom_object_type(name="bf_pop", slug="bf-pop")
        self._clear_base_columns(cot)
        self.assertFalse(
            cot.schema_document and "base_columns" in (cot.schema_document or {})
        )

        _m0010.backfill_base_columns(apps, None)

        cot.refresh_from_db()
        self.assertIn("base_columns", cot.schema_document)
        self.assertTrue(len(cot.schema_document["base_columns"]) > 0)

    def test_backfill_includes_mandatory_base_columns(self):
        """id, created, and last_updated must appear after backfill."""
        cot = self.create_custom_object_type(name="bf_mandatory", slug="bf-mandatory")
        self._clear_base_columns(cot)

        _m0010.backfill_base_columns(apps, None)

        cot.refresh_from_db()
        names = {c["name"] for c in cot.schema_document["base_columns"]}
        self.assertIn("id", names)
        self.assertIn("created", names)
        self.assertIn("last_updated", names)

    def test_backfill_excludes_user_fields(self):
        """User-defined field columns must not appear in the backfilled base_columns."""
        cot = self.create_custom_object_type(name="bf_excl", slug="bf-excl")
        self.create_custom_object_type_field(cot, name="custom_col", type="text")
        self._clear_base_columns(cot)

        _m0010.backfill_base_columns(apps, None)

        cot.refresh_from_db()
        names = {c["name"] for c in cot.schema_document["base_columns"]}
        self.assertNotIn("custom_col", names)

    def test_backfill_is_idempotent(self):
        """Running backfill twice must not alter an already-backfilled snapshot."""
        cot = self.create_custom_object_type(name="bf_idem", slug="bf-idem")
        self._clear_base_columns(cot)

        _m0010.backfill_base_columns(apps, None)
        cot.refresh_from_db()
        first_snapshot = cot.schema_document["base_columns"]

        _m0010.backfill_base_columns(apps, None)
        cot.refresh_from_db()
        second_snapshot = cot.schema_document["base_columns"]

        self.assertEqual(first_snapshot, second_snapshot)

    def test_backfill_skips_cot_with_existing_base_columns(self):
        """A COT that already has base_columns must not be modified."""
        cot = self.create_custom_object_type(name="bf_skip", slug="bf-skip")
        # Plant a sentinel value in base_columns
        sentinel_doc = {"base_columns": [{"name": "sentinel", "field_class": "TextField", "null": True}]}
        CustomObjectType.objects.filter(pk=cot.pk).update(schema_document=sentinel_doc)
        cot.refresh_from_db()

        _m0010.backfill_base_columns(apps, None)

        cot.refresh_from_db()
        self.assertEqual(
            cot.schema_document["base_columns"],
            sentinel_doc["base_columns"],
            "Pre-existing base_columns must not be overwritten",
        )

    def test_backfill_handles_multiple_cots(self):
        """Backfill must process all COTs in one pass."""
        cots = [
            self.create_custom_object_type(name=f"bf_multi{i}", slug=f"bf-multi{i}")
            for i in range(3)
        ]
        for cot in cots:
            self._clear_base_columns(cot)

        _m0010.backfill_base_columns(apps, None)

        for cot in cots:
            cot.refresh_from_db()
            self.assertIn(
                "base_columns", cot.schema_document,
                f"COT {cot.pk} missing base_columns after backfill",
            )

    def test_backfill_each_entry_has_required_keys(self):
        """Every entry in the backfilled base_columns must have name, field_class, and null."""
        cot = self.create_custom_object_type(name="bf_keys", slug="bf-keys")
        self._clear_base_columns(cot)

        _m0010.backfill_base_columns(apps, None)

        cot.refresh_from_db()
        for entry in cot.schema_document["base_columns"]:
            self.assertIn("name", entry, f"Entry missing 'name': {entry}")
            self.assertIn("field_class", entry, f"Entry missing 'field_class': {entry}")
            self.assertIn("null", entry, f"Entry missing 'null': {entry}")


# ===========================================================================
# Migration 0011 — fix_deferrable_fk_constraints
# ===========================================================================
#
# Covers three reported failure modes:
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

    # 47 chars — long enough that any column with name > 4 chars overflows 63.
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

    def test_site_id_has_exactly_one_fk_after_rerun(self):
        """The old DEFERRABLE + pre-existing non-DEFERRABLE must collapse to one constraint."""
        self._run_migration()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT count(*)
                FROM information_schema.table_constraints AS tc
                JOIN information_schema.key_column_usage AS kcu
                    ON tc.constraint_name = kcu.constraint_name
                   AND tc.table_schema = kcu.table_schema
                WHERE tc.constraint_type = 'FOREIGN KEY'
                  AND tc.table_name = %s
                  AND tc.table_schema = current_schema()
                  AND kcu.column_name = 'site_id'
                """,
                [self.TABLE],
            )
            count = cursor.fetchone()[0]
        self.assertEqual(count, 1, f'site_id should have exactly 1 FK constraint after migration, got {count}')


# ===========================================================================
# Migration 0016 — widen_integer_columns
# ===========================================================================

_m0016 = _load_migration('0016_widen_integer_columns')


class WidenIntegerColumnsTestCase(CustomObjectsTestCase, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.custom_object_type = cls.create_custom_object_type(name="TestObject")
        cls.create_custom_object_type_field(
            cls.custom_object_type,
            name="name",
            label="Name",
            type="text",
            primary=True,
            required=True
        )

    def test_integer_field_upgrade_widens_existing_32bit_column(self):
        """The 0016 migration widens older 32-bit integer columns to bigint.

        New tables already get a bigint column (test_integer_field_is_64_bit),
        but custom_objects_* tables created before that change have a 32-bit
        ``integer`` column that the field-type change alone does not alter --
        they are managed=False. This exercises that upgrade path: realize the
        column, force it back to 32-bit to mimic a legacy install, run the
        migration's data function, and confirm it is widened in place.
        """
        field = self.create_custom_object_type_field(
            self.custom_object_type,
            name="legacy_count",
            label="Legacy Count",
            type="integer",
        )

        # Realize the backing model/table/column.
        model = self.custom_object_type.get_model()

        table_name = f"custom_objects_{self.custom_object_type.pk}"
        column_name = field.name

        def column_data_type():
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT data_type FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name = %s AND column_name = %s
                    """,
                    [table_name, column_name],
                )
                row = cursor.fetchone()
                return row[0] if row else None

        # Mimic a legacy install: force the column back to a 32-bit integer.
        with connection.cursor() as cursor:
            cursor.execute(
                f'ALTER TABLE "{table_name}" ALTER COLUMN "{column_name}" TYPE integer'
            )
        self.assertEqual(column_data_type(), "integer")

        # Run the migration's data function against the legacy column.
        # widen_integer_columns only reads stable CustomObjectTypeField columns
        # (type, custom_object_type_id, name), so the current app registry is
        # equivalent to the historical one RunPython would pass. If the function
        # ever introspects historical field structure, switch to MigrationLoader.
        with connection.schema_editor() as schema_editor:
            _m0016.widen_integer_columns(apps, schema_editor)

        # Column is widened, and a value beyond the 32-bit range round-trips.
        self.assertEqual(column_data_type(), "bigint")
        big_value = 9_000_000_000  # > 2**31 - 1 (2_147_483_647)
        instance = model.objects.create(name="Test", legacy_count=big_value)
        instance.refresh_from_db()
        self.assertEqual(instance.legacy_count, big_value)
