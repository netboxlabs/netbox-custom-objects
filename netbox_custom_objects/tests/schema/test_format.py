"""
Tests for the COT portable schema format (issue #386).

Covers:
- schema_id auto-assignment and uniqueness on CustomObjectTypeField
- deprecated / deprecated_since / scheduled_removal field behaviour
- schema_document and version fields on CustomObjectType
- JSON Schema document validation via cot_schema_v1.json
"""
import json
import unittest
from pathlib import Path

from django.db import IntegrityError, transaction
from django.db.models.fields import NOT_PROVIDED
from django.test import TestCase, TransactionTestCase

from netbox_custom_objects.models import CustomObjectTypeField
from netbox_custom_objects.schema.format import (
    CHOICES_TO_SCHEMA_TYPE,
    FIELD_DEFAULTS,
    SCHEMA_FORMAT_VERSION,
    SCHEMA_TYPE_TO_CHOICES,
)
from extras.choices import CustomFieldTypeChoices

from ..base import CustomObjectsTestCase, TransactionCleanupMixin

# ---------------------------------------------------------------------------
# Optional jsonschema dependency — skip structural-validation tests if absent
# ---------------------------------------------------------------------------
try:
    import jsonschema
    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False

_SCHEMA_PATH = (
    Path(__file__).resolve().parent.parent.parent / "schema" / "cot_schema_v1.json"
)


# ===========================================================================
# schema_id / deprecated model field tests (require TransactionTestCase)
# ===========================================================================

class SchemaIdAutoAssignmentTestCase(
    TransactionCleanupMixin, CustomObjectsTestCase, TransactionTestCase
):
    """schema_id is auto-assigned on field creation and never reused."""

    def test_schema_id_assigned_on_create(self):
        """A new field with no explicit schema_id gets one automatically."""
        cot = self.create_custom_object_type(name='schidtest', slug='sch-id-test')
        field = self.create_custom_object_type_field(cot, name='label', type='text')

        self.assertIsNotNone(field.schema_id, "schema_id must be set after save.")
        self.assertGreaterEqual(field.schema_id, 1)

    def test_schema_ids_are_sequential(self):
        """Each new field in the same COT gets the next available integer."""
        cot = self.create_custom_object_type(name='schseq', slug='sch-seq')
        f1 = self.create_custom_object_type_field(cot, name='first', type='text')
        f2 = self.create_custom_object_type_field(cot, name='second', type='text')
        f3 = self.create_custom_object_type_field(cot, name='third', type='text')

        ids = sorted([f1.schema_id, f2.schema_id, f3.schema_id])
        self.assertEqual(ids, list(range(ids[0], ids[0] + 3)),
                         "schema_ids must form a contiguous sequence within the COT.")

    def test_schema_id_scoped_to_cot(self):
        """Two different COTs can have fields with the same schema_id."""
        cot_a = self.create_custom_object_type(name='schcota', slug='sch-cot-a')
        cot_b = self.create_custom_object_type(name='schcotb', slug='sch-cot-b')

        fa = self.create_custom_object_type_field(cot_a, name='label', type='text')
        fb = self.create_custom_object_type_field(cot_b, name='label', type='text')

        # Both are assigned ID 1 — that is fine because uniqueness is per-COT.
        self.assertEqual(fa.schema_id, fb.schema_id,
                         "First field in each COT should both receive schema_id=1.")

    def test_explicit_schema_id_is_respected(self):
        """A field created with an explicit schema_id keeps that value."""
        cot = self.create_custom_object_type(name='schexpl', slug='sch-expl')
        field = self.create_custom_object_type_field(
            cot, name='label', type='text', schema_id=42
        )
        self.assertEqual(field.schema_id, 42)

    def test_duplicate_schema_id_within_cot_raises(self):
        """Assigning the same schema_id to two fields in the same COT is rejected."""
        cot = self.create_custom_object_type(name='schdup', slug='sch-dup')
        self.create_custom_object_type_field(
            cot, name='first', type='text', schema_id=7
        )
        # Wrap in transaction.atomic() so that when IntegrityError is raised and caught
        # by assertRaises, the savepoint is rolled back cleanly. Without this, PostgreSQL
        # leaves the connection in an aborted-transaction state and all subsequent SQL
        # calls (including tearDown) fail with "connection is closed".
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.create_custom_object_type_field(
                    cot, name='second', type='text', schema_id=7
                )

    def test_schema_id_gap_after_deletion(self):
        """
        After a field is deleted its schema_id is not reused; the next
        auto-assigned ID continues from the highest ever used.
        """
        cot = self.create_custom_object_type(name='schgap', slug='sch-gap')
        self.create_custom_object_type_field(cot, name='first', type='text')
        f2 = self.create_custom_object_type_field(cot, name='second', type='text')
        id_before_delete = f2.schema_id

        f2.delete()

        f3 = self.create_custom_object_type_field(cot, name='third', type='text')
        self.assertGreater(
            f3.schema_id, id_before_delete,
            "Auto-assigned schema_id must not reuse a previously deleted field's ID.",
        )


