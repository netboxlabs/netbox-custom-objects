"""
Tests for the mixin column drift detection and repair (issue #391, Phase 2).

Covers:
- _expected_base_fields(): returns the correct base fields, excludes user fields
- _can_auto_add(): correct classification of nullable / defaulted fields
- heal_cot(): detects missing columns, adds safe ones, warns on unsafe ones,
              never drops, updates schema_document snapshot after healing,
              dry_run mode reports without modifying
- heal_all_cots(): iterates all COTs and returns correct summary counts
- upgrade_custom_objects management command: --dry-run and --cot flags
"""

from unittest.mock import MagicMock, patch

from django.db import connection
from django.test import TestCase, TransactionTestCase

from netbox_custom_objects.mixin_migration import (
    _can_auto_add,
    _expected_base_fields,
    heal_all_cots,
    heal_cot,
)
from netbox_custom_objects.models import CustomObjectType

from .base import CustomObjectsTestCase, TransactionCleanupMixin


# ---------------------------------------------------------------------------
# _can_auto_add()
# ---------------------------------------------------------------------------

class CanAutoAddTestCase(TestCase):
    """Unit tests for _can_auto_add() — no DB required."""

    def _field(self, null=False, has_default=False, default_value=None):
        f = MagicMock()
        f.null = null
        f.has_default.return_value = has_default
        return f

    def test_nullable_field_is_safe(self):
        self.assertTrue(_can_auto_add(self._field(null=True)))

    def test_field_with_default_is_safe(self):
        self.assertTrue(_can_auto_add(self._field(has_default=True)))

    def test_nullable_and_has_default_is_safe(self):
        self.assertTrue(_can_auto_add(self._field(null=True, has_default=True)))

    def test_non_nullable_no_default_is_unsafe(self):
        self.assertFalse(_can_auto_add(self._field(null=False, has_default=False)))


# ---------------------------------------------------------------------------
# _expected_base_fields()
# ---------------------------------------------------------------------------

class ExpectedBaseFieldsTestCase(
    TransactionCleanupMixin, CustomObjectsTestCase, TransactionTestCase
):
    """Tests for _expected_base_fields() — requires a live COT."""

    def test_returns_id_created_last_updated(self):
        cot = self.create_custom_object_type(name="ebf_basic", slug="ebf-basic")
        fields = _expected_base_fields(cot)
        self.assertIn("id", fields)
        self.assertIn("created", fields)
        self.assertIn("last_updated", fields)

    def test_excludes_user_defined_field(self):
        cot = self.create_custom_object_type(name="ebf_user", slug="ebf-user")
        self.create_custom_object_type_field(cot, name="my_col", type="text")
        fields = _expected_base_fields(cot)
        self.assertNotIn("my_col", fields)

    def test_returns_django_field_instances(self):
        from django.db.models import Field
        cot = self.create_custom_object_type(name="ebf_inst", slug="ebf-inst")
        for field in _expected_base_fields(cot).values():
            self.assertIsInstance(field, Field)


# ---------------------------------------------------------------------------
# heal_cot() — normal path
# ---------------------------------------------------------------------------

