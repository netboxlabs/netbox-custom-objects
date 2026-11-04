"""
Tests for the custom object list table.
"""

import csv
import functools
import io
from unittest import skipUnless
from urllib.parse import parse_qs, urlparse

from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from core.models import ObjectType
from dcim.models import Region, Site
from extras.choices import CustomFieldUIVisibleChoices
from extras.forms import TableConfigForm
from extras.models import CustomFieldChoiceSet, TableConfig
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
    """Test saved table configurations for custom object lists."""

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

    def test_add_view_saves_a_config(self):
        response = self.client.post(
            reverse('extras:tableconfig_add'),
            {
                'name': 'Speeds',
                'object_type': self.object_type.pk,
                'table': self.table_name,
                'weight': 1000,
                'enabled': True,
                'shared': True,
                'columns': ['name', 'speed'],
                'ordering': '-speed',
            },
        )
        self.assertEqual(response.status_code, 302)
        config = TableConfig.objects.get(name='Speeds')
        self.assertEqual(config.table, self.table_name)
        self.assertEqual(config.columns, ['name', 'speed'])
        self.assertEqual(config.ordering, ['-speed'])

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


# COMPAT(netbox<4.6.0): CustomFieldChoiceSet has no choice_colors.
HAS_CHOICE_COLORS = hasattr(CustomFieldChoiceSet, 'get_choice_color')


class ListExportTestCase(CustomObjectsTestCase, TestCase):
    """The object list's columns are headed with field labels, and export plain values."""

    def setUp(self):
        super().setUp()
        self.user.is_superuser = True
        self.user.save()
        colors = {'choice_colors': {'active': 'green'}} if HAS_CHOICE_COLORS else {}
        choices = CustomFieldChoiceSet.objects.create(
            name='Status', extra_choices=[['active', 'Active'], ['retired', 'Retired']], **colors
        )
        site_type, region_type = (ObjectType.objects.get_for_model(m) for m in (Site, Region))
        self.cot = self.create_custom_object_type(
            name='Contract', slug='contracts', display_expression='{{ vendor }} {{ contract_number }}'
        )
        add = functools.partial(self.create_custom_object_type_field, self.cot)
        add(name='contract_number', label='Contract number', type='text', primary=True)
        add(name='vendor', label='Vendor', type='text')
        add(name='notes', label='Notes', type='longtext')
        add(name='portal', label='Portal', type='url')
        add(name='status', label='Status', type='select', choice_set=choices)
        add(name='statuses', label='Statuses', type='multiselect', choice_set=choices)
        add(name='site', label='Site', type='object', related_object_type=site_type)
        add(name='sites', label='Sites', type='multiobject', related_object_type=site_type)
        add(name='anything', label='Anything', type='object', is_polymorphic=True).related_object_types.set(
            [site_type, region_type]
        )
        add(name='things', label='Things', type='multiobject', is_polymorphic=True).related_object_types.set(
            [site_type, region_type]
        )
        add(name='mac', label='MAC address', type='mac_address')
        add(name='start_date', label='', type='date')
        self.cot.refresh_from_db()

        site = Site.objects.create(name='Site 1', slug='site-1')
        region = Region.objects.create(name='Region 1', slug='region-1')
        obj = self.cot.get_model().objects.create(
            contract_number='ACME-1001',
            vendor='Acme',
            notes='**Renewed**',
            portal='https://support.acme.example',
            portal_title='Support portal',
            status='active',
            statuses=['active', 'retired'],
            site=site,
            anything=region,
        )
        obj.sites.set([site])
        obj.things.add(site, region)
        self.list_url = reverse(
            'plugins:netbox_custom_objects:customobject_list', kwargs={'custom_object_type': self.cot.slug}
        )

    def _export(self, export):
        response = self.client.get(self.list_url, {'export': export})
        self.assertEqual(response.status_code, 200)
        content = b''.join(response.streaming_content) if response.streaming else response.content
        return list(csv.DictReader(io.StringIO(content.decode())))

    def test_export_has_plain_values(self):
        expected = {
            'Contract number': 'ACME-1001',
            'Notes': '**Renewed**',
            'Portal': 'https://support.acme.example',
            'Status': 'Active',
            'Statuses': 'Active, Retired',
            'Site': 'Site 1',
            'Sites': 'Site 1',
            'Anything': 'Region 1',
            'Things': 'Region 1, Site 1',
        }
        for export in ('table', ''):
            with self.subTest(export=export):
                (row,) = self._export(export)
                self.assertEqual({name: row[name] for name in expected}, expected)
                self.assertFalse([value for value in row.values() if '<' in value])

    def test_export_has_no_name_or_type_column(self):
        # An object has no name of its own, and every row has the same type.
        (row,) = self._export('')
        self.assertNotIn('Name', row)
        self.assertNotIn('Custom object type', row)

    def test_columns_headed_with_labels(self):
        response = self.client.get(self.list_url)
        table = response.context['table']
        self.assertEqual(table.columns['mac'].header, 'MAC address')
        self.assertEqual(table.columns['start_date'].header, 'Start date')
        self.assertIn('MAC address', self._export('table')[0])

    def test_primary_column_shows_the_field_value(self):
        # Not the object's display name, which is the display expression here.
        obj = self.cot.get_model().objects.get()
        html = self.client.get(self.list_url).content.decode()
        self.assertIn(f'<a href="{obj.get_absolute_url()}">ACME-1001</a>', html)
        self.assertNotIn('>Acme ACME-1001</a>', html)

    def test_list_keeps_rendering_links(self):
        html = self.client.get(self.list_url).content.decode()
        self.assertIn('<a href="https://support.acme.example">Support portal</a>', html)
        if HAS_CHOICE_COLORS:
            self.assertIn('<span class="badge text-bg-green">Active</span>', html)
        site = Site.objects.get()
        self.assertIn(f'<a href="{site.get_absolute_url()}">Site 1</a>', html)
