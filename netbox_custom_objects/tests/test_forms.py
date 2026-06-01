"""
Unit tests for CustomObjectTypeFieldForm field visibility and fieldset construction.
"""
from django.test import TestCase

from core.models import ObjectType
from extras.choices import CustomFieldTypeChoices

from netbox_custom_objects.forms import CustomObjectTypeFieldForm
from netbox_custom_objects.models import CustomObjectType

from .base import CustomObjectsTestCase


def _fieldset_fields(form):
    """Return the flat set of field names declared across all of the form's fieldsets."""
    names = set()
    for fs in form.fieldsets:
        for item in fs.items:
            if isinstance(item, str):
                names.add(item)
    return names


def _make_form(data, instance=None):
    return CustomObjectTypeFieldForm(data=data, instance=instance)


class PolymorphicRelatedNameVisibilityTestCase(CustomObjectsTestCase, TestCase):
    """Regression tests for issue #522: related_name field hidden when Polymorphic is checked.

    CustomObjectTypeFieldForm.__init__ removed related_object_type from self.fields
    when is_polymorphic=True (replacing it with related_object_types), then used
    ``"related_object_type" in self.fields`` as the signal to append related_name
    to the Related Object fieldset.  Because related_object_type had already been
    removed, the check failed and related_name was deleted from the form instead.
    """

    @classmethod
    def setUpTestData(cls):
        cls.cot = CustomObjectType.objects.create(
            name="Tester",
            slug="tester",
            verbose_name_plural="Testers",
        )
        cls.site_ot = ObjectType.objects.get(app_label="dcim", model="site")

    def _bound_form(self, field_type, is_polymorphic, related_object_type_ids=None):
        data = {
            "custom_object_type": self.cot.pk,
            "name": "myfield",
            "label": "My Field",
            "type": field_type,
            "is_polymorphic": "1" if is_polymorphic else "",
            "required": "",
            "unique": "",
            "primary": "",
            "default": "",
            "description": "",
            "group_name": "",
            "context": "default",
            "search_weight": "1000",
            "filter_logic": "loose",
            "ui_visible": "hidden",
            "ui_editable": "hidden",
            "weight": "100",
            "is_cloneable": "",
            "related_name": "",
            "related_object_filter": "{}",
        }
        if related_object_type_ids is not None:
            data["related_object_types"] = related_object_type_ids
        else:
            data["related_object_type"] = str(self.site_ot.pk)
        return _make_form(data)

    # --- related_name visibility ---

    def test_related_name_visible_for_non_polymorphic_object(self):
        """related_name must appear in self.fields and fieldsets for non-polymorphic Object."""
        form = self._bound_form(CustomFieldTypeChoices.TYPE_OBJECT, is_polymorphic=False)
        self.assertIn("related_name", form.fields,
                      "related_name must be in form.fields for non-polymorphic Object")
        self.assertIn("related_name", _fieldset_fields(form),
                      "related_name must appear in a fieldset for non-polymorphic Object")

    def test_related_name_visible_for_non_polymorphic_multiobject(self):
        """related_name must appear for non-polymorphic MultiObject fields."""
        form = self._bound_form(CustomFieldTypeChoices.TYPE_MULTIOBJECT, is_polymorphic=False)
        self.assertIn("related_name", form.fields)
        self.assertIn("related_name", _fieldset_fields(form))

    def test_related_name_visible_for_polymorphic_object(self):
        """related_name must appear for polymorphic Object fields (regression: issue #522)."""
        form = self._bound_form(
            CustomFieldTypeChoices.TYPE_OBJECT,
            is_polymorphic=True,
            related_object_type_ids=[str(self.site_ot.pk)],
        )
        self.assertIn("related_name", form.fields,
                      "related_name must not be deleted for polymorphic Object (issue #522)")
        self.assertIn("related_name", _fieldset_fields(form),
                      "related_name must appear in a fieldset for polymorphic Object (issue #522)")

    def test_related_name_visible_for_polymorphic_multiobject(self):
        """related_name must appear for polymorphic MultiObject fields (regression: issue #522)."""
        form = self._bound_form(
            CustomFieldTypeChoices.TYPE_MULTIOBJECT,
            is_polymorphic=True,
            related_object_type_ids=[str(self.site_ot.pk)],
        )
        self.assertIn("related_name", form.fields,
                      "related_name must not be deleted for polymorphic MultiObject (issue #522)")
        self.assertIn("related_name", _fieldset_fields(form),
                      "related_name must appear in a fieldset for polymorphic MultiObject (issue #522)")

    def test_related_name_absent_for_non_object_type(self):
        """related_name must not appear for non-object field types (e.g. text)."""
        data = {
            "custom_object_type": self.cot.pk,
            "name": "myfield",
            "label": "My Field",
            "type": CustomFieldTypeChoices.TYPE_TEXT,
            "required": "",
            "unique": "",
            "primary": "",
            "default": "",
            "description": "",
            "group_name": "",
            "context": "default",
            "search_weight": "1000",
            "filter_logic": "loose",
            "ui_visible": "hidden",
            "ui_editable": "hidden",
            "weight": "100",
            "is_cloneable": "",
        }
        form = _make_form(data)
        self.assertNotIn("related_name", form.fields)

    # --- on_delete_behavior visibility ---

    def test_on_delete_behavior_visible_only_for_non_polymorphic_object(self):
        """on_delete_behavior must appear only for non-polymorphic single Object fields."""
        non_poly_obj = self._bound_form(CustomFieldTypeChoices.TYPE_OBJECT, is_polymorphic=False)
        self.assertIn("on_delete_behavior", non_poly_obj.fields)

        non_poly_multi = self._bound_form(CustomFieldTypeChoices.TYPE_MULTIOBJECT, is_polymorphic=False)
        self.assertNotIn("on_delete_behavior", non_poly_multi.fields)

        poly_obj = self._bound_form(
            CustomFieldTypeChoices.TYPE_OBJECT,
            is_polymorphic=True,
            related_object_type_ids=[str(self.site_ot.pk)],
        )
        self.assertNotIn("on_delete_behavior", poly_obj.fields,
                         "on_delete_behavior must not appear for polymorphic Object fields")

        poly_multi = self._bound_form(
            CustomFieldTypeChoices.TYPE_MULTIOBJECT,
            is_polymorphic=True,
            related_object_type_ids=[str(self.site_ot.pk)],
        )
        self.assertNotIn("on_delete_behavior", poly_multi.fields)
