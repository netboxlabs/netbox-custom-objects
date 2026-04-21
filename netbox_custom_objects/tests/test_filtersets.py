"""
Tests for filtersets used by the plugin's UI and API views.
"""
import datetime
from decimal import Decimal

from django import forms
from django.test import TestCase

from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site
from extras.models import CustomFieldChoiceSet

from netbox_custom_objects.field_types import (
    BooleanFieldType,
    MultiObjectFieldType,
    MultiSelectFieldType,
    ObjectFieldType,
    SelectFieldType,
)
from netbox_custom_objects.filtersets import get_filterset_class
from netbox_custom_objects.models import CustomObjectTypeField
from utilities.forms.fields import (
    DynamicModelChoiceField,
    DynamicModelMultipleChoiceField,
    DynamicMultipleChoiceField,
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
        cls.obj_morning = model.objects.create(ts=datetime.datetime(2025, 3, 10, 9, 0, 0))
        cls.obj_evening = model.objects.create(ts=datetime.datetime(2025, 3, 10, 18, 30, 0))

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
# BooleanFieldType.get_filterform_field — form field shape (issue #366)
# ---------------------------------------------------------------------------


class BooleanFieldFilterFormFieldTestCase(CustomObjectsTestCase, TestCase):
    """get_filterform_field() for a boolean field returns a NullBooleanField with yes/no choices."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.cot = cls.create_custom_object_type(name="BoolFFTest", slug="bool-ff-test")
        cls.create_custom_object_type_field(
            cls.cot, name="name", label="Name", type="text", primary=True, required=True
        )
        cls.field = cls.create_custom_object_type_field(
            cls.cot, name="active", label="Active", type="boolean"
        )

    def test_returns_null_boolean_field(self):
        form_field = BooleanFieldType().get_filterform_field(self.field)
        self.assertIsInstance(form_field, forms.NullBooleanField)

    def test_form_field_not_required(self):
        form_field = BooleanFieldType().get_filterform_field(self.field)
        self.assertFalse(form_field.required)

    def test_form_field_widget_is_select(self):
        form_field = BooleanFieldType().get_filterform_field(self.field)
        self.assertIsInstance(form_field.widget, forms.Select)

    def test_form_field_choices_include_yes_no(self):
        form_field = BooleanFieldType().get_filterform_field(self.field)
        choice_values = [c[0] for c in form_field.widget.choices]
        self.assertIn('true', choice_values)
        self.assertIn('false', choice_values)


# ---------------------------------------------------------------------------
# BooleanFieldType — filterset queryset filtering (issue #366)
# ---------------------------------------------------------------------------


class BooleanFieldFiltersetTestCase(CustomObjectsTestCase, TestCase):
    """Filtering by ?<field>=true/false correctly narrows results for boolean fields."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.cot = cls.create_custom_object_type(name="BoolFSTest", slug="bool-fs-test")
        cls.create_custom_object_type_field(
            cls.cot, name="name", label="Name", type="text", primary=True, required=True
        )
        cls.create_custom_object_type_field(
            cls.cot, name="active", label="Active", type="boolean"
        )
        model = cls.cot.get_model()
        cls.obj_true = model.objects.create(name="Obj True", active=True)
        cls.obj_false = model.objects.create(name="Obj False", active=False)
        cls.obj_null = model.objects.create(name="Obj Null", active=None)

    def _filterset(self, params):
        model = self.cot.get_model()
        return get_filterset_class(model)(params, model.objects.all())

    def test_filter_true_returns_only_true_objects(self):
        pks = list(self._filterset({"active": "true"}).qs.values_list("pk", flat=True))
        self.assertIn(self.obj_true.pk, pks)
        self.assertNotIn(self.obj_false.pk, pks)
        self.assertNotIn(self.obj_null.pk, pks)

    def test_filter_false_returns_only_false_objects(self):
        pks = list(self._filterset({"active": "false"}).qs.values_list("pk", flat=True))
        self.assertIn(self.obj_false.pk, pks)
        self.assertNotIn(self.obj_true.pk, pks)
        self.assertNotIn(self.obj_null.pk, pks)

    def test_no_filter_returns_all(self):
        self.assertEqual(self._filterset({}).qs.count(), 3)


# ---------------------------------------------------------------------------
# SelectFieldType.get_filterform_field — form field shape (issue #366)
# ---------------------------------------------------------------------------


class SelectFieldFilterFormFieldTestCase(CustomObjectsTestCase, TestCase):
    """get_filterform_field() for a select field returns a DynamicMultipleChoiceField."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.choice_set = cls.create_choice_set(name="SelectFFChoices366")
        cls.cot = cls.create_custom_object_type(name="SelFFTest", slug="sel-ff-test")
        cls.create_custom_object_type_field(
            cls.cot, name="name", label="Name", type="text", primary=True, required=True
        )
        cls.field = cls.create_custom_object_type_field(
            cls.cot,
            name="status",
            label="Status",
            type="select",
            choice_set=cls.choice_set,
        )

    def test_returns_dynamic_multiple_choice_field(self):
        form_field = SelectFieldType().get_filterform_field(self.field)
        self.assertIsInstance(form_field, DynamicMultipleChoiceField)

    def test_form_field_not_required(self):
        form_field = SelectFieldType().get_filterform_field(self.field)
        self.assertFalse(form_field.required)


# ---------------------------------------------------------------------------
# SelectFieldType — filterset queryset filtering (issue #366)
# ---------------------------------------------------------------------------


class SelectFieldFiltersetTestCase(CustomObjectsTestCase, TestCase):
    """Filtering by ?<field>=value uses OR semantics for select fields (MultipleChoiceFilter)."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.choice_set = cls.create_choice_set(name="SelectFSChoices366")
        cls.cot = cls.create_custom_object_type(name="SelFSTest", slug="sel-fs-test")
        cls.create_custom_object_type_field(
            cls.cot, name="name", label="Name", type="text", primary=True, required=True
        )
        cls.create_custom_object_type_field(
            cls.cot,
            name="status",
            label="Status",
            type="select",
            choice_set=cls.choice_set,
        )
        model = cls.cot.get_model()
        cls.obj_c1 = model.objects.create(name="Obj C1", status="choice1")
        cls.obj_c2 = model.objects.create(name="Obj C2", status="choice2")
        cls.obj_c3 = model.objects.create(name="Obj C3", status="choice3")

    def _filterset(self, params):
        model = self.cot.get_model()
        return get_filterset_class(model)(params, model.objects.all())

    def test_filter_single_value_returns_matching_object(self):
        pks = list(self._filterset({"status": ["choice1"]}).qs.values_list("pk", flat=True))
        self.assertIn(self.obj_c1.pk, pks)
        self.assertNotIn(self.obj_c2.pk, pks)
        self.assertNotIn(self.obj_c3.pk, pks)

    def test_filter_multiple_values_returns_union(self):
        pks = list(self._filterset({"status": ["choice1", "choice2"]}).qs.values_list("pk", flat=True))
        self.assertIn(self.obj_c1.pk, pks)
        self.assertIn(self.obj_c2.pk, pks)
        self.assertNotIn(self.obj_c3.pk, pks)

    def test_no_filter_returns_all(self):
        self.assertEqual(self._filterset({}).qs.count(), 3)


# ---------------------------------------------------------------------------
# MultiSelectFieldType.get_filterform_field — form field shape (issue #366)
# ---------------------------------------------------------------------------


class MultiSelectFieldFilterFormFieldTestCase(CustomObjectsTestCase, TestCase):
    """get_filterform_field() for a multiselect field returns a DynamicMultipleChoiceField."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.choice_set = cls.create_choice_set(name="MSelFFChoices366")
        cls.cot = cls.create_custom_object_type(name="MSelFFTest", slug="msel-ff-test")
        cls.create_custom_object_type_field(
            cls.cot, name="name", label="Name", type="text", primary=True, required=True
        )
        cls.field = cls.create_custom_object_type_field(
            cls.cot,
            name="tags",
            label="Tags",
            type="multiselect",
            choice_set=cls.choice_set,
        )

    def test_returns_dynamic_multiple_choice_field(self):
        form_field = MultiSelectFieldType().get_filterform_field(self.field)
        self.assertIsInstance(form_field, DynamicMultipleChoiceField)

    def test_form_field_not_required(self):
        form_field = MultiSelectFieldType().get_filterform_field(self.field)
        self.assertFalse(form_field.required)


# ---------------------------------------------------------------------------
# MultiSelectFieldType — filterset queryset filtering (issue #366)
# ---------------------------------------------------------------------------


class MultiSelectFieldFiltersetTestCase(CustomObjectsTestCase, TestCase):
    """Filtering by ?<field>=value uses array containment with OR semantics for multiselect fields."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.choice_set = cls.create_choice_set(name="MSelFSChoices366")
        cls.cot = cls.create_custom_object_type(name="MSelFSTest", slug="msel-fs-test")
        cls.create_custom_object_type_field(
            cls.cot, name="name", label="Name", type="text", primary=True, required=True
        )
        cls.create_custom_object_type_field(
            cls.cot,
            name="tags",
            label="Tags",
            type="multiselect",
            choice_set=cls.choice_set,
        )
        model = cls.cot.get_model()
        cls.obj_c1 = model.objects.create(name="Obj MC1", tags=["choice1"])
        cls.obj_c2 = model.objects.create(name="Obj MC2", tags=["choice2"])
        cls.obj_multi = model.objects.create(name="Obj Multi", tags=["choice1", "choice3"])
        cls.obj_empty = model.objects.create(name="Obj Empty", tags=[])

    def _filterset(self, params):
        model = self.cot.get_model()
        return get_filterset_class(model)(params, model.objects.all())

    def test_filter_single_value_returns_objects_containing_value(self):
        pks = list(self._filterset({"tags": ["choice1"]}).qs.values_list("pk", flat=True))
        self.assertIn(self.obj_c1.pk, pks)
        self.assertIn(self.obj_multi.pk, pks)
        self.assertNotIn(self.obj_c2.pk, pks)
        self.assertNotIn(self.obj_empty.pk, pks)

    def test_filter_multiple_values_returns_union(self):
        # Objects whose tags array contains choice1 OR choice2
        pks = list(self._filterset({"tags": ["choice1", "choice2"]}).qs.values_list("pk", flat=True))
        self.assertIn(self.obj_c1.pk, pks)
        self.assertIn(self.obj_c2.pk, pks)
        self.assertIn(self.obj_multi.pk, pks)
        self.assertNotIn(self.obj_empty.pk, pks)

    def test_filter_multiple_values_no_duplicates(self):
        # obj_multi contains both choice1 and choice3; it should appear exactly once
        qs = self._filterset({"tags": ["choice1", "choice3"]}).qs
        self.assertEqual(qs.filter(pk=self.obj_multi.pk).count(), 1)

    def test_no_filter_returns_all(self):
        self.assertEqual(self._filterset({}).qs.count(), 4)
