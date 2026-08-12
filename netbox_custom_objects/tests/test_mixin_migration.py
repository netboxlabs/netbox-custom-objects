"""
Tests for the mixin column drift detection and repair (issue #391, Phase 2).

Covers:
- _expected_base_fields(): returns the correct base fields, excludes user fields
- _can_auto_add(): correct classification of nullable / defaulted fields
- heal_cot(): detects missing columns, adds safe ones, warns on unsafe ones,
              records add_failed on schema_editor error, never drops,
              updates schema_document snapshot after healing,
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
from netbox_custom_objects.models import (
    CustomObjectType,
    CustomObjectTypeField,
    detect_backing_column_collisions,
)

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

    def test_add_field_failure_recorded_as_warning(self):
        """If schema_editor.add_field raises, the failure must appear in warned, not added."""
        cot = self.create_custom_object_type(name="hc_fail", slug="hc-fail")

        from django.db import models as dj_models
        bad_field = dj_models.CharField(max_length=10, null=True, blank=True)
        bad_field.name = "fail_col"
        bad_field.column = "fail_col"
        bad_field.set_attributes_from_name("fail_col")
        bad_field.model = cot.get_model()

        base_fields = _expected_base_fields(cot)
        base_fields["fail_col"] = bad_field

        with patch(
            "netbox_custom_objects.mixin_migration._expected_base_fields",
            return_value=base_fields,
        ), patch(
            "django.db.backends.base.schema.BaseDatabaseSchemaEditor.add_field",
            side_effect=Exception("simulated DB error"),
        ):
            result = heal_cot(cot, verbosity=0)

        self.assertNotIn("fail_col", result["added"])
        self.assertEqual(len(result["warned"]), 1)
        self.assertEqual(result["warned"][0]["type"], "add_failed")
        self.assertEqual(result["warned"][0]["field"], "fail_col")

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
# detect_backing_column_collisions() / heal_cot() collision surfacing
# ---------------------------------------------------------------------------

class BackingColumnCollisionTestCase(
    TransactionCleanupMixin, CustomObjectsTestCase, TransactionTestCase
):
    """
    Regression tests (PR #641 review) for pre-existing backing-column
    collisions: a plain field literally named "<url_field>_title" (or
    "<coord_field>_latitude"/"_longitude") could have been created before
    CustomObjectTypeField.clean()'s collision guard existed -- URLFieldType
    didn't expand into a second backing column until #496, so nothing
    stopped a sibling field from claiming that name first.  clean() blocks
    this combination for any *new* field going forward, but heal_cot() must
    also detect it for a COT that already has the collision baked in, since
    it's otherwise silently resolved by whichever field wins the attrs dict
    slot in CustomObjectType._fetch_and_generate_field_attrs() -- with no
    error raised anywhere.

    ``.objects.create()`` bypasses clean() (only full_clean() calls it), so
    creating the colliding pair directly reproduces a pre-validation COT
    without needing to disable or monkeypatch the guard.
    """

    def test_detects_pre_existing_url_title_collision(self):
        cot = self.create_custom_object_type(name="bcc_url", slug="bcc-url")
        self.create_custom_object_type_field(cot, name="website", label="Website", type="url")
        self.create_custom_object_type_field(
            cot, name="website_title", label="Website Title", type="text",
        )

        collisions = detect_backing_column_collisions(cot)

        self.assertEqual(len(collisions), 1)
        self.assertEqual(collisions[0]["field"], "website")
        self.assertEqual(collisions[0]["other"], "website_title")
        self.assertEqual(collisions[0]["column"], "website_title")
        # "website_title" (the plain field) is the one whose own name matches the
        # clashing column -- it's safe to rename; renaming "website" instead would
        # carry this same column along with it. See models.py's comment at the
        # collision-detection site for why.
        self.assertEqual(collisions[0]["safe_to_rename"], "website_title")
        self.assertIn("Rename 'website_title'", collisions[0]["message"])

    def test_safe_rename_preserves_sibling_data_and_resolves_collision(self):
        """
        Renaming the field identified by `safe_to_rename` (PR #641 review) must
        preserve that field's own data and leave the *other* field's column
        untouched -- proving the recovery guidance is actually safe to follow,
        not just that the collision is detected.
        """
        cot = self.create_custom_object_type(name="bcc_recover", slug="bcc-recover")
        self.create_custom_object_type_field(cot, name="name", label="Name", type="text", primary=True)
        self.create_custom_object_type_field(
            cot, name="website", label="Website", type="url",
        )
        plain_field = self.create_custom_object_type_field(
            cot, name="website_title", label="Website Title", type="text", required=False,
        )

        collisions = detect_backing_column_collisions(cot)
        self.assertEqual(collisions[0]["safe_to_rename"], "website_title")

        # Write a known value directly to the shared physical column, bypassing
        # the ORM: with two fields mapped to the same attribute, only whichever
        # was processed last in _fetch_and_generate_field_attrs() is reachable
        # through the model, so this is the only unambiguous way to establish
        # "the plain field's own data" independent of that resolution order.
        model = cot.get_model()
        # website_title="" sidesteps a quirk that's itself a symptom of the collision:
        # the physical column was created NOT NULL DEFAULT '' by the url field (created
        # first; see URLFieldType.get_model_field()'s comment on why its title column
        # isn't null=True), but the plain field's own definition -- which won the model
        # attrs slot since it was created second -- believes the column is nullable, so
        # create() with no value for it would try to insert NULL and violate that
        # constraint. Overwritten via raw SQL immediately below regardless.
        instance = model.objects.create(
            name="Test Object", website="https://example.com/", website_title="",
        )
        with connection.cursor() as cursor:
            cursor.execute(
                f'UPDATE {model._meta.db_table} SET website_title = %s WHERE id = %s',
                ["plain field's own value", instance.pk],
            )

        # The safe rename: reload from DB so the rename path has the original
        # snapshot, exactly as the edit view would (see test_schema_operations.py).
        plain_field = CustomObjectTypeField.objects.get(pk=plain_field.pk)
        plain_field.name = "description"
        plain_field.save()

        # Collision resolved, and the plain field's data followed it to the new
        # column name rather than being left behind or overwritten.
        self.assertEqual(detect_backing_column_collisions(cot), [])
        model = cot.get_model(no_cache=True)
        columns = {
            col.name for col in connection.introspection.get_table_description(
                connection.cursor(), model._meta.db_table,
            )
        }
        self.assertIn("description", columns)
        # The rename also carried the physical column away from "website_title" --
        # the url field's title sub-column name is derived from its own (unchanged)
        # name, not stored, so nothing renamed *it* back into existence. This is
        # exactly why the guidance says to re-run the heal afterward: it re-adds
        # "website_title" fresh (nullable, default ''), now unambiguously the url
        # field's alone.
        self.assertNotIn("website_title", columns)
        heal_cot(cot, verbosity=0)
        model = cot.get_model(no_cache=True)
        columns = {
            col.name for col in connection.introspection.get_table_description(
                connection.cursor(), model._meta.db_table,
            )
        }
        self.assertIn("website_title", columns)

        # Re-fetched via the post-heal model class: `instance` was built from an
        # earlier class that no longer matches the current columns.
        instance = model.objects.get(pk=instance.pk)
        self.assertEqual(instance.description, "plain field's own value")

        # The url field's own title sub-column is now unambiguously its own --
        # setting it doesn't touch (or get confused with) the renamed sibling.
        instance.website_title = "My Website"
        instance.save()
        instance = model.objects.get(pk=instance.pk)
        self.assertEqual(instance.website_title, "My Website")
        self.assertEqual(instance.description, "plain field's own value")

    def test_detects_pre_existing_coordinates_collision(self):
        cot = self.create_custom_object_type(name="bcc_coord", slug="bcc-coord")
        self.create_custom_object_type_field(cot, name="loc", label="Location", type="coordinates")
        self.create_custom_object_type_field(
            cot, name="loc_latitude", label="Loc Latitude", type="decimal",
        )

        collisions = detect_backing_column_collisions(cot)

        self.assertEqual(len(collisions), 1)
        self.assertEqual(collisions[0]["column"], "loc_latitude")

    def test_no_collision_for_normal_url_field(self):
        cot = self.create_custom_object_type(name="bcc_ok", slug="bcc-ok")
        self.create_custom_object_type_field(cot, name="website", label="Website", type="url")

        self.assertEqual(detect_backing_column_collisions(cot), [])

    def test_no_collision_between_two_plain_fields(self):
        cot = self.create_custom_object_type(name="bcc_plain", slug="bcc-plain")
        self.create_custom_object_type_field(cot, name="alpha", type="text")
        self.create_custom_object_type_field(cot, name="beta", type="text")

        self.assertEqual(detect_backing_column_collisions(cot), [])

    def test_heal_cot_surfaces_collision_as_warning(self):
        """heal_cot() must report the collision, not silently proceed."""
        cot = self.create_custom_object_type(name="bcc_heal", slug="bcc-heal")
        self.create_custom_object_type_field(cot, name="website", label="Website", type="url")
        self.create_custom_object_type_field(
            cot, name="website_title", label="Website Title", type="text",
        )

        result = heal_cot(cot, verbosity=0)

        warned_types = [w["type"] for w in result["warned"]]
        self.assertIn("backing_column_collision", warned_types)
        entry = next(w for w in result["warned"] if w["type"] == "backing_column_collision")
        self.assertEqual(entry["field"], "website")


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
        stdout, _ = self._call_command("--cot", cot.name, verbosity=2)
        # "no drift detected" is only printed at verbosity >= 2
        self.assertIn("no drift detected", stdout)

    def test_cot_flag_by_id(self):
        cot = self.create_custom_object_type(name="cmd_cotid", slug="cmd-cotid")
        stdout, _ = self._call_command("--cot", str(cot.pk), verbosity=2)
        self.assertIn("no drift detected", stdout)

    def test_unknown_cot_raises_error(self):
        from django.core.management.base import CommandError
        with self.assertRaises(CommandError):
            self._call_command("--cot", "nonexistent_cot_xyz")
