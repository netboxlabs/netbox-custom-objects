"""
Tests for filtersets used by the plugin's UI and API views.
"""
import datetime
from decimal import Decimal
from unittest.mock import MagicMock

import django_filters
from django.test import TestCase
from django.utils import timezone

from core.models import ObjectType
from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site
from extras.choices import CustomFieldTypeChoices
from extras.models import CustomFieldChoiceSet

from netbox_custom_objects.field_types import MultiObjectFieldType, ObjectFieldType
from netbox_custom_objects.filtersets import (
    ArrayContainsFilter, NonPolymorphicMultiObjectFilter, NonPolymorphicObjectFilter,
    PolymorphicMultiObjectFilter, PolymorphicObjectFilter,
    build_filter_for_field, get_filterset_class,
)
from netbox_custom_objects.models import CustomObjectTypeField
from utilities.forms.fields import (
    DynamicModelChoiceField,
    DynamicModelMultipleChoiceField,
)

from .base import CustomObjectsTestCase


def _make_device_fixtures(suffix):
    """Create minimal DCIM fixtures needed to instantiate a Device."""
    site = Site.objects.create(name=f"Site {suffix}", slug=f"site-{suffix}")
    mfr = Manufacturer.objects.create(name=f"Mfr {suffix}", slug=f"mfr-{suffix}")
    dt = DeviceType.objects.create(
        manufacturer=mfr, model=f"DT {suffix}", slug=f"dt-{suffix}"
    )
    role = DeviceRole.objects.create(
        name=f"Role {suffix}", slug=f"role-{suffix}", color="ff0000"
    )
    return site, dt, role


# ---------------------------------------------------------------------------
# ObjectFieldType.get_filterform_field — form field shape
# ---------------------------------------------------------------------------


class ObjectFieldFilterFormFieldTestCase(CustomObjectsTestCase, TestCase):
    """get_filterform_field() for an object field returns a DynamicModelChoiceField."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.cot = cls.create_custom_object_type(name="ObjFFTest", slug="obj-ff-test")
        cls.create_custom_object_type_field(
            cls.cot, name="name", label="Name", type="text", primary=True, required=True
        )
        cls.field = cls.create_custom_object_type_field(
            cls.cot,
            name="device",
            label="Device",
            type="object",
            related_object_type=cls.get_device_object_type(),
        )

    def test_returns_dynamic_model_choice_field(self):
        form_field = ObjectFieldType().get_filterform_field(self.field)
        self.assertIsInstance(form_field, DynamicModelChoiceField)

    def test_form_field_not_required(self):
        form_field = ObjectFieldType().get_filterform_field(self.field)
        self.assertFalse(form_field.required)

    def test_form_field_queryset_uses_related_model(self):
        form_field = ObjectFieldType().get_filterform_field(self.field)
        self.assertEqual(form_field.queryset.model, Device)


# ---------------------------------------------------------------------------
# ObjectFieldType — filterset queryset filtering (DCIM target)
# ---------------------------------------------------------------------------


class ObjectFieldFiltersetTestCase(CustomObjectsTestCase, TestCase):
    """Filtering by ?<field>=<pk> works for object fields pointing at DCIM models."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.cot = cls.create_custom_object_type(name="ObjFSTest", slug="obj-fs-test")
        cls.create_custom_object_type_field(
            cls.cot, name="name", label="Name", type="text", primary=True, required=True
        )
        cls.create_custom_object_type_field(
            cls.cot,
            name="device",
            label="Device",
            type="object",
            related_object_type=cls.get_device_object_type(),
        )

        site, dt, role = _make_device_fixtures("ofst")
        cls.device1 = Device.objects.create(
            name="Device OFS 1", site=site, device_type=dt, role=role
        )
        cls.device2 = Device.objects.create(
            name="Device OFS 2", site=site, device_type=dt, role=role
        )

        model = cls.cot.get_model()
        cls.obj_d1 = model.objects.create(name="Obj D1", device=cls.device1)
        cls.obj_d2 = model.objects.create(name="Obj D2", device=cls.device2)
        cls.obj_none = model.objects.create(name="Obj None")

    def _filterset(self, params):
        model = self.cot.get_model()
        return get_filterset_class(model)(params, model.objects.all())

    def test_filter_returns_matching_object(self):
        pks = list(self._filterset({"device": self.device1.pk}).qs.values_list("pk", flat=True))
        self.assertIn(self.obj_d1.pk, pks)
        self.assertNotIn(self.obj_d2.pk, pks)
        self.assertNotIn(self.obj_none.pk, pks)

    def test_filter_different_value(self):
        pks = list(self._filterset({"device": self.device2.pk}).qs.values_list("pk", flat=True))
        self.assertIn(self.obj_d2.pk, pks)
        self.assertNotIn(self.obj_d1.pk, pks)

    def test_no_filter_returns_all(self):
        self.assertEqual(self._filterset({}).qs.count(), 3)


# ---------------------------------------------------------------------------
# MultiObjectFieldType.get_filterform_field — form field shape
# ---------------------------------------------------------------------------


