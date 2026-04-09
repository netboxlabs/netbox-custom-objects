"""
Tests for the COT upgrade executor (issue #389).

Covers:
- _build_dep_order: topological sort and circular-dependency detection
- apply_document / apply_diffs: new COT creation
- apply_document / apply_diffs: COT-level attribute updates
- Field ADD (including choice_set, object, malformed rot_str), ALTER, and REMOVE operations
- Field ALTER: choice_set and related_object_type resolution
- allow_destructive guard (DestructiveChangesError)
- Cross-COT object-field dependency ordering (two new COTs, A → B)
- schema_document persisted after apply
- next_schema_id counter synced after explicit-schema_id ADD
- Transaction atomicity: partial failure rolls back entirely
"""

from django.test import SimpleTestCase, TransactionTestCase

from netbox_custom_objects.schema.comparator import (
    COTDiff,
    FieldChange,
    FieldOp,
)
from netbox_custom_objects.schema.executor import (
    CircularDependencyError,
    DestructiveChangesError,
    UnknownChoiceSetError,
    UnknownFieldTypeError,
    UnknownObjectTypeError,
    _build_dep_order,
    apply_document,
    apply_diffs,
)
from netbox_custom_objects.schema.exporter import export_cot
from netbox_custom_objects.models import CustomObjectType

from ..base import CustomObjectsTestCase, TransactionCleanupMixin


# ---------------------------------------------------------------------------
# Pure-logic tests (no DB required)
# ---------------------------------------------------------------------------

class ExecutorBuildDepOrderTestCase(SimpleTestCase):
    """Unit tests for _build_dep_order — no DB access."""

    def _make_diff(self, slug, is_new=True, rot_refs=None):
        """
        Construct a minimal COTDiff with optional object-field references.

        *rot_refs* is a list of ``related_object_type`` strings for ADD
        FieldChanges added to this diff.
        """
        field_changes = []
        for i, rot in enumerate(rot_refs or [], start=1):
            field_changes.append(FieldChange(
                op=FieldOp.ADD,
                schema_id=i,
                db_name=None,
                schema_def={"id": i, "name": f"f{i}", "type": "object", "related_object_type": rot},
            ))
        return COTDiff(name=slug, slug=slug, is_new=is_new, field_changes=field_changes)

    def test_empty_list_returns_empty(self):
        self.assertEqual(_build_dep_order([]), [])

    def test_single_diff_returned_unchanged(self):
        diff = self._make_diff("alpha")
        result = _build_dep_order([diff])
        self.assertEqual([d.slug for d in result], ["alpha"])

    def test_no_dependencies_preserves_any_valid_order(self):
        diffs = [self._make_diff("x"), self._make_diff("y"), self._make_diff("z")]
        result = _build_dep_order(diffs)
        self.assertEqual(set(d.slug for d in result), {"x", "y", "z"})

    def test_dependency_orders_dep_before_dependent(self):
        # b references a → a must come before b
        a = self._make_diff("a")
        b = self._make_diff("b", rot_refs=["custom-objects/a"])
        result = _build_dep_order([b, a])
        slugs = [d.slug for d in result]
        self.assertLess(slugs.index("a"), slugs.index("b"))

    def test_chain_dependency_a_b_c(self):
        # c → b → a
        a = self._make_diff("a")
        b = self._make_diff("b", rot_refs=["custom-objects/a"])
        c = self._make_diff("c", rot_refs=["custom-objects/b"])
        result = _build_dep_order([c, b, a])
        slugs = [d.slug for d in result]
        self.assertLess(slugs.index("a"), slugs.index("b"))
        self.assertLess(slugs.index("b"), slugs.index("c"))

    def test_circular_dependency_raises(self):
        a = self._make_diff("a", rot_refs=["custom-objects/b"])
        b = self._make_diff("b", rot_refs=["custom-objects/a"])
        with self.assertRaises(CircularDependencyError):
            _build_dep_order([a, b])

    def test_self_reference_not_treated_as_cycle(self):
        # A self-referential COT (object field pointing to itself) is not a cycle.
        a = self._make_diff("a", rot_refs=["custom-objects/a"])
        result = _build_dep_order([a])
        self.assertEqual(len(result), 1)

    def test_reference_to_existing_cot_not_in_diffs_ignored(self):
        # "existing" is not in the diffs list so its slug is not in new_slugs → ignored.
        a = self._make_diff("a", rot_refs=["custom-objects/existing"])
        result = _build_dep_order([a])
        self.assertEqual([d.slug for d in result], ["a"])

    def test_builtin_rot_not_treated_as_dependency(self):
        # "dcim/device" is a built-in reference, not a custom-objects slug.
        a = self._make_diff("a", rot_refs=["dcim/device"])
        b = self._make_diff("b")
        result = _build_dep_order([a, b])
        self.assertEqual(set(d.slug for d in result), {"a", "b"})


