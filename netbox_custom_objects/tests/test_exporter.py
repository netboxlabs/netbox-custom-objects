"""
Tests for the COT schema exporter (issue #388).

Covers:
- Minimal and full COT serialisation
- Default-value elision
- Encoding of built-in and custom related_object_type values
- choice_set serialisation
- Deprecated fields included in 'fields' list
- Fields without schema_id are skipped (warning emitted)
- Tombstones read from schema_document
- Multi-type document structure
- Output validates against cot_schema_v1.json
"""
import unittest
from pathlib import Path

from django.test import TestCase, TransactionTestCase

from netbox_custom_objects.exporter import export_cot, export_cots
from netbox_custom_objects.models import CustomObjectTypeField
from netbox_custom_objects.schema_format import SCHEMA_FORMAT_VERSION

from .base import CustomObjectsTestCase, TransactionCleanupMixin

try:
    import jsonschema
    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False

_SCHEMA_PATH = (
    Path(__file__).resolve().parent.parent / "schemas" / "cot_schema_v1.json"
)


# ===========================================================================
# Helpers
# ===========================================================================

def _field_by_name(exported_cot: dict, name: str) -> dict:
    """Return the exported field dict with the given name, or raise."""
    for f in exported_cot.get("fields", []):
        if f["name"] == name:
            return f
    raise KeyError(f"No exported field named {name!r}")


# ===========================================================================
# Tests
# ===========================================================================

class ExporterBasicTestCase(CustomObjectsTestCase, TestCase):
    """Basic structure and minimal-output tests."""

    @classmethod
    def setUpTestData(cls):
        cls.cot = cls.create_custom_object_type(
            name='widget',
            slug='widget',
            description='A widget',
            verbose_name='Widget',
            verbose_name_plural='Widgets',
            version='1.0.0',
            group_name='inventory',
        )
        cls.field = cls.create_custom_object_type_field(
            cls.cot,
            name='label',
            type='text',
            label='',        # will be omitted (default)
            required=False,  # will be omitted (default)
        )

    def test_top_level_structure(self):
        doc = export_cots([self.cot])
        self.assertEqual(doc["schema_version"], SCHEMA_FORMAT_VERSION)
        self.assertIsInstance(doc["types"], list)
        self.assertEqual(len(doc["types"]), 1)

    def test_cot_required_keys(self):
        cot_def = export_cot(self.cot)
        self.assertEqual(cot_def["name"], "widget")
        self.assertEqual(cot_def["slug"], "widget")

    def test_optional_cot_attrs_included_when_set(self):
        cot_def = export_cot(self.cot)
        self.assertEqual(cot_def["description"], "A widget")
        self.assertEqual(cot_def["verbose_name"], "Widget")
        self.assertEqual(cot_def["verbose_name_plural"], "Widgets")
        self.assertEqual(cot_def["version"], "1.0.0")
        self.assertEqual(cot_def["group_name"], "inventory")

    def test_optional_cot_attrs_omitted_when_blank(self):
        bare = self.create_custom_object_type(
            name='bare', slug='bare',
            description='', verbose_name='', verbose_name_plural='',
            version='', group_name='',
        )
        cot_def = export_cot(bare)
        for key in ("description", "verbose_name", "verbose_name_plural",
                    "version", "group_name"):
            self.assertNotIn(key, cot_def)

    def test_field_required_keys_present(self):
        f = _field_by_name(export_cot(self.cot), "label")
        self.assertIn("id", f)
        self.assertEqual(f["name"], "label")
        self.assertEqual(f["type"], "text")

    def test_field_schema_id_matches_model(self):
        f = _field_by_name(export_cot(self.cot), "label")
        self.assertEqual(f["id"], self.field.schema_id)

    def test_fields_ordered_by_schema_id(self):
        cot = self.create_custom_object_type(name='ordered', slug='ordered')
        self.create_custom_object_type_field(cot, name='first', type='text')
        self.create_custom_object_type_field(cot, name='second', type='text')
        self.create_custom_object_type_field(cot, name='third', type='text')
        exported = export_cot(cot)
        ids = [f["id"] for f in exported["fields"]]
        self.assertEqual(ids, sorted(ids))

    def test_no_fields_key_when_no_exportable_fields(self):
        bare = self.create_custom_object_type(name='nofields', slug='no-fields')
        cot_def = export_cot(bare)
        self.assertNotIn("fields", cot_def)

    def test_multi_type_document(self):
        cot2 = self.create_custom_object_type(name='gadget', slug='gadget')
        doc = export_cots([self.cot, cot2])
        names = [t["name"] for t in doc["types"]]
        self.assertIn("widget", names)
        self.assertIn("gadget", names)


