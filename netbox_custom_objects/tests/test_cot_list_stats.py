"""Tests for CustomObjectType list statistics and table columns."""

from django.test import TestCase

from netbox_custom_objects.models import CustomObjectType
from netbox_custom_objects.tables import (
    CustomObjectTypeInstanceCountColumn,
    CustomObjectTypeReferencedByColumn,
)

from .base import CustomObjectsTestCase


class CustomObjectTypeListStatsTestCase(CustomObjectsTestCase, TestCase):
    """Tests for instance/referrer count helpers on CustomObjectType."""

    def test_get_instance_count_empty(self):
        cot = self.create_simple_custom_object_type(name='empty', slug='empty-cot')
        self.assertEqual(cot.get_instance_count(), 0)

    def test_get_instance_count_with_instances(self):
        cot = self.create_simple_custom_object_type(name='filled', slug='filled-cot')
        model = cot.get_model()
        model.objects.create(name='one')
        model.objects.create(name='two')
        self.assertEqual(cot.get_instance_count(), 2)

    def test_get_referencing_custom_object_types_object_field(self):
        target = self.create_simple_custom_object_type(name='target', slug='ref-target')
        source = self.create_simple_custom_object_type(name='source', slug='ref-source')
        self.create_custom_object_type_field(
            source,
            name='ref',
            type='object',
            related_object_type=target.object_type,
        )

        referrers = target.get_referencing_custom_object_types()
        self.assertEqual(len(referrers), 1)
        self.assertEqual(referrers[0].pk, source.pk)

    def test_get_referencing_custom_object_types_polymorphic_field(self):
        target = self.create_simple_custom_object_type(name='poly_tgt', slug='poly-tgt')
        source = self.create_simple_custom_object_type(name='poly_src', slug='poly-src')
        self.create_polymorphic_field(
            source,
            related_object_types=[target.object_type],
            name='source',
            type='multiobject',
        )

        referrers = target.get_referencing_custom_object_types()
        self.assertEqual(len(referrers), 1)
        self.assertEqual(referrers[0].pk, source.pk)

    def test_get_referencing_excludes_self(self):
        cot = self.create_simple_custom_object_type(name='self', slug='self-ref')
        self.create_custom_object_type_field(
            cot,
            name='self_ref',
            type='object',
            related_object_type=cot.object_type,
        )
        self.assertEqual(cot.get_referencing_custom_object_types(), [])

    def test_bulk_load_list_stats(self):
        target = self.create_simple_custom_object_type(name='bulk_tgt', slug='bulk-tgt')
        source = self.create_simple_custom_object_type(name='bulk_src', slug='bulk-src')
        self.create_custom_object_type_field(
            source,
            name='ref',
            type='object',
            related_object_type=target.object_type,
        )
        target.get_model().objects.create(name='inst')

        cots = list(
            CustomObjectType.objects.filter(pk__in=[target.pk, source.pk]).select_related('object_type')
        )
        CustomObjectType.bulk_load_list_stats(cots)

        target_loaded = next(cot for cot in cots if cot.pk == target.pk)
        source_loaded = next(cot for cot in cots if cot.pk == source.pk)
        self.assertEqual(target_loaded.get_instance_count(), 1)
        self.assertEqual(source_loaded.get_instance_count(), 0)
        self.assertEqual(len(target_loaded.get_referencing_custom_object_types()), 1)
        self.assertEqual(target_loaded.get_referencing_custom_object_types()[0].pk, source.pk)


class CustomObjectTypeTableColumnsTestCase(CustomObjectsTestCase, TestCase):
    """Tests for CustomObjectType list table column rendering."""

    def test_instance_count_column_empty(self):
        cot = self.create_simple_custom_object_type(name='col_empty', slug='col-empty')
        html = CustomObjectTypeInstanceCountColumn().render(cot)
        self.assertIn('&mdash;', html)

    def test_instance_count_column_linked(self):
        cot = self.create_simple_custom_object_type(name='col_link', slug='col-link')
        cot.get_model().objects.create(name='x')
        html = CustomObjectTypeInstanceCountColumn().render(cot)
        self.assertIn('href=', html)
        self.assertIn('col-link', html)
        self.assertIn('>1<', html)

    def test_referenced_by_column_empty(self):
        cot = self.create_simple_custom_object_type(name='col_noref', slug='col-noref')
        html = CustomObjectTypeReferencedByColumn().render(cot)
        self.assertIn('&mdash;', html)

    def test_referenced_by_column_count_with_tooltip(self):
        target = self.create_simple_custom_object_type(name='col_tgt', slug='col-tgt')
        source = self.create_simple_custom_object_type(name='col_src', slug='col-src')
        self.create_custom_object_type_field(
            source,
            name='ref',
            type='object',
            related_object_type=target.object_type,
        )
        html = CustomObjectTypeReferencedByColumn().render(target)
        self.assertIn('title=', html)
        self.assertIn('col-src', html)
        self.assertIn('>1<', html)
        self.assertNotIn('text-warning', html)