class MultiObjectFieldFilterFormFieldTestCase(CustomObjectsTestCase, TestCase):
    """get_filterform_field() for a multiobject field returns a DynamicModelMultipleChoiceField."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.cot = cls.create_custom_object_type(name="MoFFTest", slug="mo-ff-test")
        cls.create_custom_object_type_field(
            cls.cot, name="name", label="Name", type="text", primary=True, required=True
        )
        cls.field = cls.create_custom_object_type_field(
            cls.cot,
            name="sites",
            label="Sites",
            type="multiobject",
            related_object_type=cls.get_site_object_type(),
        )

    def test_returns_dynamic_model_multiple_choice_field(self):
        form_field = MultiObjectFieldType().get_filterform_field(self.field)
        self.assertIsInstance(form_field, DynamicModelMultipleChoiceField)

    def test_form_field_not_required(self):
        form_field = MultiObjectFieldType().get_filterform_field(self.field)
        self.assertFalse(form_field.required)

    def test_form_field_queryset_uses_related_model(self):
        form_field = MultiObjectFieldType().get_filterform_field(self.field)
        self.assertEqual(form_field.queryset.model, Site)


# ---------------------------------------------------------------------------
# MultiObjectFieldType — filterset queryset filtering (DCIM target)
# ---------------------------------------------------------------------------


class MultiObjectFieldFiltersetTestCase(CustomObjectsTestCase, TestCase):
    """Filtering by ?<field>=<pk> works for multiobject fields pointing at DCIM models."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.cot = cls.create_custom_object_type(name="MoFSTest", slug="mo-fs-test")
        cls.create_custom_object_type_field(
            cls.cot, name="name", label="Name", type="text", primary=True, required=True
        )
        cls.create_custom_object_type_field(
            cls.cot,
            name="sites",
            label="Sites",
            type="multiobject",
            related_object_type=cls.get_site_object_type(),
        )

        cls.site1 = Site.objects.create(name="Site MOFS 1", slug="site-mofs-1")
        cls.site2 = Site.objects.create(name="Site MOFS 2", slug="site-mofs-2")

        model = cls.cot.get_model()
        cls.obj_s1 = model.objects.create(name="Obj S1")
        cls.obj_s1.sites.add(cls.site1)
        cls.obj_s2 = model.objects.create(name="Obj S2")
        cls.obj_s2.sites.add(cls.site2)
        cls.obj_both = model.objects.create(name="Obj Both")
        cls.obj_both.sites.add(cls.site1, cls.site2)
        cls.obj_none = model.objects.create(name="Obj None")

    def _filterset(self, params):
        model = self.cot.get_model()
        return get_filterset_class(model)(params, model.objects.all())

    def test_filter_single_site_returns_linked_objects(self):
        pks = list(self._filterset({"sites": [self.site1.pk]}).qs.values_list("pk", flat=True))
        self.assertIn(self.obj_s1.pk, pks)
        self.assertIn(self.obj_both.pk, pks)
        self.assertNotIn(self.obj_s2.pk, pks)
        self.assertNotIn(self.obj_none.pk, pks)

    def test_filter_multiple_sites_returns_union(self):
        # OR semantics: any object linked to site1 or site2
        pks = list(
            self._filterset({"sites": [self.site1.pk, self.site2.pk]}).qs.values_list("pk", flat=True)
        )
        self.assertIn(self.obj_s1.pk, pks)
        self.assertIn(self.obj_s2.pk, pks)
        self.assertIn(self.obj_both.pk, pks)
        self.assertNotIn(self.obj_none.pk, pks)

    def test_filter_multiple_sites_no_duplicates(self):
        # obj_both is linked to both sites but should appear only once
        qs = self._filterset({"sites": [self.site1.pk, self.site2.pk]}).qs
        obj_both_count = qs.filter(pk=self.obj_both.pk).count()
        self.assertEqual(obj_both_count, 1)

    def test_filter_other_site(self):
        pks = list(self._filterset({"sites": [self.site2.pk]}).qs.values_list("pk", flat=True))
        self.assertIn(self.obj_s2.pk, pks)
        self.assertIn(self.obj_both.pk, pks)
        self.assertNotIn(self.obj_s1.pk, pks)

    def test_no_filter_returns_all(self):
        self.assertEqual(self._filterset({}).qs.count(), 4)


# ---------------------------------------------------------------------------
# Object field with a custom object type as target
# ---------------------------------------------------------------------------


class CustomObjectTargetObjectFieldTestCase(CustomObjectsTestCase, TestCase):
    """Object field pointing at another custom object type: form field and filtering."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # Target COT
        cls.target_cot = cls.create_custom_object_type(
            name="TargetObj", slug="target-obj"
        )
        cls.create_custom_object_type_field(
            cls.target_cot, name="name", label="Name", type="text", primary=True, required=True
        )

        # Source COT with object field → target COT
        cls.source_cot = cls.create_custom_object_type(
            name="SourceObj", slug="source-obj"
        )
        cls.create_custom_object_type_field(
            cls.source_cot, name="name", label="Name", type="text", primary=True, required=True
        )
        cls.create_custom_object_type_field(
            cls.source_cot,
            name="related",
            label="Related",
            type="object",
            related_object_type=cls.target_cot.object_type,
        )

        # Refresh so in-memory cache_timestamp matches the DB value bumped by the signal
        # when the TYPE_OBJECT 'related' field was saved above.
        cls.target_cot.refresh_from_db()
        target_model = cls.target_cot.get_model()
        cls.target1 = target_model.objects.create(name="Target 1")
        cls.target2 = target_model.objects.create(name="Target 2")

        source_model = cls.source_cot.get_model()
        cls.source_t1 = source_model.objects.create(name="Source T1", related=cls.target1)
        cls.source_t2 = source_model.objects.create(name="Source T2", related=cls.target2)
        cls.source_none = source_model.objects.create(name="Source None")

    def _field(self):
        return CustomObjectTypeField.objects.get(
            custom_object_type=self.source_cot, name="related"
        )

    def test_filterform_field_returns_dynamic_model_choice_field(self):
        form_field = ObjectFieldType().get_filterform_field(self._field())
        self.assertIsInstance(form_field, DynamicModelChoiceField)

    def test_filterform_field_queryset_points_at_target_model(self):
        form_field = ObjectFieldType().get_filterform_field(self._field())
        target_model = self.target_cot.get_model()
        self.assertEqual(
            form_field.queryset.model._meta.db_table,
            target_model._meta.db_table,
        )

    def test_filter_by_custom_object_target(self):
        source_model = self.source_cot.get_model()
        fs = get_filterset_class(source_model)(
            {"related": self.target1.pk}, source_model.objects.all()
        )
        pks = list(fs.qs.values_list("pk", flat=True))
        self.assertIn(self.source_t1.pk, pks)
        self.assertNotIn(self.source_t2.pk, pks)
        self.assertNotIn(self.source_none.pk, pks)

    def test_filter_other_target(self):
        source_model = self.source_cot.get_model()
        fs = get_filterset_class(source_model)(
            {"related": self.target2.pk}, source_model.objects.all()
        )
        pks = list(fs.qs.values_list("pk", flat=True))
        self.assertIn(self.source_t2.pk, pks)
        self.assertNotIn(self.source_t1.pk, pks)


# ---------------------------------------------------------------------------
# Multiobject field with a custom object type as target
# ---------------------------------------------------------------------------


class CustomObjectTargetMultiObjectFieldTestCase(CustomObjectsTestCase, TestCase):
    """Multiobject field pointing at another custom object type: form field and filtering."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # Target COT
        cls.target_cot = cls.create_custom_object_type(
            name="TargetMObj", slug="target-mobj"
        )
        cls.create_custom_object_type_field(
            cls.target_cot, name="name", label="Name", type="text", primary=True, required=True
        )

        # Source COT with multiobject field → target COT
        cls.source_cot = cls.create_custom_object_type(
            name="SourceMObj", slug="source-mobj"
        )
        cls.create_custom_object_type_field(
            cls.source_cot, name="name", label="Name", type="text", primary=True, required=True
        )
        cls.create_custom_object_type_field(
            cls.source_cot,
            name="related_items",
            label="Related Items",
            type="multiobject",
            related_object_type=cls.target_cot.object_type,
        )

        target_model = cls.target_cot.get_model()
        cls.target1 = target_model.objects.create(name="Target M1")
        cls.target2 = target_model.objects.create(name="Target M2")

        source_model = cls.source_cot.get_model()
        cls.source_t1 = source_model.objects.create(name="Source MT1")
        cls.source_t1.related_items.add(cls.target1)
        cls.source_t2 = source_model.objects.create(name="Source MT2")
        cls.source_t2.related_items.add(cls.target2)
        cls.source_both = source_model.objects.create(name="Source MBoth")
        cls.source_both.related_items.add(cls.target1, cls.target2)
        cls.source_none = source_model.objects.create(name="Source MNone")

    def _field(self):
        return CustomObjectTypeField.objects.get(
            custom_object_type=self.source_cot, name="related_items"
        )

    def test_filterform_field_returns_dynamic_model_multiple_choice_field(self):
        form_field = MultiObjectFieldType().get_filterform_field(self._field())
        self.assertIsInstance(form_field, DynamicModelMultipleChoiceField)

    def test_filterform_field_queryset_points_at_target_model(self):
        form_field = MultiObjectFieldType().get_filterform_field(self._field())
        target_model = self.target_cot.get_model()
        self.assertEqual(
            form_field.queryset.model._meta.db_table,
            target_model._meta.db_table,
        )

    def test_filter_single_target(self):
        source_model = self.source_cot.get_model()
        fs = get_filterset_class(source_model)(
            {"related_items": [self.target1.pk]}, source_model.objects.all()
        )
        pks = list(fs.qs.values_list("pk", flat=True))
        self.assertIn(self.source_t1.pk, pks)
        self.assertIn(self.source_both.pk, pks)
        self.assertNotIn(self.source_t2.pk, pks)
        self.assertNotIn(self.source_none.pk, pks)

    def test_filter_multiple_targets_returns_union(self):
        source_model = self.source_cot.get_model()
        fs = get_filterset_class(source_model)(
            {"related_items": [self.target1.pk, self.target2.pk]}, source_model.objects.all()
        )
        pks = list(fs.qs.values_list("pk", flat=True))
        self.assertIn(self.source_t1.pk, pks)
        self.assertIn(self.source_t2.pk, pks)
        self.assertIn(self.source_both.pk, pks)
        self.assertNotIn(self.source_none.pk, pks)

    def test_filter_multiple_targets_no_duplicates(self):
        source_model = self.source_cot.get_model()
        qs = get_filterset_class(source_model)(
            {"related_items": [self.target1.pk, self.target2.pk]}, source_model.objects.all()
        ).qs
        self.assertEqual(qs.filter(pk=self.source_both.pk).count(), 1)


