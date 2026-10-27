from unittest import mock

from django.db import IntegrityError, OperationalError, connection, transaction
from django.test import TestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext

from core.models import ObjectType
from dcim.models import Site
from netbox_custom_objects.choices import ObjectFieldOnDeleteChoices
from netbox_custom_objects.models import CustomObjectType, CustomObjectTypeField
from netbox_custom_objects.tests.base import CustomObjectsTestCase, TransactionCleanupMixin, _fk_constraints
from tenancy.models import Tenant
from users.models import Owner


class ObjectFieldFKConstraintTestCase(CustomObjectsTestCase, TestCase):
    """The DB-level FK for a non-polymorphic Object field, created after the field commits."""

    def setUp(self):
        super().setUp()
        self.cot = self.create_simple_custom_object_type(name='fkc', slug='fkcs')
        self.table = self.cot.get_database_table_name()

    def _constraints(self, column='site_id'):
        return _fk_constraints(self.table, column)

    def _create_field(self, on_delete=ObjectFieldOnDeleteChoices.SET_NULL):
        with self.captureOnCommitCallbacks(execute=True):
            return self.create_custom_object_type_field(
                self.cot,
                name='site',
                type='object',
                related_object_type=self.get_site_object_type(),
                on_delete_behavior=on_delete,
            )

    def _set_on_delete(self, field, on_delete):
        field = CustomObjectTypeField.objects.get(pk=field.pk)
        field.on_delete_behavior = on_delete
        with self.captureOnCommitCallbacks(execute=True):
            field.save()
        return field

    def test_constraint_created_on_commit(self):
        with self.captureOnCommitCallbacks() as callbacks:
            self.create_custom_object_type_field(
                self.cot,
                name='site',
                type='object',
                related_object_type=self.get_site_object_type(),
            )
        self.assertNotIn(('dcim_site', 'n'), self._constraints())
        for callback in callbacks:
            callback()
        self.assertEqual(self._constraints(), [('dcim_site', 'n')])

    def test_on_delete_change_replaces_constraint(self):
        field = self._create_field()
        self._set_on_delete(field, ObjectFieldOnDeleteChoices.PROTECT)
        self.assertEqual(self._constraints(), [('dcim_site', 'r')])

        site = Site.objects.create(name='Kept', slug='kept')
        self.cot.get_model().objects.create(name='o', site=site)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Site.objects.filter(pk=site.pk).delete()

    def test_retarget_replaces_constraint(self):
        field = self._create_field(ObjectFieldOnDeleteChoices.CASCADE)
        field = CustomObjectTypeField.objects.get(pk=field.pk)
        field.related_object_type = ObjectType.objects.get_for_model(Tenant)
        with self.captureOnCommitCallbacks(execute=True):
            field.save()
        self.assertEqual(self._constraints(), [('tenancy_tenant', 'c')])

    def test_orphaned_values_dont_block_constraint(self):
        field = self._create_field()
        site = Site.objects.create(name='Gone', slug='gone')
        self.cot.get_model().objects.create(name='o', site=site)
        with connection.cursor() as cursor:
            cursor.execute(f'ALTER TABLE "{self.table}" DROP CONSTRAINT "{self.table}_site_id_fk"')
            cursor.execute('DELETE FROM dcim_site WHERE id = %s', [site.pk])

        self._set_on_delete(field, ObjectFieldOnDeleteChoices.CASCADE)
        self.assertEqual(self._constraints(), [('dcim_site', 'c')])

    def test_failure_is_logged_and_old_constraint_kept(self):
        field = self._create_field()
        bad_sql = {**CustomObjectType._ON_DELETE_SQL, ObjectFieldOnDeleteChoices.CASCADE: 'NONSENSE'}
        with (
            mock.patch.object(CustomObjectType, '_ON_DELETE_SQL', bad_sql),
            self.assertLogs('netbox_custom_objects.models', level='ERROR') as logs,
        ):
            self._set_on_delete(field, ObjectFieldOnDeleteChoices.CASCADE)
        self.assertIn('Failed to create the FK constraint', logs.output[0])
        self.assertEqual(CustomObjectTypeField.objects.get(pk=field.pk).on_delete_behavior, 'cascade')
        # The DROP rolled back with the failed ADD.
        self.assertEqual(self._constraints(), [('dcim_site', 'n')])

    def test_hook_db_error_is_logged_not_raised(self):
        field = self._create_field()
        with (
            mock.patch.object(CustomObjectType, '_lock_database_table', side_effect=OperationalError('timeout')),
            self.assertLogs('netbox_custom_objects.models', level='ERROR') as logs,
        ):
            self._set_on_delete(field, ObjectFieldOnDeleteChoices.CASCADE)
        self.assertIn('Failed to create the FK constraint', logs.output[0])
        self.assertEqual(self._constraints(), [('dcim_site', 'n')])

    def test_ensure_all_fk_constraints_restores_missing_constraint(self):
        self._create_field(ObjectFieldOnDeleteChoices.CASCADE)
        with connection.cursor() as cursor:
            cursor.execute(f'ALTER TABLE "{self.table}" DROP CONSTRAINT "{self.table}_site_id_fk"')
        self.cot._ensure_all_fk_constraints()
        self.assertEqual(self._constraints(), [('dcim_site', 'c')])

    def test_constraint_rejects_object_field_without_related_type(self):
        """A field record that bypassed clean() gets a clear error, not an AttributeError."""
        orphan = CustomObjectTypeField(custom_object_type=self.cot, name='orphan', type='object')
        with self.assertRaisesRegex(ValueError, 'related_object_type is not set'):
            self.cot._ensure_field_fk_constraint(orphan)

    def test_ensure_all_fk_constraints_skips_orphans_and_avoids_per_field_type_queries(self):
        site_ot = self.get_site_object_type()
        with self.captureOnCommitCallbacks(execute=True):
            self.create_custom_object_type_field(self.cot, name='site_a', type='object', related_object_type=site_ot)
            self.create_custom_object_type_field(
                self.cot,
                name='site_b',
                type='object',
                related_object_type=site_ot,
                on_delete_behavior='cascade',
            )
            orphan = self.create_custom_object_type_field(
                self.cot,
                name='orphan',
                type='object',
                related_object_type=site_ot,
            )
        # Strip the related type behind the ORM's back, as a direct DB edit would.
        CustomObjectTypeField.objects.filter(pk=orphan.pk).update(related_object_type=None)
        with connection.cursor() as cursor:
            cursor.execute(f'ALTER TABLE "{self.table}" DROP CONSTRAINT "{self.table}_site_a_id_fk"')
            cursor.execute(f'ALTER TABLE "{self.table}" DROP CONSTRAINT "{self.table}_site_b_id_fk"')

        with CaptureQueriesContext(connection) as ctx:
            CustomObjectType.objects.get(pk=self.cot.pk)._ensure_all_fk_constraints()

        self.assertEqual(self._constraints('site_a_id'), [('dcim_site', 'n')])
        self.assertEqual(self._constraints('site_b_id'), [('dcim_site', 'c')])
        # The field query joins the content type; nothing should fetch it again per field.
        self.assertFalse([q['sql'] for q in ctx.captured_queries if 'FROM "django_content_type"' in q['sql']])

    def test_long_field_names_get_distinct_constraints(self):
        prefix = 'x' * 45
        with self.captureOnCommitCallbacks(execute=True):
            for suffix in ('a', 'b'):
                self.create_custom_object_type_field(
                    self.cot,
                    name=f'{prefix}_{suffix}',
                    type='object',
                    related_object_type=self.get_site_object_type(),
                    on_delete_behavior='cascade',
                )
        self.assertEqual(self._constraints(f'{prefix}_a_id'), [('dcim_site', 'c')])
        self.assertEqual(self._constraints(f'{prefix}_b_id'), [('dcim_site', 'c')])

    def test_ensure_all_fk_constraints_continues_past_a_failing_field(self):
        site_ot = self.get_site_object_type()
        with self.captureOnCommitCallbacks(execute=True):
            self.create_custom_object_type_field(self.cot, name='site_a', type='object', related_object_type=site_ot)
            self.create_custom_object_type_field(self.cot, name='site_b', type='object', related_object_type=site_ot)
        with connection.cursor() as cursor:
            cursor.execute(f'ALTER TABLE "{self.table}" DROP CONSTRAINT "{self.table}_site_b_id_fk"')

        original = CustomObjectType._ensure_field_fk_constraint

        def fail_site_a(cot, field, using=None):
            if field.name == 'site_a':
                raise RuntimeError('boom')
            return original(cot, field, using=using)

        with (
            mock.patch.object(CustomObjectType, '_ensure_field_fk_constraint', fail_site_a),
            self.assertLogs('netbox_custom_objects.models', level='ERROR'),
        ):
            self.cot._ensure_all_fk_constraints()
        self.assertEqual(self._constraints('site_b_id'), [('dcim_site', 'n')])


