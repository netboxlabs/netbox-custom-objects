"""
Tests for polymorphic GenericForeignKey field support (issue #31).

Covers both API and UI (form) paths for:
  - Polymorphic single-object (GFK) fields
  - Polymorphic multi-object (through-table M2M) fields
"""
import json

from django.core.exceptions import ValidationError
from django.test import TransactionTestCase
from django.urls import reverse
from rest_framework import status

from core.models import ObjectType
from dcim.models import Site
from ipam.models import Prefix, IPAddress
from ipam.choices import PrefixStatusChoices
from users.models import ObjectPermission, Token

from netbox_custom_objects.constants import APP_LABEL
from netbox_custom_objects.models import CustomObjectType, CustomObjectTypeField
from netbox_custom_objects.tests.base import CustomObjectsTestCase, TransactionCleanupMixin


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_token(user):
    from users.choices import TokenVersionChoices
    t = Token(version=TokenVersionChoices.V1, user=user)
    t.save()
    return t.token  # plaintext for V1 tokens


def _grant_perm(user, action, model_class, name=None):
    perm = ObjectPermission(name=name or f"poly-test-{action}", actions=[action])
    perm.save()
    perm.users.add(user)
    perm.object_types.add(ObjectType.objects.get_for_model(model_class))
    return perm


# ---------------------------------------------------------------------------
# API tests
# ---------------------------------------------------------------------------

