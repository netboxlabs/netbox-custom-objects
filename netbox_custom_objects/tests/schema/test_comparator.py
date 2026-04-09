"""
Tests for the COT state comparator / diff engine (issue #387).

Covers:
- No-change (clean) diff
- New COT (not in DB)
- COT-level attribute changes
- Field ADD (in schema, not in DB)
- Field REMOVE (tombstoned; still in DB)
- Field ALTER: rename, type change, scalar attribute changes
- Field ALTER: choice_set change
- Field ALTER: related_object_type change (built-in and custom)
- Field ALTER: related_object_filter change
- Untracked fields (no schema_id) → warning, not REMOVE
- DB field absent from schema AND not tombstoned → warning, not REMOVE
- _encode_related_object_type deleted-COT path → warning + stable fallback string
- Multi-COT document (including empty/missing types key)
- has_changes / has_destructive_changes / adds / removes / alters helpers
"""

from django.test import TestCase

from netbox_custom_objects.schema.comparator import (
    FieldOp,
    _encode_related_object_type,
    diff_cot,
    diff_document,
)
from netbox_custom_objects.schema.exporter import export_cot, export_cots
from netbox_custom_objects.models import CustomObjectTypeField

from ..base import CustomObjectsTestCase


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

class ComparatorCleanDiffTestCase(CustomObjectsTestCase, TestCase):
    """Round-trip: export → diff should produce no changes."""

    @classmethod
    def setUpTestData(cls):
        cls.cot = cls.create_custom_object_type(
            name='clean', slug='clean', version='1.0.0',
        )
        cls.choice_set = cls.create_choice_set(name='Clean Choices')
        cls.device_ot = cls.get_device_object_type()

        cls.create_custom_object_type_field(cls.cot, name='label', type='text')
        cls.create_custom_object_type_field(
            cls.cot, name='count', type='integer',
            validation_minimum=0, validation_maximum=100,
        )
        cls.create_custom_object_type_field(
            cls.cot, name='status', type='select', choice_set=cls.choice_set,
        )
        cls.create_custom_object_type_field(
            cls.cot, name='device', type='object',
            related_object_type=cls.device_ot,
        )

    def test_clean_diff_has_no_changes(self):
        type_def = export_cot(self.cot)
        result = diff_cot(type_def)
        self.assertFalse(result.has_changes)
        self.assertEqual(result.field_changes, [])
        self.assertEqual(result.cot_changes, {})

    def test_clean_diff_document_has_no_changes(self):
        doc = export_cots([self.cot])
        diffs = diff_document(doc)
        self.assertEqual(len(diffs), 1)
        self.assertFalse(diffs[0].has_changes)

    def test_no_warnings_for_fully_tracked_cot(self):
        type_def = export_cot(self.cot)
        result = diff_cot(type_def)
        self.assertEqual(result.warnings, [])


class ComparatorNewCOTTestCase(CustomObjectsTestCase, TestCase):
    """COT not yet in DB → is_new=True, all fields are ADD."""

    def test_new_cot_is_new(self):
        result = diff_cot({
            "name": "newtype",
            "slug": "newtype",
            "fields": [
                {"id": 1, "name": "label", "type": "text"},
                {"id": 2, "name": "count", "type": "integer"},
            ],
        })
        self.assertTrue(result.is_new)

    def test_new_cot_all_fields_are_add(self):
        result = diff_cot({
            "name": "newtype2",
            "slug": "newtype2",
            "fields": [
                {"id": 1, "name": "label", "type": "text"},
                {"id": 2, "name": "count", "type": "integer"},
            ],
        })
        self.assertEqual(len(result.field_changes), 2)
        for fc in result.field_changes:
            self.assertIs(fc.op, FieldOp.ADD)

    def test_new_cot_no_fields_no_field_changes(self):
        result = diff_cot({"name": "empty", "slug": "empty"})
        self.assertTrue(result.is_new)
        self.assertEqual(result.field_changes, [])

    def test_missing_required_keys_raises_value_error(self):
        with self.assertRaises(ValueError):
            diff_cot({"name": "no-slug"})
        with self.assertRaises(ValueError):
            diff_cot({"slug": "no-name"})
        with self.assertRaises(ValueError):
            diff_cot({})