# ---------------------------------------------------------------------------
# build_filter_for_field defensive guards
# ---------------------------------------------------------------------------


class BuildFilterForFieldGuardsTestCase(TestCase):
    """build_filter_for_field returns an empty dict rather than raising when a
    field's related_object_type is missing or its ContentType is stale."""

    def _field(self, field_type, related_object_type):
        field = MagicMock()
        field.type = field_type
        field.is_polymorphic = False
        field.related_object_type = related_object_type
        return field

    def test_object_field_missing_related_object_type_returns_empty(self):
        self.assertEqual(
            build_filter_for_field(self._field(CustomFieldTypeChoices.TYPE_OBJECT, None)),
            {},
        )

    def test_multiobject_field_missing_related_object_type_returns_empty(self):
        self.assertEqual(
            build_filter_for_field(self._field(CustomFieldTypeChoices.TYPE_MULTIOBJECT, None)),
            {},
        )

    def test_object_field_stale_content_type_returns_empty(self):
        # ContentType row exists but the app/model is no longer installed
        related_ot = MagicMock()
        related_ot.model_class.return_value = None
        self.assertEqual(
            build_filter_for_field(self._field(CustomFieldTypeChoices.TYPE_OBJECT, related_ot)),
            {},
        )

    def test_multiobject_field_stale_content_type_returns_empty(self):
        related_ot = MagicMock()
        related_ot.model_class.return_value = None
        self.assertEqual(
            build_filter_for_field(self._field(CustomFieldTypeChoices.TYPE_MULTIOBJECT, related_ot)),
            {},
        )


# ---------------------------------------------------------------------------
# Regression: get_filterset_class must not raise after apps.clear_cache()
# (issue #503)
# ---------------------------------------------------------------------------


