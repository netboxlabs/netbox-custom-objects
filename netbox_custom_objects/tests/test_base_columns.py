"""
Tests for the base-column snapshot feature (issue #391, Phase 1).

Covers:
- _collect_base_columns() correctly identifies base vs user-defined columns
- _store_base_column_snapshot() writes schema_document["base_columns"] and
  preserves existing schema_document keys
- create_model() automatically stores the snapshot after table creation
- backfill_base_columns migration function populates existing COTs and is
  idempotent
"""

import importlib

from django.test import TransactionTestCase

from netbox_custom_objects.models import CustomObjectType

from .base import CustomObjectsTestCase, TransactionCleanupMixin


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_backfill():
    """Execute the 0009 backfill function directly against the live DB."""
    mod = importlib.import_module(
        "netbox_custom_objects.migrations.0009_backfill_base_columns"
    )

    class _AppsShim:
        @staticmethod
        def get_model(app_label, model_name):
            from django.apps import apps  # noqa: PLC0415
            return apps.get_model(app_label, model_name)

    mod.backfill_base_columns(_AppsShim(), None)


# ---------------------------------------------------------------------------
# _collect_base_columns()
# ---------------------------------------------------------------------------

class CollectBaseColumnsTestCase(
    TransactionCleanupMixin, CustomObjectsTestCase, TransactionTestCase
):
    """Unit-style tests for CustomObjectType._collect_base_columns()."""

    def _make_cot_and_model(self, name, slug, field_names=()):
        """Create a COT with optional user fields and return (cot, model)."""
        cot = self.create_custom_object_type(name=name, slug=slug)
        for fname in field_names:
            self.create_custom_object_type_field(cot, name=fname, type="text")
        return cot, cot.get_model()

    def test_base_columns_present_when_no_user_fields(self):
        """id, created, and last_updated must always appear as base columns."""
        cot, model = self._make_cot_and_model("no_fields", "no-fields")
        base = CustomObjectType._collect_base_columns(model, set())
        names = {c["name"] for c in base}
        self.assertIn("id", names)
        self.assertIn("created", names)
        self.assertIn("last_updated", names)

    def test_user_field_excluded_from_base_columns(self):
        """A user-defined field name must not appear in base columns."""
        cot, model = self._make_cot_and_model(
            "with_field", "with-field", field_names=["my_text"]
        )
        user_field_names = {"my_text"}
        base = CustomObjectType._collect_base_columns(model, user_field_names)
        names = {c["name"] for c in base}
        self.assertNotIn("my_text", names)
        self.assertIn("id", names)

    def test_each_entry_has_required_keys(self):
        """Every base-column dict must carry name, field_class, and null."""
        cot, model = self._make_cot_and_model("keys_check", "keys-check")
        for entry in CustomObjectType._collect_base_columns(model, set()):
            self.assertIn("name", entry, f"Missing 'name' in {entry}")
            self.assertIn("field_class", entry, f"Missing 'field_class' in {entry}")
            self.assertIn("null", entry, f"Missing 'null' in {entry}")

    def test_id_field_class(self):
        """id should be reported as AutoField (or a subclass name)."""
        cot, model = self._make_cot_and_model("id_type", "id-type")
        base = CustomObjectType._collect_base_columns(model, set())
        id_entry = next(c for c in base if c["name"] == "id")
        self.assertIn("Field", id_entry["field_class"])

    def test_multiple_user_fields_all_excluded(self):
        """All user-defined field names must be excluded regardless of count."""
        cot, model = self._make_cot_and_model(
            "multi_fields", "multi-fields",
            field_names=["alpha", "beta", "gamma"],
        )
        user_field_names = {"alpha", "beta", "gamma"}
        base = CustomObjectType._collect_base_columns(model, user_field_names)
        names = {c["name"] for c in base}
        self.assertNotIn("alpha", names)
        self.assertNotIn("beta", names)
        self.assertNotIn("gamma", names)


# ---------------------------------------------------------------------------
# _store_base_column_snapshot()
# ---------------------------------------------------------------------------

