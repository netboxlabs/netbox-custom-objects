import datetime
from decimal import Decimal
from itertools import chain

import django_filters
from django.contrib.contenttypes.fields import GenericForeignKey, GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.db.models import ForeignKey, ManyToManyField, ManyToManyRel, ManyToOneRel, OneToOneRel
from django.test import TestCase

try:
    from taggit.managers import TaggableManager
except ImportError:
    TaggableManager = None

from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site

from netbox_custom_objects.field_types import MultiObjectFieldType, ObjectFieldType
from netbox_custom_objects.filtersets import CustomObjectTypeFilterSet, get_filterset_class
from netbox_custom_objects.models import CustomObjectType, CustomObjectTypeField
from utilities.forms.fields import DynamicModelChoiceField, DynamicModelMultipleChoiceField

from .base import CustomObjectsTestCase


EXEMPT_MODEL_FIELDS = (
    'comments',
    'custom_field_data',
    'level',
    'lft',
    'rght',
    'tree_id',
)


def _make_device_fixtures(suffix):
    """Create minimal DCIM fixtures needed to instantiate a Device."""
    site = Site.objects.create(name=f'Site {suffix}', slug=f'site-{suffix}')
    mfr = Manufacturer.objects.create(name=f'Mfr {suffix}', slug=f'mfr-{suffix}')
    dt = DeviceType.objects.create(manufacturer=mfr, model=f'DT {suffix}', slug=f'dt-{suffix}')
    role = DeviceRole.objects.create(name=f'Role {suffix}', slug=f'role-{suffix}', color='ff0000')
    return site, dt, role


# ---------------------------------------------------------------------------
# BaseFilterSetTests mixin
# ---------------------------------------------------------------------------


class BaseFilterSetTests:
    """
    Mixin that asserts every model field has a corresponding filter defined on its FilterSet.
    Fields intentionally not filterable should be listed in ignore_fields.
    """

    ignore_fields = ()

    def _get_filters_for_field(self, field):
        if issubclass(field.__class__, ForeignKey) or type(field) is OneToOneRel:
            if field.related_model is ContentType:
                return [(None, None)]
            return [(f'{field.name}_id', django_filters.ModelMultipleChoiceFilter)]

        if type(field) in (ManyToManyField, ManyToManyRel):
            if field.related_model is ContentType:
                return [
                    ('object_type', None),
                    ('object_type_id', django_filters.ModelMultipleChoiceFilter),
                ]
            related_name = field.related_model._meta.verbose_name.lower().replace(' ', '_')
            return [(f'{related_name}_id', django_filters.ModelMultipleChoiceFilter)]

        if TaggableManager is not None and type(field) is TaggableManager:
            return [('tag', None)]

        return [(field.name, None)]

    def test_missing_filters(self):
        model = self.queryset.model
        defined_filters = self.filterset.get_filters()

        for model_field in model._meta.get_fields():
            if model_field.name.startswith('_'):
                continue
            if model_field.name in chain(self.ignore_fields, EXEMPT_MODEL_FIELDS):
                continue
            if type(model_field) is ManyToOneRel:
                continue
            if type(model_field) in (GenericForeignKey, GenericRelation):
                continue

            for filter_name, filter_class in self._get_filters_for_field(model_field):
                if filter_name is None:
                    continue
                self.assertIn(
                    filter_name,
                    defined_filters.keys(),
                    f'No filter defined for {filter_name} ({model_field.name})!',
                )
                if filter_class is not None:
                    self.assertIsInstance(
                        defined_filters[filter_name],
                        filter_class,
                        f'Invalid filter class for {filter_name} (expected {filter_class})!',
                    )


# ---------------------------------------------------------------------------
# CustomObjectTypeFilterSet (static)
# ---------------------------------------------------------------------------


