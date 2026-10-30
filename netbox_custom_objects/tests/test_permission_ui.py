"""
Custom-object buttons, menu buttons and tabs show only what the user is permitted to use, as in core.
"""

from django.test import TestCase
from django.urls import reverse

from core.models import ObjectType
from extras.models import JournalEntry
from netbox_custom_objects.models import CustomObjectType, CustomObjectTypeField
from netbox_custom_objects.navigation import CustomObjectTypeMenuItems, custom_object_type_plugin_menu_item
from users.models import ObjectPermission

from .base import CustomObjectsTestCase

URL_PREFIX = 'plugins:netbox_custom_objects'


class PermissionUITestCase(CustomObjectsTestCase, TestCase):
    def setUp(self):
        super().setUp()
        self.cot = self.create_simple_custom_object_type(name='zone', slug='zones')
        self.model = self.cot.get_model()
        self.obj = self.model.objects.create(name='Alpha')

    def _url(self, action, **kwargs):
        return reverse(f'{URL_PREFIX}:customobject_{action}', kwargs={'custom_object_type': 'zones', **kwargs})

    def _grant(self, *models, actions=('view',)):
        perm = ObjectPermission.objects.create(name=f'perm-{"-".join(actions)}', actions=list(actions))
        perm.users.add(self.user)
        perm.object_types.add(*(ObjectType.objects.get_for_model(model) for model in models))

    def _superuser(self):
        self.user.is_superuser = True
        self.user.save()


class ListButtonsTestCase(PermissionUITestCase):
    ACTION_URLS = ('add', 'bulk_import', 'bulk_edit', 'bulk_delete')

    def test_view_only_user_sees_only_export(self):
        self._grant(self.model)
        response = self.client.get(self._url('list'))
        self.assertEqual(response.status_code, 200)
        for action in self.ACTION_URLS:
            with self.subTest(action=action):
                self.assertNotContains(response, f'"{self._url(action)}')
        self.assertContains(response, 'Export')

    def test_superuser_sees_every_action(self):
        self._superuser()
        response = self.client.get(self._url('list'))
        for action in self.ACTION_URLS:
            with self.subTest(action=action):
                self.assertContains(response, f'"{self._url(action)}')

    def test_row_actions_include_changelog(self):
        self._superuser()
        self.assertContains(self.client.get(self._url('list')), f'href="{self._url("changelog", pk=self.obj.pk)}')


class MenuButtonsTestCase(PermissionUITestCase):
    def test_buttons_require_add_permission(self):
        add_permission = f'netbox_custom_objects.add_{self.model._meta.model_name}'
        group = CustomObjectTypeMenuItems(group_name=self.cot.group_name)
        (item,) = [item for item in group if str(item.url) == self._url('list')]
        self.assertEqual([button.permissions for button in item.buttons], [[add_permission], [add_permission]])

    def test_type_add_button_requires_add_permission(self):
        (button,) = custom_object_type_plugin_menu_item.buttons
        self.assertEqual(button.permissions, ['netbox_custom_objects.add_customobjecttype'])


class DetailTabsTestCase(PermissionUITestCase):
    TAB_ACTIONS = ('contacts', 'journal', 'changelog')

    def test_view_only_user_sees_no_feature_tabs(self):
        self._grant(self.model)
        response = self.client.get(self.obj.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        for action in self.TAB_ACTIONS:
            with self.subTest(action=action):
                self.assertNotContains(response, self._url(action, pk=self.obj.pk))

    def test_config_context_tab_needs_only_view_access(self):
        # Like core's Config Context tab, it has no permission of its own.
        cot = self.create_simple_custom_object_type(name='rack', slug='racks', config_context_enabled=True)
        model = cot.get_model()
        obj = model.objects.create(name='R1')
        self._grant(model, self.model)
        url = reverse(f'{URL_PREFIX}:customobject_configcontext', kwargs={'custom_object_type': 'racks', 'pk': obj.pk})
        self.assertContains(self.client.get(obj.get_absolute_url()), f'href="{url}"')
        self.assertNotContains(self.client.get(self.obj.get_absolute_url()), 'config-context')

    def test_tabs_in_weight_order_with_badges(self):
        self._superuser()
        JournalEntry.objects.create(assigned_object=self.obj, comments='note')
        content = self.client.get(self.obj.get_absolute_url()).content.decode()
        positions = [content.index(f'href="{self._url(action, pk=self.obj.pk)}"') for action in self.TAB_ACTIONS]
        self.assertEqual(positions, sorted(positions))
        journal = content[positions[1] : positions[2]]
        self.assertIn('class="badge', journal)


class TypePageFieldButtonsTestCase(PermissionUITestCase):
    def setUp(self):
        super().setUp()
        self.field = self.cot.fields.get(name='name')

    def _field_url(self, action):
        return reverse(f'{URL_PREFIX}:customobjecttypefield_{action}', kwargs={'pk': self.field.pk})

    def test_buttons_follow_field_permissions(self):
        self._grant(CustomObjectType, CustomObjectTypeField)
        self._grant(CustomObjectTypeField, actions=('change',))
        response = self.client.get(self.cot.get_absolute_url())
        self.assertContains(response, self._field_url('edit'))
        self.assertNotContains(response, self._field_url('delete'))

    def test_no_buttons_for_view_only_user(self):
        self._grant(CustomObjectType, CustomObjectTypeField)
        response = self.client.get(self.cot.get_absolute_url())
        self.assertNotContains(response, self._field_url('edit'))
        self.assertNotContains(response, self._field_url('delete'))
