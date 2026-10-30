"""
Tests for the custom object list table.
"""

from unittest import skipUnless
from urllib.parse import parse_qs, urlparse

from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from core.models import ObjectType
from dcim.models import Site
from extras.choices import CustomFieldUIVisibleChoices
from extras.forms import TableConfigForm
from extras.models import TableConfig
from netbox.tables import NetBoxTable
from netbox_custom_objects.models import CustomObjectTypeField
from utilities.tables import get_table_for_model

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


class TableConfigTestCase(CustomObjectsTestCase, TestCase):
    """A saved table configuration works for a custom object list, as it does for NetBox's own lists."""

    def setUp(self):
        super().setUp()
        self.user.is_superuser = True
        self.user.save()
        self.cot = self.create_custom_object_type(name='Circuit', slug='circuits')
        self.create_custom_object_type_field(self.cot, name='name', type='text', primary=True, required=True)
        self.create_custom_object_type_field(self.cot, name='speed', type='integer')
        self.cot.refresh_from_db()
        self.model = self.cot.get_model()
        self.model.objects.create(name='Circuit 1', speed=100)
        self.object_type = ObjectType.objects.get_for_model(self.model)
        self.table_name = f'{self.model.__name__}Table'
        self.list_url = reverse(
            'plugins:netbox_custom_objects:customobject_list',
            kwargs={'custom_object_type': self.cot.slug},
        )

    def test_table_is_found_by_name(self):
        table_class = get_table_for_model(self.model)
        self.assertIsNotNone(table_class)
        self.assertEqual(table_class.__name__, self.table_name)
        self.assertIn('speed', table_class([]).columns.names())

    def test_unknown_table_is_not_found(self):
        self.assertIsNone(get_table_for_model(self.model, f'Table{self.cot.pk + 1000}ModelTable'))
        self.assertIsNone(get_table_for_model(self.model, 'NoSuchTable'))

    def test_list_table_advertises_a_resolvable_table(self):
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, 200)
        params = parse_qs(urlparse('?' + response.context['table'].config_params).query)
        self.assertEqual(params['table'], [self.table_name])
        self.assertEqual(params['object_type'], [str(self.object_type.pk)])

    def test_form_lists_the_table_columns(self):
        form = TableConfigForm(initial={'object_type': self.object_type.pk, 'table': self.table_name})
        available = [name for name, _ in form.fields['available_columns'].widget.choices]
        selected = [name for name, _ in form.fields['columns'].widget.choices]
        self.assertIn('speed', available + selected)
        self.assertIn('name', selected)

    def test_add_view_lists_the_table_columns(self):
        response = self.client.get(
            reverse('extras:tableconfig_add'),
            {'object_type': self.object_type.pk, 'table': self.table_name},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="speed"')

    def test_config_validates_columns(self):
        config = TableConfig(
            name='Speeds',
            object_type=self.object_type,
            table=self.table_name,
            columns=['name', 'speed'],
            ordering=['-speed'],
            user=self.user,
        )
        config.full_clean()

        config.columns = ['name', 'nonexistent']
        with self.assertRaises(ValidationError):
            config.full_clean()

    def test_saved_config_is_offered_and_applied(self):
        config = TableConfig.objects.create(
            name='Speeds',
            object_type=self.object_type,
            table=self.table_name,
            columns=['name', 'speed'],
            user=self.user,
        )
        response = self.client.get(self.list_url)
        self.assertIn(config, response.context['table_configs'])

        response = self.client.get(self.list_url, {'tableconfig_id': config.pk})
        self.assertEqual(response.status_code, 200)
        visible = [name for name, column in response.context['table'].columns.items() if column.visible]
        self.assertIn('speed', visible)
        self.assertNotIn('id', visible)

        response = self.client.get(config.get_absolute_url())
        self.assertEqual(response.status_code, 200)