class ComparatorCOTAttrsTestCase(CustomObjectsTestCase, TestCase):
    """COT-level attribute changes are captured in cot_changes."""

    @classmethod
    def setUpTestData(cls):
        cls.cot = cls.create_custom_object_type(
            name='attrtest', slug='attr-test',
            version='1.0.0', description='old desc',
            verbose_name='', verbose_name_plural='',
        )

    def test_version_change_detected(self):
        type_def = export_cot(self.cot)
        type_def["version"] = "2.0.0"
        result = diff_cot(type_def)
        self.assertIn("version", result.cot_changes)
        self.assertEqual(result.cot_changes["version"], ("1.0.0", "2.0.0"))

    def test_description_change_detected(self):
        type_def = export_cot(self.cot)
        type_def["description"] = "new desc"
        result = diff_cot(type_def)
        self.assertIn("description", result.cot_changes)

    def test_unchanged_cot_attrs_not_in_cot_changes(self):
        type_def = export_cot(self.cot)
        result = diff_cot(type_def)
        self.assertNotIn("version", result.cot_changes)
        self.assertNotIn("description", result.cot_changes)

    def test_cot_changes_do_not_produce_field_changes(self):
        type_def = export_cot(self.cot)
        type_def["version"] = "9.9.9"
        result = diff_cot(type_def)
        self.assertEqual(result.field_changes, [])


class ComparatorFieldAddTestCase(CustomObjectsTestCase, TestCase):
    """Fields present in schema but absent from DB → ADD."""

    @classmethod
    def setUpTestData(cls):
        cls.cot = cls.create_custom_object_type(name='addtest', slug='add-test')
        cls.create_custom_object_type_field(cls.cot, name='existing', type='text')

    def test_new_field_in_schema_is_add(self):
        type_def = export_cot(self.cot)
        # Append a field with a new schema_id that doesn't exist in DB
        type_def.setdefault("fields", []).append(
            {"id": 999, "name": "brand_new", "type": "boolean"}
        )
        result = diff_cot(type_def)
        adds = result.adds
        self.assertEqual(len(adds), 1)
        self.assertEqual(adds[0].schema_id, 999)
        self.assertIsNone(adds[0].db_name)
        self.assertEqual(adds[0].schema_def["name"], "brand_new")

    def test_add_does_not_affect_existing_fields(self):
        type_def = export_cot(self.cot)
        type_def.setdefault("fields", []).append(
            {"id": 999, "name": "extra", "type": "text"}
        )
        result = diff_cot(type_def)
        # existing field should not appear in changes
        self.assertEqual(len(result.alters), 0)


class ComparatorFieldRemoveTestCase(CustomObjectsTestCase, TestCase):
    """Fields tombstoned in schema and still in DB → REMOVE."""

    @classmethod
    def setUpTestData(cls):
        cls.cot = cls.create_custom_object_type(name='removetest', slug='remove-test')
        cls.field = cls.create_custom_object_type_field(
            cls.cot, name='to_remove', type='text'
        )

    def test_tombstoned_db_field_is_remove(self):
        field_schema_id = self.field.schema_id
        type_def = {
            "name": self.cot.name,
            "slug": self.cot.slug,
            "fields": [],
            "removed_fields": [
                {"id": field_schema_id, "name": "to_remove", "type": "text"}
            ],
        }
        result = diff_cot(type_def)
        removes = result.removes
        self.assertEqual(len(removes), 1)
        self.assertEqual(removes[0].schema_id, field_schema_id)
        self.assertEqual(removes[0].db_name, "to_remove")

    def test_remove_is_flagged_as_destructive(self):
        field_schema_id = self.field.schema_id
        type_def = {
            "name": self.cot.name,
            "slug": self.cot.slug,
            "fields": [],
            "removed_fields": [
                {"id": field_schema_id, "name": "to_remove", "type": "text"}
            ],
        }
        result = diff_cot(type_def)
        self.assertTrue(result.has_destructive_changes)

    def test_already_removed_from_db_is_noop(self):
        """If tombstoned field is already gone from DB, no REMOVE emitted."""
        type_def = {
            "name": self.cot.name,
            "slug": self.cot.slug,
            "fields": [],
            "removed_fields": [
                {"id": 9999, "name": "ghost", "type": "text"}  # not in DB
            ],
        }
        result = diff_cot(type_def)
        self.assertEqual(result.removes, [])
        self.assertFalse(result.has_destructive_changes)