class FiltersetAfterClearCacheTestCase(CustomObjectsTestCase, TestCase):
    """
    Regression for #503: get_filterset_class() must not raise ValueError when
    apps.clear_cache() has cleared _meta._forward_fields_map on the model.

    When get_model() generates a fresh class it calls apps.clear_cache() at the
    end of the method (models.py line ~828).  This runs _expire_cache() on all
    registered models, deleting _forward_fields_map.  A subsequent call to
    get_filterset_class() in the same request builds a ModelChoiceFilter /
    ModelMultipleChoiceFilter in declared_filters; NetBoxModelFilterSet's
    get_additional_lookups() then calls get_model_field(), which falls through
    to _relation_tree → apps.get_models() → recursive get_model() → ValueError.

    The fix uses NonPolymorphicObjectFilter / NonPolymorphicMultiObjectFilter
    (both inheriting from django_filters.Filter, not ModelChoiceFilter) so that
    _get_filter_lookup_dict returns None and get_additional_lookups exits early.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.site = Site.objects.create(name="Site503", slug="site-503")
        cls.mfr = Manufacturer.objects.create(name="Mfr503", slug="mfr-503")
        mfr_ot = ObjectType.objects.get(app_label="dcim", model="manufacturer")
        site_ot = ObjectType.objects.get(app_label="dcim", model="site")

        # COT with a non-polymorphic FK Object field (→ dcim.Manufacturer)
        cls.cot_obj = cls.create_custom_object_type(name="C503Obj", slug="c503obj")
        cls.create_custom_object_type_field(
            cls.cot_obj, name="name", label="Name", type="text", primary=True, required=True
        )
        cls.create_custom_object_type_field(
            cls.cot_obj, name="manufacturer", label="Manufacturer",
            type="object", related_object_type=mfr_ot,
        )

        # COT with a non-polymorphic M2M MultiObject field (→ dcim.Site)
        cls.cot_m2m = cls.create_custom_object_type(name="C503M2m", slug="c503m2m")
        cls.create_custom_object_type_field(
            cls.cot_m2m, name="name", label="Name", type="text", primary=True, required=True
        )
        cls.create_custom_object_type_field(
            cls.cot_m2m, name="sites", label="Sites",
            type="multiobject", related_object_type=site_ot,
        )

    def test_filterset_builds_after_expire_cache_object_field(self):
        """get_filterset_class() does not raise after _expire_cache() on an Object-field COT."""
        model = self.cot_obj.get_model()
        model._meta._expire_cache()
        filterset_class = get_filterset_class(model)
        self.assertIsNotNone(filterset_class)

    def test_filterset_builds_after_expire_cache_multiobject_field(self):
        """get_filterset_class() does not raise after _expire_cache() on a MultiObject-field COT."""
        model = self.cot_m2m.get_model()
        model._meta._expire_cache()
        filterset_class = get_filterset_class(model)
        self.assertIsNotNone(filterset_class)

    def test_filter_class_is_non_polymorphic_object_filter(self):
        """The object-field filter is a NonPolymorphicObjectFilter, not ModelChoiceFilter."""
        model = self.cot_obj.get_model()
        filterset_class = get_filterset_class(model)
        manufacturer_filter = filterset_class.base_filters.get("manufacturer")
        self.assertIsInstance(manufacturer_filter, NonPolymorphicObjectFilter)
        self.assertNotIsInstance(manufacturer_filter, django_filters.ModelChoiceFilter)

    def test_filter_class_is_non_polymorphic_multiobject_filter(self):
        """The multiobject-field filter is a NonPolymorphicMultiObjectFilter, not ModelMultipleChoiceFilter."""
        model = self.cot_m2m.get_model()
        filterset_class = get_filterset_class(model)
        sites_filter = filterset_class.base_filters.get("sites")
        self.assertIsInstance(sites_filter, NonPolymorphicMultiObjectFilter)
        self.assertNotIsInstance(sites_filter, django_filters.ModelMultipleChoiceFilter)

    def test_filter_by_fk_value_returns_matching_objects(self):
        """NonPolymorphicObjectFilter correctly filters by FK value."""
        model = self.cot_obj.get_model()
        obj = model.objects.create(name="test-obj", manufacturer=self.mfr)
        other_mfr = Manufacturer.objects.create(name="Other503", slug="other-503")
        model.objects.create(name="other-obj", manufacturer=other_mfr)

        fs = get_filterset_class(model)({"manufacturer": self.mfr.pk}, model.objects.all())
        pks = list(fs.qs.values_list("pk", flat=True))
        self.assertEqual(set(pks), {obj.pk})

    def test_filter_by_m2m_value_returns_matching_objects(self):
        """NonPolymorphicMultiObjectFilter correctly filters by M2M value."""
        model = self.cot_m2m.get_model()
        obj = model.objects.create(name="linked")
        obj.sites.add(self.site)
        model.objects.create(name="unlinked")

        Site.objects.create(name="Other503", slug="other-site-503")
        fs = get_filterset_class(model)({"sites": [self.site.pk]}, model.objects.all())
        pks = list(fs.qs.values_list("pk", flat=True))
        self.assertEqual(set(pks), {obj.pk})

    def test_object_filter_none_value_returns_full_queryset(self):
        """NonPolymorphicObjectFilter short-circuits on None and returns qs unchanged."""
        model = self.cot_obj.get_model()
        model.objects.create(name="obj-a", manufacturer=self.mfr)
        model.objects.create(name="obj-b")
        total = model.objects.count()

        fs = get_filterset_class(model)({}, model.objects.all())
        self.assertEqual(fs.qs.count(), total)

    def test_multiobject_filter_empty_value_returns_full_queryset(self):
        """NonPolymorphicMultiObjectFilter short-circuits on empty list and returns qs unchanged."""
        model = self.cot_m2m.get_model()
        model.objects.create(name="obj-a")
        model.objects.create(name="obj-b")
        total = model.objects.count()

        fs = get_filterset_class(model)({}, model.objects.all())
        self.assertEqual(fs.qs.count(), total)


# ---------------------------------------------------------------------------
# Typeahead search for non-text primary fields (issue #440)
# ---------------------------------------------------------------------------


class IntegerPrimaryFieldSearchTestCase(CustomObjectsTestCase, TestCase):
    """Typeahead search finds objects when the primary field is an Integer."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.cot = cls.create_custom_object_type(name="NetArea", slug="netarea")
        cls.create_custom_object_type_field(
            cls.cot, name="major", label="Major", type="integer", primary=True, required=True
        )

        model = cls.cot.get_model()
        cls.obj_311 = model.objects.create(major=311)
        cls.obj_400 = model.objects.create(major=400)

    def _search(self, value):
        model = self.cot.get_model()
        return get_filterset_class(model)({"q": value}, model.objects.all()).qs

    def test_search_by_integer_value_finds_match(self):
        pks = list(self._search("311").values_list("pk", flat=True))
        self.assertIn(self.obj_311.pk, pks)
        self.assertNotIn(self.obj_400.pk, pks)

    def test_search_by_integer_no_match_returns_empty(self):
        pks = list(self._search("999").values_list("pk", flat=True))
        self.assertNotIn(self.obj_311.pk, pks)
        self.assertNotIn(self.obj_400.pk, pks)

    def test_search_non_numeric_string_returns_no_results(self):
        # Non-numeric search against an integer-only COT should return nothing,
        # not raise an exception.
        pks = list(self._search("abc").values_list("pk", flat=True))
        self.assertNotIn(self.obj_311.pk, pks)

    def test_search_empty_string_returns_all(self):
        self.assertEqual(self._search("").count(), 2)


class DecimalPrimaryFieldSearchTestCase(CustomObjectsTestCase, TestCase):
    """Typeahead search uses Decimal (not float) for TYPE_DECIMAL to preserve full precision."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.cot = cls.create_custom_object_type(name="PriceObj440", slug="price-obj-440")
        cls.create_custom_object_type_field(
            cls.cot, name="price", label="Price", type="decimal", primary=True, required=True
        )

        model = cls.cot.get_model()
        cls.obj_11 = model.objects.create(price=Decimal("1.1"))
        cls.obj_03 = model.objects.create(price=Decimal("0.3"))

    def _search(self, value):
        model = self.cot.get_model()
        return get_filterset_class(model)({"q": value}, model.objects.all()).qs

    def test_search_exact_decimal_finds_match(self):
        pks = list(self._search("1.1").values_list("pk", flat=True))
        self.assertIn(self.obj_11.pk, pks)
        self.assertNotIn(self.obj_03.pk, pks)

    def test_search_imprecise_float_value_finds_match(self):
        # 0.3 cannot be represented exactly in IEEE 754 float, but Decimal("0.3") is exact.
        pks = list(self._search("0.3").values_list("pk", flat=True))
        self.assertIn(self.obj_03.pk, pks)
        self.assertNotIn(self.obj_11.pk, pks)

    def test_search_non_numeric_returns_no_results(self):
        pks = list(self._search("abc").values_list("pk", flat=True))
        self.assertNotIn(self.obj_11.pk, pks)
        self.assertNotIn(self.obj_03.pk, pks)


class SelectPrimaryFieldSearchTestCase(CustomObjectsTestCase, TestCase):
    """Typeahead search finds objects when the primary field is a Select."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.choice_set = CustomFieldChoiceSet.objects.create(
            name="StatusChoices440",
            extra_choices=[["active", "Active"], ["planned", "Planned"], ["retired", "Retired"]],
        )
        cls.cot = cls.create_custom_object_type(name="StatusObj440", slug="status-obj-440")
        cls.create_custom_object_type_field(
            cls.cot,
            name="status",
            label="Status",
            type="select",
            primary=True,
            required=True,
            choice_set=cls.choice_set,
        )

        model = cls.cot.get_model()
        cls.obj_active = model.objects.create(status="active")
        cls.obj_planned = model.objects.create(status="planned")

    def _search(self, value):
        model = self.cot.get_model()
        return get_filterset_class(model)({"q": value}, model.objects.all()).qs

    def test_search_by_select_value_finds_match(self):
        pks = list(self._search("active").values_list("pk", flat=True))
        self.assertIn(self.obj_active.pk, pks)
        self.assertNotIn(self.obj_planned.pk, pks)

    def test_search_partial_match(self):
        pks = list(self._search("plan").values_list("pk", flat=True))
        self.assertIn(self.obj_planned.pk, pks)
        self.assertNotIn(self.obj_active.pk, pks)

    def test_search_no_match_returns_empty(self):
        pks = list(self._search("retired").values_list("pk", flat=True))
        self.assertNotIn(self.obj_active.pk, pks)
        self.assertNotIn(self.obj_planned.pk, pks)


