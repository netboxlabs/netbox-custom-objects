"""
Tests for constraints over a polymorphic Object field in the custom object edit form: a violation
must come back as a form error, as it does for other fields, not a database error.
"""
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse

from core.models import ObjectType
from dcim.models import Site
from ipam.models import Prefix
from netbox_custom_objects.models import CustomObjectTypeConstraint
from utilities.testing import create_test_user

from .base import CustomObjectsTestCase


class PolymorphicConstraintFormTestCase(CustomObjectsTestCase, TestCase):

    def setUp(self):
        super().setUp()
        self.user = create_test_user('poly-constraint-form')
        self.user.is_superuser = True
        self.user.save()
        self.client.force_login(self.user)

        self.site = Site.objects.create(name='Site 1', slug='site-1')
        self.other_site = Site.objects.create(name='Site 2', slug='site-2')
        self.site_ct = ContentType.objects.get_for_model(Site)
        self.cot = self.create_custom_object_type(name='widget', slug='widget', verbose_name_plural='widgets')
        self.name = self.create_custom_object_type_field(self.cot, name='name', type='text', primary=True)
        self.action = self.create_custom_object_type_field(self.cot, name='action', type='text')
        self.target = self.create_polymorphic_field(
            self.cot, [ObjectType.objects.get_for_model(Site), ObjectType.objects.get_for_model(Prefix)],
            name='target',
        )

    def _add_constraint(self, **kwargs):
        constraint = CustomObjectTypeConstraint(custom_object_type=self.cot, **kwargs)
        constraint.full_clean()
        constraint.save()
        self.cot.refresh_from_db()
        return self.cot.get_model()

    def _add_unique_constraint(self):
        return self._add_constraint(
            name='name_unique_per_target', field_schema_ids=[self.name.schema_id, self.target.schema_id],
        )

    def _post(self, data, obj=None):
        if obj is None:
            url = reverse('plugins:netbox_custom_objects:customobject_add', kwargs={'custom_object_type': 'widget'})
        else:
            url = reverse(
                'plugins:netbox_custom_objects:customobject_edit',
                kwargs={'custom_object_type': 'widget', 'pk': obj.pk},
            )
        return self.client.post(url, data)

    def _data(self, name, site=None, **extra):
        data = {'name': name, **extra}
        if site is not None:
            data.update({'target__ct': self.site_ct.pk, 'target__obj': site.pk})
        return data

    def test_unique_violation_is_a_form_error(self):
        model = self._add_unique_constraint()
        model.objects.create(name='w1', target=self.site)

        response = self._post(self._data('w1', self.site))

        self.assertEqual(response.status_code, 200)
        self.assertIn('already exists', ' '.join(response.context['form'].non_field_errors()))
        self.assertEqual(model.objects.count(), 1)

    def test_same_name_with_another_target_is_saved(self):
        model = self._add_unique_constraint()
        model.objects.create(name='w1', target=self.site)

        response = self._post(self._data('w1', self.other_site))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(model.objects.count(), 2)

    def test_unique_violation_on_edit_is_a_form_error(self):
        model = self._add_unique_constraint()
        model.objects.create(name='w1', target=self.site)
        obj = model.objects.create(name='w1', target=self.other_site)

        response = self._post(self._data('w1', self.site), obj=obj)

        self.assertEqual(response.status_code, 200)
        self.assertIn('already exists', ' '.join(response.context['form'].non_field_errors()))
        obj.refresh_from_db()
        self.assertEqual(obj.target, self.other_site)

    def test_check_violation_is_a_form_error(self):
        model = self._add_constraint(
            name='remark_has_no_target', type='check',
            condition=[{'field': self.action.schema_id, 'lookup': 'exact', 'value': 'remark'}],
            rule=[{'field': self.target.schema_id, 'lookup': 'isnull', 'value': True}],
            error_message='A remark has no target.',
        )

        response = self._post(self._data('w1', self.site, action='remark'))

        self.assertEqual(response.status_code, 200)
        self.assertIn('A remark has no target.', response.context['form'].non_field_errors())
        self.assertFalse(model.objects.exists())

    def test_type_without_object_is_a_field_error_only(self):
        """A type chosen without an object fails on the field itself; the constraint isn't checked."""
        model = self._add_unique_constraint()

        response = self._post({'name': 'w1', 'target__ct': self.site_ct.pk})

        self.assertEqual(response.status_code, 200)
        form = response.context['form']
        self.assertIn('target__obj', form.errors)
        self.assertEqual(form.non_field_errors(), [])
        self.assertFalse(model.objects.exists())