class ExporterDefaultElisionTestCase(CustomObjectsTestCase, TestCase):
    """Default values must be omitted; non-defaults must be present."""

    @classmethod
    def setUpTestData(cls):
        cls.cot = cls.create_custom_object_type(name='elide', slug='elide')
        cls.default_field = cls.create_custom_object_type_field(
            cls.cot, name='plain', type='text',
            label='',           # default — omit
            description='',     # default — omit
            required=False,     # default — omit
            primary=False,      # default — omit
            weight=100,         # default — omit
            search_weight=500,  # default — omit
        )
        cls.non_default_field = cls.create_custom_object_type_field(
            cls.cot, name='special', type='text',
            label='Special Label',
            description='A description',
            required=True,
            primary=True,
            weight=200,
            search_weight=1000,
            filter_logic='exact',
            ui_visible='if-set',
            ui_editable='no',
            is_cloneable=True,
        )

    def test_default_attrs_omitted(self):
        f = _field_by_name(export_cot(self.cot), "plain")
        for key in ("label", "description", "required", "primary",
                    "weight", "search_weight"):
            self.assertNotIn(key, f, f"{key!r} should be omitted when equal to default")

    def test_non_default_attrs_included(self):
        f = _field_by_name(export_cot(self.cot), "special")
        self.assertEqual(f["label"], "Special Label")
        self.assertEqual(f["description"], "A description")
        self.assertTrue(f["required"])
        self.assertTrue(f["primary"])
        self.assertEqual(f["weight"], 200)
        self.assertEqual(f["search_weight"], 1000)
        self.assertEqual(f["filter_logic"], "exact")
        self.assertEqual(f["ui_visible"], "if-set")
        self.assertEqual(f["ui_editable"], "no")
        self.assertTrue(f["is_cloneable"])

    def test_default_value_omitted_when_null(self):
        """field.default=None (the default) must not appear in export."""
        f = _field_by_name(export_cot(self.cot), "plain")
        self.assertNotIn("default", f)

    def test_non_null_default_value_included(self):
        cot = self.create_custom_object_type(name='withdefault', slug='with-default')
        self.create_custom_object_type_field(
            cot, name='active', type='boolean', default=True
        )
        f = _field_by_name(export_cot(cot), "active")
        self.assertIn("default", f)
        self.assertTrue(f["default"])


