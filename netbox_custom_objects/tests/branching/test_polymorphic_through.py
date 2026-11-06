"""
A polymorphic multi-object field's through table in a branch.

netbox-branching copies the through tables of a model's M2M fields into a branch, but a
polymorphic multi-object field isn't an M2M field on ``_meta``, so the branch needs its own
copy of the table made for it.  Without one, the branch connection's search path falls
through to main's table, and changes made in the branch are written to main.
"""

from unittest import mock

from django.contrib.contenttypes.models import ContentType
from django.db import connection as main_conn

from core.models import ObjectType
from dcim.models import Site
from netbox.plugins import get_plugin_config
from netbox_custom_objects.mixin_migration import heal_branch
from netbox_custom_objects.models import CustomObjectType, CustomObjectTypeField, _table_exists

from .base import HAS_BRANCHING, BranchingTestBase, _make_request, _provision_branch, _TestBase

if HAS_BRANCHING:
    from netbox_branching.choices import BranchStatusChoices
    from netbox_branching.models import Branch
    from netbox_branching.utilities import activate_branch

    from netbox.context_managers import event_tracking


class PolymorphicThroughTableBranchTestCase(BranchingTestBase, _TestBase):
    def setUp(self):
        super().setUp()
        self.site_ct = ContentType.objects.get_for_model(Site)
        with event_tracking(self.request):
            self.site_a = Site.objects.create(name='Site A', slug='site-a')
            self.site_b = Site.objects.create(name='Site B', slug='site-b')
            self.cot = CustomObjectType.objects.create(name='holder', slug='holders')
            CustomObjectTypeField.objects.create(custom_object_type=self.cot, name='name', type='text', primary=True)
        self.addCleanup(CustomObjectType.clear_model_cache)

    def _add_field(self):
        field = CustomObjectTypeField.objects.create(
            custom_object_type=CustomObjectType.objects.get(pk=self.cot.pk),
            name='targets',
            type='multiobject',
            is_polymorphic=True,
        )
        field.related_object_types.set([ObjectType.objects.get_for_model(Site)])
        return field

    def _model(self):
        return CustomObjectType.objects.get(pk=self.cot.pk).get_model()

    def _create_holder(self):
        """In main: the field, and a holder targeting both sites."""
        with event_tracking(self.request):
            self.field = self._add_field()
            holder = self._model().objects.create(name='holder-1')
            holder.targets.add(self.site_a, self.site_b)
        self.holder_pk = holder.pk

    def _rows(self, schema, holder_pk=None):
        """A holder's target site ids in *schema*'s own copy of the through table, or None if it has none."""
        table = self.field.through_table_name
        if not _table_exists(table, main_conn, schema=schema):
            return None
        with main_conn.cursor() as cursor:
            cursor.execute(
                f'SELECT object_id FROM "{schema}"."{table}" '
                'WHERE source_id = %s AND content_type_id = %s ORDER BY object_id',
                [holder_pk or self.holder_pk, self.site_ct.pk],
            )
            return [row[0] for row in cursor.fetchall()]

    def _main_rows(self, holder_pk=None):
        return self._rows(get_plugin_config('netbox_branching', 'main_schema'), holder_pk)

    def _remove_site_b_in(self, branch):
        with activate_branch(branch), event_tracking(_make_request(self.user)):
            self._model().objects.get(pk=self.holder_pk).targets.remove(self.site_b)

    def test_provisioned_branch_has_its_own_copy(self):
        self._create_holder()
        branch = _provision_branch('Poly Branch', 'iterative', self.user)

        self._remove_site_b_in(branch)

        self.assertEqual(self._main_rows(), [self.site_a.pk, self.site_b.pk])
        self.assertEqual(self._rows(branch.schema_name), [self.site_a.pk])

    def test_field_delete_in_branch_leaves_main_table(self):
        self._create_holder()
        branch = _provision_branch('Poly Branch', 'iterative', self.user)

        with activate_branch(branch), event_tracking(_make_request(self.user)):
            CustomObjectTypeField.objects.get(pk=self.field.pk).delete()

        self.assertIsNone(self._rows(branch.schema_name))
        self.assertEqual(self._main_rows(), [self.site_a.pk, self.site_b.pk])
        holder = self._model().objects.get(pk=self.holder_pk)
        self.assertEqual(sorted(site.pk for site in holder.targets.all()), [self.site_a.pk, self.site_b.pk])

    def test_heal_copies_table_missing_from_existing_branch(self):
        self._create_holder()
        branch = _provision_branch('Poly Branch', 'iterative', self.user)
        # As provisioned before the fix: no copy of the table.
        with main_conn.cursor() as cursor:
            cursor.execute(f'DROP TABLE IF EXISTS "{branch.schema_name}"."{self.field.through_table_name}"')

        heal_branch(branch, verbosity=0)

        self._remove_site_b_in(branch)
        self.assertEqual(self._main_rows(), [self.site_a.pk, self.site_b.pk])
        self.assertEqual(self._rows(branch.schema_name), [self.site_a.pk])

    def test_heal_leaves_out_rows_of_objects_missing_from_branch(self):
        self._create_holder()
        branch = _provision_branch('Poly Branch', 'iterative', self.user)
        with event_tracking(self.request):
            newer = self._model().objects.create(name='holder-2')
            newer.targets.add(self.site_a)
        with main_conn.cursor() as cursor:
            cursor.execute(f'DROP TABLE IF EXISTS "{branch.schema_name}"."{self.field.through_table_name}"')

        heal_branch(branch, verbosity=0)

        self.assertEqual(self._main_rows(newer.pk), [self.site_a.pk])
        self.assertEqual(self._rows(branch.schema_name, newer.pk), [])
        self.assertEqual(self._rows(branch.schema_name), [self.site_a.pk, self.site_b.pk])

    def test_failed_copy_fails_provisioning(self):
        self._create_holder()
        branch = Branch(name='Poly Branch', merge_strategy='iterative')
        branch.save(provision=False)
        with (
            mock.patch(
                'netbox_custom_objects.mixin_migration.copy_polymorphic_through_tables',
                side_effect=RuntimeError('copy failed'),
            ),
            self.assertRaisesMessage(RuntimeError, 'copy failed'),
        ):
            branch.provision(user=self.user)

        branch.refresh_from_db()
        self.assertEqual(branch.status, BranchStatusChoices.FAILED)

    def test_field_synced_into_branch_gets_its_own_table(self):
        branch = _provision_branch('Poly Branch', 'iterative', self.user)
        self._create_holder()
        branch.sync(user=self.user, commit=True)

        self._remove_site_b_in(branch)
        self.assertEqual(self._main_rows(), [self.site_a.pk, self.site_b.pk])
        self.assertEqual(self._rows(branch.schema_name), [self.site_a.pk])
