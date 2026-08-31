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
from django.test import TransactionTestCase
from rest_framework import status
from rest_framework.test import APIClient

from core.models import ObjectType
from users.models import ObjectPermission
from utilities.testing import create_test_user

from netbox_custom_objects.schema.exporter import export_cot
from netbox_custom_objects.models import CustomObjectType

from ..base import CustomObjectsTestCase, TransactionCleanupMixin, create_api_token


# ---------------------------------------------------------------------------
# Base for schema API tests
# ---------------------------------------------------------------------------

class _SchemaAPIBase(TransactionCleanupMixin, CustomObjectsTestCase, TransactionTestCase):
    """Base class providing an authenticated API client and helper shortcuts."""

    def setUp(self):
        super().setUp()
        self.user = create_test_user('schema_api_user')
        self.token = create_api_token(self.user)
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

    @staticmethod
    def _next_field_id(cot):
        # next_schema_id stores the *last assigned* ID; +1 is the next available one,
        # mirroring the auto-assign logic in CustomObjectTypeField.save().
        return cot.next_schema_id + 1


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
        next_id = self._next_field_id(self.cot)
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
        next_id = self._next_field_id(self.cot)
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
        # schema_version must be "1" (const in cot_schema_v1.json)
        resp = self.client.post(
            self.preview_url,
            data={"schema_version": "99", "types": [{"name": "x", "slug": "x"}]},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

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

    def setUp(self):
        super().setUp()
        perm = ObjectPermission(name='schema_apply_cot_perm', actions=['add', 'change'])
        perm.save()
        perm.users.add(self.user)
        perm.object_types.add(ObjectType.objects.get_for_model(CustomObjectType))

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
        next_id = self._next_field_id(cot)
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
        next_id = self._next_field_id(cot)
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
        next_id = self._next_field_id(cot)
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

    def test_apply_noop_returns_200(self):
        cot = self.create_custom_object_type(name='applynoop', slug='apply-noop')
        self.create_custom_object_type_field(cot, name='stable', type='text')
        type_def = export_cot(cot)
        schema_doc = {"schema_version": "1", "types": [type_def]}
        # First apply initialises schema_document; second apply is a true no-op.
        self.client.post(self.apply_url, data=self._apply_body(schema_doc), format="json")
        resp = self.client.post(self.apply_url, data=self._apply_body(schema_doc), format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data["applied"])
        self.assertFalse(resp.data["diffs"][0]["has_changes"])

    def test_apply_allow_destructive_string_returns_400(self):
        schema_doc = {"schema_version": "1", "types": []}
        resp = self.client.post(
            self.apply_url,
            data={"schema": schema_doc, "allow_destructive": "true"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)


# ---------------------------------------------------------------------------
# get_models() re-entrancy (issue #685)
# ---------------------------------------------------------------------------

class SchemaApplyMultiCOTRecursionTestCase(_SchemaAPIBase):
    """
    Regression test for issue #685: applying a document that creates multiple
    new, cross-referencing Custom Object Types in one request raised
    RecursionError.

    Generating a brand-new COT's model calls _after_model_generation(), which
    can itself need Django's global relation graph (e.g. via
    ObjectType.objects.get_for_model()'s .create()) -- rebuilding that graph
    calls apps.get_models(), which re-enters this plugin's own get_models(),
    which called get_model() again for every COT (including the one still
    mid-construction) with no way to ever finish. apps.clear_cache() is called
    explicitly here to force a cold relation-tree cache, matching the
    "first time these classes are touched" condition the issue's own
    investigation identified as the trigger (reproducible regardless of
    whatever unrelated activity happened to already warm the cache in a given
    process).
    """

    def setUp(self):
        super().setUp()
        perm = ObjectPermission(name='schema_apply_recursion_cot_perm', actions=['add', 'change'])
        perm.save()
        perm.users.add(self.user)
        perm.object_types.add(ObjectType.objects.get_for_model(CustomObjectType))

    def test_apply_two_new_cross_referencing_cots_in_one_request(self):
        from unittest import mock

        from django.apps import apps as django_apps

        import netbox_custom_objects as nco_pkg

        django_apps.clear_cache()

        # get_models()'s CustomObjectType-enumeration loop -- the one this
        # issue actually recurses through -- is unconditionally disabled under
        # `manage.py test` (should_skip_dynamic_model_creation() returns True
        # whenever "test" in sys.argv). Patch around that so this test
        # exercises the real vulnerable loop instead of a no-op.
        app_config = django_apps.get_app_config('netbox_custom_objects')
        self.enterContext(mock.patch.object(nco_pkg, '_app_ready', True))
        self.enterContext(
            mock.patch.object(app_config, 'should_skip_dynamic_model_creation', return_value=False)
        )

        schema_doc = {
            "schema_version": "1",
            "types": [
                {
                    "name": "ospf_instance",
                    "slug": "ospf-instances",
                    "fields": [
                        {"id": 1, "name": "name", "type": "text", "primary": True, "required": True, "unique": True},
                    ],
                },
                {
                    "name": "ospf_area",
                    "slug": "ospf-areas",
                    "fields": [
                        {"id": 1, "name": "name", "type": "text", "primary": True, "required": True, "unique": True},
                        {
                            "id": 2,
                            "name": "instance",
                            "type": "object",
                            "required": True,
                            "related_object_type": "custom-objects/ospf-instances",
                        },
                    ],
                },
            ],
        }
        resp = self.client.post(self.apply_url, data=self._apply_body(schema_doc), format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.content)
        self.assertTrue(resp.data["applied"])
        self.assertTrue(CustomObjectType.objects.filter(slug="ospf-instances").exists())
        self.assertTrue(CustomObjectType.objects.filter(slug="ospf-areas").exists())

    def test_apply_three_new_cots_chained_references_in_one_request(self):
        """The issue notes a 3-type chain (interface -> area -> instance) fails
        identically to the 2-type case; cover it too."""
        from unittest import mock

        from django.apps import apps as django_apps

        import netbox_custom_objects as nco_pkg

        django_apps.clear_cache()

        # See the comment in test_apply_two_new_cross_referencing_cots_in_one_request:
        # bypasses should_skip_dynamic_model_creation()'s "test" in sys.argv gate
        # so this test exercises get_models()'s real CustomObjectType-enumeration
        # loop instead of the no-op it reduces to under `manage.py test`.
        app_config = django_apps.get_app_config('netbox_custom_objects')
        self.enterContext(mock.patch.object(nco_pkg, '_app_ready', True))
        self.enterContext(
            mock.patch.object(app_config, 'should_skip_dynamic_model_creation', return_value=False)
        )

        schema_doc = {
            "schema_version": "1",
            "types": [
                {
                    "name": "ospf_instance",
                    "slug": "ospf-instances",
                    "fields": [
                        {"id": 1, "name": "name", "type": "text", "primary": True, "required": True, "unique": True},
                    ],
                },
                {
                    "name": "ospf_area",
                    "slug": "ospf-areas",
                    "fields": [
                        {"id": 1, "name": "name", "type": "text", "primary": True, "required": True, "unique": True},
                        {
                            "id": 2,
                            "name": "instance",
                            "type": "object",
                            "required": True,
                            "related_object_type": "custom-objects/ospf-instances",
                        },
                    ],
                },
                {
                    "name": "ospf_interface",
                    "slug": "ospf-interfaces",
                    "fields": [
                        {"id": 1, "name": "name", "type": "text", "primary": True, "required": True, "unique": True},
                        {
                            "id": 2,
                            "name": "area",
                            "type": "object",
                            "required": True,
                            "related_object_type": "custom-objects/ospf-areas",
                        },
                    ],
                },
            ],
        }
        resp = self.client.post(self.apply_url, data=self._apply_body(schema_doc), format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.content)
        self.assertTrue(resp.data["applied"])
        self.assertTrue(CustomObjectType.objects.filter(slug="ospf-instances").exists())
        self.assertTrue(CustomObjectType.objects.filter(slug="ospf-areas").exists())
        self.assertTrue(CustomObjectType.objects.filter(slug="ospf-interfaces").exists())

    def test_get_models_guards_against_reentrant_cot_generation(self):
        """
        Deterministic, environment-independent regression test for the
        get_models() re-entrancy itself.

        The two end-to-end tests above exercise the real API path from #685's
        repro, but whether that path actually blows the Python recursion limit
        depends on incidental factors (whatever else has already touched
        Options._relation_tree in the process, WSGI/middleware stack depth,
        interpreter version) that don't reliably reproduce in this harness.
        More fundamentally, `get_models()`'s CustomObjectType-enumeration loop
        (the one both issues actually recurse through) is unconditionally
        disabled under `manage.py test` -- see
        `CustomObjectsPluginConfig.should_skip_dynamic_model_creation()`, which
        returns True whenever `"test" in sys.argv`. `_app_ready` patches below
        replicate what `ready()` sets once it completes outside of tests, and
        the `should_skip_dynamic_model_creation` patch replicates a production
        (non-test) process, so this test exercises the actual vulnerable loop
        instead of the no-op it reduces to under `manage.py test`.

        This test simulates the documented trigger directly: something deep
        inside _after_model_generation() (ObjectType.objects.get_for_model()
        .create(), or a polymorphic field's related_object_types.all() query --
        see the #686 test in test_polymorphic_fields.py) causes Django to
        rebuild its relation graph, which calls apps.get_models() again while
        the outer get_models() call is still mid-iteration.

        Without the guard, that re-entrant get_models() call walks
        CustomObjectType.objects.all() and calls get_model() again for every
        COT -- including ones already fully generated -- which is the root of
        the unbounded growth. With the guard, the re-entrant call must return
        immediately after yielding the plain Django models, without touching
        CustomObjectType at all.
        """
        from collections import defaultdict
        from unittest import mock

        from django.apps import apps as django_apps

        import netbox_custom_objects as nco_pkg

        cot1 = self.create_custom_object_type(name='Reentrancy Source', slug='reentrancy-source')
        cot2 = self.create_custom_object_type(name='Reentrancy Target', slug='reentrancy-target')
        app_config = django_apps.get_app_config('netbox_custom_objects')

        call_counts = defaultdict(int)
        real_get_model = CustomObjectType.get_model
        test_case = self

        def spying_get_model(self, *args, **kwargs):
            call_counts[self.pk] += 1
            if call_counts[self.pk] > 10:
                test_case.fail(
                    f"get_model() called {call_counts[self.pk]} times for COT "
                    f"{self.pk} -- unbounded re-entrant generation (issue #685/#686)"
                )
            result = real_get_model(self, *args, **kwargs)
            if call_counts[self.pk] == 1:
                # Simulate Django re-entering get_models() mid-generation, as it
                # does when something inside _after_model_generation() needs a
                # fresh model's relation graph.
                list(app_config.get_models())
            return result

        with (
            mock.patch.object(CustomObjectType, 'get_model', spying_get_model),
            mock.patch.object(nco_pkg, '_app_ready', True),
            mock.patch.object(app_config, 'should_skip_dynamic_model_creation', return_value=False),
        ):
            # Note: deliberately not calling django_apps.clear_cache() here --
            # its own implementation walks apps.get_models(include_auto_created=True)
            # to expire every model's cache, which would drive this same
            # CustomObjectType loop to completion once already, before the
            # call below even starts.
            list(app_config.get_models())

        # Each COT's model only needs to be generated once. The re-entrant
        # get_models() call simulated above must not have triggered any
        # additional generation for either COT.
        self.assertEqual(call_counts[cot1.pk], 1)
        self.assertEqual(call_counts[cot2.pk], 1)
