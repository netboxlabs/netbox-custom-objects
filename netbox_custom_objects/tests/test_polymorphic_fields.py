"""
Tests for polymorphic GenericForeignKey field support (issue #31).

Covers both API and UI (form) paths for:
  - Polymorphic single-object (GFK) fields
  - Polymorphic multi-object (through-table M2M) fields
"""
import json

from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from rest_framework import status

from core.models import ObjectType
from dcim.models import Manufacturer, Site, DeviceType, DeviceRole, Device
from ipam.models import Prefix, IPAddress
from ipam.choices import PrefixStatusChoices
from users.models import ObjectPermission, Token

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
        response = self.client.post(self._obj_list_url(), json.dumps(data), content_type="application/json", **self.header)
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
        response = self.client.post(self._obj_list_url(), json.dumps(data), content_type="application/json", **self.header)
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
        response = self.client.post(self._obj_list_url(), json.dumps(data), content_type="application/json", **self.header)
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

    def _field_delete_url(self, field_pk):
        return reverse(
            "plugins:netbox_custom_objects:customobjecttypefield_delete",
            kwargs={"pk": field_pk},
        )

    # --- Edit form structure ---

    def test_edit_form_has_per_type_subfields_not_raw_gfk_columns(self):
        """The edit form exposes per-type sub-fields, not the raw _content_type/_object_id."""
        obj = self.model.objects.create(name="form-test-obj")
        response = self.client.get(self._edit_url(obj.pk))
        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        # Per-type sub-fields must be present
        self.assertIn("poly_obj__dcim__site", form.fields)
        self.assertIn("poly_obj__ipam__prefix", form.fields)
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

    def test_edit_form_subfield_labels_are_human_readable(self):
        """Sub-field labels use the field's label and the human-readable type name."""
        obj = self.model.objects.create(name="label-test-obj")
        response = self.client.get(self._edit_url(obj.pk))
        form = response.context["form"]
        # Should be "Poly Obj (DCIM > Site)" not "poly_obj (dcim.site)"
        label = form.fields["poly_obj__dcim__site"].label
        self.assertIn("Poly Obj", label)
        self.assertNotIn("dcim.site", label)

    def test_edit_form_preselects_existing_gfk_value(self):
        """For an existing object, the correct sub-field is pre-populated."""
        obj = self.model.objects.create(name="prefill-test")
        obj.poly_obj = self.site1
        obj.save()

        response = self.client.get(self._edit_url(obj.pk))
        form = response.context["form"]
        # Initial values are stored in form.initial (not on the field itself, since
        # DynamicModelChoiceField sets initial via the form constructor's initial= kwarg)
        site_initial = form.initial.get("poly_obj__dcim__site")
        self.assertEqual(site_initial, self.site1.pk)
        # Prefix sub-field initial should be empty
        prefix_initial = form.initial.get("poly_obj__ipam__prefix")
        self.assertFalse(prefix_initial)

    # --- Edit form submission ---

    def test_submit_edit_form_sets_gfk_to_site(self):
        """POST the edit form with a Site sub-field saves the GFK to that Site."""
        obj = self.model.objects.create(name="submit-gfk-obj")
        data = {
            "name": "submit-gfk-obj",
            "poly_obj__dcim__site": self.site1.pk,
            "csrfmiddlewaretoken": "fake",
        }
        response = self.client.post(self._edit_url(obj.pk), data, follow=True)
        self.assertNotIn(response.status_code, [400, 403, 500])
        obj.refresh_from_db()
        self.assertEqual(obj.poly_obj, self.site1)

    def test_submit_edit_form_clears_gfk_when_no_subfield_selected(self):
        """POST with no sub-field selected clears an existing GFK value."""
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

    def test_bulk_edit_form_has_polymorphic_subfields(self):
        """The bulk edit form also exposes per-type sub-fields for polymorphic fields."""
        obj1 = self.model.objects.create(name="bulk-1")
        obj2 = self.model.objects.create(name="bulk-2")
        # POST without _apply renders the form
        response = self.client.post(
            self._bulk_edit_url(),
            data={"pk": [obj1.pk, obj2.pk]},
        )
        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertIn("poly_obj__dcim__site", form.fields)
        self.assertIn("poly_multi__dcim__site", form.fields)
        self.assertNotIn("poly_obj_content_type", form.fields)

    def test_bulk_edit_applies_gfk_to_all_selected_objects(self):
        """Bulk edit sets the GFK field on all selected objects."""
        obj1 = self.model.objects.create(name="bulk-gfk-1")
        obj2 = self.model.objects.create(name="bulk-gfk-2")
        data = {
            "pk": [obj1.pk, obj2.pk],
            "_apply": "1",
            "poly_obj__dcim__site": self.site1.pk,
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