class PolymorphicFieldAPITest(TransactionCleanupMixin, CustomObjectsTestCase, TransactionTestCase):
    """
    API tests for polymorphic Object and MultiObject fields.
    Uses TransactionTestCase so that DB table creation/deletion is committed.
    """

    def setUp(self):
        super().setUp()
        from django.test import Client as DjangoClient
        from utilities.testing import create_test_user
        self.user = create_test_user("poly-api-user")
        token_key = _create_token(self.user)
        self.header = {"HTTP_AUTHORIZATION": f"Token {token_key}"}
        # Reset client to clear the session cookie set by CustomObjectsTestCase.setUp()
        # (force_login causes SessionAuthentication to take priority over TokenAuthentication)
        self.client = DjangoClient()

        # Site and Prefix used as related objects
        self.site = Site.objects.create(name="Test Site", slug="test-site")
        self.prefix = Prefix.objects.create(
            prefix="10.0.0.0/8",
            status=PrefixStatusChoices.STATUS_ACTIVE,
        )

        self.site_ot = ObjectType.objects.get(app_label="dcim", model="site")
        self.prefix_ot = ObjectType.objects.get(app_label="ipam", model="prefix")

        # COT with a primary text field
        self.cot = CustomObjectType.objects.create(
            name="PolyTest", slug="poly-test",
            verbose_name_plural="Poly Tests",
        )
        CustomObjectTypeField.objects.create(
            custom_object_type=self.cot,
            name="name", label="Name", type="text",
            primary=True, required=True,
        )

        # Polymorphic single-object (GFK) field
        self.gfk_field = CustomObjectTypeField.objects.create(
            custom_object_type=self.cot,
            name="poly_obj", label="Poly Obj", type="object",
            is_polymorphic=True,
        )
        self.gfk_field.related_object_types.set([self.site_ot, self.prefix_ot])

        # Polymorphic multi-object field
        self.m2m_field = CustomObjectTypeField.objects.create(
            custom_object_type=self.cot,
            name="poly_multi", label="Poly Multi", type="multiobject",
            is_polymorphic=True,
        )
        self.m2m_field.related_object_types.set([self.site_ot, self.prefix_ot])

        self.model = self.cot.get_model()
        self.field_perm_ot = ObjectType.objects.get_for_model(CustomObjectTypeField)

    # --- Field creation via API ---

    def test_create_polymorphic_object_field_via_api(self):
        """POSTing a new polymorphic Object field with related_object_types_input succeeds."""
        _grant_perm(self.user, "add", CustomObjectTypeField, "field-add")
        url = reverse("plugins-api:netbox_custom_objects-api:customobjecttypefield-list")
        data = {
            "custom_object_type": self.cot.pk,
            "name": "poly_obj2",
            "label": "Poly Obj 2",
            "type": "object",
            "is_polymorphic": True,
            "related_object_types_input": [
                {"app_label": "dcim", "model": "site"},
            ],
        }
        response = self.client.post(url, json.dumps(data), content_type="application/json", **self.header)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        created = CustomObjectTypeField.objects.get(pk=response.data["id"])
        self.assertTrue(created.is_polymorphic)
        self.assertEqual(created.related_object_types.count(), 1)

    def test_create_polymorphic_multiobject_field_via_api(self):
        """POSTing a new polymorphic MultiObject field succeeds."""
        _grant_perm(self.user, "add", CustomObjectTypeField, "field-add")
        url = reverse("plugins-api:netbox_custom_objects-api:customobjecttypefield-list")
        data = {
            "custom_object_type": self.cot.pk,
            "name": "poly_multi2",
            "type": "multiobject",
            "is_polymorphic": True,
            "related_object_types_input": [
                {"app_label": "dcim", "model": "site"},
                {"app_label": "ipam", "model": "prefix"},
            ],
        }
        response = self.client.post(url, json.dumps(data), content_type="application/json", **self.header)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        created = CustomObjectTypeField.objects.get(pk=response.data["id"])
        self.assertTrue(created.is_polymorphic)
        self.assertEqual(created.related_object_types.count(), 2)

    def test_polymorphic_field_requires_related_types(self):
        """POSTing a polymorphic Object field without related_object_types_input returns 400."""
        _grant_perm(self.user, "add", CustomObjectTypeField, "field-add")
        url = reverse("plugins-api:netbox_custom_objects-api:customobjecttypefield-list")
        data = {
            "custom_object_type": self.cot.pk,
            "name": "bad_poly",
            "type": "object",
            "is_polymorphic": True,
        }
        response = self.client.post(url, json.dumps(data), content_type="application/json", **self.header)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_field_list_includes_is_polymorphic_and_related_types(self):
        """GET on a polymorphic field returns is_polymorphic=True and related_object_types."""
        _grant_perm(self.user, "view", CustomObjectTypeField, "field-view")
        url = reverse(
            "plugins-api:netbox_custom_objects-api:customobjecttypefield-detail",
            kwargs={"pk": self.gfk_field.pk},
        )
        response = self.client.get(url, **self.header)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["is_polymorphic"])
        self.assertEqual(len(response.data["related_object_types"]), 2)
        app_models = {
            (r["app_label"], r["model"]) for r in response.data["related_object_types"]
        }
        self.assertIn(("dcim", "site"), app_models)
        self.assertIn(("ipam", "prefix"), app_models)

    # --- Immutability: is_polymorphic and related types cannot change after creation ---

    def test_patch_is_polymorphic_false_on_existing_polymorphic_field_rejected(self):
        """PATCH is_polymorphic=False on an existing polymorphic field returns 400."""
        _grant_perm(self.user, "change", CustomObjectTypeField, "field-change")
        url = reverse(
            "plugins-api:netbox_custom_objects-api:customobjecttypefield-detail",
            kwargs={"pk": self.gfk_field.pk},
        )
        response = self.client.patch(
            url,
            json.dumps({"is_polymorphic": False}),
            content_type="application/json",
            **self.header,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.data)
        self.gfk_field.refresh_from_db()
        self.assertTrue(self.gfk_field.is_polymorphic)

    def test_patch_related_object_types_input_on_existing_field_rejected(self):
        """PATCH related_object_types_input on an existing polymorphic field returns 400."""
        _grant_perm(self.user, "change", CustomObjectTypeField, "field-change")
        url = reverse(
            "plugins-api:netbox_custom_objects-api:customobjecttypefield-detail",
            kwargs={"pk": self.gfk_field.pk},
        )
        response = self.client.patch(
            url,
            json.dumps({"related_object_types_input": [{"app_label": "dcim", "model": "site"}]}),
            content_type="application/json",
            **self.header,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.data)

    def test_patch_app_label_model_on_existing_non_polymorphic_field_rejected(self):
        """PATCH app_label+model on an existing non-polymorphic object field returns 400."""
        _grant_perm(self.user, "change", CustomObjectTypeField, "field-change")
        # Create a non-polymorphic object field
        site_ot = ObjectType.objects.get(app_label="dcim", model="site")
        non_poly_field = CustomObjectTypeField.objects.create(
            custom_object_type=self.cot,
            name="single_obj", label="Single Obj", type="object",
            is_polymorphic=False,
            related_object_type=site_ot,
        )
        url = reverse(
            "plugins-api:netbox_custom_objects-api:customobjecttypefield-detail",
            kwargs={"pk": non_poly_field.pk},
        )
        response = self.client.patch(
            url,
            json.dumps({"app_label": "ipam", "model": "prefix"}),
            content_type="application/json",
            **self.header,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.data)
        non_poly_field.refresh_from_db()
        self.assertEqual(non_poly_field.related_object_type, site_ot)

    # --- Field name collision ---

    def test_create_polymorphic_field_with_duplicate_name_rejected(self):
        """POST a polymorphic field whose name already exists on the same COT returns 400."""
        _grant_perm(self.user, "add", CustomObjectTypeField, "field-add-dup")
        url = reverse("plugins-api:netbox_custom_objects-api:customobjecttypefield-list")
        data = {
            "custom_object_type": self.cot.pk,
            # "poly_obj" already exists on self.cot (created in setUp)
            "name": "poly_obj",
            "label": "Duplicate Poly",
            "type": "object",
            "is_polymorphic": True,
            "related_object_types_input": [
                {"app_label": "dcim", "model": "site"},
            ],
        }
        response = self.client.post(url, json.dumps(data), content_type="application/json", **self.header)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.data)

    def test_rename_polymorphic_field_to_collide_with_existing_field_rejected(self):
        """PATCH name of a polymorphic field to an already-taken name returns 400."""
        _grant_perm(self.user, "change", CustomObjectTypeField, "field-change-dup")
        url = reverse(
            "plugins-api:netbox_custom_objects-api:customobjecttypefield-detail",
            kwargs={"pk": self.m2m_field.pk},
        )
        # Rename poly_multi → poly_obj which is already taken
        response = self.client.patch(
            url,
            json.dumps({"name": "poly_obj"}),
            content_type="application/json",
            **self.header,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.data)
        self.m2m_field.refresh_from_db()
        self.assertEqual(self.m2m_field.name, "poly_multi")

    # --- Custom object CRUD with polymorphic GFK ---

    def _obj_list_url(self):
        return reverse(
            "plugins-api:netbox_custom_objects-api:customobject-list",
            kwargs={"custom_object_type": self.cot.slug},
        )

    def _obj_detail_url(self, pk):
        return reverse(
            "plugins-api:netbox_custom_objects-api:customobject-detail",
            kwargs={"custom_object_type": self.cot.slug, "pk": pk},
        )

    def test_create_custom_object_with_polymorphic_gfk_via_api(self):
        """POST a custom object with a polymorphic single-object value (Site)."""
        _grant_perm(self.user, "add", self.model, "co-add")
        from django.contrib.contenttypes.models import ContentType
        site_ct = ContentType.objects.get_for_model(Site)
        data = {
            "name": "gfk-test-obj",
            "poly_obj": {"content_type_id": site_ct.pk, "object_id": self.site.pk},
        }
        response = self.client.post(
            self._obj_list_url(), json.dumps(data), content_type="application/json", **self.header
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.content)
        obj = self.model.objects.get(pk=json.loads(response.content)["id"])
        self.assertEqual(obj.poly_obj, self.site)

    def test_create_custom_object_with_polymorphic_gfk_as_prefix(self):
        """POST a custom object with a polymorphic single-object value (Prefix)."""
        _grant_perm(self.user, "add", self.model, "co-add")
        from django.contrib.contenttypes.models import ContentType
        prefix_ct = ContentType.objects.get_for_model(Prefix)
        data = {
            "name": "gfk-prefix-obj",
            "poly_obj": {"content_type_id": prefix_ct.pk, "object_id": self.prefix.pk},
        }
        response = self.client.post(
            self._obj_list_url(), json.dumps(data), content_type="application/json", **self.header
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.content)
        obj = self.model.objects.get(pk=json.loads(response.content)["id"])
        self.assertEqual(obj.poly_obj, self.prefix)

    def test_read_custom_object_gfk_representation(self):
        """GET a custom object returns polymorphic GFK with _content_type annotation."""
        _grant_perm(self.user, "view", self.model, "co-view")
        obj = self.model.objects.create(name="gfk-read-obj")
        obj.poly_obj = self.site
        obj.save()

        response = self.client.get(self._obj_detail_url(obj.pk), **self.header)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        poly_data = response.data["poly_obj"]
        self.assertIsNotNone(poly_data)
        self.assertEqual(poly_data["_content_type"], "dcim.site")
        self.assertEqual(poly_data["id"], self.site.pk)

    def test_update_custom_object_clears_gfk(self):
        """PATCH with poly_obj=null clears the GFK."""
        _grant_perm(self.user, "change", self.model, "co-change")
        obj = self.model.objects.create(name="gfk-clear-obj")
        obj.poly_obj = self.site
        obj.save()

        response = self.client.patch(
            self._obj_detail_url(obj.pk),
            json.dumps({"poly_obj": None}),
            content_type="application/json",
            **self.header,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        obj.refresh_from_db()
        self.assertIsNone(obj.poly_obj)

    # --- Custom object CRUD with polymorphic M2M ---

    def test_create_custom_object_with_polymorphic_m2m_via_api(self):
        """POST a custom object with a list of heterogeneous polymorphic M2M values."""
        _grant_perm(self.user, "add", self.model, "co-add")
        from django.contrib.contenttypes.models import ContentType
        site_ct = ContentType.objects.get_for_model(Site)
        prefix_ct = ContentType.objects.get_for_model(Prefix)
        data = {
            "name": "m2m-test-obj",
            "poly_multi": [
                {"content_type_id": site_ct.pk, "object_id": self.site.pk},
                {"content_type_id": prefix_ct.pk, "object_id": self.prefix.pk},
            ],
        }
        response = self.client.post(
            self._obj_list_url(), json.dumps(data), content_type="application/json", **self.header
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.content)
        obj = self.model.objects.get(pk=json.loads(response.content)["id"])
        members = obj.poly_multi.all()
        self.assertIn(self.site, members)
        self.assertIn(self.prefix, members)

    def test_read_custom_object_m2m_representation(self):
        """GET returns poly_multi as a list of objects with _content_type."""
        _grant_perm(self.user, "view", self.model, "co-view")
        obj = self.model.objects.create(name="m2m-read-obj")
        obj.poly_multi.add(self.site, self.prefix)

        response = self.client.get(self._obj_detail_url(obj.pk), **self.header)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        poly_list = response.data["poly_multi"]
        self.assertEqual(len(poly_list), 2)
        content_types = {item["_content_type"] for item in poly_list}
        self.assertIn("dcim.site", content_types)
        self.assertIn("ipam.prefix", content_types)

    def test_patch_null_on_non_required_polymorphic_m2m_clears_values(self):
        """
        PATCH with poly_multi=null on a non-required polymorphic M2M field must
        return 200 and clear the relation.  Before the fix, the ListField wrapper
        had allow_null=False (the default), so null was rejected with a 400 before
        reaching the DB.
        """
        _grant_perm(self.user, "change", self.model, "co-change-null")
        obj = self.model.objects.create(name="m2m-null-patch-obj")
        obj.poly_multi.add(self.site, self.prefix)
        self.assertEqual(obj.poly_multi.count(), 2)

        response = self.client.patch(
            self._obj_detail_url(obj.pk),
            json.dumps({"poly_multi": None}),
            content_type="application/json",
            **self.header,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        obj.refresh_from_db()
        self.assertEqual(obj.poly_multi.count(), 0)

    def test_update_custom_object_replaces_m2m(self):
        """PATCH with a new poly_multi list replaces the existing values."""
        _grant_perm(self.user, "change", self.model, "co-change")
        obj = self.model.objects.create(name="m2m-replace-obj")
        obj.poly_multi.add(self.site)

        from django.contrib.contenttypes.models import ContentType
        prefix_ct = ContentType.objects.get_for_model(Prefix)
        response = self.client.patch(
            self._obj_detail_url(obj.pk),
            json.dumps({"poly_multi": [{"content_type_id": prefix_ct.pk, "object_id": self.prefix.pk}]}),
            content_type="application/json",
            **self.header,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        obj.refresh_from_db()
        members = obj.poly_multi.all()
        self.assertNotIn(self.site, members)
        self.assertIn(self.prefix, members)

    # --- Orphaned / unresolvable content type ---

    def test_create_custom_object_with_unresolvable_content_type_rejected(self):
        """POST with a content_type_id whose model_class() is None returns 400."""
        _grant_perm(self.user, "add", self.model, "co-add")

        # ObjectType is a proxy for ContentType.  An entry with a nonexistent app/model
        # gives a row whose model_class() returns None.  Use get_or_create so the test
        # is idempotent when run with --keepdb.
        orphan_ot, _ = ObjectType.objects.get_or_create(
            app_label="nonexistent_app", model="nonexistentmodel"
        )
        # Add to allowed types so we pass the allow-list check and reach model_class().
        self.gfk_field.related_object_types.add(orphan_ot)

        data = {
            "name": "orphan-ct-obj",
            "poly_obj": {"content_type_id": orphan_ot.pk, "object_id": 1},
        }
        response = self.client.post(
            self._obj_list_url(),
            json.dumps(data),
            content_type="application/json",
            **self.header,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.content)
        # Should return the sanitized message, not internal CT details.
        self.assertNotIn(b"nonexistentmodel", response.content)
        self.assertNotIn(b"nonexistent_app", response.content)

        # Remove from M2M before tearDown drops the through table; leaving the
        # stale django_content_type row itself is harmless.
        self.gfk_field.related_object_types.remove(orphan_ot)

    # --- Content-type enforcement tests ---

    def test_create_custom_object_with_disallowed_gfk_type_rejected(self):
        """POST with poly_obj set to a disallowed content type returns 400."""
        _grant_perm(self.user, "add", self.model, "co-add")
        from django.contrib.contenttypes.models import ContentType
        ip_address = IPAddress.objects.create(address="192.0.2.1/24")
        disallowed_ct = ContentType.objects.get_for_model(IPAddress)
        data = {
            "name": "gfk-disallowed-obj",
            "poly_obj": {"content_type_id": disallowed_ct.pk, "object_id": ip_address.pk},
        }
        response = self.client.post(
            self._obj_list_url(),
            json.dumps(data),
            content_type="application/json",
            **self.header,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.content)
        self.assertIn(b"not allowed", response.content.lower())

    def test_create_custom_object_with_disallowed_m2m_type_rejected(self):
        """POST with poly_multi containing a disallowed content type returns 400."""
        _grant_perm(self.user, "add", self.model, "co-add")
        from django.contrib.contenttypes.models import ContentType
        ip_address = IPAddress.objects.create(address="192.0.2.2/24")
        disallowed_ct = ContentType.objects.get_for_model(IPAddress)
        site_ct = ContentType.objects.get_for_model(Site)
        data = {
            "name": "m2m-disallowed-obj",
            "poly_multi": [
                {"content_type_id": site_ct.pk, "object_id": self.site.pk},
                {"content_type_id": disallowed_ct.pk, "object_id": ip_address.pk},
            ],
        }
        response = self.client.post(
            self._obj_list_url(),
            json.dumps(data),
            content_type="application/json",
            **self.header,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.content)
        self.assertIn(b"not allowed", response.content.lower())

    # --- DELETE ---

    def test_delete_custom_object_with_gfk_value(self):
        """DELETE a custom object with a populated GFK polymorphic field returns 204 and removes the object."""
        _grant_perm(self.user, "delete", self.model, "co-delete")
        from django.contrib.contenttypes.models import ContentType
        site_ct = ContentType.objects.get_for_model(Site)
        obj = self.model.objects.create(
            name="gfk-delete-obj",
            poly_obj_content_type=site_ct,
            poly_obj_object_id=self.site.pk,
        )
        pk = obj.pk

        response = self.client.delete(self._obj_detail_url(pk), **self.header)

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT, response.content)
        self.assertFalse(self.model.objects.filter(pk=pk).exists())

    def test_delete_custom_object_with_m2m_values(self):
        """
        DELETE a custom object with populated M2M polymorphic values returns 204, removes the object,
        and cleans up through-table rows.
        """
        from django.apps import apps as django_apps
        _grant_perm(self.user, "delete", self.model, "co-delete")
        obj = self.model.objects.create(name="m2m-delete-obj")
        obj.poly_multi.add(self.site, self.prefix)
        pk = obj.pk

        # Resolve the through model before the delete so we can verify cascade cleanup.
        through_model = django_apps.get_model(APP_LABEL, self.m2m_field.through_model_name)

        response = self.client.delete(self._obj_detail_url(pk), **self.header)

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT, response.content)
        self.assertFalse(self.model.objects.filter(pk=pk).exists())
        # Through-table rows for this object should be gone.
        self.assertFalse(through_model.objects.filter(source_id=pk).exists())

    def test_delete_custom_object_with_empty_polymorphic_fields(self):
        """DELETE a custom object with no polymorphic values set returns 204."""
        _grant_perm(self.user, "delete", self.model, "co-delete")
        obj = self.model.objects.create(name="empty-poly-delete-obj")
        pk = obj.pk

        response = self.client.delete(self._obj_detail_url(pk), **self.header)

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT, response.content)
        self.assertFalse(self.model.objects.filter(pk=pk).exists())


