"""
Applying a schema document enforces object-level permissions, as the UI and REST views do.
"""

from core.models import ObjectType
from rest_framework import status
from users.models import ObjectPermission

from netbox_custom_objects.models import CustomObjectType, CustomObjectTypeField
from netbox_custom_objects.schema.exporter import export_cot

from .test_schema_api import _SchemaAPIBase


class SchemaApplyPermissionTestCase(_SchemaAPIBase):

    def setUp(self):
        super().setUp()
        self.allowed = self.create_custom_object_type(name='allowed', slug='allowed', description='keep')
        self.other = self.create_custom_object_type(name='other', slug='other', description='keep')

    def _grant(self, model, actions, constraints=None):
        perm = ObjectPermission.objects.create(
            name=f'{model._meta.model_name}-{"-".join(actions)}', actions=actions, constraints=constraints,
        )
        perm.users.add(self.user)
        perm.object_types.add(ObjectType.objects.get_for_model(model))

    def _apply(self, *type_defs, allow_destructive=False):
        schema_doc = {"schema_version": "1", "types": list(type_defs)}
        return self.client.post(
            self.apply_url, data=self._apply_body(schema_doc, allow_destructive=allow_destructive), format="json",
        )

    def _with_description(self, cot, description):
        type_def = export_cot(cot)
        type_def["description"] = description
        return type_def

    def _field_perms(self, *actions):
        self._grant(CustomObjectTypeField, list(actions))

    def test_change_outside_constraint_refused(self):
        self._grant(CustomObjectType, ['add', 'change'], constraints={'slug': 'allowed'})
        response = self._apply(self._with_description(self.other, 'changed'))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.other.refresh_from_db()
        self.assertEqual(self.other.description, 'keep')

        response = self._apply(self._with_description(self.allowed, 'changed'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.allowed.refresh_from_db()
        self.assertEqual(self.allowed.description, 'changed')

    def test_change_leaving_constraint_rolled_back(self):
        self._grant(CustomObjectType, ['add', 'change'], constraints={'description': 'keep'})
        response = self._apply(self._with_description(self.allowed, 'changed'))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.allowed.refresh_from_db()
        self.assertEqual(self.allowed.description, 'keep')

    def test_new_type_outside_add_constraint_not_created(self):
        self._grant(CustomObjectType, ['add', 'change'], constraints={'slug__startswith': 'ok-'})
        response = self._apply({"name": "bad_new", "slug": "bad-new"})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(CustomObjectType.objects.filter(slug='bad-new').exists())

    def test_adding_field_needs_field_add_permission(self):
        self._grant(CustomObjectType, ['add', 'change'])
        type_def = export_cot(self.allowed)
        type_def.setdefault("fields", []).append(
            {"id": self._next_field_id(self.allowed), "name": "added", "type": "text"}
        )
        response = self._apply(type_def)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(self.allowed.fields.filter(name='added').exists())

        self._field_perms('add')
        self.assertEqual(self._apply(type_def).status_code, status.HTTP_200_OK)
        self.assertTrue(self.allowed.fields.filter(name='added').exists())

    def test_removing_field_needs_field_delete_permission(self):
        self._grant(CustomObjectType, ['add', 'change'])
        self._field_perms('add', 'change')
        field = self.create_custom_object_type_field(self.allowed, name='victim', type='text')
        type_def = export_cot(self.allowed)
        type_def["fields"] = []
        type_def["removed_fields"] = [{"id": field.schema_id, "name": "victim", "type": "text"}]
        response = self._apply(type_def, allow_destructive=True)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(self.allowed.fields.filter(pk=field.pk).exists())
