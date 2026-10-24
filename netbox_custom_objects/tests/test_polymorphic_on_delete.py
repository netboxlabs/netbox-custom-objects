"""
Tests for on_delete_behavior on polymorphic Object fields: what happens to custom objects when
the object their polymorphic field points at is deleted.
"""
from django.contrib.contenttypes.models import ContentType
from django.db.models import ProtectedError
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from core.choices import ObjectChangeActionChoices
from core.models import ObjectChange, ObjectType
from dcim.models import Site
from ipam.models import Prefix
from netbox_custom_objects.choices import ObjectFieldOnDeleteChoices
from netbox_custom_objects.models import CustomObjectTypeField
from utilities.testing import create_test_user

from .base import CustomObjectsTestCase, create_token


class PolymorphicOnDeleteTestCase(CustomObjectsTestCase, TestCase):
    """Set null, cascade and protect, applied when the referenced object is deleted through the ORM."""

    def setUp(self):
        super().setUp()
        self.site_ot = ObjectType.objects.get_for_model(Site)
        self.prefix_ot = ObjectType.objects.get_for_model(Prefix)
        self.site = Site.objects.create(name='Site 1', slug='site-1')
        self.other_site = Site.objects.create(name='Site 2', slug='site-2')

    def _make_type(self, on_delete, slug='widget', targets=None):
        cot = self.create_custom_object_type(name=slug, slug=slug, verbose_name_plural=f'{slug}s')
        self.create_custom_object_type_field(cot, name='name', type='text', primary=True, required=True)
        self.create_polymorphic_field(
            cot, targets or [self.site_ot, self.prefix_ot], name='target', on_delete_behavior=on_delete,
        )
        return cot.get_model()

    def _delete_protected(self, obj):
        """
        Delete *obj*, expecting ProtectedError, and return it.  As for a real foreign key, the
        error comes before the deletion's transaction starts, so the caller's transaction stays
        usable: the assertions that follow run in the same one.
        """
        with self.assertRaises(ProtectedError) as cm:
            obj.delete()
        return cm.exception

    def test_set_null_clears_reference(self):
        model = self._make_type(ObjectFieldOnDeleteChoices.SET_NULL)
        obj = model.objects.create(name='w1', target=self.site)

        self.site.delete()

        obj.refresh_from_db()
        self.assertIsNone(obj.target_content_type_id)
        self.assertIsNone(obj.target_object_id)
        self.assertIsNone(obj.target)

    def test_cascade_deletes_referencing_objects(self):
        model = self._make_type(ObjectFieldOnDeleteChoices.CASCADE)
        doomed = [model.objects.create(name=f'w{i}', target=self.site) for i in (1, 2)]
        survivor = model.objects.create(name='w3', target=self.other_site)

        self.site.delete()

        self.assertFalse(model.objects.filter(pk__in=[obj.pk for obj in doomed]).exists())
        self.assertTrue(model.objects.filter(pk=survivor.pk).exists())

    def test_protect_refuses_deletion(self):
        model = self._make_type(ObjectFieldOnDeleteChoices.PROTECT)
        obj = model.objects.create(name='w1', target=self.site)

        error = self._delete_protected(self.site)

        self.assertEqual({o.pk for o in error.protected_objects}, {obj.pk})
        self.assertTrue(Site.objects.filter(pk=self.site.pk).exists())
        obj.refresh_from_db()
        self.assertEqual(obj.target, self.site)

    def test_protect_ignores_objects_deleted_in_the_same_operation(self):
        """A custom object that a real foreign key's cascade deletes along with the site doesn't protect it."""
        self._make_type(ObjectFieldOnDeleteChoices.PROTECT)
        cot = CustomObjectTypeField.objects.get(custom_object_type__slug='widget', name='target').custom_object_type
        self.create_custom_object_type_field(
            cot, name='site', type='object', related_object_type=self.site_ot,
            on_delete_behavior=ObjectFieldOnDeleteChoices.CASCADE,
        )
        cot.refresh_from_db()
        model = cot.get_model()
        obj = model.objects.create(name='w1', target=self.site, site=self.site)

        self.site.delete()

        self.assertFalse(model.objects.filter(pk=obj.pk).exists())

    def test_protect_allows_deleting_an_unreferenced_object(self):
        model = self._make_type(ObjectFieldOnDeleteChoices.PROTECT)
        model.objects.create(name='w1', target=self.site)

        self.other_site.delete()

        self.assertFalse(Site.objects.filter(pk=self.other_site.pk).exists())

    def test_reference_to_another_type_with_the_same_pk_is_untouched(self):
        model = self._make_type(ObjectFieldOnDeleteChoices.CASCADE)
        obj = model.objects.create(name='w1')
        # Point at a prefix whose pk matches the site's; only the content type tells them apart.
        model.objects.filter(pk=obj.pk).update(
            target_content_type=self.prefix_ot.pk, target_object_id=self.site.pk,
        )

        self.site.delete()

        self.assertTrue(model.objects.filter(pk=obj.pk).exists())

    def test_queryset_delete_applies_to_each_object(self):
        model = self._make_type(ObjectFieldOnDeleteChoices.CASCADE)
        model.objects.create(name='w1', target=self.site)
        model.objects.create(name='w2', target=self.other_site)

        Site.objects.filter(pk__in=[self.site.pk, self.other_site.pk]).delete()

        self.assertFalse(model.objects.exists())

    def test_protect_on_one_field_stops_the_cascade_on_another(self):
        cascading = self._make_type(ObjectFieldOnDeleteChoices.CASCADE, slug='cascading')
        protecting = self._make_type(ObjectFieldOnDeleteChoices.PROTECT, slug='protecting')
        cascaded = cascading.objects.create(name='c1', target=self.site)
        protected = protecting.objects.create(name='p1', target=self.site)

        error = self._delete_protected(self.site)

        self.assertEqual({o.pk for o in error.protected_objects}, {protected.pk})
        self.assertTrue(cascading.objects.filter(pk=cascaded.pk).exists())

    def test_cascade_through_custom_objects(self):
        """A cascade reaches custom objects that point at the custom objects it deletes."""
        inner = self._make_type(ObjectFieldOnDeleteChoices.CASCADE, slug='inner')
        inner_ot = ObjectType.objects.get_for_model(inner)
        outer = self._make_type(ObjectFieldOnDeleteChoices.CASCADE, slug='outer', targets=[inner_ot])
        middle = inner.objects.create(name='m1', target=self.site)
        top = outer.objects.create(name='t1', target=middle)

        self.site.delete()

        self.assertFalse(inner.objects.filter(pk=middle.pk).exists())
        self.assertFalse(outer.objects.filter(pk=top.pk).exists())

    def test_cascade_cycle_deletes_each_object_once(self):
        model = self._make_type(ObjectFieldOnDeleteChoices.CASCADE)
        field = CustomObjectTypeField.objects.get(custom_object_type__slug='widget', name='target')
        field.related_object_types.add(ObjectType.objects.get_for_model(model))
        model = field.custom_object_type.get_model()
        first = model.objects.create(name='a')
        second = model.objects.create(name='b', target=first)
        first.target = second
        first.save()
        self_referencing = model.objects.create(name='c')
        self_referencing.target = self_referencing
        self_referencing.save()

        first.delete()
        self_referencing.delete()

        self.assertFalse(model.objects.exists())

    def test_deleting_a_referenced_custom_object(self):
        target_type = self._make_type(ObjectFieldOnDeleteChoices.SET_NULL, slug='target')
        target_ot = ObjectType.objects.get_for_model(target_type)
        model = self._make_type(ObjectFieldOnDeleteChoices.SET_NULL, slug='source', targets=[target_ot])
        target = target_type.objects.create(name='t1')
        obj = model.objects.create(name='s1', target=target)

        target.delete()

        obj.refresh_from_db()
        self.assertIsNone(obj.target_object_id)

    def test_clean_keeps_the_behaviour_of_a_polymorphic_field(self):
        self._make_type(ObjectFieldOnDeleteChoices.PROTECT)
        field = CustomObjectTypeField.objects.get(custom_object_type__slug='widget', name='target')

        field.full_clean()

        self.assertEqual(field.on_delete_behavior, ObjectFieldOnDeleteChoices.PROTECT)


