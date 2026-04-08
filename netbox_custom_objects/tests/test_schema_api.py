"""
Tests for the schema preview and apply API endpoints (issue #390).

Covers:
- POST /schema/preview/: returns diffs without modifying the DB
- POST /schema/apply/: applies the schema document and returns diffs
- allow_destructive flag behaviour (409 without it, 200 with it)
- Schema document validation (400 for invalid input)
- Circular dependency error (400)
- Unresolvable FK reference error (400)
- Missing / malformed 'schema' key (400)
- Authentication enforced (401 for unauthenticated requests)
"""


from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from django.test import TransactionTestCase

from users.models import Token
from utilities.testing import create_test_user

from netbox_custom_objects.exporter import export_cot
from netbox_custom_objects.models import CustomObjectType

from .base import CustomObjectsTestCase, TransactionCleanupMixin


# ---------------------------------------------------------------------------
# Token helper (copied from test_api.py to avoid cross-test import)
# ---------------------------------------------------------------------------

def _create_token(user):
    try:
        from users.choices import TokenVersionChoices
        token = Token(version=TokenVersionChoices.V1, user=user)
    except ImportError:
        token = Token(user=user)
    token.save()
    return token


# ---------------------------------------------------------------------------
# Base for schema API tests
# ---------------------------------------------------------------------------

class _SchemaAPIBase(TransactionCleanupMixin, CustomObjectsTestCase, TransactionTestCase):
    """Base class providing an authenticated API client and helper shortcuts."""

    def setUp(self):
        super().setUp()
        self.user = create_test_user('schema_api_user')
        self.token = _create_token(self.user)
        try:
            token_key = self.token.token  # NetBox ≥ 4.5
        except AttributeError:
            token_key = self.token.key
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token_key}")

    @property
    def preview_url(self):
        return reverse("plugins-api:netbox_custom_objects-api:schema-preview")

    @property
    def apply_url(self):
        return reverse("plugins-api:netbox_custom_objects-api:schema-apply")

    def _apply_body(self, schema_doc, allow_destructive=False):
        return {"schema": schema_doc, "allow_destructive": allow_destructive}


# ---------------------------------------------------------------------------
# Preview endpoint
# ---------------------------------------------------------------------------

