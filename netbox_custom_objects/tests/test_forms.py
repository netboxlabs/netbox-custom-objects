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


class PolymorphicRelatedNameCollisionFormTestCase(CustomObjectsTestCase, TestCase):
    """Form-level validation: related_name must not collide with a pre-existing attribute
    on any target model class, even for new (unsaved) fields where the model's clean()
    would not run the check (it requires self.pk to query related_object_types rows)."""

    @classmethod
    def setUpTestData(cls):
        cls.cot = CustomObjectType.objects.create(
            name="CollisionTester",
            slug="collision-tester",
            verbose_name_plural="Collision Testers",
        )
        cls.site_ot = ObjectType.objects.get(app_label="dcim", model="site")

    def _make_polymorphic_object_form(self, related_name, related_object_type_ids=None):
        if related_object_type_ids is None:
            related_object_type_ids = [str(self.site_ot.pk)]
        # custom_object_type is always disabled; pass it via initial so Django uses it.
        return CustomObjectTypeFieldForm(
            initial={"custom_object_type": self.cot.pk},
            data={
                "name": "test_field",
                "label": "Test",
                "type": CustomFieldTypeChoices.TYPE_OBJECT,
                "is_polymorphic": "1",
                "related_object_types": related_object_type_ids,
                "related_name": related_name,
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
            },
        )

    def test_new_field_related_name_colliding_with_target_model_attribute_invalid(self):
        """A new polymorphic field whose related_name clashes with a native target
        attribute must produce a form error — this is the case the model's clean()
        cannot catch (no pk yet)."""
        sentinel = object()
        attr_name = "_co_form_collision_test_attr"
        from dcim.models import Site
        setattr(Site, attr_name, sentinel)
        try:
            form = self._make_polymorphic_object_form(related_name=attr_name)
            self.assertFalse(form.is_valid())
            self.assertIn("related_name", form.errors)
        finally:
            if Site.__dict__.get(attr_name) is sentinel:
                delattr(Site, attr_name)

    def test_new_field_safe_related_name_is_valid(self):
        """A new polymorphic field with a related_name that does not conflict passes form
        validation."""
        form = self._make_polymorphic_object_form(related_name="co_safe_form_test_ref")
        self.assertTrue(form.is_valid(), form.errors)