class PolymorphicOnDeleteRequestTestCase(CustomObjectsTestCase, TestCase):
    """The same behaviour through the UI and the REST API, and the change log it leaves."""

    def setUp(self):
        super().setUp()
        self.user = create_test_user('poly-on-delete')
        self.user.is_superuser = True
        self.user.save()
        self.client.force_login(self.user)
        self.site = Site.objects.create(name='Site 1', slug='site-1')
        self.cot = self.create_custom_object_type(name='widget', slug='widget', verbose_name_plural='widgets')
        self.create_custom_object_type_field(self.cot, name='name', type='text', primary=True, required=True)

    def _add_field(self, on_delete):
        self.create_polymorphic_field(
            self.cot, [ObjectType.objects.get_for_model(Site)], name='target', on_delete_behavior=on_delete,
        )
        self.cot.refresh_from_db()
        return self.cot.get_model()

    def test_ui_delete_is_refused_when_protected(self):
        model = self._add_field(ObjectFieldOnDeleteChoices.PROTECT)
        model.objects.create(name='w1', target=self.site)

        response = self.client.post(reverse('dcim:site_delete', kwargs={'pk': self.site.pk}), {'confirm': True})

        self.assertEqual(response.status_code, status.HTTP_302_FOUND)
        self.assertTrue(Site.objects.filter(pk=self.site.pk).exists())

    def test_api_delete_is_refused_when_protected(self):
        model = self._add_field(ObjectFieldOnDeleteChoices.PROTECT)
        model.objects.create(name='w1', target=self.site)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Token {create_token(self.user)}')

        response = client.delete(reverse('dcim-api:site-detail', kwargs={'pk': self.site.pk}))

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertTrue(Site.objects.filter(pk=self.site.pk).exists())

    def _changes(self, *objects):
        """The change log entries for *objects*, oldest first, as (action, object)."""
        lookup = {(ContentType.objects.get_for_model(obj).pk, obj.pk): obj for obj in objects}
        changes = ObjectChange.objects.filter(
            changed_object_id__in=[obj.pk for obj in objects]
        ).order_by('time', 'pk')
        return [
            (change.action, lookup[key])
            for change in changes
            if (key := (change.changed_object_type_id, change.changed_object_id)) in lookup
        ]

    def test_cascade_is_logged_before_the_deletion_that_caused_it(self):
        """A branch revert replays deletions in reverse, so it restores the site before the custom object."""
        model = self._add_field(ObjectFieldOnDeleteChoices.CASCADE)
        obj = model.objects.create(name='w1', target=self.site)

        self.client.post(reverse('dcim:site_delete', kwargs={'pk': self.site.pk}), {'confirm': True})

        self.assertFalse(model.objects.filter(pk=obj.pk).exists())
        self.assertEqual(
            self._changes(self.site, obj),
            [(ObjectChangeActionChoices.ACTION_DELETE, obj), (ObjectChangeActionChoices.ACTION_DELETE, self.site)],
        )

    def test_set_null_is_logged_as_an_update(self):
        model = self._add_field(ObjectFieldOnDeleteChoices.SET_NULL)
        obj = model.objects.create(name='w1', target=self.site)

        self.client.post(reverse('dcim:site_delete', kwargs={'pk': self.site.pk}), {'confirm': True})

        self.assertEqual(
            self._changes(self.site, obj),
            [(ObjectChangeActionChoices.ACTION_UPDATE, obj), (ObjectChangeActionChoices.ACTION_DELETE, self.site)],
        )
        change = ObjectChange.objects.get(changed_object_id=obj.pk, action=ObjectChangeActionChoices.ACTION_UPDATE)
        self.assertEqual(change.prechange_data['target_object_id'], self.site.pk)
        self.assertIsNone(change.postchange_data['target_object_id'])