# ---------------------------------------------------------------------------
# Helper base for DDL tests
# ---------------------------------------------------------------------------

class _ExecutorTestBase(TransactionCleanupMixin, CustomObjectsTestCase, TransactionTestCase):
    """Common base for executor integration tests that touch the DB."""
    pass


# ---------------------------------------------------------------------------
# Destructive-guard tests
# ---------------------------------------------------------------------------

class ExecutorDestructiveGuardTestCase(_ExecutorTestBase):
    """apply_document raises DestructiveChangesError for REMOVE without allow_destructive."""

    def setUp(self):
        super().setUp()
        self.cot = self.create_custom_object_type(name='guardtest', slug='guard-test')
        self.field = self.create_custom_object_type_field(
            self.cot, name='victim', type='text',
        )
        self.schema_id = self.field.schema_id

    def _schema_with_removal(self):
        type_def = export_cot(self.cot)
        type_def["fields"] = [
            f for f in type_def.get("fields", []) if f["name"] != "victim"
        ]
        type_def.setdefault("removed_fields", []).append(
            {"id": self.schema_id, "name": "victim", "type": "text"}
        )
        return {"schema_version": "1", "types": [type_def]}

    def test_raises_without_allow_destructive(self):
        schema_doc = self._schema_with_removal()
        with self.assertRaises(DestructiveChangesError) as ctx:
            apply_document(schema_doc)
        self.assertIn("guard-test", str(ctx.exception))

    def test_diffs_attribute_populated_on_error(self):
        schema_doc = self._schema_with_removal()
        with self.assertRaises(DestructiveChangesError) as ctx:
            apply_document(schema_doc)
        self.assertEqual(len(ctx.exception.diffs), 1)
        self.assertEqual(ctx.exception.diffs[0].slug, "guard-test")

    def test_succeeds_with_allow_destructive(self):
        schema_doc = self._schema_with_removal()
        apply_document(schema_doc, allow_destructive=True)
        self.assertFalse(self.cot.fields.filter(schema_id=self.schema_id).exists())

    def test_field_still_present_after_rejected_apply(self):
        schema_doc = self._schema_with_removal()
        with self.assertRaises(DestructiveChangesError):
            apply_document(schema_doc)
        # Field must still be in the DB.
        self.assertTrue(self.cot.fields.filter(schema_id=self.schema_id).exists())


# ---------------------------------------------------------------------------
# New COT creation
# ---------------------------------------------------------------------------