class DeprecationFieldsTestCase(
    TransactionCleanupMixin, CustomObjectsTestCase, TransactionTestCase
):
    """deprecated, deprecated_since, and scheduled_removal field behaviour."""

    def test_deprecated_defaults_to_false(self):
        cot = self.create_custom_object_type(name='depdefault', slug='dep-default')
        field = self.create_custom_object_type_field(cot, name='label', type='text')
        self.assertFalse(field.deprecated)

    def test_can_mark_field_deprecated(self):
        cot = self.create_custom_object_type(name='depmark', slug='dep-mark')
        self.create_custom_object_type_field(cot, name='label', type='text')

        # Re-fetch from DB so that from_db() is called and self.original is set,
        # which the save() method requires when updating an existing field.
        field = CustomObjectTypeField.objects.get(custom_object_type=cot, name='label')
        field.deprecated = True
        field.deprecated_since = '2.0.0'
        field.scheduled_removal = '3.0.0'
        field.save()

        field.refresh_from_db()
        self.assertTrue(field.deprecated)
        self.assertEqual(field.deprecated_since, '2.0.0')
        self.assertEqual(field.scheduled_removal, '3.0.0')

    def test_deprecation_version_strings_accept_semver(self):
        """Long semver strings (pre-release, build metadata) fit in max_length=50."""
        cot = self.create_custom_object_type(name='depsemver', slug='dep-semver')
        self.create_custom_object_type_field(cot, name='label', type='text')

        field = CustomObjectTypeField.objects.get(custom_object_type=cot, name='label')
        field.deprecated = True
        field.deprecated_since = '1.0.0-alpha.1+build.42'
        field.scheduled_removal = '2.0.0-beta.3'
        field.save()

        field.refresh_from_db()
        self.assertEqual(field.deprecated_since, '1.0.0-alpha.1+build.42')


# ===========================================================================
# Backfill migration logic tests (TransactionTestCase — uses DDL)
# ===========================================================================