class ExporterFieldTypesTestCase(CustomObjectsTestCase, TestCase):
    """Type-specific attribute serialisation."""

    @classmethod
    def setUpTestData(cls):
        cls.cot = cls.create_custom_object_type(name='typed', slug='typed')
        cls.choice_set = cls.create_choice_set(name='Status Choices')
        cls.device_ot = cls.get_device_object_type()

    def test_text_field_validation_regex_included(self):
        self.create_custom_object_type_field(
            self.cot, name='code', type='text', validation_regex=r'^[A-Z]{3}$'
        )
        f = _field_by_name(export_cot(self.cot), "code")
        self.assertEqual(f["validation_regex"], r'^[A-Z]{3}$')

    def test_text_field_no_regex_omitted(self):
        self.create_custom_object_type_field(
            self.cot, name='note', type='text'
        )
        f = _field_by_name(export_cot(self.cot), "note")
        self.assertNotIn("validation_regex", f)

    def test_integer_field_min_max_included(self):
        self.create_custom_object_type_field(
            self.cot, name='count', type='integer',
            validation_minimum=0, validation_maximum=999
        )
        f = _field_by_name(export_cot(self.cot), "count")
        self.assertEqual(f["validation_minimum"], 0)
        self.assertEqual(f["validation_maximum"], 999)

    def test_integer_field_no_min_max_omitted(self):
        self.create_custom_object_type_field(
            self.cot, name='qty', type='integer'
        )
        f = _field_by_name(export_cot(self.cot), "qty")
        self.assertNotIn("validation_minimum", f)
        self.assertNotIn("validation_maximum", f)

    def test_select_field_choice_set_name(self):
        self.create_custom_object_type_field(
            self.cot, name='status', type='select', choice_set=self.choice_set
        )
        f = _field_by_name(export_cot(self.cot), "status")
        self.assertEqual(f["choice_set"], "Status Choices")

    def test_object_field_builtin_encoding(self):
        self.create_custom_object_type_field(
            self.cot, name='device', type='object',
            related_object_type=self.device_ot
        )
        f = _field_by_name(export_cot(self.cot), "device")
        self.assertEqual(f["related_object_type"], "dcim/device")

    def test_object_field_custom_cot_encoding(self):
        other = self.create_custom_object_type(name='rack', slug='rack')
        rack_ot = other.object_type
        self.create_custom_object_type_field(
            self.cot, name='rack', type='object',
            related_object_type=rack_ot
        )
        f = _field_by_name(export_cot(self.cot), "rack")
        self.assertEqual(f["related_object_type"], "custom-objects/rack")

    def test_object_field_filter_included_when_set(self):
        self.create_custom_object_type_field(
            self.cot, name='filtered_device', type='object',
            related_object_type=self.device_ot,
            related_object_filter={"site_id": [1, 2]}
        )
        f = _field_by_name(export_cot(self.cot), "filtered_device")
        self.assertEqual(f["related_object_filter"], {"site_id": [1, 2]})

    def test_object_field_filter_omitted_when_null(self):
        self.create_custom_object_type_field(
            self.cot, name='unfiltered', type='object',
            related_object_type=self.device_ot
        )
        f = _field_by_name(export_cot(self.cot), "unfiltered")
        self.assertNotIn("related_object_filter", f)

    def test_type_specific_attrs_not_leaked_across_types(self):
        """A boolean field must not carry validation_regex or choice_set."""
        self.create_custom_object_type_field(
            self.cot, name='flag', type='boolean'
        )
        f = _field_by_name(export_cot(self.cot), "flag")
        for spurious in ("validation_regex", "validation_minimum",
                         "validation_maximum", "choice_set",
                         "related_object_type", "related_object_filter"):
            self.assertNotIn(spurious, f)


class ExporterDeprecationTestCase(CustomObjectsTestCase, TestCase):
    """Deprecated fields are exported in 'fields', not removed_fields."""

    @classmethod
    def setUpTestData(cls):
        cls.cot = cls.create_custom_object_type(name='lifecycle', slug='lifecycle')
        cls.create_custom_object_type_field(cls.cot, name='active', type='text')
        # Reload from DB and mark deprecated
        dep = CustomObjectTypeField.objects.get(
            custom_object_type=cls.cot, name='active'
        )
        dep.deprecated = True
        dep.deprecated_since = '1.1.0'
        dep.scheduled_removal = '2.0.0'
        dep.save()

    def test_deprecated_field_in_fields_list(self):
        cot_def = export_cot(self.cot)
        names = [f["name"] for f in cot_def.get("fields", [])]
        self.assertIn("active", names)

    def test_deprecated_attrs_exported(self):
        f = _field_by_name(export_cot(self.cot), "active")
        self.assertTrue(f["deprecated"])
        self.assertEqual(f["deprecated_since"], "1.1.0")
        self.assertEqual(f["scheduled_removal"], "2.0.0")

    def test_deprecated_field_not_in_removed_fields(self):
        cot_def = export_cot(self.cot)
        self.assertNotIn("removed_fields", cot_def)


