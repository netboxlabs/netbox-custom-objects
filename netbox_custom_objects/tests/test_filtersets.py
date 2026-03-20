from django.test import TestCase

from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site

from netbox_custom_objects.field_types import MultiObjectFieldType, ObjectFieldType
from netbox_custom_objects.filtersets import get_filterset_class
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
