"""
A Custom Object Type survives a portable-schema round trip: export it, validate the
document, apply it under a new slug, and export the copy again.

The source type has a field of every schema type, a non-default value for every field
and constraint attribute, and every type-level attribute the format carries, so the
test covers each one's export and ADD path.  ``test_source_covers_the_format`` fails
when the format gains a type or attribute this test doesn't exercise.
"""
import copy
import json
from pathlib import Path

import jsonschema
from django.test import TestCase

from netbox_custom_objects.models import CustomObjectType, CustomObjectTypeConstraint
from netbox_custom_objects.schema.comparator import _COT_ATTRS, diff_document
from netbox_custom_objects.schema.executor import apply_document
from netbox_custom_objects.schema.exporter import export_cot, export_cots
from netbox_custom_objects.schema.format import (
    CONSTRAINT_ATTRS,
    CONSTRAINT_DEFAULTS,
    FIELD_BASE_ATTRS,
    FIELD_DEFAULTS,
    FIELD_TYPE_ATTRS,
    SCHEMA_TYPE_TO_CHOICES,
)

from ..base import CustomObjectsTestCase

_SCHEMA_FILE = Path(__file__).resolve().parents[2] / "schema" / "cot_schema_v1.json"


class SchemaRoundTripTestCase(CustomObjectsTestCase, TestCase):

    def setUp(self):
        super().setUp()
        self.source = self._build_source()
        self.document = export_cots([self.source])

    def _build_source(self):
        site, device = self.get_site_object_type(), self.get_device_object_type()
        choices = self.create_choice_set(name="Round trip choices")
        cot = self.create_custom_object_type(
            name="rt_source", slug="rt-source", version="1.2.0", verbose_name="Round trip source",
            verbose_name_plural="Round trip sources", description="Every field type", group_name="Tests",
        )

        def add(name, type, **kwargs):
            return self.create_custom_object_type_field(cot, name=name, type=type, **kwargs)

        code = add(
            "code", "text", label="Code", description="The code", group_name="Identity", primary=True,
            required=True, unique=True, weight=10, search_weight=1000, filter_logic="exact",
            is_cloneable=True, validation_regex="^[A-Z]+$",
        )
        add("notes", "longtext", ui_visible="if-set", ui_editable="no", default="n/a")
        count = add("count", "integer", validation_minimum=0, validation_maximum=100, default=5)
        # Whole-number bounds: fractional decimal bounds don't survive apply yet.
        add("ratio", "decimal", validation_minimum=0, validation_maximum=10)
        active = add("active", "boolean", default=True)
        add("born", "date", deprecated=True, deprecated_since="1.1.0", scheduled_removal="2.0.0")
        add("seen", "datetime", filter_logic="disabled")
        add("homepage", "url")
        add("extra", "json", default={"a": 1})
        add("status", "select", choice_set=choices, default="choice1")
        add("labels", "multiselect", choice_set=choices)
        add(
            "site", "object", related_object_type=site, related_name="rt_sources",
            related_object_filter={"status": "active"}, on_delete_behavior="protect",
        )
        add("peer", "object", related_object_type=cot.object_type)
        self.create_polymorphic_field(cot, [site, device], name="target", type="object")
        add("sites", "multiobject", related_object_type=site, related_name="rt_source_sets")
        self.create_polymorphic_field(cot, [site, device], name="targets", type="multiobject")
        add(
            "points", "array", array_element_type="integer", array_tuple_size=2, array_min_length=1,
            array_max_length=5, validation_minimum=0, validation_maximum=99,
        )
        add("words", "array", array_element_type="text", validation_regex="^[a-z]+$")
        add("shade", "color", default="00ff00")
        add("mac", "mac_address")
        add("location", "coordinates")

        CustomObjectTypeConstraint.objects.create(
            custom_object_type=cot, name="code_count", field_schema_ids=[code.schema_id, count.schema_id],
            case_insensitive=True, nulls_distinct=False,
            condition=[{"field": active.schema_id, "lookup": "exact", "value": True}],
            description="Unique while active",
        )
        CustomObjectTypeConstraint.objects.create(
            custom_object_type=cot, name="count_positive", type="check",
            rule=[{"field": count.schema_id, "lookup": "gte", "value": 0}], error_message="Count can't be negative",
        )
        cot.refresh_from_db()
        return cot

    def _copy_definition(self):
        """The source's definition as a new type.  Reverse relation names are per target model, so they change too."""
        type_def = copy.deepcopy(self.document["types"][0])
        type_def.update(name="rt_copy", slug="rt-copy")
        for field_def in type_def["fields"]:
            if field_def.get("related_name"):
                field_def["related_name"] += "_copy"
        return type_def

    def test_export_validates(self):
        validator = jsonschema.Draft202012Validator(json.loads(_SCHEMA_FILE.read_text()))
        self.assertEqual([error.message for error in validator.iter_errors(self.document)], [])

    def test_applied_copy_exports_the_same_definition(self):
        type_def = self._copy_definition()
        apply_document({"schema_version": self.document["schema_version"], "types": [type_def]})
        self.assertEqual(export_cot(CustomObjectType.objects.get(slug="rt-copy")), type_def)

    def test_reapplying_the_copy_changes_nothing(self):
        type_def = self._copy_definition()
        apply_document({"schema_version": self.document["schema_version"], "types": [type_def]})
        exported = export_cot(CustomObjectType.objects.get(slug="rt-copy"))
        (diff,) = diff_document({"schema_version": self.document["schema_version"], "types": [exported]})
        self.assertFalse(diff.has_changes, diff)

    def test_source_covers_the_format(self):
        type_def = self.document["types"][0]
        fields = type_def["fields"]
        self.assertEqual({f["type"] for f in fields}, set(SCHEMA_TYPE_TO_CHOICES))

        # Every field attribute is set to a non-default value on at least one field.
        exported_attrs = {attr for f in fields for attr in f if f.get(attr) != FIELD_DEFAULTS.get(attr)}
        type_specific = set().union(*FIELD_TYPE_ATTRS.values())
        self.assertEqual(set(FIELD_BASE_ATTRS) - exported_attrs, set())
        self.assertEqual(type_specific - exported_attrs, set())

        constraint_attrs = {
            attr for c in type_def["constraints"] for attr in c if c.get(attr) != CONSTRAINT_DEFAULTS.get(attr)
        }
        self.assertEqual(set(CONSTRAINT_ATTRS) - constraint_attrs, set())

        # Every type attribute the format compares; name is always present.
        self.assertEqual({attr for attr in _COT_ATTRS if attr != "name"} - set(type_def), set())
