"""
Tests for the custom object list table.
"""

from urllib.parse import parse_qs, urlparse

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from core.models import ObjectType
from extras.forms import TableConfigForm
from extras.models import TableConfig
from utilities.tables import get_table_for_model

from .base import CustomObjectsTestCase


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