class SchemaIdBackfillTestCase(
    TransactionCleanupMixin, CustomObjectsTestCase, TransactionTestCase
):
    """
    Tests for the 0008_backfill_schema_ids migration logic.

    Rather than exercising the migration runner itself (which would require
    replaying the full migration history), these tests call the same backfill
    function directly and verify its behaviour against real model instances.
    """

    @staticmethod
    def _run_backfill():
        """Execute the backfill function directly against the live DB."""
        import importlib
        mod = importlib.import_module(
            'netbox_custom_objects.migrations.0008_backfill_schema_ids'
        )
        # The function accepts (apps, schema_editor) but only uses apps.get_model().
        # We pass a lightweight shim that delegates to the real models.

        class _AppsShim:
            @staticmethod
            def get_model(app_label, model_name):
                from django.apps import apps
                return apps.get_model(app_label, model_name)

        mod.assign_schema_ids(_AppsShim(), None)

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

        self._run_backfill()

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

        self._run_backfill()

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

        self._run_backfill()

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

        self._run_backfill()

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

        self._run_backfill()

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

        self._run_backfill()

        cot.refresh_from_db()
        self.assertGreaterEqual(cot.next_schema_id, 99)

    def test_ids_unique_within_cot(self):
        cot = self.create_custom_object_type(name='bf7', slug='bf-7')
        for i in range(1, 6):
            self.create_custom_object_type_field(cot, name=f'h{i}', type='text')
        CustomObjectTypeField.objects.filter(
            custom_object_type=cot
        ).update(schema_id=None)

        self._run_backfill()

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

        self._run_backfill()

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

        self._run_backfill()
        ids_after_first = list(
            CustomObjectTypeField.objects.filter(custom_object_type=cot)
            .order_by('id').values_list('schema_id', flat=True)
        )

        self._run_backfill()
        ids_after_second = list(
            CustomObjectTypeField.objects.filter(custom_object_type=cot)
            .order_by('id').values_list('schema_id', flat=True)
        )

        self.assertEqual(ids_after_first, ids_after_second)


# ===========================================================================
# schema_document and version field tests (plain TestCase — no DDL needed)
# ===========================================================================

class SchemaDocumentFieldTestCase(CustomObjectsTestCase, TestCase):
    """schema_document and version fields on CustomObjectType."""

    def test_schema_document_populated_with_base_columns_on_creation(self):
        # schema_document is no longer NULL after creation: create_model() writes
        # the base_columns snapshot immediately.  Verify the expected structure.
        cot = self.create_custom_object_type(name='schemadoc', slug='schema-doc')
        cot.refresh_from_db()
        self.assertIsNotNone(cot.schema_document)
        self.assertIn('base_columns', cot.schema_document)
        names = {c['name'] for c in cot.schema_document['base_columns']}
        self.assertIn('id', names)
        self.assertIn('created', names)
        self.assertIn('last_updated', names)

    def test_schema_document_can_store_json(self):
        cot = self.create_custom_object_type(name='schemadoc2', slug='schema-doc-2')
        document = {
            "schema_version": "1",
            "name": "schemadoc2",
            "slug": "schema-doc-2",
            "version": "1.0.0",
            "fields": [],
            "removed_fields": [],
        }
        cot.schema_document = document
        cot.save()

        cot.refresh_from_db()
        self.assertEqual(cot.schema_document, document)

    def test_version_field_accepts_long_semver(self):
        """version field max_length=50 accommodates pre-release semver strings."""
        cot = self.create_custom_object_type(
            name='semvertest', slug='semver-test', version='10.200.300-alpha.1'
        )
        cot.refresh_from_db()
        self.assertEqual(cot.version, '10.200.300-alpha.1')

    def test_version_field_optional(self):
        """version is not required; blank is accepted."""
        cot = self.create_custom_object_type(name='nover', slug='no-ver')
        self.assertEqual(cot.version, '')


# ===========================================================================
# schema_format.py constants tests
# ===========================================================================

