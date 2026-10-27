"""
Tests for the custom object list table.
"""

from unittest import skipUnless

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from dcim.models import Site
from extras.choices import CustomFieldUIVisibleChoices
from netbox.tables import NetBoxTable
from netbox_custom_objects.models import CustomObjectTypeField

from .base import CustomObjectsTestCase


class PolymorphicColumnPrefetchTestCase(CustomObjectsTestCase, TestCase):
    """A polymorphic multi-object column is prefetched, like NetBox prefetches a table's related columns."""

    def setUp(self):
        super().setUp()
        self.user.is_superuser = True
        self.user.save()
        self.cot = self.create_custom_object_type(name='Circuit', slug='circuits')
        self.create_custom_object_type_field(self.cot, name='name', type='text', primary=True, required=True)
        self.field = self.create_polymorphic_field(
            self.cot,
            [self.get_site_object_type(), self.get_device_object_type()],
            name='targets',
            type='multiobject',
        )
        self.cot.refresh_from_db()
        self.model = self.cot.get_model()
        self.sites = [Site.objects.create(name=f'Site {i}', slug=f'site-{i}') for i in range(2)]
        self.url = reverse(
            'plugins:netbox_custom_objects:customobject_list',
            kwargs={'custom_object_type': self.cot.slug},
        )

    def _add_objects(self, count):
        for _ in range(count):
            obj = self.model.objects.create(name=f'Circuit {self.model.objects.count()}')
            obj.targets.set(self.sites)

    def _list_queries(self):
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        # The column must render its targets: one that isn't rendered costs no queries per row either.
        self.assertContains(response, 'Site 1')
        return response, len(queries)

    def test_list_query_count_does_not_grow_with_rows(self):
        self._add_objects(2)
        self._list_queries()  # Warm the per-process caches first.
        _, few = self._list_queries()
        self._add_objects(3)
        _, more = self._list_queries()
        self.assertEqual(more, few)

    def test_prefetched_field_answers_count_and_exists_without_a_query(self):
        """django-tables2 checks exists() before rendering each row's many-to-many cell."""
        self._add_objects(1)
        obj = self.model.objects.prefetch_related('targets').get()
        with self.assertNumQueries(0):
            self.assertTrue(obj.targets.exists())
            self.assertEqual(obj.targets.count(), 2)

    def test_visible_column_is_prefetched(self):
        self._add_objects(1)
        response, _ = self._list_queries()
        self.assertIn('targets', response.context['table'].data.data._prefetch_related_lookups)

    # COMPAT(netbox<4.5.4): NetBoxTable prefetches in __init__(), before the selected columns are applied.
    @skipUnless(hasattr(NetBoxTable, '_apply_prefetching'), 'NetBox prefetches every column before 4.5.4')
    def test_hidden_column_is_prefetched_only_when_requested(self):
        """A column left out of the defaults isn't prefetched, unless named, as an export of all columns does."""
        field = CustomObjectTypeField.objects.get(pk=self.field.pk)
        field.ui_visible = CustomFieldUIVisibleChoices.IF_SET
        field.save()
        self._add_objects(1)
        table = self.client.get(self.url).context['table']
        self.assertNotIn('targets', table.data.data._prefetch_related_lookups)

        table._apply_prefetching(columns=['targets'])
        self.assertIn('targets', table.data.data._prefetch_related_lookups)
