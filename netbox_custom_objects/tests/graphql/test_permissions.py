"""
Permission enforcement through the real ``/graphql/`` endpoint: on the custom-object
root queries (``custom_objects_<slug>`` and ``custom_objects_<slug>_list``), and on
objects reached through a custom object's relationship fields.
"""

import json

from django.test import TestCase, override_settings
from rest_framework import status

from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site

from ..base import CustomObjectsTestCase
from .base import GraphQLClientMixin

LIST_QUERY = '{ custom_objects_server_list { id name } }'


class GraphQLRootQueryPermissionTestCase(GraphQLClientMixin, CustomObjectsTestCase, TestCase):
    def setUp(self):
        super().setUp()
        self.cot = self.create_simple_custom_object_type(name='Server', slug='server')
        self.model = self.cot.get_model()
        self.alpha = self.model.objects.create(name='alpha')
        self.beta = self.model.objects.create(name='beta')

    def _post(self, query, **headers):
        return self.client.post(self.url, data={'query': query}, format='json', **headers)

    def _gql(self, query, **headers):
        response = self._post(query, **(headers or self.header))
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        return json.loads(response.content)

    def _names(self, payload):
        self.assertNotIn('errors', payload, msg=str(payload.get('errors')))
        return sorted(row['name'] for row in payload['data']['custom_objects_server_list'])

    def assertNotInSchema(self, payload, field='custom_objects_server_list'):
        """A type the user can't see has no root queries in their schema (see visible_custom_object_type_ids)."""
        self.assertIsNone(payload['data'])
        self.assertIn(f"Cannot query field '{field}'", payload['errors'][0]['message'])

    def _single(self, pk):
        return self._gql(f'{{ custom_objects_server(id: "{pk}") {{ id name }} }}')

    def _assert_same_error_as_missing(self, payload):
        # A hidden object must be indistinguishable from one that doesn't exist.
        self.assertIsNone(payload['data'])
        missing = self._single(self.beta.pk + 1000)
        self.assertEqual(
            [e['message'] for e in payload['errors']],
            [e['message'] for e in missing['errors']],
        )

    def test_type_hidden_without_view_permission(self):
        self.assertNotInSchema(self._gql(LIST_QUERY))

    def test_list_returns_all_with_view_permission(self):
        self._grant(self.model)
        self.assertEqual(self._names(self._gql(LIST_QUERY)), ['alpha', 'beta'])

    def test_view_permission_on_another_type_grants_nothing(self):
        other = self.create_simple_custom_object_type(name='Other', slug='other').get_model()
        other.objects.create(name='visible')
        self._grant(other)

        self.assertNotInSchema(self._gql(LIST_QUERY))
        payload = self._gql('{ custom_objects_other_list { name } }')
        self.assertNotIn('errors', payload, msg=str(payload.get('errors')))
        self.assertEqual(payload['data']['custom_objects_other_list'], [{'name': 'visible'}])

    def test_permission_without_view_action_grants_nothing(self):
        self._grant(self.model, actions=('add', 'change', 'delete'))
        self.assertNotInSchema(self._gql(LIST_QUERY))

    def test_disabled_permission_grants_nothing(self):
        self._grant(self.model, enabled=False)
        self.assertNotInSchema(self._gql(LIST_QUERY))

    def test_constrained_permission_filters_list(self):
        self._grant(self.model, constraints={'name': 'alpha'})
        self.assertEqual(self._names(self._gql(LIST_QUERY)), ['alpha'])

    def test_constrained_permission_applies_before_filters(self):
        self._grant(self.model, constraints={'name': 'alpha'})
        payload = self._gql('{ custom_objects_server_list(filters: {name: {exact: "beta"}}) { name } }')
        self.assertEqual(self._names(payload), [])

    def test_single_object_within_constraint(self):
        self._grant(self.model, constraints={'name': 'alpha'})
        payload = self._single(self.alpha.pk)
        self.assertNotIn('errors', payload, msg=str(payload.get('errors')))
        self.assertEqual(payload['data']['custom_objects_server'], {'id': str(self.alpha.pk), 'name': 'alpha'})

    def test_single_object_outside_constraint_is_not_returned(self):
        self._grant(self.model, constraints={'name': 'alpha'})
        self._assert_same_error_as_missing(self._single(self.beta.pk))

    def test_single_object_without_permission_is_not_returned(self):
        self.assertNotInSchema(self._single(self.alpha.pk), 'custom_objects_server')

    @override_settings(LOGIN_REQUIRED=True)
    def test_anonymous_rejected_when_login_required(self):
        response = self._post(LIST_QUERY, HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_invalid_token_rejected(self):
        response = self._post(LIST_QUERY, HTTP_AUTHORIZATION=f'Token {"0" * 40}')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    @override_settings(LOGIN_REQUIRED=False, EXEMPT_VIEW_PERMISSIONS=[])
    def test_anonymous_sees_nothing_without_exemption(self):
        response = self._post(LIST_QUERY)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertNotInSchema(json.loads(response.content))

    @override_settings(LOGIN_REQUIRED=False, EXEMPT_VIEW_PERMISSIONS=['*'])
    def test_anonymous_sees_all_with_exemption(self):
        response = self._post(LIST_QUERY)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertEqual(self._names(json.loads(response.content)), ['alpha', 'beta'])


@override_settings(LOGIN_REQUIRED=True)
class GraphQLPermissionTestCase(GraphQLClientMixin, CustomObjectsTestCase, TestCase):
    """
    Object-level view permissions must be enforced for objects reached through a
    custom object's relationship fields, not just for the top-level objects.
    """

    def setUp(self):
        # A non-superuser; permissions are granted explicitly per test.
        super().setUp()

        # COT 'Server' with a single-object field → Site, holding one Site.
        self.site = Site.objects.create(name='Secret', slug='secret')
        self.cot = self.create_custom_object_type(name='Server', slug='server')
        self.create_custom_object_type_field(
            self.cot, name='name', label='Name', type='text', primary=True, required=True
        )
        self.create_custom_object_type_field(
            self.cot,
            name='site',
            label='Site',
            type='object',
            related_object_type=self.get_site_object_type(),
        )
        self.model = self.cot.get_model()
        self.model.objects.create(name='srv', site=self.site)

    def _post(self, query):
        response = self.client.post(self.url, data={'query': query}, format='json', **self.header)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        return json.loads(response.content)

    def test_related_object_hidden_without_permission(self):
        # View permission on the custom object, but NOT on Site → the related
        # site must be withheld (null), while the object itself is returned.
        self._grant(self.model)
        payload = self._post('{ custom_objects_server_list { name site { id name } } }')
        self.assertNotIn('errors', payload, msg=str(payload.get('errors')))
        rows = payload['data']['custom_objects_server_list']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['name'], 'srv')
        self.assertIsNone(rows[0]['site'])

    def test_related_object_visible_with_permission(self):
        # Granting view on Site as well makes the related object appear, proving
        # the previous test's null was permission enforcement, not a broken field.
        self._grant(self.model)
        self._grant(Site)
        payload = self._post('{ custom_objects_server_list { name site { id name } } }')
        self.assertNotIn('errors', payload, msg=str(payload.get('errors')))
        related = payload['data']['custom_objects_server_list'][0]['site']
        self.assertEqual(related['id'], str(self.site.pk))
        self.assertEqual(related['name'], 'Secret')

    def test_multiobject_related_filtered_by_permission(self):
        # The multi-object resolver filters related objects through the batched
        # permission check (_filter_viewable).  Without view permission on Device
        # the list is empty; granting it makes the device appear.
        manufacturer = Manufacturer.objects.create(name='Mfr', slug='mfr')
        device_type = DeviceType.objects.create(manufacturer=manufacturer, model='Model', slug='model')
        role = DeviceRole.objects.create(name='Role', slug='role')
        site = Site.objects.create(name='DevSite', slug='devsite')
        device = Device.objects.create(name='dev1', device_type=device_type, role=role, site=site)
        cot = self.create_multi_object_custom_object_type(name='Group', slug='group')
        model = cot.get_model()
        instance = model.objects.create(name='G1')
        instance.devices.add(device)

        # View on the custom object but NOT on Device → devices filtered out.
        self._grant(model)
        payload = self._post('{ custom_objects_group_list { name devices { id } } }')
        self.assertNotIn('errors', payload, msg=str(payload.get('errors')))
        rows = payload['data']['custom_objects_group_list']
        self.assertEqual(rows[0]['name'], 'G1')
        self.assertEqual(rows[0]['devices'], [])

        # Granting view on Device makes it appear (batched check lets it through).
        self._grant(Device)
        payload = self._post('{ custom_objects_group_list { name devices { id } } }')
        self.assertNotIn('errors', payload, msg=str(payload.get('errors')))
        devices = payload['data']['custom_objects_group_list'][0]['devices']
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0]['id'], str(device.pk))

    def test_multiobject_related_filtered_to_permitted_subset(self):
        # The previous tests grant view on the whole model (all-or-nothing).  This
        # exercises the core security claim at object level: a *constrained*
        # ObjectPermission must filter related objects to just the permitted subset.
        manufacturer = Manufacturer.objects.create(name='Mfr', slug='mfr')
        device_type = DeviceType.objects.create(manufacturer=manufacturer, model='Model', slug='model')
        role = DeviceRole.objects.create(name='Role', slug='role')
        site = Site.objects.create(name='DevSite', slug='devsite')
        allowed = Device.objects.create(name='allowed', device_type=device_type, role=role, site=site)
        denied = Device.objects.create(name='denied', device_type=device_type, role=role, site=site)
        cot = self.create_multi_object_custom_object_type(name='Fleet', slug='fleet')
        model = cot.get_model()
        instance = model.objects.create(name='F1')
        instance.devices.add(allowed, denied)

        self._grant(model)
        # View on Device constrained to only the "allowed" device.
        self._grant(Device, constraints={'name': 'allowed'})

        payload = self._post('{ custom_objects_fleet_list { name devices { id name } } }')
        self.assertNotIn('errors', payload, msg=str(payload.get('errors')))
        devices = payload['data']['custom_objects_fleet_list'][0]['devices']
        self.assertEqual([d['name'] for d in devices], ['allowed'])
        self.assertEqual(devices[0]['id'], str(allowed.pk))

    @override_settings(EXEMPT_VIEW_PERMISSIONS=['*'])
    def test_related_anonymous_access_honors_exempt_view_permissions(self):
        # Anonymous/None traversal must defer to restrict(), exactly like NetBox's
        # BaseObjectType.get_queryset: when the view is exempt, related objects are
        # visible, not silently denied.
        from django.contrib.auth.models import AnonymousUser

        from netbox_custom_objects.graphql.types import _filter_viewable

        self.assertEqual(_filter_viewable(AnonymousUser(), [self.site]), [self.site])
        self.assertEqual(_filter_viewable(None, [self.site]), [self.site])

    @override_settings(EXEMPT_VIEW_PERMISSIONS=[])
    def test_related_anonymous_access_denied_without_exemption(self):
        # And when the view is not exempt, an unauthenticated user sees nothing —
        # again matching restrict()'s anonymous handling.
        from django.contrib.auth.models import AnonymousUser

        from netbox_custom_objects.graphql.types import _filter_viewable

        self.assertEqual(_filter_viewable(AnonymousUser(), [self.site]), [])
        self.assertEqual(_filter_viewable(None, [self.site]), [])
