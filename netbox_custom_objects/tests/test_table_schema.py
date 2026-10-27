"""
Tests for schema changes to custom object tables.
"""
import sys
from io import StringIO
from unittest import expectedFailure
from unittest.mock import patch

from django.apps import apps
from django.core.management import call_command
from django.db import connection
from django.test import SimpleTestCase, TestCase

import netbox_custom_objects as nco
from netbox_custom_objects.constants import APP_LABEL
from netbox_custom_objects.models import CustomObjectTypeField

from .base import CustomObjectsTestCase


class SchemaOperationsTestCase(CustomObjectsTestCase, TestCase):
    """Test database schema operations on custom object tables."""

    def test_delete_cot_with_netbox_custom_field_referencing_object_type(self):
        """Deleting a COT must not raise ProtectedError when a NetBox CustomField
        has related_object_type pointing to the COT's underlying ObjectType."""
        from core.models import ObjectType
        from extras.choices import CustomFieldTypeChoices
        from extras.models import CustomField

        cot = self.create_custom_object_type(name='protectedtest', slug='protected-test')
        object_type = ObjectType.objects.get_for_model(cot.get_model())

        # Create a regular NetBox CustomField of type "object" whose related_object_type
        # points at the COT's ContentType. This is the scenario that triggered the
        # ProtectedError because CustomField.related_object_type uses on_delete=PROTECT.
        cf = CustomField.objects.create(
            name='cat_ref',
            type=CustomFieldTypeChoices.TYPE_OBJECT,
            related_object_type=object_type,
        )

        # Should delete cleanly without raising ProtectedError.
        cot.delete()

        self.assertFalse(
            CustomField.objects.filter(pk=cf.pk).exists(),
            "The referencing CustomField must be deleted along with the COT.",
        )

    # ------------------------------------------------------------------
    # Management commands
    # ------------------------------------------------------------------

    def test_migration_with_call_command(self):
        """Running migrate via call_command() should not raise."""
        out = StringIO()
        # --check exits with code 1 if unapplied migrations exist; any other
        # error (e.g. the plugin crashing during the migrate run) would raise.
        try:
            call_command('migrate', '--check', verbosity=0, stdout=out, stderr=out)
        except SystemExit as exc:
            # If we reach here, migrate --check found unapplied migrations or the plugin crashed.
            self.fail(f"migrate --check exited with code {exc.code}: {out.getvalue()}")

    # ------------------------------------------------------------------
    # Coordinates fields expand into two backing columns; verify the schema
    # editor manages both on rename and delete.
    # ------------------------------------------------------------------

    def _db_columns(self, model):
        """Return the set of actual DB column names for a generated model's table."""
        with connection.cursor() as cursor:
            return {
                col.name
                for col in connection.introspection.get_table_description(
                    cursor, model._meta.db_table
                )
            }

    def test_coordinates_field_rename_renames_both_columns(self):
        """Renaming a coordinates field renames both backing DB columns."""
        cot = self.create_custom_object_type(name='coordrename', slug='coord-rename')
        self.create_custom_object_type_field(
            cot, name='name', label='Name', type='text', primary=True,
        )
        field = self.create_custom_object_type_field(
            cot, name='location', label='Location', type='coordinates',
        )

        columns = self._db_columns(cot.get_model())
        self.assertIn('location_latitude', columns)
        self.assertIn('location_longitude', columns)

        # Reload from DB so the rename path has the original snapshot (set in
        # from_db) — this mirrors how the edit view loads the field before saving.
        field = CustomObjectTypeField.objects.get(pk=field.pk)
        field.name = 'geo'
        field.save()

        columns = self._db_columns(cot.get_model())
        self.assertNotIn('location_latitude', columns)
        self.assertNotIn('location_longitude', columns)
        self.assertIn('geo_latitude', columns)
        self.assertIn('geo_longitude', columns)

    def test_coordinates_field_delete_drops_both_columns(self):
        """Deleting a coordinates field drops both backing DB columns."""
        cot = self.create_custom_object_type(name='coorddelete', slug='coord-delete')
        self.create_custom_object_type_field(
            cot, name='name', label='Name', type='text', primary=True,
        )
        field = self.create_custom_object_type_field(
            cot, name='location', label='Location', type='coordinates',
        )

        columns = self._db_columns(cot.get_model())
        self.assertIn('location_latitude', columns)
        self.assertIn('location_longitude', columns)

        field.delete()

        columns = self._db_columns(cot.get_model())
        self.assertNotIn('location_latitude', columns)
        self.assertNotIn('location_longitude', columns)

    def test_url_field_rename_renames_both_columns(self):
        """Renaming a url field renames both backing DB columns."""
        cot = self.create_custom_object_type(name='urlrename', slug='url-rename')
        self.create_custom_object_type_field(
            cot, name='name', label='Name', type='text', primary=True,
        )
        field = self.create_custom_object_type_field(
            cot, name='website', label='Website', type='url',
        )

        columns = self._db_columns(cot.get_model())
        self.assertIn('website', columns)
        self.assertIn('website_title', columns)

        # Reload from DB so the rename path has the original snapshot (set in
        # from_db) — this mirrors how the edit view loads the field before saving.
        field = CustomObjectTypeField.objects.get(pk=field.pk)
        field.name = 'homepage'
        field.save()

        columns = self._db_columns(cot.get_model())
        self.assertNotIn('website', columns)
        self.assertNotIn('website_title', columns)
        self.assertIn('homepage', columns)
        self.assertIn('homepage_title', columns)

    def test_url_field_delete_drops_both_columns(self):
        """Deleting a url field drops both backing DB columns."""
        cot = self.create_custom_object_type(name='urldelete', slug='url-delete')
        self.create_custom_object_type_field(
            cot, name='name', label='Name', type='text', primary=True,
        )
        field = self.create_custom_object_type_field(
            cot, name='website', label='Website', type='url',
        )

        columns = self._db_columns(cot.get_model())
        self.assertIn('website', columns)
        self.assertIn('website_title', columns)

        field.delete()

        columns = self._db_columns(cot.get_model())
        self.assertNotIn('website', columns)
        self.assertNotIn('website_title', columns)