class ComparatorFieldAlterTestCase(CustomObjectsTestCase, TestCase):
    """Attribute changes on existing fields → ALTER."""

    @classmethod
    def setUpTestData(cls):
        cls.cot = cls.create_custom_object_type(name='altertest', slug='alter-test')
        cls.choice_set_a = cls.create_choice_set(name='Set A')
        cls.choice_set_b = cls.create_choice_set(name='Set B')
        cls.device_ot = cls.get_device_object_type()
        cls.site_ot = cls.get_site_object_type()

    def _alter_field(self, field_name, **overrides):
        """Export the COT, override one field's schema dict, return diff."""
        type_def = export_cot(self.cot)
        for sf in type_def.get("fields", []):
            if sf["name"] == field_name:
                sf.update(overrides)
                break
        else:
            raise AssertionError(f"field {field_name!r} not found in exported schema")
        return diff_cot(type_def)

    def test_rename_detected(self):
        self.create_custom_object_type_field(self.cot, name='old_name', type='text')
        result = self._alter_field("old_name", name="new_name")
        fc = next(fc for fc in result.alters if fc.db_name == "old_name")
        self.assertTrue(fc.is_rename)
        self.assertEqual(fc.changed_attrs["name"], ("old_name", "new_name"))

    def test_type_change_detected(self):
        self.create_custom_object_type_field(self.cot, name='typed', type='text')
        result = self._alter_field("typed", type="longtext")
        fc = next(fc for fc in result.alters if fc.db_name == "typed")
        self.assertTrue(fc.is_type_change)
        self.assertIn("type", fc.changed_attrs)

    def test_required_change_detected(self):
        self.create_custom_object_type_field(
            self.cot, name='req_field', type='text', required=False
        )
        result = self._alter_field("req_field", required=True)
        fc = next(fc for fc in result.alters if fc.db_name == "req_field")
        self.assertIn("required", fc.changed_attrs)
        self.assertEqual(fc.changed_attrs["required"], (False, True))

    def test_weight_change_detected(self):
        self.create_custom_object_type_field(
            self.cot, name='weighted', type='text', weight=100
        )
        result = self._alter_field("weighted", weight=200)
        fc = next(fc for fc in result.alters if fc.db_name == "weighted")
        self.assertIn("weight", fc.changed_attrs)

    def test_validation_regex_change_detected(self):
        self.create_custom_object_type_field(
            self.cot, name='regex_field', type='text',
            validation_regex=r'^[A-Z]+$'
        )
        result = self._alter_field("regex_field", validation_regex=r'^\d+$')
        fc = next(fc for fc in result.alters if fc.db_name == "regex_field")
        self.assertIn("validation_regex", fc.changed_attrs)

    def test_validation_min_max_change_detected(self):
        self.create_custom_object_type_field(
            self.cot, name='numeric', type='integer',
            validation_minimum=0, validation_maximum=100
        )
        result = self._alter_field("numeric", validation_minimum=10, validation_maximum=200)
        fc = next(fc for fc in result.alters if fc.db_name == "numeric")
        self.assertIn("validation_minimum", fc.changed_attrs)
        self.assertIn("validation_maximum", fc.changed_attrs)

    def test_choice_set_change_detected(self):
        self.create_custom_object_type_field(
            self.cot, name='select_field', type='select',
            choice_set=self.choice_set_a,
        )
        result = self._alter_field("select_field", choice_set="Set B")
        fc = next(fc for fc in result.alters if fc.db_name == "select_field")
        self.assertIn("choice_set", fc.changed_attrs)
        self.assertEqual(fc.changed_attrs["choice_set"], ("Set A", "Set B"))

    def test_related_object_type_change_detected(self):
        self.create_custom_object_type_field(
            self.cot, name='obj_field', type='object',
            related_object_type=self.device_ot,
        )
        result = self._alter_field("obj_field", related_object_type="dcim/site")
        fc = next(fc for fc in result.alters if fc.db_name == "obj_field")
        self.assertIn("related_object_type", fc.changed_attrs)
        self.assertEqual(fc.changed_attrs["related_object_type"][0], "dcim/device")
        self.assertEqual(fc.changed_attrs["related_object_type"][1], "dcim/site")

    def test_related_object_type_custom_cot_encoding(self):
        other_cot = self.create_custom_object_type(name='rack', slug='rack')
        rack_ot = other_cot.object_type
        self.create_custom_object_type_field(
            self.cot, name='rack_field', type='object',
            related_object_type=rack_ot,
        )
        # Export and re-diff — should be clean (no change)
        type_def = export_cot(self.cot)
        result = diff_cot(type_def)
        rack_alters = [fc for fc in result.alters if fc.db_name == "rack_field"]
        self.assertEqual(rack_alters, [], "Custom COT related_object_type round-trips cleanly")

    def test_related_object_filter_change_detected(self):
        self.create_custom_object_type_field(
            self.cot, name='filtered', type='object',
            related_object_type=self.device_ot,
            related_object_filter={"site_id": [1]},
        )
        result = self._alter_field("filtered", related_object_filter={"site_id": [2]})
        fc = next(fc for fc in result.alters if fc.db_name == "filtered")
        self.assertIn("related_object_filter", fc.changed_attrs)

    def test_no_alter_when_nothing_changed(self):
        self.create_custom_object_type_field(
            self.cot, name='unchanged', type='text'
        )
        type_def = export_cot(self.cot)
        result = diff_cot(type_def)
        unchanged_alters = [fc for fc in result.alters if fc.db_name == "unchanged"]
        self.assertEqual(unchanged_alters, [])

    def test_deprecated_change_detected(self):
        self.create_custom_object_type_field(
            self.cot, name='dep_field', type='text'
        )
        result = self._alter_field(
            "dep_field",
            deprecated=True,
            deprecated_since="1.1.0",
            scheduled_removal="2.0.0",
        )
        fc = next(fc for fc in result.alters if fc.db_name == "dep_field")
        self.assertIn("deprecated", fc.changed_attrs)
        self.assertIn("deprecated_since", fc.changed_attrs)
        self.assertIn("scheduled_removal", fc.changed_attrs)


