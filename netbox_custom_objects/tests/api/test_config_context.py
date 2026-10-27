"""
Tests for config context in the custom object REST API.
"""

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from rest_framework import status
from rest_framework.test import APIClient

from netbox_custom_objects.models import CustomObjectType
from ..base import CustomObjectsTestCase, create_token
from core.models import ObjectType
from dcim.models import Site
from extras.models import ConfigContext, Tag
from users.models import ObjectPermission


class ConfigContextAPITestCase(CustomObjectsTestCase, TestCase):
    """REST API exposure of local_context_data and config_context for config-context-enabled types."""

    def setUp(self):
        super().setUp()
        self.cot = CustomObjectType.objects.create(
            name='cc_api',
            slug='cc-api',
            config_context_enabled=True,
        )
        self.create_custom_object_type_field(
            self.cot,
            name='name',
            label='Name',
            type='text',
            primary=True,
            required=True,
        )
        self.model = self.cot.get_model()
        token = create_token(self.user)
        self.header = {'HTTP_AUTHORIZATION': f'Token {token}'}
        self.client = APIClient()

    def _list_url(self):
        return reverse(
            'plugins-api:netbox_custom_objects-api:customobject-list',
            kwargs={'custom_object_type': self.cot.slug},
        )

    def _detail_url(self, pk):
        return reverse(
            'plugins-api:netbox_custom_objects-api:customobject-detail',
            kwargs={'pk': pk, 'custom_object_type': self.cot.slug},
        )

    def _add_perm(self, action):
        perm = ObjectPermission(name=f'{action}-{self.model._meta.model_name}', actions=[action])
        perm.save()
        perm.users.add(self.user)
        perm.object_types.add(ObjectType.objects.get_for_model(self.model))
        return perm

    def test_local_context_data_present_in_response(self):
        obj = self.model.objects.create(name='obj-1', local_context_data={'ntp': ['10.0.0.1']})
        self._add_perm('view')

        response = self.client.get(self._detail_url(obj.pk), **self.header)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('local_context_data', response.data)
        self.assertEqual(response.data['local_context_data'], {'ntp': ['10.0.0.1']})

    def test_set_local_context_data_via_patch(self):
        obj = self.model.objects.create(name='obj-2')
        self._add_perm('change')

        response = self.client.patch(
            self._detail_url(obj.pk),
            {'local_context_data': {'role': 'edge'}},
            format='json',
            **self.header,
        )
        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
            f'Expected 200; got {response.status_code}: {getattr(response, "data", response.content)}',
        )
        obj.refresh_from_db()
        self.assertEqual(obj.local_context_data, {'role': 'edge'})

    def test_local_context_data_absent_when_disabled(self):
        """A type without config context support must not expose local_context_data."""
        cot = CustomObjectType.objects.create(name='cc_off', slug='cc-off')
        self.create_custom_object_type_field(
            cot,
            name='name',
            label='Name',
            type='text',
            primary=True,
            required=True,
        )
        model = cot.get_model()
        obj = model.objects.create(name='x')

        perm = ObjectPermission(name='view-cc-off', actions=['view'])
        perm.save()
        perm.users.add(self.user)
        perm.object_types.add(ObjectType.objects.get_for_model(model))

        url = reverse(
            'plugins-api:netbox_custom_objects-api:customobject-detail',
            kwargs={'pk': obj.pk, 'custom_object_type': cot.slug},
        )
        response = self.client.get(url, **self.header)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertNotIn('local_context_data', response.data)
        self.assertNotIn('config_context', response.data)

    def _add_site_field(self):
        self.create_custom_object_type_field(
            self.cot,
            name='site',
            label='Site',
            type='object',
            related_object_type=self.get_site_object_type(),
        )
        self.model = self.cot.get_model()

    def test_config_context_renders_source_and_local_data(self):
        site = Site.objects.create(name='CC Site', slug='cc-site')
        ConfigContext.objects.create(name='site', weight=100, data={'ntp': '10.0.0.1', 'dns': 'a'}).sites.add(site)
        tag = Tag.objects.create(name='Gold', slug='gold')
        ConfigContext.objects.create(name='gold', weight=200, data={'tier': 'gold'}).tags.add(tag)
        self._add_site_field()
        obj = self.model.objects.create(name='obj-1', site=site, local_context_data={'dns': 'b'})
        obj.tags.add(tag)
        self._add_perm('view')

        response = self.client.get(self._detail_url(obj.pk), **self.header)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['config_context'], {'ntp': '10.0.0.1', 'dns': 'b', 'tier': 'gold'})

    def test_config_context_is_local_data_without_dimension_fields(self):
        ConfigContext.objects.create(name='global', weight=100, data={'global': True})
        obj = self.model.objects.create(name='obj-1', local_context_data={'local': 1})
        self._add_perm('view')

        response = self.client.get(self._detail_url(obj.pk), **self.header)
        self.assertEqual(response.data['config_context'], {'local': 1})

    def test_config_context_is_read_only(self):
        obj = self.model.objects.create(name='obj-1')
        self._add_perm('change')

        response = self.client.patch(
            self._detail_url(obj.pk),
            {'config_context': {'x': 1}},
            format='json',
            **self.header,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['config_context'], {})

    def test_custom_field_named_config_context_takes_precedence(self):
        self.create_custom_object_type_field(
            self.cot,
            name='config_context',
            label='Config context',
            type='text',
        )
        model = self.cot.get_model()
        obj = model.objects.create(name='obj-1', config_context='custom value')
        self._add_perm('view')

        response = self.client.get(self._detail_url(obj.pk), **self.header)
        self.assertEqual(response.data['config_context'], 'custom value')

    def test_config_context_can_be_omitted(self):
        site = Site.objects.create(name='CC Site', slug='cc-site')
        ConfigContext.objects.create(name='site', weight=100, data={'ntp': '10.0.0.1'}).sites.add(site)
        self._add_site_field()
        self.model.objects.create(name='obj-1', site=site)
        self._add_perm('view')
        self.client.get(self._list_url(), **self.header)  # warm caches

        with CaptureQueriesContext(connection) as with_context:
            response = self.client.get(self._list_url(), **self.header)
        self.assertIn('config_context', response.data['results'][0])
        with CaptureQueriesContext(connection) as without_context:
            response = self.client.get(f'{self._list_url()}?omit=config_context', **self.header)
        self.assertNotIn('config_context', response.data['results'][0])
        self.assertIn('local_context_data', response.data['results'][0])
        self.assertLess(len(without_context.captured_queries), len(with_context.captured_queries))

    def test_linked_objects_share_config_context_lookups(self):
        """Linked objects with the same dimensions share one source-context lookup."""
        site = Site.objects.create(name='CC Site', slug='cc-site')
        ConfigContext.objects.create(name='site', weight=100, data={'ntp': '10.0.0.1'}).sites.add(site)
        self._add_site_field()
        self._add_perm('view')
        site_perm = ObjectPermission.objects.create(name='view-site', actions=['view'])
        site_perm.users.add(self.user)
        site_perm.object_types.add(ObjectType.objects.get_for_model(Site))
        url = reverse('plugins-api:netbox_custom_objects-api:linked-objects')
        url = f'{url}?object_type=dcim.site&object_id={site.pk}'

        def context_queries_for(rows):
            self.model.objects.all().delete()
            for i in range(rows):
                self.model.objects.create(name=f'row-{i}', site=site)
            with CaptureQueriesContext(connection) as ctx:
                response = self.client.get(url, **self.header)
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertEqual(len(response.data['results']), rows)
            self.assertEqual(response.data['results'][0]['object']['config_context'], {'ntp': '10.0.0.1'})
            return sum('"extras_configcontext"' in q['sql'] for q in ctx.captured_queries)

        self.assertEqual(context_queries_for(2), context_queries_for(6))

    def test_config_context_list_query_count_stays_flat(self):
        site = Site.objects.create(name='CC Site', slug='cc-site')
        ConfigContext.objects.create(name='site', weight=100, data={'ntp': '10.0.0.1'}).sites.add(site)
        self._add_site_field()
        self._add_perm('view')

        def count_for(rows):
            self.model.objects.all().delete()
            for i in range(rows):
                self.model.objects.create(name=f'row-{i}', site=site, local_context_data={'index': i})
            with CaptureQueriesContext(connection) as ctx:
                response = self.client.get(self._list_url(), **self.header)
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertEqual(response.data['results'][0]['config_context']['ntp'], '10.0.0.1')
            return len(ctx.captured_queries)

        count_for(1)  # warm caches
        self.assertEqual(count_for(3), count_for(9))
