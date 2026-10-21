from unittest import mock

from dcim.models import Site
from django.db import IntegrityError, connection, transaction
from django.test import TestCase

from netbox_custom_objects.choices import ObjectFieldOnDeleteChoices
from netbox_custom_objects.models import CustomObjectType, CustomObjectTypeField
from netbox_custom_objects.tests.base import CustomObjectsTestCase


class ObjectFieldFKConstraintTestCase(CustomObjectsTestCase, TestCase):
    """The DB-level FK for a non-polymorphic Object field, created after the field commits."""

    def setUp(self):
        super().setUp()
        self.cot = self.create_simple_custom_object_type(name="fkc", slug="fkcs")
        self.table = self.cot.get_database_table_name()

    def _constraints(self):
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT c.confrelid::regclass::text, c.confdeltype
                FROM pg_constraint c
                JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey)
                WHERE c.contype = 'f' AND c.conrelid = %s::regclass AND a.attname = 'site_id'
                """,
                [self.table],
            )
            return cursor.fetchall()

    def _create_field(self, on_delete=ObjectFieldOnDeleteChoices.SET_NULL):
        with self.captureOnCommitCallbacks(execute=True):
            return self.create_custom_object_type_field(
                self.cot, name="site", type="object", related_object_type=self.get_site_object_type(),
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
                self.cot, name="site", type="object", related_object_type=self.get_site_object_type(),
            )
        # Inside the transaction the column has Django's deferrable NO ACTION FK.
        self.assertEqual(self._constraints(), [("dcim_site", "a")])
        for callback in callbacks:
            callback()
        self.assertEqual(self._constraints(), [("dcim_site", "n")])

    def test_on_delete_change_replaces_constraint(self):
        field = self._create_field()
        self._set_on_delete(field, ObjectFieldOnDeleteChoices.PROTECT)
        self.assertEqual(self._constraints(), [("dcim_site", "r")])

        site = Site.objects.create(name="Kept", slug="kept")
        self.cot.get_model().objects.create(name="o", site=site)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Site.objects.filter(pk=site.pk).delete()

    def test_orphaned_values_dont_block_constraint(self):
        field = self._create_field()
        site = Site.objects.create(name="Gone", slug="gone")
        self.cot.get_model().objects.create(name="o", site=site)
        with connection.cursor() as cursor:
            cursor.execute(f'ALTER TABLE "{self.table}" DROP CONSTRAINT "{self.table}_site_id_fk"')
            cursor.execute("DELETE FROM dcim_site WHERE id = %s", [site.pk])

        self._set_on_delete(field, ObjectFieldOnDeleteChoices.CASCADE)
        self.assertEqual(self._constraints(), [("dcim_site", "c")])

    def test_failure_is_logged_and_old_constraint_kept(self):
        field = self._create_field()
        bad_sql = {**CustomObjectType._ON_DELETE_SQL, ObjectFieldOnDeleteChoices.CASCADE: "NONSENSE"}
        with (
            mock.patch.object(CustomObjectType, "_ON_DELETE_SQL", bad_sql),
            self.assertLogs("netbox_custom_objects.models", level="ERROR") as logs,
        ):
            self._set_on_delete(field, ObjectFieldOnDeleteChoices.CASCADE)
        self.assertIn("Failed to create the FK constraint", logs.output[0])
        self.assertEqual(CustomObjectTypeField.objects.get(pk=field.pk).on_delete_behavior, "cascade")
        # The DROP rolled back with the failed ADD.
        self.assertEqual(self._constraints(), [("dcim_site", "n")])

    def test_ensure_all_fk_constraints_restores_missing_constraint(self):
        self._create_field(ObjectFieldOnDeleteChoices.CASCADE)
        with connection.cursor() as cursor:
            cursor.execute(f'ALTER TABLE "{self.table}" DROP CONSTRAINT "{self.table}_site_id_fk"')
        self.cot._ensure_all_fk_constraints()
        self.assertEqual(self._constraints(), [("dcim_site", "c")])
