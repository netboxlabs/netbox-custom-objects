"""
Tests for database schema operations and model-cache behaviour.

Uses TransactionTestCase so DDL and on_commit callbacks behave exactly as they
do in production (no wrapping savepoint prevents commits).
"""
from io import StringIO

from django.apps import apps
from django.core.management import call_command
from django.test import TransactionTestCase

from netbox_custom_objects.constants import APP_LABEL

from .base import CustomObjectsTestCase, TransactionCleanupMixin


class SchemaOperationsTestCase(TransactionCleanupMixin, CustomObjectsTestCase, TransactionTestCase):
    """Test database schema operations and related cache/registry behaviour."""

    # ------------------------------------------------------------------
    # Cache invalidation
    # ------------------------------------------------------------------

    def test_cache_invalidation_on_cotf_save(self):
        """#340 – cache_timestamp on the parent COT is updated when a field is saved."""
        cot = self.create_custom_object_type(name='cachetest', slug='cache-test')
        # Capture the initial timestamp
        initial_timestamp = cot.cache_timestamp

        # Adding a field triggers CustomObjectTypeField.save(), which calls
        # cot.save(update_fields=['cache_timestamp']).
        self.create_custom_object_type_field(
            cot,
            name='myfield',
            label='My Field',
            type='text',
        )

        cot.refresh_from_db()
        self.assertNotEqual(
            cot.cache_timestamp,
            initial_timestamp,
            "cache_timestamp must be updated after a field is saved.",
        )

    def test_cache_invalidation_on_cotf_delete(self):
        """cache_timestamp is updated when a field is deleted."""
        cot = self.create_custom_object_type(name='cachedel', slug='cache-del')
        field = self.create_custom_object_type_field(
            cot,
            name='tempfield',
            label='Temp Field',
            type='text',
        )
        cot.refresh_from_db()
        timestamp_after_add = cot.cache_timestamp

        field.delete()

        cot.refresh_from_db()
        self.assertNotEqual(
            cot.cache_timestamp,
            timestamp_after_add,
            "cache_timestamp must be updated after a field is deleted.",
        )

    # ------------------------------------------------------------------
    # Model registry
    # ------------------------------------------------------------------

    def test_model_registered_in_apps_after_cotf_save(self):
        """#335 – The regenerated model is present in apps.get_models() after a field change."""
        cot = self.create_custom_object_type(name='regtest', slug='reg-test')
        self.create_custom_object_type_field(
            cot,
            name='fieldone',
            label='Field One',
            type='text',
            primary=True,
        )

        # Force model generation and registration
        model = cot.get_model()
        model_name = model.__name__.lower()

        # The model must appear in the app registry
        self.assertIn(
            model_name,
            apps.all_models.get(APP_LABEL, {}),
            "Generated model should be registered in Django's app registry.",
        )
        self.assertIn(
            model,
            apps.get_models(),
            "Generated model should be returned by apps.get_models().",
        )

    def test_model_regenerated_after_field_added(self):
        """Adding a field clears the model cache so get_model() reflects the new schema."""
        cot = self.create_custom_object_type(name='regentest', slug='regen-test')
        self.create_custom_object_type_field(
            cot,
            name='name',
            label='Name',
            type='text',
            primary=True,
        )
        old_model = cot.get_model()
        self.assertFalse(
            'extra' in {f.name for f in old_model._meta.get_fields()},
            "Field 'extra' should not exist before it is added.",
        )

        # Add a new field — this invalidates the cache
        self.create_custom_object_type_field(
            cot,
            name='extra',
            label='Extra',
            type='text',
        )

        new_model = cot.get_model()
        self.assertIn(
            'extra',
            {f.name for f in new_model._meta.get_fields()},
            "Field 'extra' should be present on the regenerated model.",
        )

    def test_model_not_in_registry_after_cot_deleted(self):
        """Deleting a COT removes its generated model from Django's app registry."""
        cot = self.create_custom_object_type(name='delregtest', slug='del-reg-test')
        self.create_custom_object_type_field(
            cot,
            name='name',
            label='Name',
            type='text',
            primary=True,
        )
        model = cot.get_model()
        model_name = model.__name__.lower()

        self.assertIn(
            model_name,
            apps.all_models.get(APP_LABEL, {}),
            "Model should be in registry before deletion.",
        )

        cot.delete()

        self.assertNotIn(
            model_name,
            apps.all_models.get(APP_LABEL, {}),
            "Deleted COT's model must be removed from the app registry.",
        )

    def test_delete_cot_with_netbox_custom_field_referencing_object_type(self):
        """#523 – Deleting a COT must not raise ProtectedError when a NetBox CustomField
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
        """#326 – Running migrate via call_command() should not raise."""
        out = StringIO()
        # --check exits with code 1 if unapplied migrations exist; any other
        # error (e.g. the plugin crashing during the migrate run) would raise.
        try:
            call_command('migrate', '--check', verbosity=0, stdout=out, stderr=out)
        except SystemExit as exc:
            # If we reach here, migrate --check found unapplied migrations or the plugin crashed.
            self.fail(f"migrate --check exited with code {exc.code}: {out.getvalue()}")

    def test_collectstatic_without_database(self):
        """#347 – collectstatic should complete without requiring database access."""
        out = StringIO()
        err = StringIO()
        # --dry-run does not write files; --no-input skips confirmation prompts.
        # The important assertion is that no exception (especially no database
        # error originating from the plugin's AppConfig) is raised.
        call_command(
            'collectstatic',
            '--dry-run',
            '--no-input',
            verbosity=0,
            stdout=out,
            stderr=err,
        )
        # No uncaught exceptions reaching here means success.
