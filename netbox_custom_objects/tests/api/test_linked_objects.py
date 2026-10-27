"""
Tests for the linked-objects REST API endpoint.
"""

import uuid

from django.test import TestCase
from django.urls import reverse
from rest_framework import status

from core.models import ObjectType
from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site
from extras.models import Dashboard
from netbox_custom_objects.models import CustomObjectType
from users.models import ObjectPermission
from utilities.testing import create_test_user

from ..base import CustomObjectsTestCase, create_token


class LinkedObjectsAPITestCase(CustomObjectsTestCase, TestCase):
    """
    Tests for the GET /api/plugins/custom-objects/linked-objects/ endpoint.
    """

    def setUp(self):
        self.user = create_test_user('linkedobjectstestuser')
        token_key = create_token(self.user)
        self.header = {'HTTP_AUTHORIZATION': f'Token {token_key}'}

        # Build a custom object type with both an FK and a M2M device field
        self.cot = CustomObjectsTestCase.create_complex_custom_object_type(
            name='LinkedTest',
            slug='linked-test',
        )
        self.model = self.cot.get_model()

        # Create a device to link against
        manufacturer = Manufacturer.objects.create(name='LO Manufacturer', slug='lo-manufacturer')
        device_type = DeviceType.objects.create(manufacturer=manufacturer, model='LO Type', slug='lo-type')
        role = DeviceRole.objects.create(name='LO Role', slug='lo-role', color='ffffff')
        site = Site.objects.create(name='LO Site', slug='lo-site')
        self.device = Device.objects.create(device_type=device_type, role=role, name='LO Device', site=site)
        self._grant(Device)
        self._grant(self.model)

    def _grant(self, model, constraints=None, user=None):
        """Give the user (default: self.user) view permission on *model*."""
        perm = ObjectPermission.objects.create(
            name=f'LO view {model._meta.label} {uuid.uuid4().hex[:8]}', actions=['view'], constraints=constraints
        )
        perm.users.add(user or self.user)
        perm.object_types.add(ObjectType.objects.get_for_model(model))

    def tearDown(self):
        CustomObjectType.clear_model_cache()
        super().tearDown()

    def _url(self, **params):
        base = reverse('plugins-api:netbox_custom_objects-api:linked-objects')
        if params:
            base += '?' + '&'.join(f'{k}={v}' for k, v in params.items())
        return base

    def test_fk_field_linked_object_appears(self):
        """An object linked via a FK field is returned in the results."""
        linked = self.model.objects.create(
            name='linked-fk',
            device=self.device,
        )
        url = self._url(object_type='dcim.device', object_id=self.device.pk)
        response = self.client.get(url, **self.header)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data['results']
        fk_results = [r for r in results if r['field_name'] == 'device']
        self.assertTrue(
            any(r['object']['id'] == linked.pk for r in fk_results), 'Linked FK object not found in results'
        )

    def test_m2m_field_linked_object_appears(self):
        """An object linked via a M2M field is returned in the results."""
        linked = self.model.objects.create(
            name='linked-m2m',
        )
        # Attach the device via the M2M field
        linked.devices.add(self.device)

        url = self._url(object_type='dcim.device', object_id=self.device.pk)
        response = self.client.get(url, **self.header)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data['results']
        m2m_results = [r for r in results if r['field_name'] == 'devices']
        self.assertTrue(
            any(r['object']['id'] == linked.pk for r in m2m_results), 'Linked M2M object not found in results'
        )

    def test_no_linked_objects_returns_empty(self):
        """An object with no linked custom objects returns count=0 and empty results."""
        url = self._url(object_type='dcim.device', object_id=self.device.pk)
        response = self.client.get(url, **self.header)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 0)
        self.assertEqual(response.data['results'], [])

    def test_missing_params_returns_400(self):
        """Omitting required query params returns 400."""
        url = reverse('plugins-api:netbox_custom_objects-api:linked-objects')
        response = self.client.get(url, **self.header)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_invalid_object_type_returns_400(self):
        """A non-existent object_type returns 400."""
        url = self._url(object_type='nonexistent.model', object_id=1)
        response = self.client.get(url, **self.header)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_nonexistent_object_id_returns_404(self):
        """A valid object_type but non-existent object_id returns 404."""
        url = self._url(object_type='dcim.device', object_id=999999)
        response = self.client.get(url, **self.header)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_polymorphic_gfk_field_linked_object_appears(self):
        """An object linked via a polymorphic GFK object field appears in the results."""
        cot = CustomObjectsTestCase.create_custom_object_type(name='PolyLOTest', slug='poly-lo-test')
        CustomObjectsTestCase.create_custom_object_type_field(
            cot, name='name', label='Name', type='text', primary=True, required=True
        )
        CustomObjectsTestCase.create_polymorphic_field(
            cot,
            related_object_types=[
                CustomObjectsTestCase.get_device_object_type(),
                CustomObjectsTestCase.get_site_object_type(),
            ],
            name='target',
            label='Target',
            type='object',
        )
        model = cot.get_model()
        self._grant(model)
        linked = model.objects.create(name='poly-gfk-linked', target=self.device)

        url = self._url(object_type='dcim.device', object_id=self.device.pk)
        response = self.client.get(url, **self.header)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data['results']
        self.assertTrue(
            any(r['field_name'] == 'target' and r['object']['id'] == linked.pk for r in results),
            'Polymorphic GFK-linked object not found in linked-objects results',
        )

    def test_polymorphic_m2m_field_linked_object_appears(self):
        """An object linked via a polymorphic multiobject field appears in the results."""
        cot = CustomObjectsTestCase.create_custom_object_type(name='PolyM2MLOTest', slug='poly-m2m-lo-test')
        CustomObjectsTestCase.create_custom_object_type_field(
            cot, name='name', label='Name', type='text', primary=True, required=True
        )
        CustomObjectsTestCase.create_polymorphic_field(
            cot,
            related_object_types=[
                CustomObjectsTestCase.get_device_object_type(),
                CustomObjectsTestCase.get_site_object_type(),
            ],
            name='targets',
            label='Targets',
            type='multiobject',
        )
        model = cot.get_model()
        self._grant(model)
        linked = model.objects.create(name='poly-m2m-linked')
        linked.targets.add(self.device)

        url = self._url(object_type='dcim.device', object_id=self.device.pk)
        response = self.client.get(url, **self.header)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data['results']
        self.assertTrue(
            any(r['field_name'] == 'targets' and r['object']['id'] == linked.pk for r in results),
            'Polymorphic M2M-linked object not found in linked-objects results',
        )

    def test_unviewable_custom_objects_omitted(self):
        """Custom objects the user can't view aren't returned."""
        self.model.objects.create(name='hidden', device=self.device)
        user = create_test_user('linkedobjectsdeviceonly')
        self._grant(Device, user=user)
        header = {'HTTP_AUTHORIZATION': f'Token {create_token(user)}'}

        response = self.client.get(self._url(object_type='dcim.device', object_id=self.device.pk), **header)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual((response.data['count'], response.data['results']), (0, []))

    def test_constrained_permission_limits_custom_objects(self):
        """A constrained view permission limits the custom objects returned, for every field kind."""
        visible = self.model.objects.create(name='visible', device=self.device)
        visible.devices.add(self.device)
        hidden = self.model.objects.create(name='hidden', device=self.device)
        hidden.devices.add(self.device)
        user = create_test_user('linkedobjectsconstrained')
        self._grant(Device, user=user)
        self._grant(self.model, constraints={'name': 'visible'}, user=user)
        header = {'HTTP_AUTHORIZATION': f'Token {create_token(user)}'}

        response = self.client.get(self._url(object_type='dcim.device', object_id=self.device.pk), **header)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            sorted((r['field_name'], r['object']['id']) for r in response.data['results']),
            [('device', visible.pk), ('devices', visible.pk)],
        )

    def test_unviewable_target_returns_404(self):
        """A target object the user can't view is reported as not found."""
        self.model.objects.create(name='linked', device=self.device)
        user = create_test_user('linkedobjectsnodevice')
        self._grant(self.model, user=user)
        header = {'HTTP_AUTHORIZATION': f'Token {create_token(user)}'}

        response = self.client.get(self._url(object_type='dcim.device', object_id=self.device.pk), **header)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_target_without_restrict_needs_model_permission(self):
        """A target whose manager isn't permission-aware needs the model's view permission."""
        dashboard = Dashboard.objects.create(user=self.user)
        url = self._url(object_type='extras.dashboard', object_id=dashboard.pk)

        response = self.client.get(url, **self.header)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

        self._grant(Dashboard)
        response = self.client.get(url, **self.header)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 0)

    def test_response_shape(self):
        """Each result contains custom_object_type, field_name, and object keys."""
        self.model.objects.create(
            name='shape-test',
            device=self.device,
        )
        url = self._url(object_type='dcim.device', object_id=self.device.pk)
        response = self.client.get(url, **self.header)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        result = response.data['results'][0]
        self.assertIn('custom_object_type', result)
        self.assertIn('field_name', result)
        self.assertIn('object', result)