class CustomObjectTypeFilterSetTestCase(CustomObjectsTestCase, TestCase, BaseFilterSetTests):
    filterset = CustomObjectTypeFilterSet
    # Fields intentionally not covered by CustomObjectTypeFilterSet
    ignore_fields = (
        'slug',
        'description',
        'verbose_name_plural',
    )

    @classmethod
    def setUpTestData(cls):
        CustomObjectType.objects.create(name='Type 1', slug='type-1')
        CustomObjectType.objects.create(name='Type 2', slug='type-2', group_name='Group A')
        CustomObjectType.objects.create(name='Type 3', slug='type-3', group_name='Group A')

    @property
    def queryset(self):
        return CustomObjectType.objects.all()

    def test_id(self):
        params = {'id': list(CustomObjectType.objects.values_list('pk', flat=True)[:2])}
        self.assertEqual(self.filterset(params, CustomObjectType.objects.all()).qs.count(), 2)

    def test_name(self):
        params = {'name': ['Type 1', 'Type 2']}
        self.assertEqual(self.filterset(params, CustomObjectType.objects.all()).qs.count(), 2)

    def test_group_name(self):
        params = {'group_name': ['Group A']}
        self.assertEqual(self.filterset(params, CustomObjectType.objects.all()).qs.count(), 2)

    def test_q(self):
        params = {'q': 'Type 1'}
        self.assertEqual(self.filterset(params, CustomObjectType.objects.all()).qs.count(), 1)


# ---------------------------------------------------------------------------
# Dynamic filterset — one field per supported type
# ---------------------------------------------------------------------------


