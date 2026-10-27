"""
Query-count tests for the custom object REST API.
"""

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from rest_framework import status
from rest_framework.test import APIClient

from netbox_custom_objects.utilities import is_branching_enabled
from ..base import CustomObjectsTestCase, create_token
from core.models import ObjectType
from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site
from extras.models import Tag
from users.models import ObjectPermission, Owner

try:
    from utilities.testing.query_counts import assert_expected_query_count
except ImportError:
    # COMPAT(netbox<4.6.2): NetBox has no query-count baseline helper.
    assert_expected_query_count = None

_HAS_BRANCHING = is_branching_enabled()


class CustomObjectAPIQueryCountTestCase(CustomObjectsTestCase, TestCase):
    """Query counts of the custom object list and detail endpoints, for a type with every relation kind."""

    query_count_model_label = 'customobject-api-relational'

    def setUp(self):
        super().setUp()
        self.cot = self.create_custom_object_type(name='Relational', slug='relational')
        self.create_custom_object_type_field(
            self.cot,
            name='name',
            label='Name',
            type='text',
            primary=True,
            required=True,
        )
        self.create_custom_object_type_field(
            self.cot,
            name='site',
            label='Site',
            type='object',
            related_object_type=self.get_site_object_type(),
        )
        self.create_custom_object_type_field(
            self.cot,
            name='sites',
            label='Sites',
            type='multiobject',
            related_object_type=self.get_site_object_type(),
        )
        targets = [self.get_site_object_type(), self.get_device_object_type()]
        self.create_polymorphic_field(self.cot, targets, name='target', type='object')
        self.create_polymorphic_field(self.cot, targets, name='targets', type='multiobject')
        self.model = self.cot.get_model()

        self.tag = Tag.objects.create(name='Gold', slug='gold')
        self.owner = Owner.objects.create(name='Query count owner')
        manufacturer = Manufacturer.objects.create(name='Mfr', slug='mfr')
        self.device_type = DeviceType.objects.create(manufacturer=manufacturer, model='Model', slug='model')
        self.role = DeviceRole.objects.create(name='Role', slug='role')

        perm = ObjectPermission(name='view-relational', actions=['view'])
        perm.save()
        perm.users.add(self.user)
        perm.object_types.add(ObjectType.objects.get_for_model(self.model))

        token = create_token(self.user)
        self.header = {'HTTP_AUTHORIZATION': f'Token {token}'}
        self.client = APIClient()

    def _make_row(self, i):
        site = Site.objects.create(name=f'Site {i}', slug=f'site-{i}')
        device = Device.objects.create(name=f'Device {i}', device_type=self.device_type, role=self.role, site=site)
        obj = self.model.objects.create(name=f'row-{i}', site=site, target=device, owner=self.owner)
        obj.sites.set([site])
        obj.targets.set([site, device])
        obj.tags.add(self.tag)
        return obj

    def _make_rows(self, count):
        self.model.objects.all().delete()
        start = Site.objects.count()
        return [self._make_row(start + i) for i in range(count)]

    def _list_url(self):
        # One page for every row count used here, so each request serializes all rows.
        url = reverse(
            'plugins-api:netbox_custom_objects-api:customobject-list',
            kwargs={'custom_object_type': self.cot.slug},
        )
        return f'{url}?limit=100'

    def _detail_url(self, pk):
        return reverse(
            'plugins-api:netbox_custom_objects-api:customobject-detail',
            kwargs={'custom_object_type': self.cot.slug, 'pk': pk},
        )

    def _get(self, url):
        response = self.client.get(url, **self.header)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return response

    def _skip_unless_baselines_apply(self):
        if assert_expected_query_count is None:
            self.skipTest('query-count baselines need NetBox 4.6.2 or later')
        if _HAS_BRANCHING:
            self.skipTest('query-count baselines not valid with netbox-branching installed')

    def test_list_query_count(self):
        self._skip_unless_baselines_apply()
        self._make_rows(3)
        self._get(self._list_url())  # warm caches

        with assert_expected_query_count(self, 'api_list_objects'):
            response = self._get(self._list_url())
        self.assertEqual(len(response.data['results']), 3)

    def test_detail_query_count(self):
        self._skip_unless_baselines_apply()
        obj = self._make_rows(1)[0]
        self._get(self._detail_url(obj.pk))  # warm caches

        with assert_expected_query_count(self, 'api_get_object'):
            response = self._get(self._detail_url(obj.pk))
        self.assertEqual(response.data['name'], obj.name)

    def test_list_query_count_does_not_grow_with_rows(self):
        def count_for(rows):
            self._make_rows(rows)
            with CaptureQueriesContext(connection) as ctx:
                response = self._get(self._list_url())
            self.assertEqual(len(response.data['results']), rows)
            return len(ctx.captured_queries)

        count_for(1)  # warm caches
        self.assertEqual(count_for(3), count_for(9))