class DatePrimaryFieldSearchTestCase(CustomObjectsTestCase, TestCase):
    """Typeahead search finds objects when the primary field is a Date."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.cot = cls.create_custom_object_type(name="DateObj440", slug="date-obj-440")
        cls.create_custom_object_type_field(
            cls.cot, name="start_date", label="Start Date", type="date", primary=True, required=True
        )

        model = cls.cot.get_model()
        cls.obj_jan = model.objects.create(start_date=datetime.date(2025, 1, 15))
        cls.obj_feb = model.objects.create(start_date=datetime.date(2025, 2, 20))

    def _search(self, value):
        model = self.cot.get_model()
        return get_filterset_class(model)({"q": value}, model.objects.all()).qs

    def test_search_by_date_finds_match(self):
        pks = list(self._search("2025-01-15").values_list("pk", flat=True))
        self.assertIn(self.obj_jan.pk, pks)
        self.assertNotIn(self.obj_feb.pk, pks)

    def test_search_invalid_date_returns_no_results(self):
        pks = list(self._search("not-a-date").values_list("pk", flat=True))
        self.assertNotIn(self.obj_jan.pk, pks)
        self.assertNotIn(self.obj_feb.pk, pks)


class DateTimePrimaryFieldSearchTestCase(CustomObjectsTestCase, TestCase):
    """Typeahead search finds objects when the primary field is a DateTime."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.cot = cls.create_custom_object_type(name="DtObj440", slug="dt-obj-440")
        cls.create_custom_object_type_field(
            cls.cot, name="ts", label="Timestamp", type="datetime", primary=True, required=True
        )

        model = cls.cot.get_model()
        utc = datetime.timezone.utc
        cls.obj_morning = model.objects.create(ts=datetime.datetime(2025, 3, 10, 9, 0, 0, tzinfo=utc))
        cls.obj_evening = model.objects.create(ts=datetime.datetime(2025, 3, 10, 18, 30, 0, tzinfo=utc))

    def _search(self, value):
        model = self.cot.get_model()
        return get_filterset_class(model)({"q": value}, model.objects.all()).qs

    def test_search_by_datetime_finds_match(self):
        pks = list(self._search("2025-03-10 09:00:00").values_list("pk", flat=True))
        self.assertIn(self.obj_morning.pk, pks)
        self.assertNotIn(self.obj_evening.pk, pks)

    def test_search_invalid_datetime_returns_no_results(self):
        pks = list(self._search("not-a-datetime").values_list("pk", flat=True))
        self.assertNotIn(self.obj_morning.pk, pks)
        self.assertNotIn(self.obj_evening.pk, pks)


