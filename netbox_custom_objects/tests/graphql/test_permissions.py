"""
Permission enforcement on the custom-object root queries (``custom_objects_<slug>``
and ``custom_objects_<slug>_list``), through the real ``/graphql/`` endpoint.
"""

import json
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from core.models import ObjectType
from users.models import ObjectPermission

from netbox_custom_objects.graphql import live as live_module

from ..base import CustomObjectsTestCase, create_token

LIST_QUERY = "{ custom_objects_server_list { id name } }"


class GraphQLRootQueryPermissionTestCase(CustomObjectsTestCase, TestCase):

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

        self.client = APIClient()
        self.header = {"HTTP_AUTHORIZATION": f"Token {create_token(self.user)}"}
        self.url = reverse("graphql")

        self.cot = self.create_simple_custom_object_type(name="Server", slug="server")
        self.model = self.cot.get_model()
        self.alpha = self.model.objects.create(name="alpha")
        self.beta = self.model.objects.create(name="beta")

    def _post(self, query, **headers):
        return self.client.post(self.url, data={"query": query}, format="json", **headers)

    def _gql(self, query, **headers):
        response = self._post(query, **(headers or self.header))
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        return json.loads(response.content)

    def _names(self, payload):
        self.assertNotIn("errors", payload, msg=str(payload.get("errors")))
        return sorted(row["name"] for row in payload["data"]["custom_objects_server_list"])

    def _grant(self, model, actions=("view",), constraints=None, enabled=True):
        perm = ObjectPermission.objects.create(
            name=f"perm-{model._meta.model_name}-{'-'.join(actions)}",
            actions=list(actions),
            constraints=constraints,
            enabled=enabled,
        )
        perm.users.add(self.user)
        perm.object_types.add(ObjectType.objects.get_for_model(model))
        return perm

    def _single(self, pk):
        return self._gql(f'{{ custom_objects_server(id: "{pk}") {{ id name }} }}')

    def _assert_same_error_as_missing(self, payload):
        # A hidden object must be indistinguishable from one that doesn't exist.
        self.assertIsNone(payload["data"])
        missing = self._single(self.beta.pk + 1000)
        self.assertEqual(
            [e["message"] for e in payload["errors"]], [e["message"] for e in missing["errors"]],
        )

    def test_list_empty_without_view_permission(self):
        self.assertEqual(self._names(self._gql(LIST_QUERY)), [])

    def test_list_returns_all_with_view_permission(self):
        self._grant(self.model)
        self.assertEqual(self._names(self._gql(LIST_QUERY)), ["alpha", "beta"])

    def test_view_permission_on_another_type_grants_nothing(self):
        other = self.create_simple_custom_object_type(name="Other", slug="other").get_model()
        other.objects.create(name="visible")
        self._grant(other)

        payload = self._gql("{ custom_objects_server_list { name } custom_objects_other_list { name } }")
        self.assertNotIn("errors", payload, msg=str(payload.get("errors")))
        self.assertEqual(payload["data"]["custom_objects_server_list"], [])
        self.assertEqual(payload["data"]["custom_objects_other_list"], [{"name": "visible"}])

    def test_permission_without_view_action_grants_nothing(self):
        self._grant(self.model, actions=("add", "change", "delete"))
        self.assertEqual(self._names(self._gql(LIST_QUERY)), [])

    def test_disabled_permission_grants_nothing(self):
        self._grant(self.model, enabled=False)
        self.assertEqual(self._names(self._gql(LIST_QUERY)), [])

    def test_constrained_permission_filters_list(self):
        self._grant(self.model, constraints={"name": "alpha"})
        self.assertEqual(self._names(self._gql(LIST_QUERY)), ["alpha"])

    def test_constrained_permission_applies_before_filters(self):
        self._grant(self.model, constraints={"name": "alpha"})
        payload = self._gql('{ custom_objects_server_list(filters: {name: {exact: "beta"}}) { name } }')
        self.assertEqual(self._names(payload), [])

    def test_single_object_within_constraint(self):
        self._grant(self.model, constraints={"name": "alpha"})
        payload = self._single(self.alpha.pk)
        self.assertNotIn("errors", payload, msg=str(payload.get("errors")))
        self.assertEqual(payload["data"]["custom_objects_server"], {"id": str(self.alpha.pk), "name": "alpha"})

    def test_single_object_outside_constraint_is_not_returned(self):
        self._grant(self.model, constraints={"name": "alpha"})
        self._assert_same_error_as_missing(self._single(self.beta.pk))

    def test_single_object_without_permission_is_not_returned(self):
        self._assert_same_error_as_missing(self._single(self.alpha.pk))

    @override_settings(LOGIN_REQUIRED=True)
    def test_anonymous_rejected_when_login_required(self):
        response = self._post(LIST_QUERY, HTTP_ACCEPT="application/json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_invalid_token_rejected(self):
        response = self._post(LIST_QUERY, HTTP_AUTHORIZATION="Token 0123456789abcdef0123456789abcdef01234567")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    @override_settings(LOGIN_REQUIRED=False, EXEMPT_VIEW_PERMISSIONS=[])
    def test_anonymous_sees_nothing_without_exemption(self):
        response = self._post(LIST_QUERY)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertEqual(self._names(json.loads(response.content)), [])

    @override_settings(LOGIN_REQUIRED=False, EXEMPT_VIEW_PERMISSIONS=["*"])
    def test_anonymous_sees_all_with_exemption(self):
        response = self._post(LIST_QUERY)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertEqual(self._names(json.loads(response.content)), ["alpha", "beta"])
