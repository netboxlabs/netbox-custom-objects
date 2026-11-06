"""
Schema changes in a branch while netbox-branching's ``exempt_models`` includes the schema models.

An exempt model's rows are written to main, but the DDL runs on the branch's tables, so its
schema changes are refused in a branch. Models that aren't exempt, custom object data, and
schema changes with no branch active are unaffected.
"""

from django.conf import settings
from django.db import connection as main_conn
from django.db import connections
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from extras.models import CustomFieldChoiceSet
from netbox_custom_objects.models import (
    CustomObjectType,
    CustomObjectTypeConstraint,
    CustomObjectTypeField,
    SchemaChangeRefused,
)
from netbox_custom_objects.schema.exporter import export_cot

from ..base import create_token
from .base import HAS_BRANCHING, BranchingTestBase, _make_request, _provision_branch, _TestBase

if HAS_BRANCHING:
    from netbox_branching.utilities import activate_branch

    from netbox.context_managers import event_tracking

SCHEMA_MODELS = [
    'netbox_custom_objects.customobjecttype',
    'netbox_custom_objects.customobjecttypefield',
    'netbox_custom_objects.customobjecttypeconstraint',
]
# The exemption earlier docs recommended, from before constraints existed
OLD_DOCS_MODELS = SCHEMA_MODELS[:2]


def _exempt(models):
    plugins_config = {**settings.PLUGINS_CONFIG}
    plugins_config['netbox_branching'] = {**plugins_config.get('netbox_branching', {}), 'exempt_models': models}
    return override_settings(PLUGINS_CONFIG=plugins_config)


def _db_constraint_names(conn, cot):
    with conn.cursor() as cursor:
        return set(conn.introspection.get_constraints(cursor, cot.get_database_table_name()))