# ---------------------------------------------------------------------------
# UI / form tests
# ---------------------------------------------------------------------------

class PolymorphicFieldUITest(TransactionCleanupMixin, CustomObjectsTestCase, TransactionTestCase):
    """
    UI tests for polymorphic fields on the custom object edit form.
    """

    def setUp(self):
        super().setUp()
        from utilities.testing import create_test_user
        self.user = create_test_user("poly-ui-user")
        self.client.force_login(self.user)

        self.site1 = Site.objects.create(name="UI Site 1", slug="ui-site-1")
        self.site2 = Site.objects.create(name="UI Site 2", slug="ui-site-2")
        self.prefix1 = Prefix.objects.create(
            prefix="192.168.0.0/24",
            status=PrefixStatusChoices.STATUS_ACTIVE,
        )

        self.site_ot = ObjectType.objects.get(app_label="dcim", model="site")
        self.prefix_ot = ObjectType.objects.get(app_label="ipam", model="prefix")

        self.cot = CustomObjectType.objects.create(
            name="UIPolyTest", slug="ui-poly-test",
            verbose_name_plural="UI Poly Tests",
        )
        CustomObjectTypeField.objects.create(
            custom_object_type=self.cot,
            name="name", label="Name", type="text",
            primary=True, required=True,
        )
        self.gfk_field = CustomObjectTypeField.objects.create(
            custom_object_type=self.cot,
            name="poly_obj", label="Poly Obj", type="object",
            is_polymorphic=True,
        )
        self.gfk_field.related_object_types.set([self.site_ot, self.prefix_ot])

        self.m2m_field = CustomObjectTypeField.objects.create(
            custom_object_type=self.cot,
            name="poly_multi", label="Poly Multi", type="multiobject",
            is_polymorphic=True,
        )
        self.m2m_field.related_object_types.set([self.site_ot, self.prefix_ot])

        self.model = self.cot.get_model()

        # Grant the user all relevant permissions
        for action in ("view", "add", "change", "delete"):
            _grant_perm(self.user, action, self.model, f"ui-{action}")
            _grant_perm(self.user, action, CustomObjectTypeField, f"ui-field-{action}")
        # restrict_form_fields() restricts DynamicModelChoiceField querysets to objects
        # the user can view; grant view on Site and Prefix so form validation passes.
        _grant_perm(self.user, "view", Site, "ui-site-view")
        _grant_perm(self.user, "view", Prefix, "ui-prefix-view")

    def _add_url(self):
        return reverse(
            "plugins:netbox_custom_objects:customobject_add",
            kwargs={"custom_object_type": self.cot.slug},
        )

    def _edit_url(self, pk):
        return reverse(
            "plugins:netbox_custom_objects:customobject_edit",
            kwargs={"custom_object_type": self.cot.slug, "pk": pk},
        )

    def _bulk_edit_url(self):
        return reverse(
            "plugins:netbox_custom_objects:customobject_bulk_edit",
            kwargs={"custom_object_type": self.cot.slug},
        )

    def _detail_url(self, pk):
        return reverse(
            "plugins:netbox_custom_objects:customobject",
            kwargs={"custom_object_type": self.cot.slug, "pk": pk},
        )

    def _field_delete_url(self, field_pk):
        return reverse(
            "plugins:netbox_custom_objects:customobjecttypefield_delete",
            kwargs={"pk": field_pk},
        )

    # --- Edit form structure ---

    def test_edit_form_has_scope_style_fields_not_raw_gfk_columns(self):
        """The edit form exposes a scope-style type+object selector pair, not raw GFK columns."""
        obj = self.model.objects.create(name="form-test-obj")
        response = self.client.get(self._edit_url(obj.pk))
        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        # Scope-style sub-fields must be present
        self.assertIn("poly_obj__ct", form.fields)
        self.assertIn("poly_obj__obj", form.fields)
        # Old per-type sub-fields must NOT be present
        self.assertNotIn("poly_obj__dcim__site", form.fields)
        self.assertNotIn("poly_obj__ipam__prefix", form.fields)
        # Raw GFK columns must be excluded
        self.assertNotIn("poly_obj_content_type", form.fields)
        self.assertNotIn("poly_obj_object_id", form.fields)

    def test_edit_form_has_per_type_subfields_for_m2m(self):
        """The edit form exposes per-type sub-fields for polymorphic MultiObject."""
        obj = self.model.objects.create(name="m2m-form-obj")
        response = self.client.get(self._edit_url(obj.pk))
        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertIn("poly_multi__dcim__site", form.fields)
        self.assertIn("poly_multi__ipam__prefix", form.fields)

    def test_form_poly_obj_grouping_metadata(self):
        """
        The form's poly_obj_ct_names and poly_obj_pairs attrs are populated so the
        template can render the ct+obj pair under a shared heading.  obj_sub must be
        in rendered_names (prevents fallback double-render) but NOT as a standalone
        entry in field_groups (the template renders it via the pair).
        """
        obj = self.model.objects.create(name="grouping-meta-obj")
        response = self.client.get(self._edit_url(obj.pk))
        form = response.context["form"]

        ct_sub = "poly_obj__ct"
        obj_sub = "poly_obj__obj"

        # ct_sub is flagged as the start of a poly object pair
        self.assertIn(ct_sub, form.custom_object_type_poly_obj_ct_names)

        # pairs dict maps ct_sub → (obj_sub, label)
        pair = form.custom_object_type_poly_obj_pairs.get(ct_sub)
        self.assertIsNotNone(pair, "poly_obj_pairs missing entry for poly_obj__ct")
        self.assertEqual(pair[0], obj_sub)
        self.assertIn("Poly Obj", pair[1])

        # obj_sub is in rendered_names so the fallback field loop skips it
        self.assertIn(obj_sub, form.custom_object_type_rendered_names)

        # obj_sub must NOT appear as a standalone entry in any field group
        all_grouped = [
            name
            for names in form.custom_object_type_field_groups.values()
            for name in names
        ]
        self.assertNotIn(obj_sub, all_grouped)

    def test_form_poly_m2m_grouping_metadata(self):
        """
        The form's poly_m2m_groups attr maps the first M2M sub-field name to
        (all_sub_names, label).  Only the first sub-name appears in field_groups;
        the rest are in rendered_names so they won't double-render.
        """
        obj = self.model.objects.create(name="m2m-grouping-meta-obj")
        response = self.client.get(self._edit_url(obj.pk))
        form = response.context["form"]

        all_grouped = [
            name
            for names in form.custom_object_type_field_groups.values()
            for name in names
        ]

        # Exactly one of the two M2M sub-fields should be in field_groups
        dcim_sub = "poly_multi__dcim__site"
        ipam_sub = "poly_multi__ipam__prefix"
        grouped_m2m = [n for n in all_grouped if n.startswith("poly_multi__")]
        self.assertEqual(len(grouped_m2m), 1, "Expected exactly one M2M sub-name in field_groups")
        first_sub = grouped_m2m[0]

        # poly_m2m_groups maps that first sub-name to (all_subs, label)
        group_info = form.custom_object_type_poly_m2m_groups.get(first_sub)
        self.assertIsNotNone(group_info, "poly_m2m_groups missing entry for first M2M sub-field")
        all_subs, label = group_info
        self.assertIn(dcim_sub, all_subs)
        self.assertIn(ipam_sub, all_subs)
        self.assertIn("Poly Multi", label)

        # All sub-names are in rendered_names so the fallback loop skips them
        self.assertIn(dcim_sub, form.custom_object_type_rendered_names)
        self.assertIn(ipam_sub, form.custom_object_type_rendered_names)

        # Only the first sub-name is in field_groups; the rest are not
        non_first_subs = [s for s in all_subs if s != first_sub]
        for sub in non_first_subs:
            self.assertNotIn(sub, all_grouped)

    def test_edit_form_type_selector_label_is_human_readable(self):
        """The type-selector field has a user-friendly label."""
        obj = self.model.objects.create(name="label-test-obj")
        response = self.client.get(self._edit_url(obj.pk))
        form = response.context["form"]
        ct_label = form.fields["poly_obj__ct"].label
        self.assertIn("Poly Obj", ct_label)
        self.assertNotIn("__ct", ct_label)

    def test_edit_form_preselects_existing_gfk_value(self):
        """For an existing object, the ct and obj sub-fields are pre-populated."""
        from django.contrib.contenttypes.models import ContentType
        site_ct = ContentType.objects.get_for_model(Site)
        obj = self.model.objects.create(name="prefill-test")
        obj.poly_obj = self.site1
        obj.save()

        response = self.client.get(self._edit_url(obj.pk))
        form = response.context["form"]
        self.assertEqual(form.initial.get("poly_obj__ct"), site_ct.pk)
        self.assertEqual(form.initial.get("poly_obj__obj"), self.site1.pk)

    # --- Edit form submission ---

    def test_submit_edit_form_sets_gfk_to_site(self):
        """POST the edit form with a type+object selection saves the GFK to that Site."""
        from django.contrib.contenttypes.models import ContentType
        site_ct = ContentType.objects.get_for_model(Site)
        obj = self.model.objects.create(name="submit-gfk-obj")
        data = {
            "name": "submit-gfk-obj",
            "poly_obj__ct": site_ct.pk,
            "poly_obj__obj": self.site1.pk,
            "csrfmiddlewaretoken": "fake",
        }
        response = self.client.post(self._edit_url(obj.pk), data, follow=True)
        self.assertNotIn(response.status_code, [400, 403, 500])
        obj.refresh_from_db()
        self.assertEqual(obj.poly_obj, self.site1)

    def test_submit_edit_form_clears_gfk_when_no_type_selected(self):
        """POST with no type selection clears an existing GFK value."""
        obj = self.model.objects.create(name="clear-gfk-obj")
        obj.poly_obj = self.site1
        obj.save()

        data = {"name": "clear-gfk-obj", "csrfmiddlewaretoken": "fake"}
        response = self.client.post(self._edit_url(obj.pk), data, follow=True)
        self.assertNotIn(response.status_code, [400, 403, 500])
        obj.refresh_from_db()
        self.assertIsNone(obj.poly_obj)

    def test_submit_edit_form_sets_polymorphic_m2m(self):
        """POST the edit form with M2M sub-fields saves values across types."""
        obj = self.model.objects.create(name="submit-m2m-obj")
        data = {
            "name": "submit-m2m-obj",
            "poly_multi__dcim__site": [self.site1.pk, self.site2.pk],
            "poly_multi__ipam__prefix": [self.prefix1.pk],
            "csrfmiddlewaretoken": "fake",
        }
        response = self.client.post(self._edit_url(obj.pk), data, follow=True)
        self.assertNotIn(response.status_code, [400, 403, 500])
        members = obj.poly_multi.all()
        self.assertIn(self.site1, members)
        self.assertIn(self.site2, members)
        self.assertIn(self.prefix1, members)

    def test_form_create_with_gfk_produces_single_object_change(self):
        """
        Submitting the add form with a polymorphic GFK value must create exactly
        one ObjectChange entry.  Before the fix, custom_save() called instance.save()
        twice — once to get a PK and again to write the GFK attrs — producing a
        spurious UPDATE record immediately after the CREATE.
        """
        from django.contrib.contenttypes.models import ContentType
        from core.models import ObjectChange

        site_ct = ContentType.objects.get_for_model(Site)
        data = {
            "name": "single-change-obj",
            "poly_obj__ct": site_ct.pk,
            "poly_obj__obj": self.site1.pk,
            "csrfmiddlewaretoken": "fake",
        }
        response = self.client.post(self._add_url(), data, follow=True)
        self.assertNotIn(response.status_code, [400, 403, 500])
        obj = self.model.objects.get(name="single-change-obj")

        obj_ct = ContentType.objects.get_for_model(obj)
        changes = ObjectChange.objects.filter(
            changed_object_type=obj_ct,
            changed_object_id=obj.pk,
        )
        self.assertEqual(
            changes.count(), 1,
            f"Expected exactly 1 ObjectChange after form create, got {changes.count()}. "
            "A double-save produces a CREATE + spurious UPDATE.",
        )
        self.assertEqual(changes.first().action, "create")

    def test_submit_add_form_creates_object_with_polymorphic_m2m(self):
        """POST the add form creates a new custom object with polymorphic M2M values."""
        data = {
            "name": "add-m2m-obj",
            "poly_multi__dcim__site": [self.site1.pk],
            "csrfmiddlewaretoken": "fake",
        }
        response = self.client.post(self._add_url(), data, follow=True)
        self.assertNotIn(response.status_code, [400, 403, 500])
        obj = self.model.objects.get(name="add-m2m-obj")
        self.assertIn(self.site1, obj.poly_multi.all())

    # --- Bulk edit form ---

    def test_bulk_edit_form_has_scope_style_and_m2m_subfields(self):
        """The bulk edit form exposes scope-style fields for GFK and per-type fields for M2M."""
        obj1 = self.model.objects.create(name="bulk-1")
        obj2 = self.model.objects.create(name="bulk-2")
        # POST without _apply renders the form
        response = self.client.post(
            self._bulk_edit_url(),
            data={"pk": [obj1.pk, obj2.pk]},
        )
        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        # Scope-style fields for single-object GFK
        self.assertIn("poly_obj__ct", form.fields)
        self.assertIn("poly_obj__obj", form.fields)
        # Per-type sub-fields for M2M (unchanged)
        self.assertIn("poly_multi__dcim__site", form.fields)
        # Raw GFK columns excluded
        self.assertNotIn("poly_obj_content_type", form.fields)

    def test_bulk_edit_applies_gfk_to_all_selected_objects(self):
        """Bulk edit sets the GFK field on all selected objects."""
        from django.contrib.contenttypes.models import ContentType
        site_ct = ContentType.objects.get_for_model(Site)
        obj1 = self.model.objects.create(name="bulk-gfk-1")
        obj2 = self.model.objects.create(name="bulk-gfk-2")
        data = {
            "pk": [obj1.pk, obj2.pk],
            "_apply": "1",
            "poly_obj__ct": site_ct.pk,
            "poly_obj__obj": self.site1.pk,
            "csrfmiddlewaretoken": "fake",
        }
        response = self.client.post(self._bulk_edit_url(), data, follow=True)
        self.assertNotIn(response.status_code, [400, 403, 500])
        obj1.refresh_from_db()
        obj2.refresh_from_db()
        self.assertEqual(obj1.poly_obj, self.site1)
        self.assertEqual(obj2.poly_obj, self.site1)

    def test_bulk_edit_applies_m2m_to_all_selected_objects(self):
        """Bulk edit sets poly M2M on all selected objects."""
        obj1 = self.model.objects.create(name="bulk-m2m-1")
        obj2 = self.model.objects.create(name="bulk-m2m-2")
        data = {
            "pk": [obj1.pk, obj2.pk],
            "_apply": "1",
            "poly_multi__dcim__site": [self.site1.pk],
            "csrfmiddlewaretoken": "fake",
        }
        response = self.client.post(self._bulk_edit_url(), data, follow=True)
        self.assertNotIn(response.status_code, [400, 403, 500])
        self.assertIn(self.site1, obj1.poly_multi.all())
        self.assertIn(self.site1, obj2.poly_multi.all())

    # --- Detail view ---

    def test_detail_view_shows_type_column_for_polymorphic_m2m(self):
        """
        The detail view renders a 'Type' column header and the verbose_name of each
        linked object's model when the field is polymorphic.  Non-polymorphic M2M
        fields must not show that column.
        """
        obj = self.model.objects.create(name="detail-poly-obj")
        obj.poly_multi.add(self.site1, self.prefix1)

        response = self.client.get(self._detail_url(obj.pk))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()

        # The polymorphic card must have a Type column header
        self.assertIn("Type", content)
        # Each object's model verbose_name must appear
        site_vn = str(Site._meta.verbose_name)       # "site"
        prefix_vn = str(Prefix._meta.verbose_name)   # "prefix"
        self.assertIn(site_vn, content)
        self.assertIn(prefix_vn, content)
        # Both linked objects themselves must appear
        self.assertIn(str(self.site1), content)
        self.assertIn(str(self.prefix1), content)

    # --- Delete confirmation for polymorphic fields ---

    def test_delete_confirmation_page_for_polymorphic_m2m_field_returns_200(self):
        """GET the delete confirmation page for a polymorphic M2M field does not raise FieldError."""
        obj = self.model.objects.create(name="del-m2m-obj")
        obj.poly_multi.add(self.site1)

        response = self.client.get(self._field_delete_url(self.m2m_field.pk))
        self.assertEqual(response.status_code, 200)

    def test_delete_confirmation_page_for_polymorphic_gfk_field_returns_200(self):
        """GET the delete confirmation page for a polymorphic GFK field does not raise FieldError."""
        obj = self.model.objects.create(name="del-gfk-obj")
        obj.poly_obj = self.site1
        obj.save()

        response = self.client.get(self._field_delete_url(self.gfk_field.pk))
        self.assertEqual(response.status_code, 200)


