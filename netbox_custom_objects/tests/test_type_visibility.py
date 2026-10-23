"""
Custom Object Type definitions are visible only to users who may view the type or its objects.
"""

import json
from unittest import mock

from django.contrib.auth.models import AnonymousUser
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from core.models import ObjectType
from users.models import ObjectPermission

from netbox_custom_objects.graphql import live as live_module
from netbox_custom_objects.models import CustomObjectType, CustomObjectTypeField
from netbox_custom_objects.schema.exporter import export_cot
from netbox_custom_objects.utilities import visible_custom_object_type_ids

from .base import CustomObjectsTestCase, create_token

INTROSPECTION = (
    "{ __schema { queryType { fields { name } } types { name fields { name type { name ofType { name } } } } } }"
)


class TypeVisibilityTestCase(CustomObjectsTestCase, TestCase):

    def setUp(self):
        super().setUp()
        # "contracts" has a field pointing at "secrets"; "secrets" has a field only it defines.
        self.secrets = self.create_simple_custom_object_type(name="secret_project", slug="secret-projects")
        self.create_custom_object_type_field(self.secrets, name="codename", label="Codename", type="text")
        self.contracts = self.create_simple_custom_object_type(name="contract", slug="contracts")
        self.create_custom_object_type_field(
            self.contracts, name="project", label="Project", type="object",
            related_object_type=self.secrets.object_type,
        )
        self.contracts_model = self.contracts.get_model()
        self.secrets_model = self.secrets.get_model()

        self.client = APIClient()
        self.header = {"HTTP_AUTHORIZATION": f"Token {create_token(self.user)}"}

    def _grant(self, model, constraints=None):
        perm = ObjectPermission.objects.create(name="view", actions=["view"], constraints=constraints)
        perm.users.add(self.user)
        perm.object_types.add(ObjectType.objects.get_for_model(model))


class VisibleIdsTestCase(TypeVisibilityTestCase):

    def test_nothing_without_permissions(self):
        self.assertEqual(visible_custom_object_type_ids(self.user), frozenset())
        self.assertEqual(visible_custom_object_type_ids(AnonymousUser()), frozenset())

    def test_superuser_sees_all(self):
        self.user.is_superuser = True
        self.user.save()
        self.assertEqual(visible_custom_object_type_ids(self.user), {self.secrets.pk, self.contracts.pk})

    def test_view_on_type_honours_constraints(self):
        self._grant(CustomObjectType, constraints={"slug": "contracts"})
        self.assertEqual(visible_custom_object_type_ids(self.user), {self.contracts.pk})

    def test_view_on_objects_shows_their_type(self):
        self._grant(self.contracts_model)
        self.assertEqual(visible_custom_object_type_ids(self.user), {self.contracts.pk})

    def test_exempt_view_permission_applies_to_anonymous(self):
        exempt = [self.contracts_model._meta.label_lower]
        with override_settings(EXEMPT_VIEW_PERMISSIONS=exempt):
            self.assertEqual(visible_custom_object_type_ids(AnonymousUser()), {self.contracts.pk})


class APIRootTestCase(TypeVisibilityTestCase):

    def _root(self):
        response = self.client.get(reverse("plugins-api:netbox_custom_objects-api:api-root"), **self.header)
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_lists_no_types_without_permission(self):
        root = self._root()
        self.assertNotIn("contracts", root)
        self.assertNotIn("secret-projects", root)

    def test_lists_only_visible_types(self):
        self._grant(self.contracts_model)
        root = self._root()
        self.assertIn("contracts", root)
        self.assertNotIn("secret-projects", root)


class SchemaPreviewTestCase(TypeVisibilityTestCase):

    def _preview(self, slug):
        doc = {"schema_version": "1", "types": [{"name": slug.replace("-", "_"), "slug": slug}]}
        return self.client.post(
            reverse("plugins-api:netbox_custom_objects-api:schema-preview"), doc, format="json", **self.header,
        )

    def test_forbidden_without_view_permission(self):
        response = self._preview("secret-projects")
        self.assertEqual(response.status_code, 403)
        self.assertNotIn(b"codename", response.content)

    def test_forbidden_for_new_type_without_view_permission(self):
        self.assertEqual(self._preview("brand-new").status_code, 403)

    def test_forbidden_for_type_outside_constraint(self):
        self._grant(CustomObjectType, constraints={"slug": "contracts"})
        self.assertEqual(self._preview("secret-projects").status_code, 403)
        self.assertEqual(self._preview("contracts").status_code, 200)

    def _retarget_project(self, url):
        type_def = export_cot(self.contracts)
        (project,) = [f for f in type_def["fields"] if f["name"] == "project"]
        project["related_object_type"] = "dcim/site"
        doc = {"schema_version": "1", "types": [type_def]}
        if url.endswith("apply"):
            doc = {"schema": doc}
        return self.client.post(reverse(url), doc, format="json", **self.header)

    def test_forbidden_when_stored_related_type_outside_constraint(self):
        self._grant(CustomObjectType, constraints={"slug": "contracts"})
        response = self._retarget_project("plugins-api:netbox_custom_objects-api:schema-preview")
        self.assertEqual(response.status_code, 403)
        self.assertNotIn(b"secret-projects", response.content)

        self._grant(CustomObjectType)
        response = self._retarget_project("plugins-api:netbox_custom_objects-api:schema-preview")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"custom-objects/secret-projects", response.content)

    def test_apply_forbidden_when_stored_related_type_outside_constraint(self):
        for model, actions in ((CustomObjectType, ["view", "add", "change"]), (CustomObjectTypeField, ["change"])):
            perm = ObjectPermission.objects.create(
                name=f"{model._meta.model_name}-apply", actions=actions,
                constraints={"slug": "contracts"} if model is CustomObjectType else None,
            )
            perm.users.add(self.user)
            perm.object_types.add(ObjectType.objects.get_for_model(model))
        response = self._retarget_project("plugins-api:netbox_custom_objects-api:schema-apply")
        self.assertEqual(response.status_code, 403)
        self.assertNotIn(b"secret-projects", response.content)
        self.assertEqual(self.contracts.fields.get(name="project").related_object_type, self.secrets.object_type)