class MultiSelectPrimaryFieldSearchTestCase(CustomObjectsTestCase, TestCase):
    """Typeahead search for a multiselect (ArrayField) primary field uses array containment,
    not icontains, to avoid a FieldError on PostgreSQL array columns."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.choice_set = CustomFieldChoiceSet.objects.create(
            name="TagChoices440",
            extra_choices=[["red", "Red"], ["green", "Green"], ["blue", "Blue"]],
        )
        cls.cot = cls.create_custom_object_type(name="TagObj440", slug="tag-obj-440")
        cls.create_custom_object_type_field(
            cls.cot,
            name="colors",
            label="Colors",
            type="multiselect",
            primary=True,
            required=False,
            choice_set=cls.choice_set,
        )

        model = cls.cot.get_model()
        cls.obj_red = model.objects.create(colors=["red"])
        cls.obj_multi = model.objects.create(colors=["red", "blue"])
        cls.obj_green = model.objects.create(colors=["green"])

    def _search(self, value):
        model = self.cot.get_model()
        return get_filterset_class(model)({"q": value}, model.objects.all()).qs

    def test_search_finds_exact_element(self):
        pks = list(self._search("red").values_list("pk", flat=True))
        self.assertIn(self.obj_red.pk, pks)
        self.assertIn(self.obj_multi.pk, pks)
        self.assertNotIn(self.obj_green.pk, pks)

    def test_search_no_match_returns_empty(self):
        pks = list(self._search("yellow").values_list("pk", flat=True))
        self.assertNotIn(self.obj_red.pk, pks)
        self.assertNotIn(self.obj_multi.pk, pks)
        self.assertNotIn(self.obj_green.pk, pks)

    def test_search_does_not_raise_on_array_field(self):
        # Regression: must not raise FieldError/DatabaseError from icontains on ArrayField.
        try:
            list(self._search("blue").values_list("pk", flat=True))
        except Exception as exc:
            self.fail(f"search raised unexpectedly: {exc}")

    def test_search_finds_element_in_multi_value(self):
        # obj_multi has both "red" and "blue"; searching "blue" should find it.
        pks = list(self._search("blue").values_list("pk", flat=True))
        self.assertIn(self.obj_multi.pk, pks)
        self.assertNotIn(self.obj_red.pk, pks)


# ---------------------------------------------------------------------------
# Scalar field type filterset tests (build_filter_for_field / FIELD_TYPE_FILTERS)
# ---------------------------------------------------------------------------


class ScalarFieldFiltersetTestCase(CustomObjectsTestCase):
    """
    Base for filterset tests covering scalar custom field types.

    Subclasses must:
      - set ``match_params`` (class attr): filter params that should return obj_match
      - set ``obj_match`` / ``obj_no_match`` in ``setUpTestData``
      - set ``total_count`` (class attr, default 2) when the fixture has more rows
    """
    match_params: dict = {}
    total_count: int = 2

    def _filterset(self, params):
        model = self.cot.get_model()
        return get_filterset_class(model)(params, model.objects.all())

    def test_filter_returns_match_not_other(self):
        pks = list(self._filterset(self.match_params).qs.values_list('pk', flat=True))
        self.assertIn(self.obj_match.pk, pks)
        self.assertNotIn(self.obj_no_match.pk, pks)

    def test_no_filter_returns_all(self):
        self.assertEqual(self._filterset({}).qs.count(), self.total_count)


class TextFieldFiltersetTestCase(ScalarFieldFiltersetTestCase, TestCase):
    """CharFilter with icontains is generated for TYPE_TEXT fields."""

    match_params = {'note': 'foo'}

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.cot = cls.create_custom_object_type(name='TextFS', slug='text-fs')
        cls.create_custom_object_type_field(
            cls.cot, name='name', label='Name', type='text', primary=True, required=True
        )
        cls.create_custom_object_type_field(cls.cot, name='note', label='Note', type='text')

        model = cls.cot.get_model()
        cls.obj_match = model.objects.create(name='alpha', note='foo bar')
        cls.obj_no_match = model.objects.create(name='beta', note='baz qux')

    def test_icontains_case_insensitive(self):
        pks = list(self._filterset({'note': 'FOO'}).qs.values_list('pk', flat=True))
        self.assertIn(self.obj_match.pk, pks)
        self.assertNotIn(self.obj_no_match.pk, pks)


class LongTextFieldFiltersetTestCase(ScalarFieldFiltersetTestCase, TestCase):
    """CharFilter with icontains is generated for TYPE_LONGTEXT fields."""

    match_params = {'body': 'match'}

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.cot = cls.create_custom_object_type(name='LongTextFS', slug='longtext-fs')
        cls.create_custom_object_type_field(
            cls.cot, name='name', label='Name', type='text', primary=True, required=True
        )
        cls.create_custom_object_type_field(cls.cot, name='body', label='Body', type='longtext')

        model = cls.cot.get_model()
        cls.obj_match = model.objects.create(name='alpha', body='match content')
        cls.obj_no_match = model.objects.create(name='beta', body='other content')

    def test_icontains_case_insensitive(self):
        pks = list(self._filterset({'body': 'MATCH'}).qs.values_list('pk', flat=True))
        self.assertIn(self.obj_match.pk, pks)
        self.assertNotIn(self.obj_no_match.pk, pks)


class IntegerFieldFiltersetTestCase(ScalarFieldFiltersetTestCase, TestCase):
    """NumberFilter with exact lookup is generated for TYPE_INTEGER fields."""

    match_params = {'count': 10}
    total_count = 3

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.cot = cls.create_custom_object_type(name='IntFS', slug='int-fs')
        cls.create_custom_object_type_field(
            cls.cot, name='name', label='Name', type='text', primary=True, required=True
        )
        cls.create_custom_object_type_field(cls.cot, name='count', label='Count', type='integer')

        model = cls.cot.get_model()
        cls.obj_match = model.objects.create(name='ten', count=10)
        cls.obj_no_match = model.objects.create(name='twenty', count=20)
        cls.obj_null = model.objects.create(name='none')

    def test_null_excluded_by_exact_filter(self):
        pks = list(self._filterset(self.match_params).qs.values_list('pk', flat=True))
        self.assertNotIn(self.obj_null.pk, pks)


class DecimalFieldFiltersetTestCase(ScalarFieldFiltersetTestCase, TestCase):
    """NumberFilter with exact lookup is generated for TYPE_DECIMAL fields."""

    match_params = {'price': '1.5'}
    total_count = 3

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.cot = cls.create_custom_object_type(name='DecFS', slug='dec-fs')
        cls.create_custom_object_type_field(
            cls.cot, name='name', label='Name', type='text', primary=True, required=True
        )
        cls.create_custom_object_type_field(cls.cot, name='price', label='Price', type='decimal')

        model = cls.cot.get_model()
        cls.obj_match = model.objects.create(name='cheap', price=Decimal('1.5'))
        cls.obj_no_match = model.objects.create(name='expensive', price=Decimal('9.9'))
        cls.obj_null = model.objects.create(name='free')

    def test_null_excluded_by_exact_filter(self):
        pks = list(self._filterset(self.match_params).qs.values_list('pk', flat=True))
        self.assertNotIn(self.obj_null.pk, pks)


class BooleanFieldFiltersetTestCase(ScalarFieldFiltersetTestCase, TestCase):
    """BooleanFilter is generated for TYPE_BOOLEAN fields."""

    match_params = {'active': True}
    total_count = 3

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.cot = cls.create_custom_object_type(name='BoolFS', slug='bool-fs')
        cls.create_custom_object_type_field(
            cls.cot, name='name', label='Name', type='text', primary=True, required=True
        )
        cls.create_custom_object_type_field(
            cls.cot, name='active', label='Active', type='boolean'
        )

        model = cls.cot.get_model()
        cls.obj_true = model.objects.create(name='on', active=True)
        cls.obj_false = model.objects.create(name='off', active=False)
        cls.obj_null = model.objects.create(name='unknown')
        cls.obj_match = cls.obj_true
        cls.obj_no_match = cls.obj_false

    def test_filter_false(self):
        pks = list(self._filterset({'active': False}).qs.values_list('pk', flat=True))
        self.assertIn(self.obj_false.pk, pks)
        self.assertNotIn(self.obj_true.pk, pks)


class DateFieldFiltersetTestCase(ScalarFieldFiltersetTestCase, TestCase):
    """DateFilter with exact lookup is generated for TYPE_DATE fields."""

    match_params = {'start': '2025-01-15'}

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.cot = cls.create_custom_object_type(name='DateFS', slug='date-fs')
        cls.create_custom_object_type_field(
            cls.cot, name='name', label='Name', type='text', primary=True, required=True
        )
        cls.create_custom_object_type_field(cls.cot, name='start', label='Start', type='date')

        model = cls.cot.get_model()
        cls.obj_match = model.objects.create(name='jan', start=datetime.date(2025, 1, 15))
        cls.obj_no_match = model.objects.create(name='feb', start=datetime.date(2025, 2, 20))


class DateTimeFieldFiltersetTestCase(ScalarFieldFiltersetTestCase, TestCase):
    """DateTimeFilter with exact lookup is generated for TYPE_DATETIME fields."""

    match_params = {'ts': '2025-06-01 09:00:00'}

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.cot = cls.create_custom_object_type(name='DtFS', slug='dt-fs')
        cls.create_custom_object_type_field(
            cls.cot, name='name', label='Name', type='text', primary=True, required=True
        )
        cls.create_custom_object_type_field(cls.cot, name='ts', label='Timestamp', type='datetime')

        model = cls.cot.get_model()
        cls.obj_match = model.objects.create(
            name='am', ts=timezone.make_aware(datetime.datetime(2025, 6, 1, 9, 0, 0))
        )
        cls.obj_no_match = model.objects.create(
            name='pm', ts=timezone.make_aware(datetime.datetime(2025, 6, 1, 18, 0, 0))
        )


class URLFieldFiltersetTestCase(ScalarFieldFiltersetTestCase, TestCase):
    """CharFilter with icontains is generated for TYPE_URL fields."""

    match_params = {'link': 'github'}

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.cot = cls.create_custom_object_type(name='URLFS', slug='url-fs')
        cls.create_custom_object_type_field(
            cls.cot, name='name', label='Name', type='text', primary=True, required=True
        )
        cls.create_custom_object_type_field(cls.cot, name='link', label='Link', type='url')

        model = cls.cot.get_model()
        cls.obj_match = model.objects.create(name='gh', link='https://github.com/example')
        cls.obj_no_match = model.objects.create(name='gl', link='https://gitlab.com/example')


class JSONFieldFiltersetTestCase(ScalarFieldFiltersetTestCase, TestCase):
    """CharFilter with icontains is generated for TYPE_JSON fields."""

    match_params = {'meta': 'prod'}

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.cot = cls.create_custom_object_type(name='JSONFS', slug='json-fs')
        cls.create_custom_object_type_field(
            cls.cot, name='name', label='Name', type='text', primary=True, required=True
        )
        cls.create_custom_object_type_field(cls.cot, name='meta', label='Meta', type='json')

        model = cls.cot.get_model()
        cls.obj_match = model.objects.create(name='a', meta={'env': 'prod'})
        cls.obj_no_match = model.objects.create(name='b', meta={'env': 'staging'})


class SelectFieldFiltersetTestCase(ScalarFieldFiltersetTestCase, TestCase):
    """MultipleChoiceFilter with OR semantics is generated for TYPE_SELECT fields."""

    match_params = {'status': ['active']}
    total_count = 3

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.choice_set = CustomFieldChoiceSet.objects.create(
            name='StatusChoicesFS',
            extra_choices=[['active', 'Active'], ['inactive', 'Inactive'], ['retired', 'Retired']],
        )
        cls.cot = cls.create_custom_object_type(name='SelectFS', slug='select-fs')
        cls.create_custom_object_type_field(
            cls.cot, name='name', label='Name', type='text', primary=True, required=True
        )
        cls.create_custom_object_type_field(
            cls.cot, name='status', label='Status', type='select', choice_set=cls.choice_set
        )

        model = cls.cot.get_model()
        cls.obj_active = model.objects.create(name='a', status='active')
        cls.obj_inactive = model.objects.create(name='b', status='inactive')
        cls.obj_no_status = model.objects.create(name='c')
        cls.obj_match = cls.obj_active
        cls.obj_no_match = cls.obj_inactive

    def test_different_choice(self):
        pks = list(self._filterset({'status': ['inactive']}).qs.values_list('pk', flat=True))
        self.assertIn(self.obj_inactive.pk, pks)
        self.assertNotIn(self.obj_active.pk, pks)

    def test_null_excluded(self):
        pks = list(self._filterset(self.match_params).qs.values_list('pk', flat=True))
        self.assertNotIn(self.obj_no_status.pk, pks)

    def test_multi_value_or_semantics(self):
        pks = list(self._filterset({'status': ['active', 'inactive']}).qs.values_list('pk', flat=True))
        self.assertIn(self.obj_active.pk, pks)
        self.assertIn(self.obj_inactive.pk, pks)
        self.assertNotIn(self.obj_no_status.pk, pks)


class MultiSelectFieldFiltersetTestCase(ScalarFieldFiltersetTestCase, TestCase):
    """ArrayContainsFilter is generated for TYPE_MULTISELECT: containment semantics, not exact match."""

    match_params = {'colors': ['red']}
    total_count = 4

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.choice_set = CustomFieldChoiceSet.objects.create(
            name='ColorChoicesFS',
            extra_choices=[['red', 'Red'], ['green', 'Green'], ['blue', 'Blue']],
        )
        cls.cot = cls.create_custom_object_type(name='MultiSelFS', slug='multisel-fs')
        cls.create_custom_object_type_field(
            cls.cot, name='name', label='Name', type='text', primary=True, required=True
        )
        cls.create_custom_object_type_field(
            cls.cot, name='colors', label='Colors', type='multiselect', choice_set=cls.choice_set
        )

        model = cls.cot.get_model()
        cls.obj_red = model.objects.create(name='r', colors=['red'])
        cls.obj_multi = model.objects.create(name='rm', colors=['red', 'blue'])
        cls.obj_green = model.objects.create(name='g', colors=['green'])
        cls.obj_none = model.objects.create(name='n')
        cls.obj_match = cls.obj_red
        cls.obj_no_match = cls.obj_green

    def test_filter_returns_match_not_other(self):
        # Extends the base test: obj_multi (["red","blue"]) must also match on "red"
        pks = list(self._filterset(self.match_params).qs.values_list('pk', flat=True))
        self.assertIn(self.obj_red.pk, pks)
        self.assertIn(self.obj_multi.pk, pks)
        self.assertNotIn(self.obj_green.pk, pks)
        self.assertNotIn(self.obj_none.pk, pks)

    def test_filter_partial_array_match(self):
        # obj_multi has ["red", "blue"]; filtering by "blue" should still match it
        pks = list(self._filterset({'colors': ['blue']}).qs.values_list('pk', flat=True))
        self.assertIn(self.obj_multi.pk, pks)
        self.assertNotIn(self.obj_red.pk, pks)
        self.assertNotIn(self.obj_green.pk, pks)

    def test_filter_multiple_values_returns_union(self):
        pks = list(self._filterset({'colors': ['red', 'green']}).qs.values_list('pk', flat=True))
        self.assertIn(self.obj_red.pk, pks)
        self.assertIn(self.obj_multi.pk, pks)
        self.assertIn(self.obj_green.pk, pks)
        self.assertNotIn(self.obj_none.pk, pks)

    def test_no_duplicates_when_object_matches_multiple_filter_values(self):
        # obj_multi has both "red" and "blue"; must appear only once in results
        qs = self._filterset({'colors': ['red', 'blue']}).qs
        self.assertEqual(qs.filter(pk=self.obj_multi.pk).count(), 1)

    def test_uses_array_contains_filter_class(self):
        model = self.cot.get_model()
        fs_class = get_filterset_class(model)
        self.assertIsInstance(fs_class.base_filters.get('colors'), ArrayContainsFilter)


# ---------------------------------------------------------------------------
# Polymorphic Object field — filter form fields and filterset queryset
# ---------------------------------------------------------------------------


class PolymorphicObjectFilterFormFieldTestCase(CustomObjectsTestCase, TestCase):
    """get_filterform_field() for a polymorphic object field returns a dict of
    DynamicModelChoiceField instances keyed by {field}_{app}_{model}."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.cot = cls.create_custom_object_type(name="PolyObjFFTest", slug="poly-obj-ff-test")
        cls.create_custom_object_type_field(
            cls.cot, name="name", label="Name", type="text", primary=True, required=True
        )
        cls.field = cls.create_polymorphic_field(
            cls.cot,
            related_object_types=[cls.get_device_object_type(), cls.get_site_object_type()],
            name="target",
            label="Target",
            type="object",
        )

    def test_returns_dict(self):
        result = ObjectFieldType().get_filterform_field(self.field)
        self.assertIsInstance(result, dict)

    def test_dict_has_one_key_per_allowed_type(self):
        result = ObjectFieldType().get_filterform_field(self.field)
        self.assertIn("target_dcim_device", result)
        self.assertIn("target_dcim_site", result)

    def test_each_form_field_is_dynamic_model_choice(self):
        result = ObjectFieldType().get_filterform_field(self.field)
        for form_field in result.values():
            self.assertIsInstance(form_field, DynamicModelChoiceField)

    def test_each_form_field_not_required(self):
        result = ObjectFieldType().get_filterform_field(self.field)
        for form_field in result.values():
            self.assertFalse(form_field.required)

    def test_filterset_has_per_type_filters(self):
        model = self.cot.get_model()
        fs_class = get_filterset_class(model)
        self.assertIsInstance(fs_class.base_filters.get("target_dcim_device"), PolymorphicObjectFilter)
        self.assertIsInstance(fs_class.base_filters.get("target_dcim_site"), PolymorphicObjectFilter)
        self.assertNotIn("target", fs_class.base_filters)