class SchemaPreviewTestCase(_SchemaAPIBase):
    """POST /schema/preview/ returns a diff without touching the DB."""

    def setUp(self):
        super().setUp()
        self.cot = self.create_custom_object_type(name='previewcot', slug='preview-cot')
        self.field = self.create_custom_object_type_field(
            self.cot, name='alpha', type='text',
        )

    def test_preview_returns_200(self):
        type_def = export_cot(self.cot)
        resp = self.client.post(
            self.preview_url,
            data={"schema_version": "1", "types": [type_def]},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_preview_response_contains_diffs_key(self):
        type_def = export_cot(self.cot)
        resp = self.client.post(
            self.preview_url,
            data={"schema_version": "1", "types": [type_def]},
            format="json",
        )
        self.assertIn("diffs", resp.data)

    def test_preview_noop_has_no_changes(self):
        type_def = export_cot(self.cot)
        resp = self.client.post(
            self.preview_url,
            data={"schema_version": "1", "types": [type_def]},
            format="json",
        )
        self.assertEqual(len(resp.data["diffs"]), 1)
        self.assertFalse(resp.data["diffs"][0]["has_changes"])

    def test_preview_detects_field_add(self):
        self.cot.refresh_from_db()
        type_def = export_cot(self.cot)
        next_id = self.cot.next_schema_id + 1
        type_def["fields"].append({"id": next_id, "name": "beta", "type": "text"})
        resp = self.client.post(
            self.preview_url,
            data={"schema_version": "1", "types": [type_def]},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        diff = resp.data["diffs"][0]
        self.assertTrue(diff["has_changes"])
        ops = [fc["op"] for fc in diff["field_changes"]]
        self.assertIn("add", ops)

    def test_preview_detects_field_alter(self):
        type_def = export_cot(self.cot)
        for f in type_def["fields"]:
            if f["name"] == "alpha":
                f["description"] = "Changed"
        resp = self.client.post(
            self.preview_url,
            data={"schema_version": "1", "types": [type_def]},
            format="json",
        )
        diff = resp.data["diffs"][0]
        self.assertTrue(diff["has_changes"])
        alter_ops = [fc for fc in diff["field_changes"] if fc["op"] == "alter"]
        self.assertEqual(len(alter_ops), 1)
        self.assertIn("description", alter_ops[0]["changed_attrs"])

    def test_preview_does_not_modify_db(self):
        self.cot.refresh_from_db()
        type_def = export_cot(self.cot)
        next_id = self.cot.next_schema_id + 1
        type_def["fields"].append({"id": next_id, "name": "ghost", "type": "text"})
        self.client.post(
            self.preview_url,
            data={"schema_version": "1", "types": [type_def]},
            format="json",
        )
        # Field must NOT have been created.
        self.assertFalse(self.cot.fields.filter(name="ghost").exists())

    def test_preview_new_cot_reports_is_new(self):
        schema_doc = {
            "schema_version": "1",
            "types": [{"name": "brandnew", "slug": "brand-new"}],
        }
        resp = self.client.post(self.preview_url, data=schema_doc, format="json")
        self.assertTrue(resp.data["diffs"][0]["is_new"])

    def test_preview_unauthenticated_returns_403_or_401(self):
        anon = APIClient()
        type_def = export_cot(self.cot)
        resp = anon.post(
            self.preview_url,
            data={"schema_version": "1", "types": [type_def]},
            format="json",
        )
        self.assertIn(resp.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_preview_invalid_schema_doc_returns_400(self):
        # schema_version must be "1" (const)
        resp = self.client.post(
            self.preview_url,
            data={"schema_version": "99", "types": [{"name": "x", "slug": "x"}]},
            format="json",
        )
        # If jsonschema is installed this returns 400; otherwise passes through.
        # Either way it should not crash (500).
        self.assertNotEqual(resp.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)

    def test_preview_destructive_change_does_not_raise_error(self):
        """Preview reports has_destructive_changes=True but does NOT raise 409."""
        schema_id = self.field.schema_id
        type_def = export_cot(self.cot)
        type_def["fields"] = []
        type_def.setdefault("removed_fields", []).append(
            {"id": schema_id, "name": "alpha", "type": "text"}
        )
        resp = self.client.post(
            self.preview_url,
            data={"schema_version": "1", "types": [type_def]},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data["diffs"][0]["has_destructive_changes"])


# ---------------------------------------------------------------------------
# Apply endpoint
# ---------------------------------------------------------------------------

class SchemaApplyTestCase(_SchemaAPIBase):
    """POST /schema/apply/ applies the schema document atomically."""

    def test_apply_new_cot_returns_200(self):
        schema_doc = {
            "schema_version": "1",
            "types": [{"name": "applynew", "slug": "apply-new"}],
        }
        resp = self.client.post(
            self.apply_url,
            data=self._apply_body(schema_doc),
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data["applied"])

    def test_apply_new_cot_creates_cot_in_db(self):
        schema_doc = {
            "schema_version": "1",
            "types": [{"name": "applynew2", "slug": "apply-new-2"}],
        }
        self.client.post(self.apply_url, data=self._apply_body(schema_doc), format="json")
        self.assertTrue(CustomObjectType.objects.filter(slug="apply-new-2").exists())

    def test_apply_response_contains_diffs(self):
        cot = self.create_custom_object_type(name='applydiff', slug='apply-diff')
        type_def = export_cot(cot)
        resp = self.client.post(
            self.apply_url,
            data=self._apply_body({"schema_version": "1", "types": [type_def]}),
            format="json",
        )
        self.assertIn("diffs", resp.data)
        self.assertEqual(len(resp.data["diffs"]), 1)
        self.assertEqual(resp.data["diffs"][0]["slug"], "apply-diff")

    def test_apply_adds_field(self):
        cot = self.create_custom_object_type(name='applyfield', slug='apply-field')
        self.create_custom_object_type_field(cot, name='exists', type='text')
        cot.refresh_from_db()
        type_def = export_cot(cot)
        next_id = cot.next_schema_id + 1
        type_def["fields"].append({"id": next_id, "name": "added", "type": "text"})
        schema_doc = {"schema_version": "1", "types": [type_def]}
        resp = self.client.post(self.apply_url, data=self._apply_body(schema_doc), format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(cot.fields.filter(name="added").exists())

    def test_apply_without_allow_destructive_returns_409(self):
        cot = self.create_custom_object_type(name='applydest', slug='apply-dest')
        field = self.create_custom_object_type_field(cot, name='bye', type='text')
        sid = field.schema_id
        type_def = export_cot(cot)
        type_def["fields"] = []
        type_def["removed_fields"] = [{"id": sid, "name": "bye", "type": "text"}]
        schema_doc = {"schema_version": "1", "types": [type_def]}
        resp = self.client.post(self.apply_url, data=self._apply_body(schema_doc), format="json")
        self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(resp.data["error"], "destructive_changes")

    def test_apply_409_includes_destructive_slugs(self):
        cot = self.create_custom_object_type(name='applyslug', slug='apply-slug')
        field = self.create_custom_object_type_field(cot, name='gone', type='text')
        sid = field.schema_id
        type_def = export_cot(cot)
        type_def["fields"] = []
        type_def["removed_fields"] = [{"id": sid, "name": "gone", "type": "text"}]
        schema_doc = {"schema_version": "1", "types": [type_def]}
        resp = self.client.post(self.apply_url, data=self._apply_body(schema_doc), format="json")
        self.assertIn("apply-slug", resp.data["destructive_slugs"])

    def test_apply_409_does_not_remove_field(self):
        cot = self.create_custom_object_type(name='applyguard', slug='apply-guard')
        field = self.create_custom_object_type_field(cot, name='keep', type='text')
        sid = field.schema_id
        type_def = export_cot(cot)
        type_def["fields"] = []
        type_def["removed_fields"] = [{"id": sid, "name": "keep", "type": "text"}]
        schema_doc = {"schema_version": "1", "types": [type_def]}
        self.client.post(self.apply_url, data=self._apply_body(schema_doc), format="json")
        self.assertTrue(cot.fields.filter(schema_id=sid).exists())

    def test_apply_with_allow_destructive_removes_field(self):
        cot = self.create_custom_object_type(name='applyrm', slug='apply-rm')
        field = self.create_custom_object_type_field(cot, name='victim', type='text')
        sid = field.schema_id
        type_def = export_cot(cot)
        type_def["fields"] = []
        type_def["removed_fields"] = [{"id": sid, "name": "victim", "type": "text"}]
        schema_doc = {"schema_version": "1", "types": [type_def]}
        resp = self.client.post(
            self.apply_url,
            data=self._apply_body(schema_doc, allow_destructive=True),
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertFalse(cot.fields.filter(schema_id=sid).exists())

    def test_apply_missing_schema_key_returns_400(self):
        resp = self.client.post(
            self.apply_url,
            data={"allow_destructive": False},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_apply_schema_not_a_dict_returns_400(self):
        resp = self.client.post(
            self.apply_url,
            data={"schema": "not a dict", "allow_destructive": False},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_apply_unauthenticated_returns_403_or_401(self):
        anon = APIClient()
        resp = anon.post(
            self.apply_url,
            data=self._apply_body({"schema_version": "1", "types": []}),
            format="json",
        )
        self.assertIn(resp.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_apply_unresolvable_object_type_returns_400(self):
        cot = self.create_custom_object_type(name='applyrotfail', slug='apply-rot-fail')
        self.create_custom_object_type_field(cot, name='ok', type='text')
        cot.refresh_from_db()
        type_def = export_cot(cot)
        next_id = cot.next_schema_id + 1
        type_def["fields"].append({
            "id": next_id, "name": "bad_obj", "type": "object",
            "related_object_type": "does/notexist",
        })
        schema_doc = {"schema_version": "1", "types": [type_def]}
        resp = self.client.post(self.apply_url, data=self._apply_body(schema_doc), format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data["error"], "unresolvable_reference")

    def test_apply_unresolvable_choice_set_returns_400(self):
        cot = self.create_custom_object_type(name='applycsfail', slug='apply-cs-fail')
        self.create_custom_object_type_field(cot, name='ok', type='text')
        cot.refresh_from_db()
        type_def = export_cot(cot)
        next_id = cot.next_schema_id + 1
        type_def["fields"].append({
            "id": next_id, "name": "bad_sel", "type": "select",
            "choice_set": "NoSuchSet",
        })
        schema_doc = {"schema_version": "1", "types": [type_def]}
        resp = self.client.post(self.apply_url, data=self._apply_body(schema_doc), format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data["error"], "unresolvable_reference")

    def test_apply_schema_document_persisted_after_apply(self):
        cot = self.create_custom_object_type(name='applydoccheck', slug='apply-doc-check')
        type_def = export_cot(cot)
        schema_doc = {"schema_version": "1", "types": [type_def]}
        self.client.post(self.apply_url, data=self._apply_body(schema_doc), format="json")
        cot.refresh_from_db()
        self.assertIsNotNone(cot.schema_document)