class ComparatorWarningsTestCase(CustomObjectsTestCase, TestCase):
    """Warning conditions: untracked fields, ambiguous absences."""

    @classmethod
    def setUpTestData(cls):
        cls.cot = cls.create_custom_object_type(name='warntest', slug='warn-test')

    def test_untracked_field_emits_warning(self):
        f = self.create_custom_object_type_field(
            self.cot, name='untracked', type='text'
        )
        CustomObjectTypeField.objects.filter(pk=f.pk).update(schema_id=None)
        type_def = export_cot(self.cot)  # won't include the untracked field
        result = diff_cot(type_def)
        self.assertTrue(
            any("untracked" in w for w in result.warnings),
            "Expected warning about field with no schema_id",
        )

    def test_untracked_field_not_in_field_changes(self):
        f = self.create_custom_object_type_field(
            self.cot, name='untracked2', type='text'
        )
        CustomObjectTypeField.objects.filter(pk=f.pk).update(schema_id=None)
        type_def = export_cot(self.cot)
        result = diff_cot(type_def)
        self.assertEqual(result.field_changes, [])

    def test_encode_related_object_type_deleted_cot_emits_warning(self):
        """_encode_related_object_type emits a warning and returns a stable fallback string when
        the referenced CustomObjectType no longer exists (slug not in cache).
        This is the only warning path in comparator.py that can't be triggered
        via diff_cot because CustomObjectType.delete() removes referencing fields
        before deleting the ObjectType, making the state unreachable via normal
        model deletion.
        """
        target = self.create_custom_object_type(name='rot-target', slug='rot-target')
        rot = target.object_type  # ObjectType with app_label matching constants.APP_LABEL
        warnings = []
        result = _encode_related_object_type(rot, cot_slug_cache={}, warnings=warnings)
        self.assertEqual(len(warnings), 1)
        self.assertIn("no longer exists", warnings[0])
        self.assertIn("<deleted:", result)

    def test_db_field_absent_from_schema_and_not_tombstoned_emits_warning(self):
        self.create_custom_object_type_field(
            self.cot, name='mystery', type='text'
        )
        # Schema with empty fields (field not tombstoned, just absent)
        type_def = {
            "name": self.cot.name,
            "slug": self.cot.slug,
            "fields": [],
        }
        result = diff_cot(type_def)
        self.assertTrue(
            any("mystery" in w for w in result.warnings),
            "Expected warning about field absent from schema but not tombstoned",
        )
        # Must NOT be a REMOVE
        self.assertFalse(result.has_destructive_changes)