class ExecutorNewCOTTestCase(_ExecutorTestBase):
    """Executor creates new CustomObjectType records and their backing tables."""

    def _new_cot_schema(self, **overrides):
        td = {
            "name": "brandnew",
            "slug": "brand-new",
            "version": "1.0.0",
            "verbose_name": "Brand New",
            "verbose_name_plural": "Brand News",
            "description": "Test COT",
            "fields": [
                {"id": 1, "name": "label", "type": "text", "required": True},
                {"id": 2, "name": "count", "type": "integer"},
            ],
        }
        td.update(overrides)
        return {"schema_version": "1", "types": [td]}

    def test_new_cot_is_created(self):
        apply_document(self._new_cot_schema())
        self.assertTrue(CustomObjectType.objects.filter(slug="brand-new").exists())

    def test_new_cot_attrs_set_correctly(self):
        apply_document(self._new_cot_schema())
        cot = CustomObjectType.objects.get(slug="brand-new")
        self.assertEqual(cot.name, "brandnew")
        self.assertEqual(cot.version, "1.0.0")
        self.assertEqual(cot.verbose_name, "Brand New")
        self.assertEqual(cot.description, "Test COT")

    def test_new_cot_fields_created(self):
        apply_document(self._new_cot_schema())
        cot = CustomObjectType.objects.get(slug="brand-new")
        self.assertEqual(cot.fields.count(), 2)

    def test_new_cot_field_attrs_set_correctly(self):
        apply_document(self._new_cot_schema())
        cot = CustomObjectType.objects.get(slug="brand-new")
        label_field = cot.fields.get(name="label")
        self.assertEqual(label_field.type, "text")
        self.assertTrue(label_field.required)
        self.assertEqual(label_field.schema_id, 1)

    def test_new_cot_next_schema_id_synced(self):
        apply_document(self._new_cot_schema())
        cot = CustomObjectType.objects.get(slug="brand-new")
        # next_schema_id should be ≥ highest field id in the schema (2).
        cot.refresh_from_db()
        self.assertGreaterEqual(cot.next_schema_id, 2)

    def test_new_cot_schema_document_persisted(self):
        schema_doc = self._new_cot_schema()
        apply_document(schema_doc)
        cot = CustomObjectType.objects.get(slug="brand-new")
        cot.refresh_from_db()
        self.assertIsNotNone(cot.schema_document)
        self.assertIn("types", cot.schema_document)

    def test_returned_diffs_include_new_cot(self):
        schema_doc = self._new_cot_schema()
        diffs = apply_document(schema_doc)
        self.assertEqual(len(diffs), 1)
        self.assertTrue(diffs[0].is_new)
        self.assertEqual(diffs[0].slug, "brand-new")

    def test_new_cot_with_no_fields(self):
        schema_doc = {"schema_version": "1", "types": [
            {"name": "empty_cot", "slug": "empty-cot"}
        ]}
        apply_document(schema_doc)
        cot = CustomObjectType.objects.get(slug="empty-cot")
        self.assertEqual(cot.fields.count(), 0)


# ---------------------------------------------------------------------------
# COT attribute updates
# ---------------------------------------------------------------------------

class ExecutorCOTAttrsTestCase(_ExecutorTestBase):
    """Executor updates top-level COT attributes when they differ from the schema."""

    def setUp(self):
        super().setUp()
        self.cot = self.create_custom_object_type(
            name='attrtest', slug='attr-test', version='1.0.0',
            description='Old description', verbose_name='Old VN',
        )

    def test_description_updated(self):
        type_def = export_cot(self.cot)
        type_def["description"] = "New description"
        apply_document({"schema_version": "1", "types": [type_def]})
        self.cot.refresh_from_db()
        self.assertEqual(self.cot.description, "New description")

    def test_version_updated(self):
        type_def = export_cot(self.cot)
        type_def["version"] = "2.0.0"
        apply_document({"schema_version": "1", "types": [type_def]})
        self.cot.refresh_from_db()
        self.assertEqual(self.cot.version, "2.0.0")

    def test_verbose_name_updated(self):
        type_def = export_cot(self.cot)
        type_def["verbose_name"] = "New Verbose Name"
        apply_document({"schema_version": "1", "types": [type_def]})
        self.cot.refresh_from_db()
        self.assertEqual(self.cot.verbose_name, "New Verbose Name")

    def test_no_changes_leaves_cot_unchanged(self):
        type_def = export_cot(self.cot)
        apply_document({"schema_version": "1", "types": [type_def]})
        self.cot.refresh_from_db()
        self.assertEqual(self.cot.description, "Old description")


# ---------------------------------------------------------------------------
# Field ADD
# ---------------------------------------------------------------------------