# ---------------------------------------------------------------------------
# Through-model registration
# ---------------------------------------------------------------------------

class PolymorphicThroughModelRegistrationTest(
    TransactionCleanupMixin, CustomObjectsTestCase, TransactionTestCase
):
    """Verify the through model is re-registered on model regeneration (simulates restart)."""

    def setUp(self):
        super().setUp()
        from utilities.testing import create_test_user
        self.user = create_test_user("poly-reg-user")
        self.client.force_login(self.user)

        self.cot = CustomObjectType.objects.create(
            name="RegTest", slug="reg-test",
            verbose_name_plural="Reg Tests",
        )
        CustomObjectTypeField.objects.create(
            custom_object_type=self.cot,
            name="name", type="text", primary=True,
        )
        self.m2m_field = CustomObjectTypeField.objects.create(
            custom_object_type=self.cot,
            name="links", type="multiobject", is_polymorphic=True,
        )
        site_ot = ObjectType.objects.get(app_label="dcim", model="site")
        self.m2m_field.related_object_types.set([site_ot])

    def test_through_model_registered_after_get_model(self):
        """After clearing the cache and calling get_model(), the through model is accessible."""
        from django.apps import apps as django_apps
        from netbox_custom_objects.constants import APP_LABEL

        # Simulate restart by clearing cache
        CustomObjectType.clear_model_cache()

        # Re-generate the model (as a request would)
        self.cot.get_model()

        # The through model must be findable
        through_name = self.m2m_field.through_model_name
        try:
            through = django_apps.get_model(APP_LABEL, through_name)
        except LookupError:
            self.fail(
                f"Through model '{through_name}' not in app registry after get_model()."
            )
        self.assertIsNotNone(through)