class ExporterSchemaIdTestCase(CustomObjectsTestCase, TransactionTestCase,
                               TransactionCleanupMixin):
    """Fields without schema_id are skipped with a warning."""

    def test_field_without_schema_id_is_skipped(self):
        cot = self.create_custom_object_type(name='skiptest', slug='skip-test')
        field = self.create_custom_object_type_field(cot, name='noid', type='text')
        # Force schema_id to None after creation (simulating a pre-feature field)
        CustomObjectTypeField.objects.filter(pk=field.pk).update(schema_id=None)

        with self.assertLogs('netbox_custom_objects.exporter', level='WARNING') as cm:
            cot_def = export_cot(cot)

        self.assertNotIn("fields", cot_def)
        self.assertTrue(any("noid" in line for line in cm.output))


class ExporterTombstoneTestCase(CustomObjectsTestCase, TestCase):
    """removed_fields are read from schema_document."""

    @classmethod
    def setUpTestData(cls):
        cls.cot = cls.create_custom_object_type(name='tombstone', slug='tombstone')
        cls.cot.schema_document = {
            "schema_version": "1",
            "types": [
                {
                    "name": "tombstone",
                    "slug": "tombstone",
                    "fields": [],
                    "removed_fields": [
                        {"id": 7, "name": "old_field", "type": "text",
                         "removed_in": "2.0.0"}
                    ],
                }
            ],
        }
        cls.cot.save(update_fields=["schema_document"])

    def test_removed_fields_from_schema_document(self):
        cot_def = export_cot(self.cot)
        self.assertIn("removed_fields", cot_def)
        self.assertEqual(len(cot_def["removed_fields"]), 1)
        self.assertEqual(cot_def["removed_fields"][0]["id"], 7)
        self.assertEqual(cot_def["removed_fields"][0]["name"], "old_field")

    def test_no_removed_fields_key_when_document_empty(self):
        bare = self.create_custom_object_type(name='noremoved', slug='no-removed')
        cot_def = export_cot(bare)
        self.assertNotIn("removed_fields", cot_def)


@unittest.skipUnless(HAS_JSONSCHEMA, "jsonschema not installed")
class ExporterSchemaValidationTestCase(CustomObjectsTestCase, TestCase):
    """Exported documents must validate against cot_schema_v1.json."""

    _validator = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        import json
        raw = json.loads(_SCHEMA_PATH.read_text())
        cls._validator = jsonschema.Draft202012Validator(raw)

    def _assert_valid(self, doc):
        errors = list(self._validator.iter_errors(doc))
        if errors:
            self.fail(
                "Schema validation failed:\n"
                + "\n".join(f"  {e.json_path}: {e.message}" for e in errors)
            )

    @classmethod
    def setUpTestData(cls):
        cls.cot = cls.create_custom_object_type(
            name='schvalid', slug='sch-valid', version='1.0.0'
        )
        cls.choice_set = cls.create_choice_set(name='Sch Valid Choices')
        cls.device_ot = cls.get_device_object_type()

        cls.create_custom_object_type_field(
            cls.cot, name='label', type='text',
            required=True, primary=True
        )
        cls.create_custom_object_type_field(
            cls.cot, name='count', type='integer',
            validation_minimum=0
        )
        cls.create_custom_object_type_field(
            cls.cot, name='status', type='select',
            choice_set=cls.choice_set
        )
        cls.create_custom_object_type_field(
            cls.cot, name='device', type='object',
            related_object_type=cls.device_ot
        )

    def test_single_cot_validates(self):
        self._assert_valid(export_cots([self.cot]))

    def test_minimal_cot_validates(self):
        bare = self.create_custom_object_type(name='barevalid', slug='bare-valid')
        self._assert_valid(export_cots([bare]))

    def test_multi_type_document_validates(self):
        cot2 = self.create_custom_object_type(name='second', slug='second')
        self._assert_valid(export_cots([self.cot, cot2]))