class ExecutorFieldAddTestCase(_ExecutorTestBase):
    """Executor adds new fields to existing COTs."""

    def setUp(self):
        super().setUp()
        self.cot = self.create_custom_object_type(name='addtest', slug='add-test')
        self.create_custom_object_type_field(self.cot, name='existing', type='text')
        # Refresh so next_schema_id reflects the counter updated by field creation.
        self.cot.refresh_from_db()

    def test_field_added_to_existing_cot(self):
        type_def = export_cot(self.cot)
        next_id = self.cot.next_schema_id + 1
        type_def["fields"].append({"id": next_id, "name": "newfield", "type": "integer"})
        apply_document({"schema_version": "1", "types": [type_def]})
        self.assertTrue(self.cot.fields.filter(name="newfield").exists())

    def test_added_field_schema_id_correct(self):
        type_def = export_cot(self.cot)
        next_id = self.cot.next_schema_id + 1
        type_def["fields"].append({"id": next_id, "name": "newfield", "type": "integer"})
        apply_document({"schema_version": "1", "types": [type_def]})
        field = self.cot.fields.get(name="newfield")
        self.assertEqual(field.schema_id, next_id)

    def test_added_select_field_resolves_choice_set(self):
        cs = self.create_choice_set(name='Colours')
        type_def = export_cot(self.cot)
        next_id = self.cot.next_schema_id + 1
        type_def["fields"].append({
            "id": next_id, "name": "colour", "type": "select", "choice_set": "Colours",
        })
        apply_document({"schema_version": "1", "types": [type_def]})
        field = self.cot.fields.select_related("choice_set").get(name="colour")
        self.assertEqual(field.choice_set, cs)

    def test_added_object_field_resolves_related_object_type(self):
        device_ot = self.get_device_object_type()
        type_def = export_cot(self.cot)
        next_id = self.cot.next_schema_id + 1
        type_def["fields"].append({
            "id": next_id, "name": "device", "type": "object",
            "related_object_type": "dcim/device",
        })
        apply_document({"schema_version": "1", "types": [type_def]})
        field = self.cot.fields.select_related("related_object_type").get(name="device")
        self.assertEqual(field.related_object_type, device_ot)

    def test_unknown_choice_set_raises(self):
        type_def = export_cot(self.cot)
        next_id = self.cot.next_schema_id + 1
        type_def["fields"].append({
            "id": next_id, "name": "flavour", "type": "select",
            "choice_set": "NoSuchChoiceSet",
        })
        with self.assertRaises(UnknownChoiceSetError):
            apply_document({"schema_version": "1", "types": [type_def]})

    def test_unknown_object_type_raises(self):
        type_def = export_cot(self.cot)
        next_id = self.cot.next_schema_id + 1
        type_def["fields"].append({
            "id": next_id, "name": "ghost", "type": "object",
            "related_object_type": "does/notexist",
        })
        with self.assertRaises(UnknownObjectTypeError):
            apply_document({"schema_version": "1", "types": [type_def]})

    def test_unknown_field_type_raises(self):
        type_def = export_cot(self.cot)
        next_id = self.cot.next_schema_id + 1
        type_def["fields"].append({"id": next_id, "name": "bad", "type": "nosuchtype"})
        with self.assertRaises(UnknownFieldTypeError):
            apply_document({"schema_version": "1", "types": [type_def]})

    def test_malformed_related_object_type_raises(self):
        """A rot_str with no '/' raises UnknownObjectTypeError, not ValueError."""
        type_def = export_cot(self.cot)
        next_id = self.cot.next_schema_id + 1
        type_def["fields"].append({
            "id": next_id, "name": "bad", "type": "object",
            "related_object_type": "nodslash",
        })
        with self.assertRaises(UnknownObjectTypeError):
            apply_document({"schema_version": "1", "types": [type_def]})

    def test_next_schema_id_updated_after_add(self):
        type_def = export_cot(self.cot)
        next_id = self.cot.next_schema_id + 1
        type_def["fields"].append({"id": next_id, "name": "newfield", "type": "text"})
        apply_document({"schema_version": "1", "types": [type_def]})
        self.cot.refresh_from_db()
        self.assertGreaterEqual(self.cot.next_schema_id, next_id)


# ---------------------------------------------------------------------------
# Field ALTER
# ---------------------------------------------------------------------------

