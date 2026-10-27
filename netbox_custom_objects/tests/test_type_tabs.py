"""
The Custom Object Type's Fields and Constraints tabs behave like core child-object tabs.
"""

from urllib.parse import parse_qs, urlparse

from django.test import TestCase
from django.urls import reverse

from core.models import ObjectType
from users.models import ObjectPermission

from netbox_custom_objects.models import CustomObjectType, CustomObjectTypeConstraint

from .base import CustomObjectsTestCase

URL_PREFIX = 'plugins:netbox_custom_objects'


class TypeTabTestCase(CustomObjectsTestCase, TestCase):
    def setUp(self):
        super().setUp()
        self.user.is_superuser = True
        self.user.save()
        self.cot = self.create_custom_object_type(name='zone', slug='zones')
        self.name = self.create_custom_object_type_field(
            self.cot,
            name='name',
            label='Name',
            type='text',
            primary=True,
            required=True,
        )
        self.code = self.create_custom_object_type_field(self.cot, name='code', label='Code', type='text')
        self.notes = self.create_custom_object_type_field(self.cot, name='notes', label='Notes', type='text')

    def _tab(self, name):
        return reverse(f'{URL_PREFIX}:customobjecttype_{name}', kwargs={'pk': self.cot.pk})

    def _constraint(self, name, *fields):
        return CustomObjectTypeConstraint.objects.create(
            custom_object_type=self.cot,
            name=name,
            field_schema_ids=[f.schema_id for f in fields],
        )


class TabControlsTestCase(TypeTabTestCase):
    def test_tabs_keep_type_buttons_and_add_menu(self):
        field_add = reverse(f'{URL_PREFIX}:customobjecttypefield_add')
        constraint_add = reverse(f'{URL_PREFIX}:customobjecttypeconstraint_add')
        for tab in ('fields', 'constraints'):
            with self.subTest(tab=tab):
                response = self.client.get(self._tab(tab))
                edit_url = reverse(f'{URL_PREFIX}:customobjecttype_edit', kwargs={'pk': self.cot.pk})
                self.assertContains(response, edit_url)
                self.assertContains(
                    response,
                    f'{field_add}?custom_object_type={self.cot.pk}&return_url={self._tab("fields")}"',
                )
                self.assertContains(
                    response,
                    f'{constraint_add}?custom_object_type={self.cot.pk}&return_url={self._tab("constraints")}"',
                )
                self.assertContains(response, 'placeholder="Quick search"')

    def test_add_menu_gated_per_child_type(self):
        self.user.is_superuser = False
        self.user.save()
        perm = ObjectPermission.objects.create(name='perm', actions=['view', 'change', 'add'])
        perm.users.add(self.user)
        perm.object_types.add(
            ObjectType.objects.get_for_model(CustomObjectType),
            ObjectType.objects.get_for_model(CustomObjectTypeConstraint),
        )
        response = self.client.get(self.cot.get_absolute_url())
        self.assertContains(response, reverse(f'{URL_PREFIX}:customobjecttypeconstraint_add'))
        self.assertNotContains(response, reverse(f'{URL_PREFIX}:customobjecttypefield_add'))

    def test_quick_search_filters_fields(self):
        response = self.client.get(self._tab('fields'), {'q': 'not'}, HTTP_HX_REQUEST='true')

        def edit_url(field):
            return reverse(f'{URL_PREFIX}:customobjecttypefield_edit', kwargs={'pk': field.pk})

        self.assertContains(response, edit_url(self.notes))
        self.assertNotContains(response, edit_url(self.code))

    def test_bulk_delete_only_on_constraints(self):
        self._constraint('unique_code_notes', self.code, self.notes)
        bulk_delete = reverse(f'{URL_PREFIX}:customobjecttypeconstraint_bulk_delete')
        response = self.client.get(self._tab('constraints'))
        self.assertContains(response, 'name="pk"')
        self.assertContains(response, bulk_delete)
        response = self.client.get(self._tab('fields'))
        self.assertNotContains(response, 'name="pk"')


class ConstraintActionsTestCase(TypeTabTestCase):
    def test_create_and_add_another_keeps_type_and_return_url(self):
        url = reverse(f'{URL_PREFIX}:customobjecttypeconstraint_add')
        return_url = self._tab('constraints')
        response = self.client.post(
            f'{url}?custom_object_type={self.cot.pk}&return_url={return_url}',
            {
                'name': 'unique_code_notes',
                'type': 'unique',
                'field_schema_ids': [self.code.pk, self.notes.pk],
                'nulls_distinct': 'on',
                '_addanother': '',
            },
        )
        self.assertEqual(response.status_code, 302)
        location = urlparse(response['Location'])
        self.assertEqual(location.path, url)
        self.assertEqual(
            parse_qs(location.query),
            {'custom_object_type': [str(self.cot.pk)], 'return_url': [return_url]},
        )

    def test_bulk_delete(self):
        constraint = self._constraint('unique_code_notes', self.code, self.notes)
        return_url = self._tab('constraints')
        response = self.client.post(
            f'{reverse(f"{URL_PREFIX}:customobjecttypeconstraint_bulk_delete")}?return_url={return_url}',
            {'pk': [constraint.pk], 'confirm': True, '_confirm': True},
        )
        self.assertRedirects(response, return_url, fetch_redirect_response=False)
        self.assertFalse(CustomObjectTypeConstraint.objects.filter(pk=constraint.pk).exists())


class FieldDeleteTestCase(TypeTabTestCase):
    def test_delete_form_offers_changelog_message(self):
        response = self.client.get(reverse(f'{URL_PREFIX}:customobjecttypefield_delete', kwargs={'pk': self.notes.pk}))
        self.assertContains(response, 'name="changelog_message"')
        self.assertContains(response, 'class="modal modal-lg"')
