"""
REST API writes enforce add, change and delete permissions, including their constraints, as core endpoints do.

Without the action's permission a write is refused (403); an object outside the user's constraint can't be
reached (404); and a write whose result falls outside the constraint is refused (403) and leaves nothing changed.
"""

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from packaging.version import Version
from rest_framework import status
from rest_framework.test import APIClient

from core.models import ObjectType
from users.models import ObjectPermission

from netbox_custom_objects.models import CustomObjectType, CustomObjectTypeConstraint, CustomObjectTypeField

from ..base import CustomObjectsTestCase, create_token

API = "plugins-api:netbox_custom_objects-api"
NETBOX_VERSION = Version(settings.RELEASE.version)


class APIPermissionTestCase(CustomObjectsTestCase, TestCase):

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.header = {"HTTP_AUTHORIZATION": f"Token {create_token(self.user)}"}

    def _grant(self, model, actions, constraints=None):
        perm = ObjectPermission.objects.create(
            name=f"{model._meta.model_name}-{'-'.join(actions)}", actions=list(actions), constraints=constraints,
        )
        perm.users.add(self.user)
        perm.object_types.add(ObjectType.objects.get_for_model(model))

    def _post(self, url, data):
        return self.client.post(url, data, format="json", **self.header)

    def _patch(self, url, data):
        return self.client.patch(url, data, format="json", **self.header)

    def _delete(self, url):
        return self.client.delete(url, **self.header)