class StoreBaseColumnSnapshotTestCase(
    TransactionCleanupMixin, CustomObjectsTestCase, TransactionTestCase
):
    """Tests for CustomObjectType._store_base_column_snapshot()."""

    def test_snapshot_written_to_schema_document(self):
        """schema_document['base_columns'] must be populated after the call."""
        cot = self.create_custom_object_type(name="snap_test", slug="snap-test")
        model = cot.get_model()
        # Wipe base_columns so we can test the write explicitly
        CustomObjectType.objects.filter(pk=cot.pk).update(schema_document=None)
        cot.schema_document = None

        cot._store_base_column_snapshot(model)

        cot.refresh_from_db()
        self.assertIsNotNone(cot.schema_document)
        self.assertIn("base_columns", cot.schema_document)
        self.assertIsInstance(cot.schema_document["base_columns"], list)
        self.assertTrue(len(cot.schema_document["base_columns"]) > 0)

    def test_existing_schema_document_keys_preserved(self):
        """Calling _store_base_column_snapshot must not remove pre-existing keys."""
        cot = self.create_custom_object_type(name="preserve_keys", slug="preserve-keys")
        # Seed schema_document with an existing key
        existing_doc = {"schema_version": "1", "types": []}
        CustomObjectType.objects.filter(pk=cot.pk).update(schema_document=existing_doc)
        cot.refresh_from_db()

        model = cot.get_model()
        cot._store_base_column_snapshot(model)

        cot.refresh_from_db()
        self.assertIn("schema_version", cot.schema_document)
        self.assertEqual(cot.schema_document["schema_version"], "1")
        self.assertIn("base_columns", cot.schema_document)

    def test_in_memory_schema_document_updated(self):
        """The in-memory cot.schema_document must reflect the write immediately."""
        cot = self.create_custom_object_type(name="in_memory", slug="in-memory")
        CustomObjectType.objects.filter(pk=cot.pk).update(schema_document=None)
        cot.schema_document = None

        model = cot.get_model()
        cot._store_base_column_snapshot(model)

        self.assertIsNotNone(cot.schema_document)
        self.assertIn("base_columns", cot.schema_document)

    def test_base_columns_does_not_include_user_fields(self):
        """User-defined fields must not appear in the stored base_columns."""
        cot = self.create_custom_object_type(name="excl_user", slug="excl-user")
        self.create_custom_object_type_field(cot, name="my_field", type="text")
        model = cot.get_model()

        CustomObjectType.objects.filter(pk=cot.pk).update(schema_document=None)
        cot.schema_document = None
        cot._store_base_column_snapshot(model)

        cot.refresh_from_db()
        names = {c["name"] for c in cot.schema_document["base_columns"]}
        self.assertNotIn("my_field", names)
        self.assertIn("id", names)


# ---------------------------------------------------------------------------
# create_model() automatic snapshot
# ---------------------------------------------------------------------------

class CreateModelSnapshotTestCase(
    TransactionCleanupMixin, CustomObjectsTestCase, TransactionTestCase
):
    """Tests that create_model() stores a base-column snapshot automatically."""

    def test_schema_document_has_base_columns_after_creation(self):
        """A freshly created COT must have base_columns in schema_document."""
        cot = self.create_custom_object_type(name="auto_snap", slug="auto-snap")
        cot.refresh_from_db()
        self.assertIsNotNone(cot.schema_document)
        self.assertIn("base_columns", cot.schema_document)

    def test_base_columns_includes_id_created_last_updated(self):
        """The mandatory base columns must always be present."""
        cot = self.create_custom_object_type(name="mandatory_cols", slug="mandatory-cols")
        cot.refresh_from_db()
        names = {c["name"] for c in cot.schema_document["base_columns"]}
        self.assertIn("id", names)
        self.assertIn("created", names)
        self.assertIn("last_updated", names)

    def test_user_field_not_in_base_columns(self):
        """A user field added after COT creation must not appear in base_columns."""
        cot = self.create_custom_object_type(name="user_excl", slug="user-excl")
        self.create_custom_object_type_field(cot, name="extra_col", type="text")
        # Force a fresh snapshot by calling _store_base_column_snapshot again,
        # simulating what create_model does at COT birth (user field didn't exist yet)
        CustomObjectType.objects.filter(pk=cot.pk).update(schema_document=None)
        cot.refresh_from_db()
        model = cot.get_model()
        cot._store_base_column_snapshot(model)
        cot.refresh_from_db()

        names = {c["name"] for c in cot.schema_document["base_columns"]}
        self.assertNotIn("extra_col", names)


# ---------------------------------------------------------------------------
# 0009 backfill migration
# ---------------------------------------------------------------------------

class BackfillBaseColumnsTestCase(
    TransactionCleanupMixin, CustomObjectsTestCase, TransactionTestCase
):
    """
    Tests for the 0009_backfill_base_columns migration function.

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

        _run_backfill()

        cot.refresh_from_db()
        self.assertIn("base_columns", cot.schema_document)
        self.assertTrue(len(cot.schema_document["base_columns"]) > 0)

    def test_backfill_includes_mandatory_base_columns(self):
        """id, created, and last_updated must appear after backfill."""
        cot = self.create_custom_object_type(name="bf_mandatory", slug="bf-mandatory")
        self._clear_base_columns(cot)

        _run_backfill()

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

        _run_backfill()

        cot.refresh_from_db()
        names = {c["name"] for c in cot.schema_document["base_columns"]}
        self.assertNotIn("custom_col", names)

    def test_backfill_is_idempotent(self):
        """Running backfill twice must not alter an already-backfilled snapshot."""
        cot = self.create_custom_object_type(name="bf_idem", slug="bf-idem")
        self._clear_base_columns(cot)

        _run_backfill()
        cot.refresh_from_db()
        first_snapshot = cot.schema_document["base_columns"]

        _run_backfill()
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

        _run_backfill()

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

        _run_backfill()

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

        _run_backfill()

        cot.refresh_from_db()
        for entry in cot.schema_document["base_columns"]:
            self.assertIn("name", entry, f"Entry missing 'name': {entry}")
            self.assertIn("field_class", entry, f"Entry missing 'field_class': {entry}")
            self.assertIn("null", entry, f"Entry missing 'null': {entry}")