class CustomObjectFilterSetTestCase(CustomObjectsTestCase, TestCase):
    """
    Tests for dynamically generated filtersets on custom object instances.
    Verifies that a filter for each supported field type is functional and
    returns the correct results. Range filters (__lte/__gte) on date and numeric
    fields are auto-generated by NetBoxModelFilterSet via get_additional_lookups().
    """

    @classmethod
    def setUpTestData(cls):
        # Devices used for object/multiobject field tests
        manufacturer = Manufacturer.objects.create(name='FS Manufacturer', slug='fs-manufacturer')
        device_type = DeviceType.objects.create(
            manufacturer=manufacturer, model='FS Device Type', slug='fs-device-type'
        )
        role = DeviceRole.objects.create(name='FS Role', slug='fs-role', color='ff0000')
        site = Site.objects.create(name='FS Site', slug='fs-site')
        cls.device1 = Device.objects.create(name='FS Device 1', device_type=device_type, role=role, site=site)
        cls.device2 = Device.objects.create(name='FS Device 2', device_type=device_type, role=role, site=site)

        choice_set = CustomObjectsTestCase.create_choice_set(name='FS Choice Set')
        device_object_type = CustomObjectsTestCase.get_device_object_type()

        cls.cot = CustomObjectsTestCase.create_custom_object_type(name='FilterSetObject', slug='filterset-objects')

        for field_def in [
            {'name': 'text_field', 'label': 'Text Field', 'type': 'text'},
            {'name': 'longtext_field', 'label': 'Long Text Field', 'type': 'longtext'},
            {'name': 'int_field', 'label': 'Integer Field', 'type': 'integer'},
            {'name': 'decimal_field', 'label': 'Decimal Field', 'type': 'decimal'},
            {'name': 'bool_field', 'label': 'Boolean Field', 'type': 'boolean'},
            {'name': 'date_field', 'label': 'Date Field', 'type': 'date'},
            {'name': 'url_field', 'label': 'URL Field', 'type': 'url'},
            {'name': 'json_field', 'label': 'JSON Field', 'type': 'json'},
        ]:
            CustomObjectsTestCase.create_custom_object_type_field(cls.cot, **field_def)

        CustomObjectsTestCase.create_custom_object_type_field(
            cls.cot, name='select_field', label='Select Field', type='select', choice_set=choice_set
        )
        CustomObjectsTestCase.create_custom_object_type_field(
            cls.cot, name='device_field', label='Device Field', type='object', related_object_type=device_object_type
        )
        CustomObjectsTestCase.create_custom_object_type_field(
            cls.cot,
            name='devices_field',
            label='Devices Field',
            type='multiobject',
            related_object_type=device_object_type,
        )

        cls.model = cls.cot.get_model()
        cls.filterset = get_filterset_class(cls.model)

        cls.obj1 = cls.model.objects.create(
            text_field='Alpha value',
            longtext_field='Alpha long text',
            int_field=10,
            decimal_field=Decimal('1.5000'),
            bool_field=True,
            date_field=datetime.date(2024, 1, 1),
            url_field='https://alpha.example.com',
            json_field={'tag': 'alpha'},
            select_field='choice1',
            device_field=cls.device1,
        )
        cls.obj2 = cls.model.objects.create(
            text_field='Beta value',
            longtext_field='Beta long text',
            int_field=20,
            decimal_field=Decimal('2.5000'),
            bool_field=False,
            date_field=datetime.date(2024, 6, 15),
            url_field='https://beta.example.com',
            json_field={'tag': 'beta'},
            select_field='choice2',
            device_field=cls.device2,
        )
        cls.obj3 = cls.model.objects.create(
            text_field='Gamma value',
            longtext_field='Gamma long text',
            int_field=30,
            decimal_field=Decimal('3.5000'),
            bool_field=True,
            date_field=datetime.date(2024, 12, 31),
            url_field='https://gamma.example.com',
            json_field={'tag': 'gamma'},
            select_field='choice1',
            device_field=cls.device1,
        )

        cls.obj1.devices_field.add(cls.device1)
        cls.obj2.devices_field.add(cls.device2)
        cls.obj3.devices_field.add(cls.device1, cls.device2)

    @property
    def queryset(self):
        return self.model.objects.all()

    # --- Text types (icontains) ---

    def test_text_field(self):
        params = {'text_field': 'alpha'}
        self.assertEqual(self.filterset(params, self.queryset).qs.count(), 1)

    def test_longtext_field(self):
        params = {'longtext_field': 'beta'}
        self.assertEqual(self.filterset(params, self.queryset).qs.count(), 1)

    def test_url_field(self):
        params = {'url_field': 'gamma'}
        self.assertEqual(self.filterset(params, self.queryset).qs.count(), 1)

    def test_json_field(self):
        params = {'json_field': 'alpha'}
        self.assertEqual(self.filterset(params, self.queryset).qs.count(), 1)

    # --- Numeric types (exact + range lookups) ---

    def test_integer_field(self):
        params = {'int_field': 20}
        self.assertEqual(self.filterset(params, self.queryset).qs.count(), 1)

    def test_integer_field_lte(self):
        params = {'int_field__lte': 20}
        self.assertEqual(self.filterset(params, self.queryset).qs.count(), 2)

    def test_integer_field_gte(self):
        params = {'int_field__gte': 20}
        self.assertEqual(self.filterset(params, self.queryset).qs.count(), 2)

    def test_decimal_field(self):
        params = {'decimal_field': '2.5'}
        self.assertEqual(self.filterset(params, self.queryset).qs.count(), 1)

    def test_decimal_field_lte(self):
        params = {'decimal_field__lte': '2.5'}
        self.assertEqual(self.filterset(params, self.queryset).qs.count(), 2)

    def test_decimal_field_gte(self):
        params = {'decimal_field__gte': '2.5'}
        self.assertEqual(self.filterset(params, self.queryset).qs.count(), 2)

    # --- Boolean ---

    def test_boolean_field_true(self):
        params = {'bool_field': True}
        self.assertEqual(self.filterset(params, self.queryset).qs.count(), 2)

    def test_boolean_field_false(self):
        params = {'bool_field': False}
        self.assertEqual(self.filterset(params, self.queryset).qs.count(), 1)

    # --- Date (exact + range lookups auto-generated by NetBoxModelFilterSet) ---

    def test_date_field(self):
        params = {'date_field': '2024-01-01'}
        self.assertEqual(self.filterset(params, self.queryset).qs.count(), 1)

    def test_date_field_lte(self):
        # obj1 (2024-01-01) and obj2 (2024-06-15) are on or before 2024-06-15
        params = {'date_field__lte': '2024-06-15'}
        self.assertEqual(self.filterset(params, self.queryset).qs.count(), 2)

    def test_date_field_gte(self):
        # obj2 (2024-06-15) and obj3 (2024-12-31) are on or after 2024-06-15
        params = {'date_field__gte': '2024-06-15'}
        self.assertEqual(self.filterset(params, self.queryset).qs.count(), 2)

    # --- Choice ---

    def test_select_field(self):
        # obj1 and obj3 have choice1
        params = {'select_field': 'choice1'}
        self.assertEqual(self.filterset(params, self.queryset).qs.count(), 2)

    # --- Object references ---

    def test_object_field(self):
        # obj1 and obj3 reference device1
        params = {'device_field': self.device1.pk}
        self.assertEqual(self.filterset(params, self.queryset).qs.count(), 2)

    def test_multiobject_field(self):
        # obj2 and obj3 reference device2
        params = {'devices_field': [self.device2.pk]}
        self.assertEqual(self.filterset(params, self.queryset).qs.count(), 2)