class ExemptSchemaModelsTestCase(BranchingTestBase, _TestBase):
    def setUp(self):
        super().setUp()
        with event_tracking(self.request):
            self.choice_set = CustomFieldChoiceSet.objects.create(name='Colors', extra_choices=[['red', 'Red']])
            self.cot = CustomObjectType.objects.create(name='server', slug='servers')
            self.name_field = CustomObjectTypeField.objects.create(
                custom_object_type=self.cot, name='name', label='Name', type='text', primary=True
            )
            self.color_field = CustomObjectTypeField.objects.create(
                custom_object_type=self.cot, name='color', label='Color', type='select', choice_set=self.choice_set
            )
            self.constraint = CustomObjectTypeConstraint.objects.create(
                custom_object_type=CustomObjectType.objects.get(pk=self.cot.pk),
                name='unique_name',
                field_schema_ids=[self.name_field.schema_id],
            )
            self.cot.get_model().objects.create(name='web1')
        self.branch = _provision_branch('Exempt Branch', 'iterative', self.user)
        self.addCleanup(CustomObjectType.clear_model_cache)

    def _name_and_color(self):
        return [self.name_field.schema_id, self.color_field.schema_id]

    def _main_state(self):
        cot = CustomObjectType.objects.get(pk=self.cot.pk)
        return (
            sorted(CustomObjectType.objects.values_list('slug', flat=True)),
            sorted(cot.fields.values_list('name', 'label')),
            sorted(CustomObjectTypeConstraint.objects.filter(custom_object_type=cot).values_list('name', flat=True)),
            # Every column, so a column missing from main's table fails.
            [str(obj) for obj in cot.get_model().objects.all()],
        )

    def test_schema_changes_of_exempt_models_are_refused_in_a_branch(self):
        before = self._main_state()

        def relabel_field():
            field = CustomObjectTypeField.objects.get(pk=self.name_field.pk)
            field.label = 'Hostname'
            field.save()

        with _exempt(SCHEMA_MODELS), activate_branch(self.branch), event_tracking(_make_request(self.user)):
            cot = CustomObjectType.objects.get(pk=self.cot.pk)
            for name, change in (
                ('create type', lambda: CustomObjectType.objects.create(name='switch', slug='switches')),
                (
                    'add field',
                    lambda: CustomObjectTypeField.objects.create(
                        custom_object_type=cot, name='rack', label='Rack', type='text'
                    ),
                ),
                ('change field', relabel_field),
                ('delete field', lambda: CustomObjectTypeField.objects.get(pk=self.name_field.pk).delete()),
                (
                    'add constraint',
                    lambda: CustomObjectTypeConstraint.objects.create(
                        custom_object_type=cot, name='unique_name_2', field_schema_ids=[self.name_field.schema_id]
                    ),
                ),
                ('delete constraint', lambda: CustomObjectTypeConstraint.objects.get(pk=self.constraint.pk).delete()),
                ('delete type', lambda: CustomObjectType.objects.get(pk=self.cot.pk).delete()),
            ):
                with self.subTest(change=name), self.assertRaisesMessage(SchemaChangeRefused, ', '.join(SCHEMA_MODELS)):
                    change()

        self.assertEqual(self._main_state(), before)

    def test_other_changes_still_work(self):
        with _exempt(OLD_DOCS_MODELS):
            with activate_branch(self.branch), event_tracking(_make_request(self.user)):
                cot = CustomObjectType.objects.get(pk=self.cot.pk)
                model = cot.get_model()
                model.objects.create(name='web2', color='red')
                model.objects.filter(name='web1').update(color='red')
                self.assertEqual(sorted(model.objects.values_list('name', 'color')), [('web1', 'red'), ('web2', 'red')])
                # Bumps the cache_timestamp of the types using the choice set.
                self.choice_set.extra_choices = [['red', 'Red'], ['blue', 'Blue']]
                self.choice_set.save()
                # Runs no DDL, so it's written to main like any change to an exempt model.
                cot.description = 'Physical servers'
                cot.save()
                # Its model isn't exempt, so it stays in the branch with its DDL.
                constraint = CustomObjectTypeConstraint.objects.create(
                    custom_object_type=cot, name='unique_name_color', field_schema_ids=self._name_and_color()
                )
                self.assertIn(constraint.db_name, _db_constraint_names(connections[self.branch.connection_name], cot))

            with event_tracking(self.request):
                CustomObjectTypeField.objects.create(
                    custom_object_type=CustomObjectType.objects.get(pk=self.cot.pk),
                    name='rack',
                    label='Rack',
                    type='text',
                )

        cot = CustomObjectType.objects.get(pk=self.cot.pk)
        self.assertEqual(cot.description, 'Physical servers')
        self.assertFalse(CustomObjectTypeConstraint.objects.filter(name='unique_name_color').exists())
        self.assertNotIn(constraint.db_name, _db_constraint_names(main_conn, cot))
        # Main has its own objects, and the field added in main.
        objects = [(obj.name, obj.color, obj.rack) for obj in cot.get_model().objects.all()]
        self.assertEqual(objects, [('web1', None, None)])

    def test_sync_replays_schema_changes_of_models_that_are_not_exempt(self):
        with _exempt(OLD_DOCS_MODELS):
            with event_tracking(self.request):
                constraint = CustomObjectTypeConstraint.objects.create(
                    custom_object_type=CustomObjectType.objects.get(pk=self.cot.pk),
                    name='unique_name_color',
                    field_schema_ids=self._name_and_color(),
                )
            self.branch.sync(user=self.user, commit=True)

            with activate_branch(self.branch):
                self.assertTrue(CustomObjectTypeConstraint.objects.filter(pk=constraint.pk).exists())
                cot = CustomObjectType.objects.get(pk=self.cot.pk)
                self.assertIn(constraint.db_name, _db_constraint_names(connections[self.branch.connection_name], cot))

    def test_rest_api_and_schema_apply_return_400_in_a_branch(self):
        self.user.is_superuser = True
        self.user.save()
        client = APIClient()
        headers = {
            'HTTP_AUTHORIZATION': f'Token {create_token(self.user)}',
            'HTTP_X_NETBOX_BRANCH': self.branch.schema_id,
        }
        type_def = export_cot(self.cot)
        type_def['fields'].append({'id': self.cot.next_schema_id + 1, 'name': 'rack', 'type': 'text'})
        fields_before = sorted(self.cot.fields.values_list('name', flat=True))

        with _exempt(OLD_DOCS_MODELS):
            response = client.post(
                reverse('plugins-api:netbox_custom_objects-api:customobjecttypefield-list'),
                {'custom_object_type': self.cot.pk, 'name': 'rack', 'label': 'Rack', 'type': 'text'},
                format='json',
                **headers,
            )
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.content)
            self.assertIn(', '.join(OLD_DOCS_MODELS), response.json()['detail'])

            response = client.post(
                reverse('plugins-api:netbox_custom_objects-api:schema-apply'),
                {'schema': {'schema_version': '1', 'types': [type_def]}},
                format='json',
                **headers,
            )
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.content)
            self.assertEqual(response.json()['error'], 'exempt_from_branching')
            self.assertIn(', '.join(OLD_DOCS_MODELS), response.json()['detail'])

        self.assertEqual(sorted(self.cot.fields.values_list('name', flat=True)), fields_before)