# ---------------------------------------------------------------------------
# Referenced-object deletion
# ---------------------------------------------------------------------------

class ReferencedObjectDeletionTest(
    TransactionCleanupMixin, CustomObjectsTestCase, TransactionTestCase
):
    """
    Tests for the on_delete behaviour when a referenced object (e.g. a Site) or
    the CustomObjectType itself is deleted.

    GFK fields use on_delete=SET_NULL on the content_type FK (so deleting the
    ContentType row nulls the pointer), but there is no DB-level FK from
    object_id to the concrete target table — generic relations cannot express
    that.  Deleting a Site therefore leaves a stale object_id; Django's GFK
    accessor silently returns None in that case.

    Polymorphic M2M through-tables store (source_id, content_type_id, object_id).
    The content_type FK has on_delete=CASCADE (so deleting the ContentType drops
    the through-table rows), but again there is no FK from object_id to the
    concrete target table.  Deleting a Site leaves a stale through-table row;
    PolymorphicManyToManyManager._get_objects() already skips such rows because
    the batch-fetch query returns no matching object for the deleted PK.
    """

    def setUp(self):
        super().setUp()

        self.site = Site.objects.create(name="Del Site", slug="del-site")
        self.site_ot = ObjectType.objects.get(app_label="dcim", model="site")
        self.prefix_ot = ObjectType.objects.get(app_label="ipam", model="prefix")

        self.cot = CustomObjectType.objects.create(
            name="DelTest", slug="del-test",
            verbose_name_plural="Del Tests",
        )
        CustomObjectTypeField.objects.create(
            custom_object_type=self.cot,
            name="name", type="text", primary=True, required=True,
        )
        self.gfk_field = CustomObjectTypeField.objects.create(
            custom_object_type=self.cot,
            name="poly_obj", label="Poly Obj", type="object",
            is_polymorphic=True,
        )
        self.gfk_field.related_object_types.set([self.site_ot, self.prefix_ot])

        self.m2m_field = CustomObjectTypeField.objects.create(
            custom_object_type=self.cot,
            name="poly_multi", label="Poly Multi", type="multiobject",
            is_polymorphic=True,
        )
        self.m2m_field.related_object_types.set([self.site_ot, self.prefix_ot])

        self.model = self.cot.get_model()

    def test_deleting_referenced_site_nulls_gfk_accessor(self):
        """
        Deleting a Site that is referenced by a polymorphic GFK field does not
        raise an exception and causes the accessor to return None.

        There is no DB FK from object_id → dcim_site, so the row is not touched
        at the DB level; Django's GenericForeignKey.__get__ returns None when
        the target object no longer exists.
        """
        obj = self.model.objects.create(name="stale-gfk")
        obj.poly_obj = self.site
        obj.save()

        self.site.delete()

        obj.refresh_from_db()
        # The content_type column still points to the Site ContentType (site still
        # exists in the app registry), but the object is gone — accessor returns None.
        self.assertIsNone(obj.poly_obj)

    def test_deleting_referenced_site_leaves_stale_m2m_row_excluded_from_all(self):
        """
        Deleting a Site that is in a polymorphic M2M through table leaves a stale
        row in the through table (no DB FK on object_id), but all() gracefully
        excludes it because the batch-fetch query returns no matching object.
        """
        obj = self.model.objects.create(name="stale-m2m")
        obj.poly_multi.add(self.site)

        site_pk = self.site.pk
        self.site.delete()

        # Verify the stale row persists: the through table has no DB-level FK from
        # object_id to dcim_site, so deleting a Site cannot cascade into the through
        # table.  Fetch the through model directly from the manager to avoid a
        # global app-registry lookup that could resolve to a stale model from a
        # prior test run when using --keepdb.
        manager = obj.poly_multi  # PolymorphicManyToManyManager
        through = manager._get_through_model()
        self.assertTrue(
            through.objects.filter(source_id=obj.pk, object_id=site_pk).exists(),
            "Stale through-table row should remain after target deletion (no DB FK on object_id).",
        )

        # all() skips the stale row — the result list must be empty.
        self.assertEqual(list(obj.poly_multi.all()), [])

    def test_deleting_custom_object_type_drops_db_table_and_deregisters_model(self):
        """
        Deleting a CustomObjectType drops its DB table, drops polymorphic through
        tables, and removes the model from Django's app registry.
        """
        from django.apps import apps as django_apps
        from django.db import connection
        from netbox_custom_objects.constants import APP_LABEL

        main_table = self.cot.get_database_table_name()
        through_table = self.m2m_field.through_table_name
        through_model_name = self.m2m_field.through_model_name
        model_name = self.model.__name__.lower()

        # Create a row so the delete path exercises cascade logic too.
        obj = self.model.objects.create(name="to-be-cascaded")
        obj.poly_multi.add(self.site)

        self.cot.delete()

        with connection.cursor() as cursor:
            existing_tables = connection.introspection.table_names(cursor)

        self.assertNotIn(main_table, existing_tables, "Main DB table should be dropped.")
        self.assertNotIn(through_table, existing_tables, "Through table should be dropped.")

        # Model and through model must be de-registered from the app registry.
        self.assertNotIn(model_name, django_apps.all_models.get(APP_LABEL, {}))
        self.assertNotIn(
            through_model_name.lower(), django_apps.all_models.get(APP_LABEL, {})
        )