class CoreObjectTypesTestCase(TypeVisibilityTestCase):

    def test_descriptions_omit_field_names(self):
        response = self.client.get(
            reverse("core-api:objecttype-list"), {"app_label": "netbox_custom_objects", "limit": 0}, **self.header,
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"codename", response.content)
        descriptions = {
            row["model"]: row["description"] for row in response.data["results"]
        }
        self.assertEqual(descriptions[self.secrets_model._meta.model_name], "An object of a Custom Object Type.")


class GraphQLSchemaTestCase(TypeVisibilityTestCase):

    def setUp(self):
        super().setUp()
        live_module.reset_cache()
        self.addCleanup(live_module.reset_cache)
        patcher = mock.patch(
            "netbox_custom_objects.CustomObjectsPluginConfig.should_skip_dynamic_model_creation",
            return_value=False,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _introspect(self, **headers):
        response = self.client.post(reverse("graphql"), {"query": INTROSPECTION}, format="json", **headers)
        self.assertEqual(response.status_code, 200, response.content)
        return json.loads(response.content)["data"]["__schema"]

    @staticmethod
    def _root_fields(schema):
        return {field["name"] for field in schema["queryType"]["fields"]}

    @staticmethod
    def _field_names(schema):
        return {field["name"] for t in schema["types"] for field in (t["fields"] or [])}

    def test_no_definitions_without_permission(self):
        schema = self._introspect(**self.header)
        self.assertFalse({name for name in self._root_fields(schema) if name.startswith("custom_objects_")})
        self.assertFalse({"codename", "project"} & self._field_names(schema))

    @override_settings(LOGIN_REQUIRED=False)
    def test_no_definitions_for_anonymous(self):
        schema = self._introspect()
        self.assertFalse({name for name in self._root_fields(schema) if name.startswith("custom_objects_")})

    def test_only_visible_types_and_hidden_targets_stubbed(self):
        self._grant(self.contracts_model)
        schema = self._introspect(**self.header)
        root = {name for name in self._root_fields(schema) if name.startswith("custom_objects_")}
        self.assertEqual(root, {"custom_objects_contracts", "custom_objects_contracts_list"})
        self.assertNotIn("codename", self._field_names(schema))
        (project,) = [
            field for t in schema["types"] for field in (t["fields"] or []) if field["name"] == "project"
        ]
        self.assertEqual(project["type"]["name"] or project["type"]["ofType"]["name"], "CustomObjectRelatedObjectType")

    def test_superuser_sees_every_type(self):
        self.user.is_superuser = True
        self.user.save()
        root = self._root_fields(self._introspect(**self.header))
        self.assertTrue({"custom_objects_contracts", "custom_objects_secret_projects"} <= root)


class SchemaCacheCapTestCase(SimpleTestCase):
    """The cached GraphQL schemas are capped per process, across every branch."""

    def setUp(self):
        live_module.reset_cache()
        self.addCleanup(live_module.reset_cache)
        for patcher in (
            mock.patch(
                "netbox_custom_objects.CustomObjectsPluginConfig.should_skip_dynamic_model_creation",
                return_value=False,
            ),
            mock.patch.object(live_module, "cached_schema_signature", return_value="signature"),
            mock.patch.object(live_module, "build_full_schema", side_effect=lambda visible: object()),
            mock.patch.object(live_module, "_MAX_SCHEMAS", 3),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _schema(self, branch, visible):
        with (
            mock.patch.object(live_module, "_active_branch_key", return_value=branch),
            mock.patch(
                "netbox_custom_objects.utilities.visible_custom_object_type_ids", return_value=frozenset(visible),
            ),
        ):
            return live_module.get_live_schema(None)

    def test_users_who_see_the_same_types_share_a_schema(self):
        self.assertIs(self._schema(None, {1}), self._schema(None, {1}))

    def test_oldest_schema_dropped_across_branches(self):
        first = self._schema(None, {1})
        self._schema(None, {2})
        self._schema(7, {1})
        self._schema(7, {2})
        self.assertEqual(sum(len(schemas) for schemas in live_module._schema_cache.values()), 3)
        self.assertNotIn(frozenset({1}), live_module._schema_cache[None])
        self.assertIsNot(self._schema(None, {1}), first)