class HealCotTestCase(
    TransactionCleanupMixin, CustomObjectsTestCase, TransactionTestCase
):
    """Integration tests for heal_cot() against a real DB."""

    def test_no_drift_returns_empty_results(self):
        cot = self.create_custom_object_type(name="hc_nodrift", slug="hc-nodrift")
        result = heal_cot(cot)
        self.assertEqual(result["added"], [])
        self.assertEqual(result["warned"], [])

    def test_missing_nullable_column_is_added(self):
        """
        Simulate a new nullable base column appearing in the mixin by patching
        _expected_base_fields to return an extra field, then verifying that
        heal_cot adds it to the actual DB table.
        """
        cot = self.create_custom_object_type(name="hc_add", slug="hc-add")
        table_name = cot.get_database_table_name()

        # Confirm the column doesn't exist yet
        with connection.cursor() as cur:
            actual_before = {
                c.name for c in connection.introspection.get_table_description(cur, table_name)
            }
        self.assertNotIn("new_nullable_col", actual_before)

        # Build a real nullable CharField to inject
        from django.db import models as dj_models
        new_field = dj_models.CharField(max_length=50, null=True, blank=True)
        new_field.name = "new_nullable_col"
        new_field.column = "new_nullable_col"
        new_field.set_attributes_from_name("new_nullable_col")
        new_field.model = cot.get_model()

        base_fields = _expected_base_fields(cot)
        base_fields["new_nullable_col"] = new_field

        with patch(
            "netbox_custom_objects.mixin_migration._expected_base_fields",
            return_value=base_fields,
        ):
            result = heal_cot(cot, verbosity=0)

        self.assertIn("new_nullable_col", result["added"])
        self.assertEqual(result["warned"], [])

        # Verify the column now exists in the DB
        with connection.cursor() as cur:
            actual_after = {
                c.name for c in connection.introspection.get_table_description(cur, table_name)
            }
        self.assertIn("new_nullable_col", actual_after)

        # Clean up the added column so tearDown can drop the table cleanly
        with connection.schema_editor() as editor:
            editor.remove_field(cot.get_model(), new_field)

    def test_missing_non_nullable_no_default_produces_warning(self):
        """A NOT NULL column without a default cannot be auto-added; must warn."""
        cot = self.create_custom_object_type(name="hc_warn", slug="hc-warn")

        from django.db import models as dj_models
        bad_field = dj_models.IntegerField()
        bad_field.name = "required_int"
        bad_field.column = "required_int"
        bad_field.set_attributes_from_name("required_int")
        bad_field.model = cot.get_model()

        base_fields = _expected_base_fields(cot)
        base_fields["required_int"] = bad_field

        with patch(
            "netbox_custom_objects.mixin_migration._expected_base_fields",
            return_value=base_fields,
        ):
            result = heal_cot(cot, verbosity=0)

        self.assertEqual(result["added"], [])
        self.assertEqual(len(result["warned"]), 1)
        self.assertEqual(result["warned"][0]["type"], "new_non_nullable")
        self.assertEqual(result["warned"][0]["field"], "required_int")

    def test_snapshot_updated_after_addition(self):
        """schema_document['base_columns'] must be refreshed after columns are added."""
        cot = self.create_custom_object_type(name="hc_snap", slug="hc-snap")

        from django.db import models as dj_models
        extra_field = dj_models.CharField(max_length=10, null=True, blank=True)
        extra_field.name = "snap_col"
        extra_field.column = "snap_col"
        extra_field.set_attributes_from_name("snap_col")
        extra_field.model = cot.get_model()

        base_fields = _expected_base_fields(cot)
        base_fields["snap_col"] = extra_field

        with patch(
            "netbox_custom_objects.mixin_migration._expected_base_fields",
            return_value=base_fields,
        ):
            heal_cot(cot, verbosity=0)

        cot.refresh_from_db()
        names = {c["name"] for c in cot.schema_document.get("base_columns", [])}
        self.assertIn("snap_col", names)

        # Clean up
        with connection.schema_editor() as editor:
            editor.remove_field(cot.get_model(), extra_field)

    def test_removed_column_produces_warning_not_drop(self):
        """A column in schema_document['base_columns'] but removed from model must only warn."""
        cot = self.create_custom_object_type(name="hc_drop", slug="hc-drop")

        # Add ghost_col to the actual DB table so the heal checker sees it.
        # This simulates a column that was once part of a mixin but has since
        # been removed from the CustomObject base class.
        from django.db import models as dj_models
        ghost_field = dj_models.CharField(max_length=50, null=True, blank=True)
        ghost_field.name = "ghost_col"
        ghost_field.column = "ghost_col"
        ghost_field.set_attributes_from_name("ghost_col")
        ghost_field.model = cot.get_model()
        with connection.schema_editor() as editor:
            editor.add_field(cot.get_model(), ghost_field)

        # Record ghost_col in schema_document["base_columns"] as if it was
        # always a base column, but do NOT add it to _expected_base_fields
        # (it is absent from the patched expected set below).
        doc = cot.schema_document or {}
        doc["base_columns"] = list(doc.get("base_columns", [])) + [
            {"name": "ghost_col", "field_class": "CharField", "null": True}
        ]
        CustomObjectType.objects.filter(pk=cot.pk).update(schema_document=doc)
        cot.refresh_from_db()

        result = heal_cot(cot, verbosity=0)

        warned_types = [w["type"] for w in result["warned"]]
        self.assertIn("removed_from_model", warned_types)
        # Must not have tried to drop anything
        self.assertEqual(result["added"], [])

        # Clean up
        with connection.schema_editor() as editor:
            editor.remove_field(cot.get_model(), ghost_field)

    def test_type_change_detected_as_warning(self):
        """A column present in DB and model but with a changed field class must warn."""
        cot = self.create_custom_object_type(name="hc_type", slug="hc-type")

        # Seed schema_document to claim 'created' was originally an IntegerField
        # (in reality it's a DateTimeField).  heal_cot should detect the mismatch.
        doc = cot.schema_document or {}
        cols = {c["name"]: c for c in doc.get("base_columns", [])}
        if "created" in cols:
            cols["created"] = {"name": "created", "field_class": "IntegerField", "null": False}
        else:
            cols["created"] = {"name": "created", "field_class": "IntegerField", "null": False}
        doc["base_columns"] = list(cols.values())
        CustomObjectType.objects.filter(pk=cot.pk).update(schema_document=doc)
        cot.refresh_from_db()

        result = heal_cot(cot, verbosity=0)

        warned_types = [w["type"] for w in result["warned"]]
        self.assertIn("type_changed", warned_types)
        changed = next(w for w in result["warned"] if w["type"] == "type_changed")
        self.assertEqual(changed["field"], "created")

    # ------------------------------------------------------------------
    # dry_run mode
    # ------------------------------------------------------------------

    def test_dry_run_does_not_modify_db(self):
        """dry_run=True must report additions without touching the DB."""
        cot = self.create_custom_object_type(name="hc_dryrun", slug="hc-dryrun")
        table_name = cot.get_database_table_name()

        from django.db import models as dj_models
        extra_field = dj_models.CharField(max_length=10, null=True, blank=True)
        extra_field.name = "dry_col"
        extra_field.column = "dry_col"
        extra_field.set_attributes_from_name("dry_col")
        extra_field.model = cot.get_model()

        base_fields = _expected_base_fields(cot)
        base_fields["dry_col"] = extra_field

        with patch(
            "netbox_custom_objects.mixin_migration._expected_base_fields",
            return_value=base_fields,
        ):
            result = heal_cot(cot, verbosity=0, dry_run=True)

        # Column must be reported as would-be-added
        self.assertIn("dry_col", result["added"])

        # But must NOT exist in the actual DB
        with connection.cursor() as cur:
            actual = {
                c.name for c in connection.introspection.get_table_description(cur, table_name)
            }
        self.assertNotIn("dry_col", actual)

    def test_dry_run_does_not_update_snapshot(self):
        """dry_run=True must not update schema_document."""
        cot = self.create_custom_object_type(name="hc_drysn", slug="hc-drysn")
        original_doc = cot.schema_document

        from django.db import models as dj_models
        extra_field = dj_models.CharField(max_length=10, null=True, blank=True)
        extra_field.name = "dry_snap_col"
        extra_field.column = "dry_snap_col"
        extra_field.set_attributes_from_name("dry_snap_col")
        extra_field.model = cot.get_model()

        base_fields = _expected_base_fields(cot)
        base_fields["dry_snap_col"] = extra_field

        with patch(
            "netbox_custom_objects.mixin_migration._expected_base_fields",
            return_value=base_fields,
        ):
            heal_cot(cot, verbosity=0, dry_run=True)

        cot.refresh_from_db()
        self.assertEqual(cot.schema_document, original_doc)