# ---------------------------------------------------------------------------
# Cycle-detection: multi-hop polymorphic cycles
# ---------------------------------------------------------------------------

class PolymorphicCycleDetectionTest(
    TransactionCleanupMixin, CustomObjectsTestCase, TransactionTestCase
):
    """
    Verifies that _has_circular_reference detects cycles that pass entirely
    through polymorphic legs.

    Polymorphic fields store their allowed target types on the related_object_types
    M2M rather than the related_object_type FK, so the DFS must explicitly walk
    that M2M to catch multi-hop polymorphic cycles.
    """

    def setUp(self):
        super().setUp()

        self.cot_a = CustomObjectType.objects.create(
            name="CycleA", slug="cycle-a", verbose_name_plural="Cycle As",
        )
        CustomObjectTypeField.objects.create(
            custom_object_type=self.cot_a, name="name", type="text", primary=True, required=True,
        )

        self.cot_b = CustomObjectType.objects.create(
            name="CycleB", slug="cycle-b", verbose_name_plural="Cycle Bs",
        )
        CustomObjectTypeField.objects.create(
            custom_object_type=self.cot_b, name="name", type="text", primary=True, required=True,
        )

        self.cot_c = CustomObjectType.objects.create(
            name="CycleC", slug="cycle-c", verbose_name_plural="Cycle Cs",
        )
        CustomObjectTypeField.objects.create(
            custom_object_type=self.cot_c, name="name", type="text", primary=True, required=True,
        )

        # Generate models so ContentTypes are registered.
        self.cot_a.get_model()
        self.cot_b.get_model()
        self.cot_c.get_model()

        self.ot_a = ObjectType.objects.get_for_model(self.cot_a.get_model())
        self.ot_b = ObjectType.objects.get_for_model(self.cot_b.get_model())
        self.ot_c = ObjectType.objects.get_for_model(self.cot_c.get_model())

    def test_first_poly_edge_is_accepted(self):
        """A →(poly) B: no cycle yet, must succeed."""
        field_a = CustomObjectTypeField.objects.create(
            custom_object_type=self.cot_a,
            name="link_to_b", type="object", is_polymorphic=True,
        )
        try:
            field_a.related_object_types.set([self.ot_b])
        except Exception as exc:
            self.fail(
                f"Adding COT-B as allowed type for COT-A's poly field raised "
                f"{type(exc).__name__}: {exc}."
            )
        self.assertIn(self.ot_b, field_a.related_object_types.all())

    def test_two_hop_polymorphic_cycle_is_detected(self):
        """A →(poly) B →(poly) A must be rejected."""
        field_a = CustomObjectTypeField.objects.create(
            custom_object_type=self.cot_a,
            name="link_to_b", type="object", is_polymorphic=True,
        )
        field_a.related_object_types.set([self.ot_b])  # no cycle yet

        field_b = CustomObjectTypeField.objects.create(
            custom_object_type=self.cot_b,
            name="link_to_a", type="object", is_polymorphic=True,
        )
        with self.assertRaises(ValidationError):
            field_b.related_object_types.set([self.ot_a])  # closes A→B→A cycle

    def test_three_hop_polymorphic_cycle_is_detected(self):
        """A →(poly) B →(poly) C →(poly) A must be rejected."""
        field_a = CustomObjectTypeField.objects.create(
            custom_object_type=self.cot_a,
            name="link_to_b", type="object", is_polymorphic=True,
        )
        field_a.related_object_types.set([self.ot_b])  # no cycle

        field_b = CustomObjectTypeField.objects.create(
            custom_object_type=self.cot_b,
            name="link_to_c", type="object", is_polymorphic=True,
        )
        field_b.related_object_types.set([self.ot_c])  # no cycle

        field_c = CustomObjectTypeField.objects.create(
            custom_object_type=self.cot_c,
            name="link_to_a", type="object", is_polymorphic=True,
        )
        with self.assertRaises(ValidationError):
            field_c.related_object_types.set([self.ot_a])  # closes A→B→C→A cycle

    def test_mixed_leg_cycle_is_detected(self):
        """A →(non-poly) B →(poly) A must be rejected."""
        # Non-polymorphic object field on A pointing to B.
        CustomObjectTypeField.objects.create(
            custom_object_type=self.cot_a,
            name="link_to_b", type="object",
            related_object_type=self.ot_b,
        )

        # Polymorphic object field on B attempting to point back to A.
        field_b = CustomObjectTypeField.objects.create(
            custom_object_type=self.cot_b,
            name="link_to_a", type="object", is_polymorphic=True,
        )
        with self.assertRaises(ValidationError):
            field_b.related_object_types.set([self.ot_a])  # closes A→B→A cycle