class ComparatorMultiCOTTestCase(CustomObjectsTestCase, TestCase):
    """diff_document processes all COTs in the document."""

    @classmethod
    def setUpTestData(cls):
        cls.cot1 = cls.create_custom_object_type(name='multi1', slug='multi-1')
        cls.cot2 = cls.create_custom_object_type(name='multi2', slug='multi-2')
        cls.create_custom_object_type_field(cls.cot1, name='f1', type='text')
        cls.create_custom_object_type_field(cls.cot2, name='f2', type='text')

    def test_document_diff_returns_one_result_per_type(self):
        doc = export_cots([self.cot1, self.cot2])
        diffs = diff_document(doc)
        self.assertEqual(len(diffs), 2)

    def test_document_diff_clean_round_trip(self):
        doc = export_cots([self.cot1, self.cot2])
        diffs = diff_document(doc)
        for d in diffs:
            self.assertFalse(d.has_changes)

    def test_document_diff_mixed_new_and_existing(self):
        doc = export_cots([self.cot1])
        doc["types"].append({"name": "brandnew", "slug": "brand-new"})
        diffs = diff_document(doc)
        self.assertEqual(len(diffs), 2)
        existing = next(d for d in diffs if d.slug == "multi-1")
        new_ = next(d for d in diffs if d.slug == "brand-new")
        self.assertFalse(existing.is_new)
        self.assertTrue(new_.is_new)

    def test_document_missing_types_key_returns_empty(self):
        self.assertEqual(diff_document({}), [])

    def test_document_empty_types_list_returns_empty(self):
        self.assertEqual(diff_document({"types": []}), [])


class ComparatorHelperPropertiesTestCase(CustomObjectsTestCase, TestCase):
    """has_changes, has_destructive_changes, adds/removes/alters helpers."""

    @classmethod
    def setUpTestData(cls):
        cls.cot = cls.create_custom_object_type(name='helpers', slug='helpers')
        cls.field = cls.create_custom_object_type_field(
            cls.cot, name='h1', type='text'
        )

    def test_has_changes_false_when_clean(self):
        type_def = export_cot(self.cot)
        result = diff_cot(type_def)
        self.assertFalse(result.has_changes)

    def test_has_changes_true_when_field_added(self):
        type_def = export_cot(self.cot)
        type_def.setdefault("fields", []).append(
            {"id": 999, "name": "extra", "type": "text"}
        )
        result = diff_cot(type_def)
        self.assertTrue(result.has_changes)

    def test_has_destructive_changes_false_when_no_removes(self):
        type_def = export_cot(self.cot)
        result = diff_cot(type_def)
        self.assertFalse(result.has_destructive_changes)

    def test_adds_removes_alters_filters_correctly(self):
        fid = self.field.schema_id
        type_def = {
            "name": self.cot.name,
            "slug": self.cot.slug,
            "fields": [
                {"id": fid, "name": "h1", "type": "text", "required": True},  # alter
                {"id": 888, "name": "new_one", "type": "boolean"},              # add
            ],
            "removed_fields": [],
        }
        result = diff_cot(type_def)
        self.assertEqual(len(result.adds), 1)
        self.assertEqual(result.adds[0].schema_id, 888)
        self.assertEqual(len(result.alters), 1)
        self.assertEqual(result.alters[0].schema_id, fid)
        self.assertEqual(result.removes, [])