class PolymorphicObjectFiltersetTestCase(CustomObjectsTestCase, TestCase):
    """?{field}_{app}_{model}=<pk> filters correctly for polymorphic GFK object fields."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.cot = cls.create_custom_object_type(name="PolyObjFSTest", slug="poly-obj-fs-test")
        cls.create_custom_object_type_field(
            cls.cot, name="name", label="Name", type="text", primary=True, required=True
        )
        cls.field = cls.create_polymorphic_field(
            cls.cot,
            related_object_types=[cls.get_device_object_type(), cls.get_site_object_type()],
            name="target",
            label="Target",
            type="object",
        )

        site, dt, role = _make_device_fixtures("polyobj")
        cls.device = Device.objects.create(
            name="Device PolyObj", site=site, device_type=dt, role=role
        )
        cls.site = site

        model = cls.cot.get_model()
        cls.obj_device = model.objects.create(name="Obj Device", target=cls.device)
        cls.obj_site = model.objects.create(name="Obj Site", target=cls.site)
        cls.obj_none = model.objects.create(name="Obj None")

    def _filterset(self, params):
        model = self.cot.get_model()
        return get_filterset_class(model)(params, model.objects.all())

    def test_filter_by_device(self):
        pks = list(self._filterset({"target_dcim_device": self.device.pk}).qs.values_list("pk", flat=True))
        self.assertIn(self.obj_device.pk, pks)
        self.assertNotIn(self.obj_site.pk, pks)
        self.assertNotIn(self.obj_none.pk, pks)

    def test_filter_by_site(self):
        pks = list(self._filterset({"target_dcim_site": self.site.pk}).qs.values_list("pk", flat=True))
        self.assertIn(self.obj_site.pk, pks)
        self.assertNotIn(self.obj_device.pk, pks)
        self.assertNotIn(self.obj_none.pk, pks)

    def test_no_filter_returns_all(self):
        self.assertEqual(self._filterset({}).qs.count(), 3)

    def test_wrong_type_filter_returns_nothing(self):
        # Querying target_dcim_site with a device PK should not return the
        # device-linked object — the content_type_id for that row is Device,
        # not Site, so it won't match the site filter regardless of object_id.
        pks = list(self._filterset({"target_dcim_site": self.device.pk}).qs.values_list("pk", flat=True))
        self.assertNotIn(self.obj_device.pk, pks)


# ---------------------------------------------------------------------------
# Polymorphic MultiObject field — filterset queryset
# ---------------------------------------------------------------------------


class PolymorphicMultiObjectFiltersetTestCase(CustomObjectsTestCase, TestCase):
    """?{field}_{app}_{model}=<pk> filters correctly for polymorphic GFK multiobject fields."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.cot = cls.create_custom_object_type(name="PolyMoFSTest", slug="poly-mo-fs-test")
        cls.create_custom_object_type_field(
            cls.cot, name="name", label="Name", type="text", primary=True, required=True
        )
        cls.field = cls.create_polymorphic_field(
            cls.cot,
            related_object_types=[cls.get_device_object_type(), cls.get_site_object_type()],
            name="targets",
            label="Targets",
            type="multiobject",
        )

        site, dt, role = _make_device_fixtures("polymo")
        cls.device1 = Device.objects.create(
            name="Device PolyMo 1", site=site, device_type=dt, role=role
        )
        cls.device2 = Device.objects.create(
            name="Device PolyMo 2", site=site, device_type=dt, role=role
        )
        cls.site = site

        model = cls.cot.get_model()
        cls.obj_d1 = model.objects.create(name="Obj D1")
        cls.obj_d1.targets.add(cls.device1)
        cls.obj_d2 = model.objects.create(name="Obj D2")
        cls.obj_d2.targets.add(cls.device2)
        cls.obj_site = model.objects.create(name="Obj Site")
        cls.obj_site.targets.add(cls.site)
        cls.obj_both = model.objects.create(name="Obj Both")
        cls.obj_both.targets.add(cls.device1, cls.site)
        cls.obj_none = model.objects.create(name="Obj None")

    def _filterset(self, params):
        model = self.cot.get_model()
        return get_filterset_class(model)(params, model.objects.all())

    def test_filter_by_device_returns_linked_objects(self):
        pks = list(self._filterset({"targets_dcim_device": [self.device1.pk]}).qs.values_list("pk", flat=True))
        self.assertIn(self.obj_d1.pk, pks)
        self.assertIn(self.obj_both.pk, pks)
        self.assertNotIn(self.obj_d2.pk, pks)
        self.assertNotIn(self.obj_site.pk, pks)
        self.assertNotIn(self.obj_none.pk, pks)

    def test_filter_by_site(self):
        pks = list(self._filterset({"targets_dcim_site": [self.site.pk]}).qs.values_list("pk", flat=True))
        self.assertIn(self.obj_site.pk, pks)
        self.assertIn(self.obj_both.pk, pks)
        self.assertNotIn(self.obj_d1.pk, pks)
        self.assertNotIn(self.obj_none.pk, pks)

    def test_filter_multiple_devices_returns_union(self):
        pks = list(
            self._filterset({"targets_dcim_device": [self.device1.pk, self.device2.pk]}).qs.values_list("pk", flat=True)
        )
        self.assertIn(self.obj_d1.pk, pks)
        self.assertIn(self.obj_d2.pk, pks)
        self.assertIn(self.obj_both.pk, pks)
        self.assertNotIn(self.obj_none.pk, pks)

    def test_no_duplicates_for_object_matching_multiple_values(self):
        qs = self._filterset({"targets_dcim_device": [self.device1.pk, self.device2.pk]}).qs
        self.assertEqual(qs.filter(pk=self.obj_both.pk).count(), 1)

    def test_no_filter_returns_all(self):
        self.assertEqual(self._filterset({}).qs.count(), 5)

    def test_filterset_has_per_type_filters(self):
        model = self.cot.get_model()
        fs_class = get_filterset_class(model)
        self.assertIsInstance(fs_class.base_filters.get("targets_dcim_device"), PolymorphicMultiObjectFilter)
        self.assertIsInstance(fs_class.base_filters.get("targets_dcim_site"), PolymorphicMultiObjectFilter)
        self.assertNotIn("targets", fs_class.base_filters)

    def test_form_fields_are_multi_choice(self):
        result = MultiObjectFieldType().get_filterform_field(self.field)
        self.assertIsInstance(result, dict)
        for form_field in result.values():
            self.assertIsInstance(form_field, DynamicModelMultipleChoiceField)