class ExecutorFieldAlterTestCase(_ExecutorTestBase):
    """Executor applies attribute changes to existing fields."""

    def setUp(self):
        super().setUp()
        self.cot = self.create_custom_object_type(name='altertest', slug='alter-test')
        self.field = self.create_custom_object_type_field(
            self.cot, name='target', type='text',
            description='Old desc', weight=100,
        )

    def test_alter_description(self):
        type_def = export_cot(self.cot)
        for f in type_def["fields"]:
            if f["name"] == "target":
                f["description"] = "New desc"
        apply_document({"schema_version": "1", "types": [type_def]})
        self.field.refresh_from_db()
        self.assertEqual(self.field.description, "New desc")

    def test_alter_weight(self):
        type_def = export_cot(self.cot)
        for f in type_def["fields"]:
            if f["name"] == "target":
                f["weight"] = 50
        apply_document({"schema_version": "1", "types": [type_def]})
        self.field.refresh_from_db()
        self.assertEqual(self.field.weight, 50)

    def test_rename_field(self):
        schema_id = self.field.schema_id
        type_def = export_cot(self.cot)
        for f in type_def["fields"]:
            if f["name"] == "target":
                f["name"] = "renamed_target"
        apply_document({"schema_version": "1", "types": [type_def]})
        # Old name gone, new name present with same schema_id.
        self.assertFalse(self.cot.fields.filter(name="target").exists())
        self.assertTrue(self.cot.fields.filter(name="renamed_target").exists())
        self.assertEqual(
            self.cot.fields.get(name="renamed_target").schema_id, schema_id,
        )

    def test_alter_required_flag(self):
        type_def = export_cot(self.cot)
        for f in type_def["fields"]:
            if f["name"] == "target":
                f["required"] = True
        apply_document({"schema_version": "1", "types": [type_def]})
        self.field.refresh_from_db()
        self.assertTrue(self.field.required)

    def test_alter_validation_minimum(self):
        # First change the field to integer type directly in the DB so we
        # can then test altering validation_minimum via the executor.
        int_cot = self.create_custom_object_type(name='intalter', slug='int-alter')
        int_field = self.create_custom_object_type_field(
            int_cot, name='qty', type='integer', validation_minimum=0,
        )
        type_def = export_cot(int_cot)
        for f in type_def["fields"]:
            if f["name"] == "qty":
                f["validation_minimum"] = 10
        apply_document({"schema_version": "1", "types": [type_def]})
        int_field.refresh_from_db()
        self.assertEqual(int_field.validation_minimum, 10)

    def test_alter_choice_set(self):
        cs_a = self.create_choice_set(name='Status A')
        cs_b = self.create_choice_set(name='Status B')
        sel_cot = self.create_custom_object_type(name='seltest', slug='sel-test')
        sel_field = self.create_custom_object_type_field(
            sel_cot, name='status', type='select', choice_set=cs_a,
        )
        type_def = export_cot(sel_cot)
        for f in type_def["fields"]:
            if f["name"] == "status":
                f["choice_set"] = "Status B"
        apply_document({"schema_version": "1", "types": [type_def]})
        sel_field.refresh_from_db()
        self.assertEqual(sel_field.choice_set, cs_b)

    def test_alter_related_object_type(self):
        device_ot = self.get_device_object_type()
        site_ot = self.get_site_object_type()
        obj_cot = self.create_custom_object_type(name='objtest', slug='obj-test')
        obj_field = self.create_custom_object_type_field(
            obj_cot, name='thing', type='object', related_object_type=device_ot,
        )
        type_def = export_cot(obj_cot)
        for f in type_def["fields"]:
            if f["name"] == "thing":
                f["related_object_type"] = "dcim/site"
        apply_document({"schema_version": "1", "types": [type_def]})
        obj_field.refresh_from_db()
        self.assertEqual(
            obj_field.related_object_type, site_ot,
        )


# ---------------------------------------------------------------------------
# Field REMOVE
# ---------------------------------------------------------------------------

class ExecutorFieldRemoveTestCase(_ExecutorTestBase):
    """Executor removes (tombstoned) fields from existing COTs."""

    def setUp(self):
        super().setUp()
        self.cot = self.create_custom_object_type(name='removetest', slug='remove-test')
        self.field_a = self.create_custom_object_type_field(
            self.cot, name='keeper', type='text',
        )
        self.field_b = self.create_custom_object_type_field(
            self.cot, name='goner', type='text',
        )
        self.goner_schema_id = self.field_b.schema_id

    def _removal_schema(self):
        type_def = export_cot(self.cot)
        type_def["fields"] = [
            f for f in type_def.get("fields", []) if f["name"] != "goner"
        ]
        type_def.setdefault("removed_fields", []).append(
            {"id": self.goner_schema_id, "name": "goner", "type": "text"}
        )
        return {"schema_version": "1", "types": [type_def]}

    def test_field_removed_from_db(self):
        apply_document(self._removal_schema(), allow_destructive=True)
        self.assertFalse(
            self.cot.fields.filter(schema_id=self.goner_schema_id).exists()
        )

    def test_surviving_field_unaffected(self):
        apply_document(self._removal_schema(), allow_destructive=True)
        self.assertTrue(self.cot.fields.filter(name="keeper").exists())

    def test_tombstone_persisted_in_schema_document(self):
        apply_document(self._removal_schema(), allow_destructive=True)
        self.cot.refresh_from_db()
        doc = self.cot.schema_document
        self.assertIsNotNone(doc)
        type_defs = doc.get("types", [])
        self.assertEqual(len(type_defs), 1)
        removed = type_defs[0].get("removed_fields", [])
        removed_ids = [r["id"] for r in removed]
        self.assertIn(self.goner_schema_id, removed_ids)