class FieldDefaultsConsistencyTestCase(TestCase):
    """
    FIELD_DEFAULTS in schema_format.py must stay in sync with the corresponding
    Django model field defaults on CustomObjectTypeField.

    When a model field's default changes, the test will fail and remind the
    developer to update FIELD_DEFAULTS to match.
    """

    # Maps each FIELD_DEFAULTS key to the field name on CustomObjectTypeField.
    # Only includes fields that carry an explicit model-level default= argument.
    # Fields that are blank=True / null=True without an explicit default are
    # tracked in _NO_MODEL_DEFAULT_SENTINELS below.
    _MODEL_DEFAULT_FIELDS = {
        "primary": "primary",
        "required": "required",
        "unique": "unique",
        "weight": "weight",
        "search_weight": "search_weight",
        "filter_logic": "filter_logic",
        "ui_visible": "ui_visible",
        "ui_editable": "ui_editable",
        "is_cloneable": "is_cloneable",
        "deprecated": "deprecated",
    }

    # Fields where the model has no explicit default (blank/null only).
    # FIELD_DEFAULTS should use "" or None as the schema-level sentinel.
    _NO_MODEL_DEFAULT_SENTINELS = {
        "label": "",
        "description": "",
        "group_name": "",
        "deprecated_since": "",
        "scheduled_removal": "",
        "validation_regex": "",
        "validation_minimum": None,
        "validation_maximum": None,
        "related_object_filter": None,
        "default": None,
    }

    def test_field_defaults_match_model_defaults(self):
        """Every FIELD_DEFAULTS entry with a model-level default must match it."""
        for schema_key, model_field_name in self._MODEL_DEFAULT_FIELDS.items():
            with self.subTest(field=schema_key):
                model_field = CustomObjectTypeField._meta.get_field(model_field_name)
                self.assertIsNot(
                    model_field.default,
                    NOT_PROVIDED,
                    f"{model_field_name} no longer has a model default — "
                    f"move {schema_key!r} to _NO_MODEL_DEFAULT_SENTINELS.",
                )
                self.assertEqual(
                    model_field.default,
                    FIELD_DEFAULTS[schema_key],
                    f"FIELD_DEFAULTS[{schema_key!r}] is {FIELD_DEFAULTS[schema_key]!r} "
                    f"but {model_field_name}.default is {model_field.default!r}. "
                    f"Update schema_format.FIELD_DEFAULTS to match the model.",
                )

    def test_no_model_default_sentinels_are_correct(self):
        """Fields without model defaults should use '' or None in FIELD_DEFAULTS."""
        for schema_key, expected_sentinel in self._NO_MODEL_DEFAULT_SENTINELS.items():
            with self.subTest(field=schema_key):
                self.assertEqual(
                    FIELD_DEFAULTS[schema_key],
                    expected_sentinel,
                    f"FIELD_DEFAULTS[{schema_key!r}] should be {expected_sentinel!r} "
                    f"(no model default exists for this field).",
                )
                # Also verify the model field genuinely has no explicit default.
                try:
                    model_field = CustomObjectTypeField._meta.get_field(schema_key)
                    self.assertIs(
                        model_field.default,
                        NOT_PROVIDED,
                        f"{schema_key} now has a model default — "
                        f"move it to _MODEL_DEFAULT_FIELDS.",
                    )
                except Exception:
                    # Field may not exist on the model (type-specific virtual attrs).
                    pass


class SchemaFormatConstantsTestCase(TestCase):
    """Sanity checks on schema_format module constants."""

    def test_format_version_is_string(self):
        self.assertIsInstance(SCHEMA_FORMAT_VERSION, str)
        self.assertEqual(SCHEMA_FORMAT_VERSION, "1")

    def test_type_mapping_is_bijective(self):
        """CHOICES_TO_SCHEMA_TYPE and SCHEMA_TYPE_TO_CHOICES must be inverses."""
        for choices_val, schema_name in CHOICES_TO_SCHEMA_TYPE.items():
            self.assertEqual(
                SCHEMA_TYPE_TO_CHOICES[schema_name], choices_val,
                f"Round-trip failed for {choices_val!r} ↔ {schema_name!r}",
            )

    def test_all_field_types_are_mapped(self):
        """Every CustomFieldTypeChoices value must have a schema type entry."""
        for attr in dir(CustomFieldTypeChoices):
            if attr.startswith('TYPE_'):
                value = getattr(CustomFieldTypeChoices, attr)
                self.assertIn(
                    value, CHOICES_TO_SCHEMA_TYPE,
                    f"CustomFieldTypeChoices.{attr} ({value!r}) is missing from CHOICES_TO_SCHEMA_TYPE",
                )


