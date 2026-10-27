"""
Polymorphic object and multi-object fields over the REST API: defining the fields,
and reading and writing their values.
"""
import json

from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.test import Client, TestCase
from django.urls import reverse
from rest_framework import status

from core.models import ObjectType
from dcim.models import Site
from extras.validators import CustomValidator
from ipam.models import Prefix, IPAddress
from ipam.choices import PrefixStatusChoices
from utilities.testing import create_test_user

from netbox_custom_objects.constants import APP_LABEL
from netbox_custom_objects.models import CustomObjectType, CustomObjectTypeField
from netbox_custom_objects.tests.base import (
    CustomObjectsTestCase,
    _grant_perm,
    create_token,
)


class PolymorphicFieldAPITestCase(CustomObjectsTestCase, TestCase):
    """
    API tests for polymorphic Object and MultiObject fields.
    """

    def setUp(self):
        super().setUp()
        self.user = create_test_user("poly-api-user")
        token_key = create_token(self.user)
        self.header = {"HTTP_AUTHORIZATION": f"Token {token_key}"}
        # Reset client to clear the session cookie set by CustomObjectsTestCase.setUp()
        # (force_login causes SessionAuthentication to take priority over TokenAuthentication)
        self.client = Client()

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

    def test_patch_unrelated_field_with_stale_gfk_succeeds(self):
        """PATCH of another field is not blocked by a stored reference whose target was deleted."""
        _grant_perm(self.user, "change", self.model, "co-change-stale")
        obj = self.model.objects.create(name="gfk-stale-obj")
        site_pk = self.site.pk
        self.site.delete()
        # Deleting the target now clears the reference, so write the stale one back as older data would have it.
        self.model.objects.filter(pk=obj.pk).update(poly_obj_content_type=self.site_ot, poly_obj_object_id=site_pk)

        response = self.client.patch(
            self._obj_detail_url(obj.pk),
            json.dumps({"name": "gfk-stale-renamed"}),
            content_type="application/json",
            **self.header,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        obj.refresh_from_db()
        self.assertEqual(obj.name, "gfk-stale-renamed")
        self.assertEqual(obj.poly_obj_object_id, site_pk)

    def test_patch_gfk_to_deleted_object_rejected(self):
        """PATCH pointing the reference at an object that no longer exists is a 400, not a 500."""
        _grant_perm(self.user, "change", self.model, "co-change-deleted")
        site_ct = ContentType.objects.get_for_model(Site)
        obj = self.model.objects.create(name="gfk-deleted-target")
        site_pk = self.site.pk
        self.site.delete()

        response = self.client.patch(
            self._obj_detail_url(obj.pk),
            json.dumps({"poly_obj": {"content_type_id": site_ct.pk, "object_id": site_pk}}),
            content_type="application/json",
            **self.header,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.content)
        obj.refresh_from_db()
        self.assertIsNone(obj.poly_obj_object_id)

    def test_custom_validator_sees_gfk_value_on_api_create(self):
        """A CUSTOM_VALIDATORS validate() call during API create must see the
        submitted polymorphic GFK value, not None."""

        _grant_perm(self.user, "add", self.model, "co-add-validator")
        seen = {}

        class _CaptureValidator(CustomValidator):
            def validate(self, instance, request):
                seen['poly_obj'] = instance.poly_obj

        site_ct = ContentType.objects.get_for_model(Site)
        data = {
            "name": "validator-api-create-obj",
            "poly_obj": {"content_type_id": site_ct.pk, "object_id": self.site.pk},
        }
        with self.settings(CUSTOM_VALIDATORS={f'{APP_LABEL}.{self.cot.slug}': [_CaptureValidator()]}):
            response = self.client.post(
                self._obj_list_url(), json.dumps(data), content_type="application/json", **self.header
            )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.content)
        self.assertEqual(seen.get('poly_obj'), self.site)

    def test_custom_validator_sees_updated_gfk_value_on_api_update(self):
        """A CUSTOM_VALIDATORS validate() call during an API PATCH that changes the
        polymorphic GFK value must see the new value, not the pre-update one."""

        _grant_perm(self.user, "change", self.model, "co-change-validator")
        obj = self.model.objects.create(name="validator-api-update-obj")
        obj.poly_obj = self.site
        obj.save()

        seen = {}

        class _CaptureValidator(CustomValidator):
            def validate(self, instance, request):
                seen['poly_obj'] = instance.poly_obj

        prefix_ct = ContentType.objects.get_for_model(Prefix)
        data = {"poly_obj": {"content_type_id": prefix_ct.pk, "object_id": self.prefix.pk}}
        with self.settings(CUSTOM_VALIDATORS={f'{APP_LABEL}.{self.cot.slug}': [_CaptureValidator()]}):
            response = self.client.patch(
                self._obj_detail_url(obj.pk), json.dumps(data), content_type="application/json", **self.header
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertEqual(seen.get('poly_obj'), self.prefix)

    def test_custom_validator_sees_cleared_gfk_value_on_api_update(self):
        """PATCHing poly_obj to null must clear it on the instance validate() runs
        against, and the None restore after validate() must still round-trip
        correctly into update()."""

        _grant_perm(self.user, "change", self.model, "co-change-validator-null")
        obj = self.model.objects.create(name="validator-api-clear-obj")
        obj.poly_obj = self.site
        obj.save()

        seen = {}
        called = []

        class _CaptureValidator(CustomValidator):
            def validate(self, instance, request):
                called.append(True)
                seen['poly_obj'] = instance.poly_obj

        data = {"poly_obj": None}
        with self.settings(CUSTOM_VALIDATORS={f'{APP_LABEL}.{self.cot.slug}': [_CaptureValidator()]}):
            response = self.client.patch(
                self._obj_detail_url(obj.pk), json.dumps(data), content_type="application/json", **self.header
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertTrue(called, "validator was never invoked")
        self.assertIsNone(seen.get('poly_obj'))
        obj.refresh_from_db()
        self.assertIsNone(obj.poly_obj)

    # --- Custom object CRUD with polymorphic M2M ---

    def test_create_custom_object_with_polymorphic_m2m_via_api(self):
        """POST a custom object with a list of heterogeneous polymorphic M2M values."""
        _grant_perm(self.user, "add", self.model, "co-add")
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
        _grant_perm(self.user, "delete", self.model, "co-delete")
        obj = self.model.objects.create(name="m2m-delete-obj")
        obj.poly_multi.add(self.site, self.prefix)
        pk = obj.pk

        # Resolve the through model before the delete so we can verify cascade cleanup.
        through_model = apps.get_model(APP_LABEL, self.m2m_field.through_model_name)

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