# ---------------------------------------------------------------------------
# schema_document persistence
# ---------------------------------------------------------------------------

class ExecutorSchemaDocumentTestCase(_ExecutorTestBase):
    """schema_document is always persisted after a successful apply."""

    def test_schema_document_set_after_noop(self):
        cot = self.create_custom_object_type(name='doctest', slug='doc-test')
        self.create_custom_object_type_field(cot, name='x', type='text')
        type_def = export_cot(cot)
        schema_doc = {"schema_version": "1", "types": [type_def]}
        apply_document(schema_doc)
        cot.refresh_from_db()
        self.assertIsNotNone(cot.schema_document)

    def test_schema_document_contains_correct_slug(self):
        cot = self.create_custom_object_type(name='doctest2', slug='doc-test-2')
        type_def = export_cot(cot)
        schema_doc = {"schema_version": "1", "types": [type_def]}
        apply_document(schema_doc)
        cot.refresh_from_db()
        types = cot.schema_document.get("types", [])
        self.assertEqual(len(types), 1)
        self.assertEqual(types[0]["slug"], "doc-test-2")

    def test_schema_document_schema_version_correct(self):
        cot = self.create_custom_object_type(name='doctest3', slug='doc-test-3')
        type_def = export_cot(cot)
        apply_document({"schema_version": "1", "types": [type_def]})
        cot.refresh_from_db()
        self.assertEqual(cot.schema_document.get("schema_version"), "1")


# ---------------------------------------------------------------------------
# Cross-COT dependency ordering
# ---------------------------------------------------------------------------

class ExecutorDependencyTestCase(_ExecutorTestBase):
    """
    Two new COTs: cot_b is a standalone type; cot_a has an object field
    pointing to cot_b.  Both are new in the document (not in DB).
    """

    def _two_cot_schema(self):
        return {
            "schema_version": "1",
            "types": [
                {
                    "name": "cot_a",
                    "slug": "cot-a",
                    "fields": [
                        {"id": 1, "name": "title", "type": "text"},
                        {
                            "id": 2,
                            "name": "related_b",
                            "type": "object",
                            "related_object_type": "custom-objects/cot-b",
                        },
                    ],
                },
                {
                    "name": "cot_b",
                    "slug": "cot-b",
                    "fields": [
                        {"id": 1, "name": "name", "type": "text"},
                    ],
                },
            ],
        }

    def test_both_cots_created(self):
        apply_document(self._two_cot_schema())
        self.assertTrue(CustomObjectType.objects.filter(slug="cot-a").exists())
        self.assertTrue(CustomObjectType.objects.filter(slug="cot-b").exists())

    def test_cot_a_has_object_field_referencing_cot_b(self):
        apply_document(self._two_cot_schema())
        cot_a = CustomObjectType.objects.get(slug="cot-a")
        field = cot_a.fields.select_related("related_object_type").get(name="related_b")
        self.assertEqual(field.type, "object")
        # The related ObjectType should point to the cot-b table.
        cot_b = CustomObjectType.objects.get(slug="cot-b")
        expected_model = CustomObjectType.get_table_model_name(cot_b.id).lower()
        self.assertEqual(field.related_object_type.model, expected_model)

    def test_document_in_reversed_order_also_works(self):
        # Even if cot-b is listed AFTER cot-a, the executor must create b first.
        schema = self._two_cot_schema()
        schema["types"] = list(reversed(schema["types"]))
        apply_document(schema)
        self.assertTrue(CustomObjectType.objects.filter(slug="cot-a").exists())
        self.assertTrue(CustomObjectType.objects.filter(slug="cot-b").exists())


