"""
Contract test: every field on CustomObjectTypeField must be either tracked in
the portable schema pipeline or explicitly listed in _SCHEMA_EXCLUDED_ATTRS
with a documented reason.

When this test fails after a model field is added or renamed, the developer
must do one of two things:

  1. Add the field to FIELD_BASE_ATTRS (all field types) or FIELD_TYPE_ATTRS[<type>]
     (type-specific) in schema/format.py, then wire it up in the exporter,
     comparator, and executor.

  2. Add the field name to _SCHEMA_EXCLUDED_ATTRS below with a short comment
     explaining why it is intentionally omitted from the portable schema format.

This test does not require database access.
"""
from django.test import SimpleTestCase

from netbox_custom_objects.models import CustomObjectTypeField
from netbox_custom_objects.schema.format import FIELD_BASE_ATTRS, FIELD_TYPE_ATTRS


# Fields that are intentionally NOT part of the portable schema format.
# Each comment explains why.
_SCHEMA_EXCLUDED_ATTRS = frozenset({
    # ── Infrastructure / identity ────────────────────────────────────────────
    "id",                  # DB primary key — meaningless across installations
    "custom_object_type",  # parent FK — establishes ownership, not a field attribute
    "schema_id",           # assigned by the executor; not authored in schema documents
    # ── Required schema keys handled directly (not via the attrs loops) ──────
    "name",                # always present as a required top-level key in schema defs
    "type",                # always present as a required top-level key in schema defs
    # ── Audit timestamps (from ChangeLoggedModel) ───────────────────────────
    "created",             # system-managed; meaningless to import
    "last_updated",        # system-managed; meaningless to import
    # ── Intentionally omitted (editorial) ───────────────────────────────────
    "comments",            # editorial annotation; see cot_schema_v1.json $comment
    # ── Not yet in the schema format (pending design decision) ──────────────
    "context",             # display preference flag — not structural; omitted for now
    "related_name",        # reverse relation accessor name — omitted for now
    # Polymorphic field support in the schema pipeline is deferred (#442 follow-up):
    # - is_polymorphic: bool flag that changes how object/multiobject fields resolve targets
    # - related_object_types: M2M — requires list encoding/decoding and M2M apply logic
    #   that does not yet exist in exporter, comparator, or executor.
    "is_polymorphic",
    "related_object_types",
})


class SchemaFieldCoverageTestCase(SimpleTestCase):
    """Assert every CustomObjectTypeField field is accounted for in the schema pipeline."""

    def test_all_cotf_fields_accounted_for(self):
        # All attrs known to the schema pipeline.
        covered = set(FIELD_BASE_ATTRS)
        for attrs in FIELD_TYPE_ATTRS.values():
            covered |= attrs

        # All concrete forward fields defined directly on the model
        # (local_fields = non-m2m; local_many_to_many = m2m if any).
        model_field_names = {f.name for f in CustomObjectTypeField._meta.local_fields}
        model_field_names |= {f.name for f in CustomObjectTypeField._meta.local_many_to_many}

        unaccounted = model_field_names - covered - _SCHEMA_EXCLUDED_ATTRS

        self.assertEqual(
            unaccounted,
            set(),
            "\n\nThe following CustomObjectTypeField field(s) are neither wired into "
            "the portable schema pipeline (FIELD_BASE_ATTRS / FIELD_TYPE_ATTRS in "
            "schema/format.py) nor listed in _SCHEMA_EXCLUDED_ATTRS in this test:\n"
            f"  {sorted(unaccounted)}\n\n"
            "Either add the field(s) to the schema pipeline (exporter, comparator, "
            "executor, and cot_schema_v1.json) or add them to _SCHEMA_EXCLUDED_ATTRS "
            "with a comment explaining why they are intentionally excluded.",
        )