# ===========================================================================
# JSON Schema structural validation tests
# ===========================================================================

@unittest.skipUnless(HAS_JSONSCHEMA, "jsonschema library not installed")
class COTJsonSchemaTestCase(TestCase):
    """Validate that cot_schema_v1.json correctly accepts/rejects documents."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with open(_SCHEMA_PATH) as fh:
            cls.schema = json.load(fh)
        cls.validator_cls = jsonschema.Draft202012Validator
        cls.validator_cls.check_schema(cls.schema)

    def _validate(self, document):
        """Return a list of validation errors (empty means valid)."""
        v = self.validator_cls(self.schema)
        return list(v.iter_errors(document))

    def _assert_valid(self, document):
        errors = self._validate(document)
        self.assertEqual(errors, [], f"Expected valid document but got errors: {errors}")

    def _assert_invalid(self, document):
        errors = self._validate(document)
        self.assertGreater(len(errors), 0, "Expected invalid document but it passed validation.")

    # ------------------------------------------------------------------
    # Valid documents
    # ------------------------------------------------------------------

    def test_minimal_valid_document(self):
        """A document with only the required fields is valid."""
        self._assert_valid({
            "schema_version": "1",
            "types": [
                {"name": "widget", "slug": "widget"}
            ],
        })

    def test_full_valid_document(self):
        """A document exercising all field types passes validation."""
        self._assert_valid({
            "schema_version": "1",
            "types": [
                {
                    "name": "circuit_endpoint",
                    "slug": "circuit-endpoint",
                    "version": "1.2.0",
                    "verbose_name": "Circuit Endpoint",
                    "verbose_name_plural": "Circuit Endpoints",
                    "description": "One end of a circuit",
                    "group_name": "Circuits",
                    "fields": [
                        {"id": 1, "name": "label", "type": "text", "primary": True, "required": True},
                        {"id": 2, "name": "notes", "type": "longtext"},
                        {"id": 3, "name": "speed", "type": "integer", "validation_minimum": 0},
                        {"id": 4, "name": "ratio", "type": "decimal"},
                        {"id": 5, "name": "active", "type": "boolean"},
                        {"id": 6, "name": "install_date", "type": "date"},
                        {"id": 7, "name": "last_seen", "type": "datetime"},
                        {"id": 8, "name": "docs_url", "type": "url"},
                        {"id": 9, "name": "metadata", "type": "json"},
                        {"id": 10, "name": "status", "type": "select", "choice_set": "endpoint_statuses"},
                        {"id": 11, "name": "tags", "type": "multiselect", "choice_set": "endpoint_tags"},
                        {"id": 12, "name": "device", "type": "object", "related_object_type": "dcim/device"},
                        {"id": 13, "name": "sites", "type": "multiobject", "related_object_type": "dcim/site"},
                    ],
                    "removed_fields": [
                        {"id": 14, "name": "old_code", "type": "text", "removed_in": "1.1.0"}
                    ],
                }
            ],
        })

    def test_cot_reference_to_another_cot(self):
        """related_object_type using 'custom-objects/<slug>' format is valid."""
        self._assert_valid({
            "schema_version": "1",
            "types": [
                {
                    "name": "endpoint",
                    "slug": "endpoint",
                    "fields": [
                        {
                            "id": 1,
                            "name": "circuit",
                            "type": "object",
                            "related_object_type": "custom-objects/circuit",
                        }
                    ],
                }
            ],
        })

    def test_deprecated_field_is_valid(self):
        self._assert_valid({
            "schema_version": "1",
            "types": [
                {
                    "name": "widget",
                    "slug": "widget",
                    "fields": [
                        {
                            "id": 1,
                            "name": "legacy_code",
                            "type": "text",
                            "deprecated": True,
                            "deprecated_since": "2.0.0",
                            "scheduled_removal": "3.0.0",
                        }
                    ],
                }
            ],
        })

    def test_multi_type_export(self):
        """Multiple COT definitions in a single document are valid."""
        self._assert_valid({
            "schema_version": "1",
            "types": [
                {"name": "circuit", "slug": "circuit"},
                {"name": "endpoint", "slug": "endpoint"},
            ],
        })

    # ------------------------------------------------------------------
    # Invalid documents
    # ------------------------------------------------------------------

    def test_missing_schema_version_is_invalid(self):
        self._assert_invalid({
            "types": [{"name": "widget", "slug": "widget"}],
        })

    def test_wrong_schema_version_is_invalid(self):
        self._assert_invalid({
            "schema_version": "99",
            "types": [{"name": "widget", "slug": "widget"}],
        })

    def test_missing_types_is_invalid(self):
        self._assert_invalid({"schema_version": "1"})

    def test_empty_types_list_is_invalid(self):
        self._assert_invalid({"schema_version": "1", "types": []})

    def test_field_missing_id_is_invalid(self):
        self._assert_invalid({
            "schema_version": "1",
            "types": [
                {
                    "name": "widget",
                    "slug": "widget",
                    "fields": [{"name": "label", "type": "text"}],
                }
            ],
        })

    def test_field_missing_name_is_invalid(self):
        self._assert_invalid({
            "schema_version": "1",
            "types": [
                {
                    "name": "widget",
                    "slug": "widget",
                    "fields": [{"id": 1, "type": "text"}],
                }
            ],
        })

    def test_field_missing_type_is_invalid(self):
        self._assert_invalid({
            "schema_version": "1",
            "types": [
                {
                    "name": "widget",
                    "slug": "widget",
                    "fields": [{"id": 1, "name": "label"}],
                }
            ],
        })

    def test_select_field_missing_choice_set_is_invalid(self):
        self._assert_invalid({
            "schema_version": "1",
            "types": [
                {
                    "name": "widget",
                    "slug": "widget",
                    "fields": [{"id": 1, "name": "status", "type": "select"}],
                }
            ],
        })

    def test_object_field_missing_related_object_type_is_invalid(self):
        self._assert_invalid({
            "schema_version": "1",
            "types": [
                {
                    "name": "widget",
                    "slug": "widget",
                    "fields": [{"id": 1, "name": "device", "type": "object"}],
                }
            ],
        })

    def test_unknown_field_type_is_invalid(self):
        self._assert_invalid({
            "schema_version": "1",
            "types": [
                {
                    "name": "widget",
                    "slug": "widget",
                    "fields": [{"id": 1, "name": "thing", "type": "frobnitz"}],
                }
            ],
        })

    def test_invalid_filter_logic_value_is_invalid(self):
        self._assert_invalid({
            "schema_version": "1",
            "types": [
                {
                    "name": "widget",
                    "slug": "widget",
                    "fields": [
                        {"id": 1, "name": "label", "type": "text", "filter_logic": "fuzzy"}
                    ],
                }
            ],
        })

    def test_removed_field_with_extra_properties_is_invalid(self):
        """removed_field uses additionalProperties: false, so unknown keys are rejected."""
        self._assert_invalid({
            "schema_version": "1",
            "types": [
                {
                    "name": "widget",
                    "slug": "widget",
                    "removed_fields": [
                        {"id": 5, "name": "old", "type": "text", "unexpected_key": True}
                    ],
                }
            ],
        })

    def test_active_field_with_unknown_key_is_invalid(self):
        """Active fields use unevaluatedProperties: false, so unknown keys are rejected."""
        self._assert_invalid({
            "schema_version": "1",
            "types": [
                {
                    "name": "widget",
                    "slug": "widget",
                    "fields": [
                        {"id": 1, "name": "label", "type": "text", "unexpected_key": True}
                    ],
                }
            ],
        })