class CustomObjectPermissionTestCase(APIPermissionTestCase):

    def setUp(self):
        super().setUp()
        self.cot = self.create_simple_custom_object_type(name="zone", slug="zones")
        self.model = self.cot.get_model()
        self.ok = self.model.objects.create(name="ok")
        self.other = self.model.objects.create(name="other")
        self.list_url = reverse(f"{API}:customobject-list", kwargs={"custom_object_type": "zones"})

    def _detail(self, obj):
        return reverse(f"{API}:customobject-detail", kwargs={"custom_object_type": "zones", "pk": obj.pk})

    def test_create_needs_add_permission(self):
        self._grant(self.model, ["view"])
        self.assertEqual(self._post(self.list_url, {"name": "new"}).status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(self.model.objects.filter(name="new").exists())

    def test_create_outside_add_constraint_refused(self):
        self._grant(self.model, ["view", "add"], constraints={"name__startswith": "ok"})
        self.assertEqual(self._post(self.list_url, {"name": "bad"}).status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(self.model.objects.filter(name="bad").exists())
        self.assertEqual(self._post(self.list_url, {"name": "ok-2"}).status_code, status.HTTP_201_CREATED)

    def test_change_needs_change_permission(self):
        self._grant(self.model, ["view"])
        response = self._patch(self._detail(self.ok), {"description": "x"})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_change_outside_constraint_not_found(self):
        self._grant(self.model, ["view", "change"], constraints={"name": "ok"})
        response = self._patch(self._detail(self.other), {"description": "x"})
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.other.refresh_from_db()
        self.assertNotEqual(self.other.description, "x")

    def test_change_leaving_constraint_refused(self):
        self._grant(self.model, ["view", "change"], constraints={"name": "ok"})
        self.assertEqual(self._patch(self._detail(self.ok), {"name": "renamed"}).status_code, status.HTTP_403_FORBIDDEN)
        self.ok.refresh_from_db()
        self.assertEqual(self.ok.name, "ok")
        self.assertEqual(self._patch(self._detail(self.ok), {"description": "x"}).status_code, status.HTTP_200_OK)

    def test_delete_needs_delete_permission(self):
        self._grant(self.model, ["view"])
        self.assertEqual(self._delete(self._detail(self.ok)).status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(self.model.objects.filter(pk=self.ok.pk).exists())

    def test_delete_outside_constraint_not_found(self):
        self._grant(self.model, ["view", "delete"], constraints={"name": "ok"})
        self.assertEqual(self._delete(self._detail(self.other)).status_code, status.HTTP_404_NOT_FOUND)
        self.assertTrue(self.model.objects.filter(pk=self.other.pk).exists())
        self.assertEqual(self._delete(self._detail(self.ok)).status_code, status.HTTP_204_NO_CONTENT)

    def test_bulk_delete_outside_constraint_spares_it(self):
        self._grant(self.model, ["view", "delete"], constraints={"name": "ok"})
        response = self.client.delete(
            self.list_url, [{"id": self.ok.pk}, {"id": self.other.pk}], format="json", **self.header,
        )
        self.assertTrue(self.model.objects.filter(pk=self.other.pk).exists())
        if NETBOX_VERSION >= Version("4.7"):
            # Core rejects the batch when any object is outside the user's queryset.
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertTrue(self.model.objects.filter(pk=self.ok.pk).exists())
        else:
            # COMPAT(netbox<4.7.0): core skips objects outside the queryset and deletes the rest.
            self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
            self.assertFalse(self.model.objects.filter(pk=self.ok.pk).exists())

    def test_permission_on_another_type_grants_nothing(self):
        other_model = self.create_simple_custom_object_type(name="rack", slug="racks").get_model()
        self._grant(other_model, ["view", "add", "change", "delete"])
        self.assertEqual(self._post(self.list_url, {"name": "new"}).status_code, status.HTTP_403_FORBIDDEN)


class CustomObjectTypePermissionTestCase(APIPermissionTestCase):

    def setUp(self):
        super().setUp()
        self.allowed = self.create_custom_object_type(name="allowed", slug="allowed")
        self.other = self.create_custom_object_type(name="other", slug="other")
        self.list_url = reverse(f"{API}:customobjecttype-list")

    def _detail(self, cot):
        return reverse(f"{API}:customobjecttype-detail", kwargs={"pk": cot.pk})

    def test_create_needs_add_permission(self):
        self._grant(CustomObjectType, ["view"])
        response = self._post(self.list_url, {"name": "new", "slug": "new"})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(CustomObjectType.objects.filter(slug="new").exists())

    def test_create_outside_add_constraint_refused(self):
        self._grant(CustomObjectType, ["view", "add"], constraints={"slug__startswith": "ok-"})
        response = self._post(self.list_url, {"name": "bad", "slug": "bad"})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(CustomObjectType.objects.filter(slug="bad").exists())

    def test_change_needs_change_permission(self):
        self._grant(CustomObjectType, ["view"])
        response = self._patch(self._detail(self.allowed), {"description": "x"})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_delete_needs_delete_permission(self):
        self._grant(CustomObjectType, ["view"])
        self.assertEqual(self._delete(self._detail(self.allowed)).status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(CustomObjectType.objects.filter(pk=self.allowed.pk).exists())

    def test_change_outside_constraint_not_found(self):
        self._grant(CustomObjectType, ["view", "change"], constraints={"slug": "allowed"})
        self.assertEqual(
            self._patch(self._detail(self.other), {"description": "x"}).status_code, status.HTTP_404_NOT_FOUND,
        )
        self.assertEqual(self._patch(self._detail(self.allowed), {"description": "x"}).status_code, status.HTTP_200_OK)

    def test_change_leaving_constraint_refused(self):
        self._grant(CustomObjectType, ["view", "change"], constraints={"slug": "allowed"})
        response = self._patch(self._detail(self.allowed), {"slug": "moved"})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.allowed.refresh_from_db()
        self.assertEqual(self.allowed.slug, "allowed")

    def test_delete_outside_constraint_not_found(self):
        self._grant(CustomObjectType, ["view", "delete"], constraints={"slug": "allowed"})
        self.assertEqual(self._delete(self._detail(self.other)).status_code, status.HTTP_404_NOT_FOUND)
        self.assertTrue(CustomObjectType.objects.filter(pk=self.other.pk).exists())


class CustomObjectTypeFieldPermissionTestCase(APIPermissionTestCase):

    def setUp(self):
        super().setUp()
        self.cot = self.create_custom_object_type(name="zone", slug="zones")
        self.code = self.create_custom_object_type_field(self.cot, name="code", label="Code", type="text")
        self.notes = self.create_custom_object_type_field(self.cot, name="notes", label="Notes", type="text")
        self.list_url = reverse(f"{API}:customobjecttypefield-list")

    def _detail(self, field):
        return reverse(f"{API}:customobjecttypefield-detail", kwargs={"pk": field.pk})

    def test_create_needs_add_permission(self):
        self._grant(CustomObjectTypeField, ["view"])
        response = self._post(self.list_url, {"custom_object_type": self.cot.pk, "name": "new", "type": "text"})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(self.cot.fields.filter(name="new").exists())

    def test_create_outside_add_constraint_refused(self):
        self._grant(CustomObjectTypeField, ["view", "add"], constraints={"type": "integer"})
        response = self._post(self.list_url, {"custom_object_type": self.cot.pk, "name": "new", "type": "text"})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(self.cot.fields.filter(name="new").exists())

    def test_change_needs_change_permission(self):
        self._grant(CustomObjectTypeField, ["view"])
        self.assertEqual(self._patch(self._detail(self.code), {"label": "X"}).status_code, status.HTTP_403_FORBIDDEN)

    def test_delete_needs_delete_permission(self):
        self._grant(CustomObjectTypeField, ["view"])
        self.assertEqual(self._delete(self._detail(self.code)).status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(self.cot.fields.filter(pk=self.code.pk).exists())

    def test_change_leaving_constraint_refused(self):
        self._grant(CustomObjectTypeField, ["view", "change"], constraints={"label": "Code"})
        self.assertEqual(self._patch(self._detail(self.code), {"label": "X"}).status_code, status.HTTP_403_FORBIDDEN)
        self.code.refresh_from_db()
        self.assertEqual(self.code.label, "Code")

    def test_change_outside_constraint_not_found(self):
        self._grant(CustomObjectTypeField, ["view", "change"], constraints={"name": "code"})
        self.assertEqual(self._patch(self._detail(self.notes), {"label": "X"}).status_code, status.HTTP_404_NOT_FOUND)
        self.notes.refresh_from_db()
        self.assertEqual(self.notes.label, "Notes")

    def test_delete_outside_constraint_not_found(self):
        self._grant(CustomObjectTypeField, ["view", "delete"], constraints={"name": "code"})
        self.assertEqual(self._delete(self._detail(self.notes)).status_code, status.HTTP_404_NOT_FOUND)
        self.assertTrue(self.cot.fields.filter(pk=self.notes.pk).exists())


class CustomObjectTypeConstraintPermissionTestCase(APIPermissionTestCase):

    def setUp(self):
        super().setUp()
        self.cot = self.create_custom_object_type(name="asset", slug="assets")
        self.vendor = self.create_custom_object_type_field(self.cot, name="vendor", type="text")
        self.serial = self.create_custom_object_type_field(self.cot, name="serial", type="text")
        self.model_field = self.create_custom_object_type_field(self.cot, name="model", type="text")
        self.list_url = reverse(f"{API}:customobjecttypeconstraint-list")
        self.existing = CustomObjectTypeConstraint.objects.create(
            custom_object_type=self.cot, name="vendor_serial",
            field_schema_ids=[self.vendor.schema_id, self.serial.schema_id],
        )

    def _detail(self, constraint):
        return reverse(f"{API}:customobjecttypeconstraint-detail", kwargs={"pk": constraint.pk})

    def _new(self, name="vendor_model"):
        return {"custom_object_type": self.cot.pk, "name": name, "fields": [self.vendor.pk, self.model_field.pk]}

    def test_list_needs_view_permission(self):
        response = self.client.get(self.list_url, **self.header)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_list_honours_view_constraint(self):
        self._grant(CustomObjectTypeConstraint, ["view"], constraints={"name": "nothing"})
        response = self.client.get(self.list_url, **self.header)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 0)

    def test_create_needs_add_permission(self):
        self._grant(CustomObjectTypeConstraint, ["view"])
        self.assertEqual(self._post(self.list_url, self._new()).status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(CustomObjectTypeConstraint.objects.filter(name="vendor_model").exists())

    def test_create_outside_add_constraint_refused(self):
        self._grant(CustomObjectTypeConstraint, ["view", "add"], constraints={"name__startswith": "ok_"})
        self.assertEqual(self._post(self.list_url, self._new()).status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(CustomObjectTypeConstraint.objects.filter(name="vendor_model").exists())

    def test_change_outside_constraint_not_found(self):
        self._grant(CustomObjectTypeConstraint, ["view", "change"], constraints={"name": "nothing"})
        response = self._patch(self._detail(self.existing), {"description": "x"})
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_delete_needs_delete_permission(self):
        self._grant(CustomObjectTypeConstraint, ["view"])
        self.assertEqual(self._delete(self._detail(self.existing)).status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(CustomObjectTypeConstraint.objects.filter(pk=self.existing.pk).exists())
