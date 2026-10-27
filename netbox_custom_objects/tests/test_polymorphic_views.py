"""
Polymorphic object and multi-object fields in the UI: the edit, bulk edit, detail and
delete pages and their forms.
"""

from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse

from core.models import ObjectChange, ObjectType
from dcim.models import Site
from extras.validators import CustomValidator
from ipam.models import Prefix
from ipam.choices import PrefixStatusChoices
from utilities.testing import create_test_user

from netbox_custom_objects.constants import APP_LABEL
from netbox_custom_objects.models import CustomObjectType, CustomObjectTypeField
from netbox_custom_objects.tests.base import (
    CustomObjectsTestCase,
    _grant_perm,
)


class PolymorphicFieldUITestCase(CustomObjectsTestCase, TestCase):
    """
    UI tests for polymorphic fields on the custom object edit form.
    """

    def setUp(self):
        super().setUp()
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

    def test_submit_edit_form_hidden_poly_obj_with_stale_target(self):
        """A hidden polymorphic field pointing at a deleted object must not block editing.

        The form has no field for the GFK backing columns, so a validation error keyed
        to them would raise ValueError in add_error() and the edit page would 500.
        """
        self.gfk_field.ui_editable = "hidden"
        self.gfk_field.save()
        model = self.cot.get_model()
        obj = model.objects.create(name="hidden-stale")
        site2_pk = self.site2.pk
        self.site2.delete()
        # Deleting the target now clears the reference, so write the stale one back as older data would have it.
        model.objects.filter(pk=obj.pk).update(poly_obj_content_type=self.site_ot, poly_obj_object_id=site2_pk)

        data = {"name": "hidden-stale-renamed", "csrfmiddlewaretoken": "fake"}
        response = self.client.post(self._edit_url(obj.pk), data, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.redirect_chain, "edit form was re-rendered with errors")
        obj = model.objects.get(pk=obj.pk)
        self.assertEqual(obj.name, "hidden-stale-renamed")
        # The hidden field is left as it was.
        self.assertEqual(obj.poly_obj_object_id, site2_pk)

    def test_submit_edit_form_hidden_poly_obj_with_null_content_type(self):
        """As above when the target type was deleted: content type NULL, object ID still set."""
        self.gfk_field.ui_editable = "hidden"
        self.gfk_field.save()
        model = self.cot.get_model()
        obj = model.objects.create(name="hidden-half")
        model.objects.filter(pk=obj.pk).update(poly_obj_content_type=None, poly_obj_object_id=42)

        data = {"name": "hidden-half-renamed", "csrfmiddlewaretoken": "fake"}
        response = self.client.post(self._edit_url(obj.pk), data, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.redirect_chain, "edit form was re-rendered with errors")
        obj = model.objects.get(pk=obj.pk)
        self.assertEqual(obj.name, "hidden-half-renamed")
        self.assertEqual(obj.poly_obj_object_id, 42)

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

    def test_bulk_edit_leaves_stale_gfk_alone(self):
        """Bulk editing an unrelated field must not fail on a GFK whose target is gone.

        BulkEditView._update_objects() calls full_clean() with no exclude, so a stale
        reference would abort the whole operation.
        """
        stale_site = self.model.objects.create(name="bulk-stale-site")
        site2_pk = self.site2.pk
        self.site2.delete()
        # Deleting the target now clears the reference, so write the stale one back as older data would have it.
        self.model.objects.filter(pk=stale_site.pk).update(
            poly_obj_content_type=self.site_ot, poly_obj_object_id=site2_pk
        )
        stale_type = self.model.objects.create(name="bulk-stale-type")
        self.model.objects.filter(pk=stale_type.pk).update(poly_obj_content_type=None, poly_obj_object_id=42)

        data = {
            "pk": [stale_site.pk, stale_type.pk],
            "_apply": "1",
            "name": "bulk-renamed",
            "csrfmiddlewaretoken": "fake",
        }
        response = self.client.post(self._bulk_edit_url(), data, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.redirect_chain, "bulk edit form was re-rendered with errors")
        stale_site.refresh_from_db()
        stale_type.refresh_from_db()
        self.assertEqual(stale_site.name, "bulk-renamed")
        self.assertEqual(stale_type.name, "bulk-renamed")
        # The stale references are left as they were.
        self.assertEqual(stale_site.poly_obj_object_id, site2_pk)
        self.assertEqual(stale_type.poly_obj_object_id, 42)

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

    # --- CUSTOM_VALIDATORS visibility of polymorphic GFK values ---

    def test_custom_validator_sees_gfk_value_on_create(self):
        """CUSTOM_VALIDATORS must see the submitted GFK value during form-based
        create, not None."""

        seen = {}

        class _CaptureValidator(CustomValidator):
            def validate(self, instance, request):
                seen['poly_obj'] = instance.poly_obj

        site_ct = ContentType.objects.get_for_model(Site)
        data = {
            "name": "validator-create-obj",
            "poly_obj__ct": site_ct.pk,
            "poly_obj__obj": self.site1.pk,
            "csrfmiddlewaretoken": "fake",
        }
        with self.settings(CUSTOM_VALIDATORS={f'{APP_LABEL}.{self.cot.slug}': [_CaptureValidator()]}):
            response = self.client.post(self._add_url(), data, follow=True)
        self.assertNotIn(response.status_code, [400, 403, 500])
        self.assertEqual(seen.get('poly_obj'), self.site1)

    def test_custom_validator_sees_updated_gfk_value_on_edit(self):
        """Editing an object and changing its polymorphic GFK value must be visible
        to CUSTOM_VALIDATORS during that same request, not the pre-edit value."""

        obj = self.model.objects.create(name="validator-edit-obj")
        obj.poly_obj = self.site1
        obj.save()

        seen = {}

        class _CaptureValidator(CustomValidator):
            def validate(self, instance, request):
                seen['poly_obj'] = instance.poly_obj

        prefix_ct = ContentType.objects.get_for_model(Prefix)
        data = {
            "name": "validator-edit-obj",
            "poly_obj__ct": prefix_ct.pk,
            "poly_obj__obj": self.prefix1.pk,
            "csrfmiddlewaretoken": "fake",
        }
        with self.settings(CUSTOM_VALIDATORS={f'{APP_LABEL}.{self.cot.slug}': [_CaptureValidator()]}):
            response = self.client.post(self._edit_url(obj.pk), data, follow=True)
        self.assertNotIn(response.status_code, [400, 403, 500])
        self.assertEqual(seen.get('poly_obj'), self.prefix1)