class CollectstaticWithoutDatabaseTestCase(SimpleTestCase):
    """collectstatic must not need the database (SimpleTestCase fails any query)."""

    def test_collectstatic_without_database(self):
        # Run as collectstatic would: under the test runner, "test" in argv and an unfinished
        # ready() would skip model generation anyway.
        with (
            patch.object(sys, 'argv', ['manage.py', 'collectstatic']),
            patch.object(nco, '_app_ready', True),
            patch.object(nco, '_migrations_checked', None),
        ):
            call_command('collectstatic', '--dry-run', '--no-input', verbosity=0, stdout=StringIO())
            list(apps.get_app_config(APP_LABEL).get_models())


class OwnerFieldNameCollisionTestCase(CustomObjectsTestCase, TestCase):
    """A user field named 'owner' shadows CustomObject's own OwnerMixin.owner field.
    Renaming or deleting it must not crash on the unmasked mixin field's missing column.
    """

    def _db_columns(self, model):
        with connection.cursor() as cursor:
            return {
                col.name
                for col in connection.introspection.get_table_description(
                    cursor, model._meta.db_table
                )
            }

    def _fks(self, model, column):
        """(target table, ON DELETE action code) of each FK on *column*."""
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT c.confrelid::regclass::text, c.confdeltype
                FROM pg_constraint c
                JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey)
                WHERE c.contype = 'f' AND c.conrelid = %s::regclass AND a.attname = %s
                """,
                [model._meta.db_table, column],
            )
            return cursor.fetchall()

    def _make_owner_collision_cot(self, slug):
        from core.models import ObjectType
        contact_ot = ObjectType.objects.get(app_label='tenancy', model='contact')

        cot = self.create_custom_object_type(name=slug, slug=slug)
        self.create_custom_object_type_field(
            cot, name='name', label='Name', type='text', primary=True, required=True,
        )
        # .objects.create() bypasses the reserved-name check in clean().  The FK
        # constraint is added on commit, as in production.
        with self.captureOnCommitCallbacks(execute=True):
            field = self.create_custom_object_type_field(
                cot, name='owner', label='Owner', type='object', related_object_type=contact_ot,
            )
        return cot, field

    def test_renaming_away_from_owner_does_not_break_list_view(self):
        cot, field = self._make_owner_collision_cot('owner-rename')
        model = cot.get_model()
        model.objects.create(name='Obj1')
        model.objects.create(name='Obj2')

        field = CustomObjectTypeField.objects.get(pk=field.pk)
        field.name = 'client_contact'
        with self.captureOnCommitCallbacks(execute=True):
            field.save()

        updated_model = cot.get_model(no_cache=True)
        columns = self._db_columns(updated_model)
        self.assertIn('client_contact_id', columns)
        self.assertIn('owner_id', columns, "OwnerMixin's own column must be healed back in")
        self.assertEqual([t for t, _ in self._fks(updated_model, 'client_contact_id')], ['tenancy_contact'])

        results = list(updated_model.objects.all())
        self.assertEqual(len(results), 2)

    @expectedFailure  # A rename recreates the FK as NO ACTION and the on-commit hook doesn't restore it.
    def test_renaming_away_from_owner_keeps_on_delete_action(self):
        cot, field = self._make_owner_collision_cot('owner-rename-fk')
        field = CustomObjectTypeField.objects.get(pk=field.pk)
        field.name = 'client_contact'
        with self.captureOnCommitCallbacks(execute=True):
            field.save()

        updated_model = cot.get_model(no_cache=True)
        # 'n' is ON DELETE SET NULL, the field's default.
        self.assertEqual(self._fks(updated_model, 'client_contact_id'), [('tenancy_contact', 'n')])

    def test_deleting_owner_field_does_not_break_list_view(self):
        cot, field = self._make_owner_collision_cot('owner-delete')
        model = cot.get_model()
        model.objects.create(name='Obj1')

        field = CustomObjectTypeField.objects.get(pk=field.pk)
        field.delete()

        updated_model = cot.get_model()
        columns = self._db_columns(updated_model)
        self.assertIn('owner_id', columns)

        results = list(updated_model.objects.all())
        self.assertEqual(len(results), 1)