# ---------------------------------------------------------------------------
# heal_all_cots()
# ---------------------------------------------------------------------------

class HealAllCotsTestCase(
    TransactionCleanupMixin, CustomObjectsTestCase, TransactionTestCase
):
    """Tests for heal_all_cots() summary behaviour."""

    def test_summary_total_matches_cot_count(self):
        for i in range(3):
            self.create_custom_object_type(name=f"hac_{i}", slug=f"hac-{i}")
        summary = heal_all_cots(verbosity=0)
        self.assertGreaterEqual(summary["total"], 3)

    def test_summary_healed_zero_when_no_drift(self):
        self.create_custom_object_type(name="hac_nd", slug="hac-nd")
        summary = heal_all_cots(verbosity=0)
        self.assertEqual(summary["healed"], 0)
        self.assertEqual(summary["warnings"], 0)

    def test_summary_keys_present(self):
        summary = heal_all_cots(verbosity=0)
        self.assertIn("total", summary)
        self.assertIn("healed", summary)
        self.assertIn("warnings", summary)


# ---------------------------------------------------------------------------
# Management command
# ---------------------------------------------------------------------------

class UpgradeCustomObjectsCommandTestCase(
    TransactionCleanupMixin, CustomObjectsTestCase, TransactionTestCase
):
    """Smoke tests for the upgrade_custom_objects management command."""

    def _call_command(self, *args, **kwargs):
        from django.core.management import call_command
        from io import StringIO
        out = StringIO()
        err = StringIO()
        call_command(
            "upgrade_custom_objects", *args, stdout=out, stderr=err, **kwargs
        )
        return out.getvalue(), err.getvalue()

    def test_command_runs_without_error(self):
        self.create_custom_object_type(name="cmd_basic", slug="cmd-basic")
        stdout, stderr = self._call_command(verbosity=0)
        # No exception means success; no DB errors in stderr
        self.assertNotIn("Error", stderr)

    def test_dry_run_flag_accepted(self):
        self.create_custom_object_type(name="cmd_dry", slug="cmd-dry")
        stdout, stderr = self._call_command("--dry-run", verbosity=1)
        self.assertIn("DRY RUN", stdout)

    def test_cot_flag_by_name(self):
        cot = self.create_custom_object_type(name="cmd_cot", slug="cmd-cot")
        stdout, _ = self._call_command("--cot", cot.name, verbosity=1)
        # Should succeed with no-drift message
        self.assertIn("no drift detected", stdout)

    def test_cot_flag_by_id(self):
        cot = self.create_custom_object_type(name="cmd_cotid", slug="cmd-cotid")
        stdout, _ = self._call_command("--cot", str(cot.pk), verbosity=1)
        self.assertIn("no drift detected", stdout)

    def test_unknown_cot_raises_error(self):
        from django.core.management.base import CommandError
        with self.assertRaises(CommandError):
            self._call_command("--cot", "nonexistent_cot_xyz")