class ObjectFieldFKConstraintCommitTestCase(TransactionCleanupMixin, CustomObjectsTestCase, TransactionTestCase):
    """Field changes made in a transaction that already has pending deferred
    trigger events.  These need real commits: the hook must run after the
    events are resolved, as it does in production."""

    def _make_source_and_target_types(self, slug):
        """A source type with a multi-object field ``links`` to a target type and an
        Object field ``site``; returns (source, target, site field)."""
        tgt = self.create_custom_object_type(name=f'{slug}tgt', slug=f'{slug}-tgt')
        self.create_custom_object_type_field(tgt, name='name', type='text', primary=True)
        src = self.create_custom_object_type(name=f'{slug}src', slug=f'{slug}-src')
        self.create_custom_object_type_field(src, name='name', type='text', primary=True)
        self.create_custom_object_type_field(src, name='links', type='multiobject', related_object_type=tgt.object_type)
        site_field = self.create_custom_object_type_field(
            src,
            name='site',
            type='object',
            related_object_type=self.get_site_object_type(),
            on_delete_behavior='set_null',
        )
        return src, tgt, site_field

    def test_on_delete_change_keeps_through_table_target_fk_deferred(self):
        """A through table's target FK is DEFERRABLE INITIALLY DEFERRED so a branch
        merge can insert through rows before their target custom object exists.
        Changing an Object field's on_delete_behavior mid-transaction must not
        switch that constraint to IMMEDIATE for the rest of the transaction."""
        src, tgt, site_field = self._make_source_and_target_types('fkdefer')
        src_model = src.get_model()
        tgt.refresh_from_db()
        tgt_model = tgt.get_model()
        through_table = src.fields.get(name='links').through_table_name
        source = src_model.objects.create(name='s')

        with transaction.atomic():
            self._set(site_field, on_delete_behavior='cascade')
            with connection.cursor() as cursor:
                cursor.execute(
                    f'INSERT INTO "{through_table}" (source_id, target_id) VALUES (%s, %s)',
                    [source.pk, 1000],
                )
            tgt_model.objects.create(id=1000, name='t')

        self.assertEqual(_fk_constraints(src.get_database_table_name(), 'site_id'), [('dcim_site', 'c')])
        self.assertEqual(list(src_model.objects.get(pk=source.pk).links.values_list('pk', flat=True)), [1000])

    def test_on_delete_change_after_pending_events_on_its_own_table(self):
        """Deferred events queued against the source table itself (an owner_id
        insert, and a delete seen by a through table's deferrable source FK)."""
        src, tgt, site_field = self._make_source_and_target_types('fkown')
        src_model = src.get_model()
        owner = Owner.objects.create(name='fkown owner')
        doomed = src_model.objects.create(name='doomed')

        with transaction.atomic():
            src_model.objects.create(name='owned', owner=owner)
            doomed.delete()
            self._set(site_field, on_delete_behavior='cascade')

        self.assertEqual(_fk_constraints(src.get_database_table_name(), 'site_id'), [('dcim_site', 'c')])

    def test_on_delete_change_after_pending_events_on_target_table(self):
        """Deleting a Site queues deferred events on dcim_site through core's
        deferrable NO ACTION FKs, as a merge replaying that delete would."""
        src, tgt, site_field = self._make_source_and_target_types('fktgt')
        site = Site.objects.create(name='fktgt site', slug='fktgt-site')

        with transaction.atomic():
            site.delete()
            self._set(site_field, on_delete_behavior='cascade')

        self.assertEqual(_fk_constraints(src.get_database_table_name(), 'site_id'), [('dcim_site', 'c')])

    def test_on_delete_change_after_pending_events_on_custom_object_target_table(self):
        """The target is a custom object type with its own multi-object field:
        deleting one of its rows queues a deferred event via the through
        table's deferrable NO ACTION source FK."""
        src, tgt, site_field = self._make_source_and_target_types('fkcot')
        self.create_custom_object_type_field(tgt, name='peers', type='multiobject', related_object_type=tgt.object_type)
        tgt_field = self.create_custom_object_type_field(
            src,
            name='tgt',
            type='object',
            related_object_type=tgt.object_type,
            on_delete_behavior='set_null',
        )
        tgt.refresh_from_db()
        doomed = tgt.get_model().objects.create(name='doomed')

        with transaction.atomic():
            doomed.delete()
            self._set(tgt_field, on_delete_behavior='cascade')

        self.assertEqual(
            _fk_constraints(src.get_database_table_name(), 'tgt_id'),
            [(tgt.get_database_table_name(), 'c')],
        )