# ---------------------------------------------------------------------------
# ObjectFieldType.get_filterform_field — form field shape
# ---------------------------------------------------------------------------


class ObjectFieldFilterFormFieldTestCase(CustomObjectsTestCase, TestCase):
    """get_filterform_field() for an object field returns a DynamicModelChoiceField."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.cot = cls.create_custom_object_type(name='ObjFFTest', slug='obj-ff-test')
        cls.create_custom_object_type_field(
            cls.cot, name='name', label='Name', type='text', primary=True, required=True
        )
        cls.field = cls.create_custom_object_type_field(
            cls.cot,
            name='device',
            label='Device',
            type='object',
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
        cls.cot = cls.create_custom_object_type(name='ObjFSTest', slug='obj-fs-test')
        cls.create_custom_object_type_field(
            cls.cot, name='name', label='Name', type='text', primary=True, required=True
        )
        cls.create_custom_object_type_field(
            cls.cot,
            name='device',
            label='Device',
            type='object',
            related_object_type=cls.get_device_object_type(),
        )

        site, dt, role = _make_device_fixtures('ofst')
        cls.device1 = Device.objects.create(name='Device OFS 1', site=site, device_type=dt, role=role)
        cls.device2 = Device.objects.create(name='Device OFS 2', site=site, device_type=dt, role=role)

        model = cls.cot.get_model()
        cls.obj_d1 = model.objects.create(name='Obj D1', device=cls.device1)
        cls.obj_d2 = model.objects.create(name='Obj D2', device=cls.device2)
        cls.obj_none = model.objects.create(name='Obj None')

    def _filterset(self, params):
        model = self.cot.get_model()
        return get_filterset_class(model)(params, model.objects.all())

    def test_filter_returns_matching_object(self):
        pks = list(self._filterset({'device': self.device1.pk}).qs.values_list('pk', flat=True))
        self.assertIn(self.obj_d1.pk, pks)
        self.assertNotIn(self.obj_d2.pk, pks)
        self.assertNotIn(self.obj_none.pk, pks)

    def test_filter_different_value(self):
        pks = list(self._filterset({'device': self.device2.pk}).qs.values_list('pk', flat=True))
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
        cls.cot = cls.create_custom_object_type(name='MoFFTest', slug='mo-ff-test')
        cls.create_custom_object_type_field(
            cls.cot, name='name', label='Name', type='text', primary=True, required=True
        )
        cls.field = cls.create_custom_object_type_field(
            cls.cot,
            name='sites',
            label='Sites',
            type='multiobject',
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
        cls.cot = cls.create_custom_object_type(name='MoFSTest', slug='mo-fs-test')
        cls.create_custom_object_type_field(
            cls.cot, name='name', label='Name', type='text', primary=True, required=True
        )
        cls.create_custom_object_type_field(
            cls.cot,
            name='sites',
            label='Sites',
            type='multiobject',
            related_object_type=cls.get_site_object_type(),
        )

        cls.site1 = Site.objects.create(name='Site MOFS 1', slug='site-mofs-1')
        cls.site2 = Site.objects.create(name='Site MOFS 2', slug='site-mofs-2')

        model = cls.cot.get_model()
        cls.obj_s1 = model.objects.create(name='Obj S1')
        cls.obj_s1.sites.add(cls.site1)
        cls.obj_s2 = model.objects.create(name='Obj S2')
        cls.obj_s2.sites.add(cls.site2)
        cls.obj_both = model.objects.create(name='Obj Both')
        cls.obj_both.sites.add(cls.site1, cls.site2)
        cls.obj_none = model.objects.create(name='Obj None')

    def _filterset(self, params):
        model = self.cot.get_model()
        return get_filterset_class(model)(params, model.objects.all())

    def test_filter_single_site_returns_linked_objects(self):
        pks = list(self._filterset({'sites': [self.site1.pk]}).qs.values_list('pk', flat=True))
        self.assertIn(self.obj_s1.pk, pks)
        self.assertIn(self.obj_both.pk, pks)
        self.assertNotIn(self.obj_s2.pk, pks)
        self.assertNotIn(self.obj_none.pk, pks)

    def test_filter_multiple_sites_returns_union(self):
        # OR semantics: any object linked to site1 or site2
        pks = list(
            self._filterset({'sites': [self.site1.pk, self.site2.pk]}).qs.values_list('pk', flat=True)
        )
        self.assertIn(self.obj_s1.pk, pks)
        self.assertIn(self.obj_s2.pk, pks)
        self.assertIn(self.obj_both.pk, pks)
        self.assertNotIn(self.obj_none.pk, pks)

    def test_filter_multiple_sites_no_duplicates(self):
        # obj_both is linked to both sites but should appear only once
        qs = self._filterset({'sites': [self.site1.pk, self.site2.pk]}).qs
        self.assertEqual(qs.filter(pk=self.obj_both.pk).count(), 1)

    def test_filter_other_site(self):
        pks = list(self._filterset({'sites': [self.site2.pk]}).qs.values_list('pk', flat=True))
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
        cls.target_cot = cls.create_custom_object_type(name='TargetObj', slug='target-obj')
        cls.create_custom_object_type_field(
            cls.target_cot, name='name', label='Name', type='text', primary=True, required=True
        )

        cls.source_cot = cls.create_custom_object_type(name='SourceObj', slug='source-obj')
        cls.create_custom_object_type_field(
            cls.source_cot, name='name', label='Name', type='text', primary=True, required=True
        )
        cls.create_custom_object_type_field(
            cls.source_cot,
            name='related',
            label='Related',
            type='object',
            related_object_type=cls.target_cot.object_type,
        )

        target_model = cls.target_cot.get_model()
        cls.target1 = target_model.objects.create(name='Target 1')
        cls.target2 = target_model.objects.create(name='Target 2')

        source_model = cls.source_cot.get_model()
        cls.source_t1 = source_model.objects.create(name='Source T1', related=cls.target1)
        cls.source_t2 = source_model.objects.create(name='Source T2', related=cls.target2)
        cls.source_none = source_model.objects.create(name='Source None')

    def _field(self):
        return CustomObjectTypeField.objects.get(custom_object_type=self.source_cot, name='related')

    def test_filterform_field_returns_dynamic_model_choice_field(self):
        form_field = ObjectFieldType().get_filterform_field(self._field())
        self.assertIsInstance(form_field, DynamicModelChoiceField)

    def test_filterform_field_queryset_points_at_target_model(self):
        form_field = ObjectFieldType().get_filterform_field(self._field())
        target_model = self.target_cot.get_model()
        self.assertEqual(form_field.queryset.model._meta.db_table, target_model._meta.db_table)

    def test_filter_by_custom_object_target(self):
        source_model = self.source_cot.get_model()
        fs = get_filterset_class(source_model)({'related': self.target1.pk}, source_model.objects.all())
        pks = list(fs.qs.values_list('pk', flat=True))
        self.assertIn(self.source_t1.pk, pks)
        self.assertNotIn(self.source_t2.pk, pks)
        self.assertNotIn(self.source_none.pk, pks)

    def test_filter_other_target(self):
        source_model = self.source_cot.get_model()
        fs = get_filterset_class(source_model)({'related': self.target2.pk}, source_model.objects.all())
        pks = list(fs.qs.values_list('pk', flat=True))
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
        cls.target_cot = cls.create_custom_object_type(name='TargetMObj', slug='target-mobj')
        cls.create_custom_object_type_field(
            cls.target_cot, name='name', label='Name', type='text', primary=True, required=True
        )

        cls.source_cot = cls.create_custom_object_type(name='SourceMObj', slug='source-mobj')
        cls.create_custom_object_type_field(
            cls.source_cot, name='name', label='Name', type='text', primary=True, required=True
        )
        cls.create_custom_object_type_field(
            cls.source_cot,
            name='related_items',
            label='Related Items',
            type='multiobject',
            related_object_type=cls.target_cot.object_type,
        )

        target_model = cls.target_cot.get_model()
        cls.target1 = target_model.objects.create(name='Target M1')
        cls.target2 = target_model.objects.create(name='Target M2')

        source_model = cls.source_cot.get_model()
        cls.source_t1 = source_model.objects.create(name='Source MT1')
        cls.source_t1.related_items.add(cls.target1)
        cls.source_t2 = source_model.objects.create(name='Source MT2')
        cls.source_t2.related_items.add(cls.target2)
        cls.source_both = source_model.objects.create(name='Source MBoth')
        cls.source_both.related_items.add(cls.target1, cls.target2)
        cls.source_none = source_model.objects.create(name='Source MNone')

    def _field(self):
        return CustomObjectTypeField.objects.get(custom_object_type=self.source_cot, name='related_items')

    def test_filterform_field_returns_dynamic_model_multiple_choice_field(self):
        form_field = MultiObjectFieldType().get_filterform_field(self._field())
        self.assertIsInstance(form_field, DynamicModelMultipleChoiceField)

    def test_filterform_field_queryset_points_at_target_model(self):
        form_field = MultiObjectFieldType().get_filterform_field(self._field())
        target_model = self.target_cot.get_model()
        self.assertEqual(form_field.queryset.model._meta.db_table, target_model._meta.db_table)

    def test_filter_single_target(self):
        source_model = self.source_cot.get_model()
        fs = get_filterset_class(source_model)(
            {'related_items': [self.target1.pk]}, source_model.objects.all()
        )
        pks = list(fs.qs.values_list('pk', flat=True))
        self.assertIn(self.source_t1.pk, pks)
        self.assertIn(self.source_both.pk, pks)
        self.assertNotIn(self.source_t2.pk, pks)
        self.assertNotIn(self.source_none.pk, pks)

    def test_filter_multiple_targets_returns_union(self):
        source_model = self.source_cot.get_model()
        fs = get_filterset_class(source_model)(
            {'related_items': [self.target1.pk, self.target2.pk]}, source_model.objects.all()
        )
        pks = list(fs.qs.values_list('pk', flat=True))
        self.assertIn(self.source_t1.pk, pks)
        self.assertIn(self.source_t2.pk, pks)
        self.assertIn(self.source_both.pk, pks)
        self.assertNotIn(self.source_none.pk, pks)

    def test_filter_multiple_targets_no_duplicates(self):
        source_model = self.source_cot.get_model()
        qs = get_filterset_class(source_model)(
            {'related_items': [self.target1.pk, self.target2.pk]}, source_model.objects.all()
        ).qs
        self.assertEqual(qs.filter(pk=self.source_both.pk).count(), 1)
