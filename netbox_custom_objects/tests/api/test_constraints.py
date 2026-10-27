"""
Tests for the Custom Object Type constraint REST API endpoint.
"""

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from rest_framework import status
from rest_framework.test import APIClient

from netbox_custom_objects.models import CustomObjectTypeConstraint
from ..base import CustomObjectsTestCase, create_token


class CustomObjectTypeConstraintAPITestCase(CustomObjectsTestCase, TestCase):
    """Constraints are managed through their own endpoint and enforced on custom object writes."""

    constraint_list_url = 'plugins-api:netbox_custom_objects-api:customobjecttypeconstraint-list'

    def setUp(self):
        super().setUp()
        self.user.is_superuser = True
        self.user.save()
        self.client = APIClient()
        self.header = {'HTTP_AUTHORIZATION': f'Token {create_token(self.user)}'}
        self.cot = self.create_custom_object_type(name='asset', slug='assets')
        self.vendor = self.create_custom_object_type_field(self.cot, name='vendor', type='text', primary=True)
        self.serial = self.create_custom_object_type_field(self.cot, name='serial', type='text')

    def _create_constraint(self, fields, **extra):
        return self.client.post(
            reverse(self.constraint_list_url),
            {'custom_object_type': self.cot.pk, 'name': 'vendor_serial', 'fields': fields, **extra},
            format='json',
            **self.header,
        )

    def test_create_and_enforce(self):
        response = self._create_constraint([self.vendor.pk, self.serial.pk])
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.content)
        self.assertEqual([f['name'] for f in response.data['fields']], ['vendor', 'serial'])
        self.assertEqual(
            CustomObjectTypeConstraint.objects.get().field_schema_ids,
            [self.vendor.schema_id, self.serial.schema_id],
        )

        self.cot.refresh_from_db()
        model = self.cot.get_model()
        url = reverse(
            'plugins-api:netbox_custom_objects-api:customobject-list', kwargs={'custom_object_type': 'assets'}
        )
        data = {'vendor': 'acme', 'serial': '1'}
        response = self.client.post(url, data, format='json', **self.header)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.content)
        response = self.client.post(url, data, format='json', **self.header)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.content)
        self.assertEqual(model.objects.count(), 1)

    def test_rejects_field_of_another_type(self):
        other = self.create_custom_object_type(name='other', slug='others')
        foreign = self.create_custom_object_type_field(other, name='foreign', type='text')
        response = self._create_constraint([self.vendor.pk, foreign.pk])
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.content)
        self.assertIn('fields', response.data)

        condition = [{'field': foreign.pk, 'lookup': 'isnull', 'value': True}]
        response = self._create_constraint([self.serial.pk], condition=condition)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.content)
        self.assertIn('condition', response.data)

        # Including a field of another type as the value a rule compares with.
        rule = [{'field': self.serial.pk, 'lookup': 'exact', 'value': {'field': foreign.pk}}]
        response = self._create_constraint([], name='check', type='check', rule=rule)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.content)
        self.assertIn('rule', response.data)

    def test_create_check_constraint(self):
        start = self.create_custom_object_type_field(self.cot, name='start_date', type='date')
        end = self.create_custom_object_type_field(self.cot, name='end_date', type='date')
        rule = [{'field': end.pk, 'lookup': 'gt', 'value': {'field': start.pk}}]
        response = self.client.post(
            reverse(self.constraint_list_url),
            {
                'custom_object_type': self.cot.pk,
                'name': 'dates',
                'type': 'check',
                'rule': rule,
                'error_message': 'The end date must be after the start date.',
            },
            format='json',
            **self.header,
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.content)
        self.assertEqual(
            CustomObjectTypeConstraint.objects.get().rule,
            [{'field': end.schema_id, 'lookup': 'gt', 'value': {'field': start.schema_id}}],
        )
        self.assertEqual(response.data['fields'], [])
        predicate = response.data['rule'][0]
        self.assertEqual((predicate['field']['name'], predicate['value']['field']['name']), ('end_date', 'start_date'))

        url = reverse(
            'plugins-api:netbox_custom_objects-api:customobject-list', kwargs={'custom_object_type': 'assets'}
        )
        data = {'vendor': 'acme', 'start_date': '2026-02-01', 'end_date': '2026-01-01'}
        response = self.client.post(url, data, format='json', **self.header)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.content)
        self.assertIn('The end date must be after the start date.', str(response.data))
        response = self.client.post(url, {**data, 'end_date': '2026-03-01'}, format='json', **self.header)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.content)

    def test_create_with_condition(self):
        status_field = self.create_custom_object_type_field(self.cot, name='status', type='text')
        condition = [{'field': status_field.pk, 'lookup': 'n', 'value': 'retired'}]
        response = self._create_constraint([self.serial.pk], condition=condition)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.content)
        self.assertEqual(
            CustomObjectTypeConstraint.objects.get().condition,
            [{'field': status_field.schema_id, 'lookup': 'n', 'value': 'retired'}],
        )
        predicate = response.data['condition'][0]
        self.assertEqual(predicate['field']['id'], status_field.pk)
        self.assertEqual(predicate['field']['name'], 'status')
        self.assertEqual((predicate['lookup'], predicate['value']), ('n', 'retired'))

        response = self._create_constraint(
            [self.vendor.pk], name='invalid', condition=[{'field': status_field.pk, 'lookup': 'isnull'}]
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.content)

    def test_rejects_invalid_constraint(self):
        response = self._create_constraint([self.vendor.pk])
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.content)
        self.assertFalse(CustomObjectTypeConstraint.objects.exists())

    def test_update_and_filter(self):
        response = self._create_constraint([self.vendor.pk, self.serial.pk])
        url = reverse(
            'plugins-api:netbox_custom_objects-api:customobjecttypeconstraint-detail',
            kwargs={'pk': response.data['id']},
        )
        response = self.client.patch(url, {'case_insensitive': True}, format='json', **self.header)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertTrue(CustomObjectTypeConstraint.objects.get().case_insensitive)

        # Schema IDs are numbered per type, so another type's fields can match the constraint's.
        other = self.create_custom_object_type(name='other', slug='others')
        self.create_custom_object_type_field(other, name='vendor', type='text', primary=True)
        self.create_custom_object_type_field(other, name='serial', type='text')
        response = self.client.patch(url, {'custom_object_type': other.pk}, format='json', **self.header)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.content)
        self.assertIn('custom_object_type', response.data)
        self.assertEqual(CustomObjectTypeConstraint.objects.get().custom_object_type, self.cot)

        response = self.client.get(
            reverse(self.constraint_list_url), {'custom_object_type_id': other.pk}, **self.header
        )
        self.assertEqual(response.data['count'], 0)
        response = self.client.get(reverse(self.constraint_list_url), {'custom_object_type': 'assets'}, **self.header)
        self.assertEqual(response.data['count'], 1)

    def test_list_query_count_does_not_grow_with_constraints(self):
        model_field = self.create_custom_object_type_field(self.cot, name='model', type='text')

        def list_queries():
            with CaptureQueriesContext(connection) as ctx:
                response = self.client.get(reverse(self.constraint_list_url), **self.header)
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
            # Ignore core's own per-request queries (auth, config), which vary by NetBox version.
            return len([q for q in ctx.captured_queries if 'netbox_custom_objects_' in q['sql']])

        CustomObjectTypeConstraint.objects.create(
            custom_object_type=self.cot, name='c1', field_schema_ids=[self.vendor.schema_id, self.serial.schema_id]
        )
        one = list_queries()
        condition = [{'field': self.vendor.schema_id, 'lookup': 'isnull', 'value': False}]
        for name, fields in (('c2', (self.vendor, model_field)), ('c3', (self.serial, model_field))):
            CustomObjectTypeConstraint.objects.create(
                custom_object_type=self.cot,
                name=name,
                field_schema_ids=[f.schema_id for f in fields],
                condition=condition,
            )
        self.assertEqual(list_queries(), one)