# ---------------------------------------------------------------------------
# Transaction atomicity
# ---------------------------------------------------------------------------

class ExecutorAtomicityTestCase(_ExecutorTestBase):
    """A failure inside apply_diffs rolls back the entire transaction."""

    def test_failed_add_rolls_back_new_cot(self):
        """
        If an ADD field references a non-existent related_object_type, the
        entire atomic block must roll back — including the COT table that was
        created in Phase 1.
        """
        schema_doc = {
            "schema_version": "1",
            "types": [{
                "name": "rollback_cot",
                "slug": "rollback-cot",
                "fields": [
                    {"id": 1, "name": "ok_field", "type": "text"},
                    {
                        "id": 2,
                        "name": "bad_field",
                        "type": "object",
                        "related_object_type": "does/notexist",
                    },
                ],
            }],
        }
        with self.assertRaises(UnknownObjectTypeError):
            apply_document(schema_doc)

        # The COT must not exist after the rollback.
        self.assertFalse(
            CustomObjectType.objects.filter(slug="rollback-cot").exists()
        )

    def test_failed_add_field_to_existing_cot_rolls_back(self):
        """
        If a field ADD fails midway, no partial changes should be visible.
        """
        cot = self.create_custom_object_type(name='partialfail', slug='partial-fail')
        self.create_custom_object_type_field(cot, name='stays', type='text')
        initial_field_count = cot.fields.count()

        type_def = export_cot(cot)
        cot.refresh_from_db()  # next_schema_id updated by field creation via update()
        next_id = cot.next_schema_id + 1
        # Add one good field and one bad field.
        type_def["fields"].append({"id": next_id, "name": "good_new", "type": "text"})
        type_def["fields"].append({
            "id": next_id + 1,
            "name": "bad_new",
            "type": "object",
            "related_object_type": "does/notexist",
        })

        with self.assertRaises(UnknownObjectTypeError):
            apply_document({"schema_version": "1", "types": [type_def]})

        cot.refresh_from_db()
        # Field count unchanged — both the good and bad new fields are absent.
        self.assertEqual(cot.fields.count(), initial_field_count)


# ---------------------------------------------------------------------------
# apply_diffs low-level interface
# ---------------------------------------------------------------------------

class ExecutorApplyDiffsTestCase(_ExecutorTestBase):
    """Tests targeting apply_diffs directly (pre-computed diffs)."""

    def test_apply_diffs_with_type_defs_by_slug(self):
        """apply_diffs works with pre-computed diffs and a type_defs_by_slug map."""
        from netbox_custom_objects.schema.comparator import diff_document

        cot = self.create_custom_object_type(name='directdiff', slug='direct-diff')
        self.create_custom_object_type_field(cot, name='alpha', type='text')

        type_def = export_cot(cot)
        cot.refresh_from_db()
        next_id = cot.next_schema_id + 1
        type_def["fields"].append({"id": next_id, "name": "beta", "type": "text"})
        schema_doc = {"schema_version": "1", "types": [type_def]}

        diffs = diff_document(schema_doc)
        type_defs_by_slug = {td["slug"]: td for td in schema_doc["types"]}
        apply_diffs(diffs, type_defs_by_slug)

        self.assertTrue(cot.fields.filter(name="beta").exists())

    def test_apply_diffs_raises_destructive_without_flag(self):
        from netbox_custom_objects.schema.comparator import diff_document

        cot = self.create_custom_object_type(name='diffdest', slug='diff-dest')
        f = self.create_custom_object_type_field(cot, name='bye', type='text')
        sid = f.schema_id

        type_def = export_cot(cot)
        type_def["fields"] = []
        type_def["removed_fields"] = [{"id": sid, "name": "bye", "type": "text"}]
        schema_doc = {"schema_version": "1", "types": [type_def]}

        diffs = diff_document(schema_doc)
        type_defs_by_slug = {td["slug"]: td for td in schema_doc["types"]}

        with self.assertRaises(DestructiveChangesError):
            apply_diffs(diffs, type_defs_by_slug)