# ---------------------------------------------------------------------------
# Polymorphic reverse descriptor tests (issue #385 / PR #548)
# ---------------------------------------------------------------------------

class PolymorphicReverseDescriptorTest(
    TransactionCleanupMixin, CustomObjectsTestCase, TransactionTestCase
):
    """Verify that polymorphic GFK and M2M fields with a related_name expose a working
    reverse accessor on target model instances."""

    def setUp(self):
        super().setUp()
        self.site_ot = ObjectType.objects.get(app_label="dcim", model="site")
        self.prefix_ot = ObjectType.objects.get(app_label="ipam", model="prefix")

        self.cot = CustomObjectType.objects.create(
            name="RevTest", slug="rev-test",
            verbose_name_plural="Rev Tests",
        )
        CustomObjectTypeField.objects.create(
            custom_object_type=self.cot,
            name="name", type="text", primary=True, required=True,
        )

    # --- GFK reverse descriptor ---

    def _create_gfk_field(self, related_name="rev_co_gfk"):
        field = CustomObjectTypeField.objects.create(
            custom_object_type=self.cot,
            name="target_obj", label="Target", type="object",
            is_polymorphic=True,
            related_name=related_name,
        )
        field.related_object_types.set([self.site_ot, self.prefix_ot])
        return field

    def test_gfk_reverse_descriptor_set_on_target_class(self):
        """After get_model(), the related_name descriptor must exist on the target class."""
        self._create_gfk_field(related_name="co_gfk_reverse")
        self.cot.get_model()
        self.assertTrue(
            hasattr(Site, "co_gfk_reverse"),
            "Reverse descriptor must be set on Site after get_model()",
        )

    def test_gfk_reverse_descriptor_returns_matching_instances(self):
        """site.related_name.all() returns CO instances whose GFK points at that site."""
        from ipam.models import Prefix
        from ipam.choices import PrefixStatusChoices

        self._create_gfk_field(related_name="co_gfk_rev")
        model = self.cot.get_model()

        site = Site.objects.create(name="RevSite", slug="rev-site")
        prefix = Prefix.objects.create(
            prefix="192.168.0.0/24", status=PrefixStatusChoices.STATUS_ACTIVE
        )

        co_a = model.objects.create(name="co-a")
        co_a.target_obj = site
        co_a.save()

        co_b = model.objects.create(name="co-b")
        co_b.target_obj = prefix
        co_b.save()

        site_results = list(site.co_gfk_rev.all())
        self.assertEqual(len(site_results), 1)
        self.assertEqual(site_results[0].pk, co_a.pk)

        prefix_results = list(prefix.co_gfk_rev.all())
        self.assertEqual(len(prefix_results), 1)
        self.assertEqual(prefix_results[0].pk, co_b.pk)

    def test_gfk_reverse_descriptor_count_and_exists(self):
        """count() and exists() work correctly on the reverse manager."""
        self._create_gfk_field(related_name="co_gfk_ce")
        model = self.cot.get_model()

        site = Site.objects.create(name="CeSite", slug="ce-site")
        self.assertEqual(site.co_gfk_ce.count(), 0)
        self.assertFalse(site.co_gfk_ce.exists())

        co = model.objects.create(name="co-ce")
        co.target_obj = site
        co.save()

        self.assertEqual(site.co_gfk_ce.count(), 1)
        self.assertTrue(site.co_gfk_ce.exists())

    def test_gfk_no_reverse_descriptor_when_related_name_blank(self):
        """When related_name is blank, neither m2m_changed nor get_model() injects anything."""
        field = CustomObjectTypeField.objects.create(
            custom_object_type=self.cot,
            name="anon_obj", type="object", is_polymorphic=True,
        )
        before = set(Site.__dict__.keys())
        field.related_object_types.set([self.site_ot])  # fires m2m_changed
        self.cot.get_model()                             # fires _after_model_generation
        after = set(Site.__dict__.keys())
        self.assertEqual(before, after, "No new attribute should be injected on Site for blank related_name")

    def test_gfk_reverse_descriptor_wired_when_type_added(self):
        """Adding a type to related_object_types after initial wiring injects the descriptor via m2m_changed."""
        from dcim.models import Device
        device_ot = ObjectType.objects.get(app_label="dcim", model="device")

        field = CustomObjectTypeField.objects.create(
            custom_object_type=self.cot,
            name="target_obj", label="Target", type="object",
            is_polymorphic=True,
            related_name="co_gfk_added",
        )
        field.related_object_types.set([self.site_ot])
        self.cot.get_model()
        self.assertFalse(hasattr(Device, "co_gfk_added"), "Device not yet in allowed types")

        field.related_object_types.add(device_ot)  # fires m2m_changed post_add

        self.assertTrue(
            hasattr(Device, "co_gfk_added"),
            "Descriptor must be wired on Device after adding it to related_object_types",
        )

    def test_gfk_reverse_descriptor_unwired_when_type_removed(self):
        """Removing a type from related_object_types removes the descriptor from that class."""
        field = self._create_gfk_field(related_name="co_gfk_removed")
        self.cot.get_model()
        self.assertTrue(hasattr(Site, "co_gfk_removed"), "Descriptor must be present before removal")

        field.related_object_types.remove(self.site_ot)  # fires m2m_changed pre_remove

        self.assertFalse(
            hasattr(Site, "co_gfk_removed"),
            "Descriptor must be removed from Site after removing it from related_object_types",
        )

    def test_gfk_reverse_descriptor_removed_on_field_delete(self):
        """Deleting the field removes the reverse descriptor from target models."""
        field = self._create_gfk_field(related_name="co_gfk_del")
        self.cot.get_model()
        self.assertTrue(hasattr(Site, "co_gfk_del"), "Descriptor must be present before delete")

        field.delete()

        self.assertFalse(
            hasattr(Site, "co_gfk_del"),
            "Reverse descriptor must be removed after field delete",
        )

    # --- M2M reverse descriptor ---

    def _create_m2m_field(self, related_name="rev_co_m2m"):
        field = CustomObjectTypeField.objects.create(
            custom_object_type=self.cot,
            name="target_multi", label="Targets", type="multiobject",
            is_polymorphic=True,
            related_name=related_name,
        )
        field.related_object_types.set([self.site_ot, self.prefix_ot])
        return field

    def test_m2m_reverse_descriptor_set_on_target_class(self):
        """After get_model(), the related_name descriptor must exist on the target class."""
        self._create_m2m_field(related_name="co_m2m_reverse")
        self.cot.get_model()
        self.assertTrue(
            hasattr(Site, "co_m2m_reverse"),
            "Reverse descriptor must be set on Site after get_model()",
        )

    def test_m2m_reverse_descriptor_returns_matching_instances(self):
        """site.related_name.all() returns CO instances whose M2M includes that site."""
        from ipam.models import Prefix
        from ipam.choices import PrefixStatusChoices

        self._create_m2m_field(related_name="co_m2m_rev")
        model = self.cot.get_model()

        site = Site.objects.create(name="M2MSite", slug="m2m-site")
        prefix = Prefix.objects.create(
            prefix="10.1.0.0/24", status=PrefixStatusChoices.STATUS_ACTIVE
        )

        co_a = model.objects.create(name="m2m-a")
        co_a.target_multi.add(site)

        co_b = model.objects.create(name="m2m-b")
        co_b.target_multi.add(prefix)

        co_both = model.objects.create(name="m2m-both")
        co_both.target_multi.add(site, prefix)

        site_results = {obj.pk for obj in site.co_m2m_rev.all()}
        self.assertEqual(site_results, {co_a.pk, co_both.pk})

        prefix_results = {obj.pk for obj in prefix.co_m2m_rev.all()}
        self.assertEqual(prefix_results, {co_b.pk, co_both.pk})

    def test_m2m_reverse_descriptor_count_and_exists(self):
        """count() and exists() work correctly on the M2M reverse manager."""
        self._create_m2m_field(related_name="co_m2m_ce")
        model = self.cot.get_model()

        site = Site.objects.create(name="M2MCeSite", slug="m2m-ce-site")
        self.assertEqual(site.co_m2m_ce.count(), 0)
        self.assertFalse(site.co_m2m_ce.exists())

        co = model.objects.create(name="m2m-ce")
        co.target_multi.add(site)

        self.assertEqual(site.co_m2m_ce.count(), 1)
        self.assertTrue(site.co_m2m_ce.exists())

    def test_m2m_reverse_descriptor_removed_on_field_delete(self):
        """Deleting the field removes the reverse descriptor from target models."""
        field = self._create_m2m_field(related_name="co_m2m_del")
        self.cot.get_model()
        self.assertTrue(hasattr(Site, "co_m2m_del"), "Descriptor must be present before delete")

        field.delete()

        self.assertFalse(
            hasattr(Site, "co_m2m_del"),
            "Reverse descriptor must be removed after field delete",
        )

    def test_m2m_reverse_descriptor_wired_when_type_added(self):
        """Adding a type via m2m_changed post_add wires the M2M reverse descriptor."""
        from dcim.models import Device
        device_ot = ObjectType.objects.get(app_label="dcim", model="device")

        field = CustomObjectTypeField.objects.create(
            custom_object_type=self.cot,
            name="target_multi2", label="Targets", type="multiobject",
            is_polymorphic=True,
            related_name="co_m2m_added",
        )
        field.related_object_types.set([self.site_ot])
        self.cot.get_model()
        self.assertFalse(hasattr(Device, "co_m2m_added"), "Device not yet in allowed types")

        field.related_object_types.add(device_ot)  # fires m2m_changed post_add

        self.assertTrue(
            hasattr(Device, "co_m2m_added"),
            "Descriptor must be wired on Device after adding it to related_object_types",
        )

    def test_gfk_reverse_descriptor_unwired_on_clear(self):
        """Clearing related_object_types via pre_clear removes descriptors from all target classes."""
        field = self._create_gfk_field(related_name="co_gfk_cleared")
        self.cot.get_model()
        self.assertTrue(hasattr(Site, "co_gfk_cleared"))

        field.related_object_types.clear()  # fires m2m_changed pre_clear

        self.assertFalse(
            hasattr(Site, "co_gfk_cleared"),
            "Descriptor must be removed from Site after clear()",
        )

    def test_m2m_reverse_descriptor_unwired_on_clear(self):
        """Clearing related_object_types via pre_clear removes M2M descriptors from all target classes."""
        field = self._create_m2m_field(related_name="co_m2m_cleared")
        self.cot.get_model()
        self.assertTrue(hasattr(Site, "co_m2m_cleared"))

        field.related_object_types.clear()  # fires m2m_changed pre_clear

        self.assertFalse(
            hasattr(Site, "co_m2m_cleared"),
            "Descriptor must be removed from Site after clear()",
        )
