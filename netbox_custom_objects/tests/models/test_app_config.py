"""
Tests for the plugin's app config.
"""

import importlib.abc
import sys
from unittest.mock import patch

from django.apps import apps as django_apps
from django.db import connection
from django.db.utils import OperationalError, ProgrammingError
from django.test import TestCase

import netbox_custom_objects as nco
import netbox_custom_objects.models as nco_models
from netbox_custom_objects.graphql.live import _active_branch_key
from netbox_custom_objects.models import CustomObjectType, CustomObjectTypeField

from ..base import CustomObjectsTestCase


class PluginConfigGetModelTestCase(CustomObjectsTestCase, TestCase):
    """
    Regression tests for CustomObjectsPluginConfig.get_model().

    Covers the bug where get_model() queried the DB unconditionally, causing
    "column does not exist" errors during `manage.py migrate` when a new
    migration added a column to CustomObjectType but hadn't run yet.
    See: https://github.com/netboxlabs/netbox-custom-objects/issues/456
    """

    def setUp(self):
        super().setUp()
        self.config = django_apps.get_app_config('netbox_custom_objects')

    def test_get_model_raises_lookup_error_when_skipping(self):
        """get_model() raises LookupError instead of querying DB when should_skip returns True."""
        model_name = 'table99996model'  # not in the app registry, so get_model() would look it up

        with (
            patch.object(self.config.__class__, 'should_skip_dynamic_model_creation', return_value=True),
            self.assertNumQueries(0),
            self.assertRaises(LookupError),
        ):
            self.config.get_model(model_name)

    def test_get_model_returns_model_when_not_skipping(self):
        """get_model() successfully returns the dynamic model when migrations are up to date."""
        cot = self.create_custom_object_type(name='MigrateTest2', slug='migrate-test-2')
        model_name = f'table{cot.pk}model'

        with patch.object(self.config.__class__, 'should_skip_dynamic_model_creation', return_value=False):
            model = self.config.get_model(model_name)
        self.assertIsNotNone(model)

    def test_get_model_skips_db_with_migrate_in_argv(self):
        """get_model() raises LookupError when 'migrate' is in sys.argv (pre-migration state)."""
        model_name = 'table99995model'  # not in the app registry, so get_model() would look it up

        original_argv = sys.argv[:]
        try:
            sys.argv = ['manage.py', 'migrate']
            # Reset cached migration check so argv is re-evaluated
            nco._migrations_checked = None
            with self.assertNumQueries(0), self.assertRaises(LookupError):
                self.config.get_model(model_name)
        finally:
            sys.argv = original_argv
            nco._migrations_checked = None

    def test_get_model_converts_programming_error_to_lookup_error(self):
        """
        Regression: get_model() must not let ProgrammingError escape when the DB
        schema is incomplete (e.g. a new column was added by a migration that
        hasn't run yet).  Reproduces the reported failure where
        upgrading from v0.4.6 to v0.4.7 aborted `manage.py migrate` with
        "column netbox_custom_objects_customobjecttype.group_name does not exist".

        Use a model_name with a non-existent COT ID so the dynamic model is not
        already in the app registry; this ensures we reach the objects.get() call
        that needs to be guarded.
        """
        model_name = 'table99998model'  # no such COT — not in the app registry

        with (
            patch.object(self.config.__class__, 'should_skip_dynamic_model_creation', return_value=False),
            patch.object(CustomObjectType.objects, 'get', side_effect=ProgrammingError('column does not exist')),
            self.assertRaises(LookupError),
        ):
            self.config.get_model(model_name)

    def test_get_model_converts_operational_error_to_lookup_error(self):
        """
        get_model() must convert OperationalError (e.g. table missing entirely)
        to LookupError for the same reason as ProgrammingError above.
        """
        model_name = 'table99999model'  # no such COT — not in the app registry

        with (
            patch.object(self.config.__class__, 'should_skip_dynamic_model_creation', return_value=False),
            patch.object(CustomObjectType.objects, 'get', side_effect=OperationalError('no such table')),
            self.assertRaises(LookupError),
        ):
            self.config.get_model(model_name)

    def test_ready_survives_programming_error(self):
        """
        ready() must not propagate ProgrammingError from an incomplete DB schema.
        Calling ready() a second time is safe — signals are re-connected idempotently
        and the dynamic model loop is the only part that can raise here.
        """
        with (
            patch.object(self.config.__class__, 'should_skip_dynamic_model_creation', return_value=False),
            patch.object(CustomObjectType.objects, 'all', side_effect=ProgrammingError('column does not exist')),
        ):
            # Must not raise — bad schema should be silently skipped.
            self.config.ready()

    def test_ready_survives_operational_error(self):
        """ready() must not propagate OperationalError from a missing table."""
        with (
            patch.object(self.config.__class__, 'should_skip_dynamic_model_creation', return_value=False),
            patch.object(CustomObjectType.objects, 'all', side_effect=OperationalError('no such table')),
        ):
            self.config.ready()

    def test_get_models_survives_programming_error(self):
        """
        get_models() must not propagate ProgrammingError when the DB schema is
        incomplete.  The DB-driven portion yields nothing; static models already
        in the app registry are still returned via super().get_models().
        """
        with (
            patch.object(self.config.__class__, 'should_skip_dynamic_model_creation', return_value=False),
            patch.object(CustomObjectType.objects, 'all', side_effect=ProgrammingError('column does not exist')),
        ):
            # Consuming the generator must not raise.
            list(self.config.get_models())

    def test_get_models_survives_operational_error(self):
        """get_models() must not propagate OperationalError from a missing table."""
        with (
            patch.object(self.config.__class__, 'should_skip_dynamic_model_creation', return_value=False),
            patch.object(CustomObjectType.objects, 'all', side_effect=OperationalError('no such table')),
        ):
            list(self.config.get_models())

    def test_should_skip_returns_true_on_programming_error(self):
        """
        should_skip_dynamic_model_creation() must return True (skip) when the
        migration infrastructure raises ProgrammingError, e.g. on a fresh install
        before the django_migrations table exists.  An uncaught exception here
        would bypass all the guards in ready(), get_model(), and get_models().
        """
        original_checked = nco._migrations_checked
        nco._migrations_checked = None  # force the migration-loader path
        try:
            with patch(
                'netbox_custom_objects.MigrationLoader', side_effect=ProgrammingError('relation does not exist')
            ):
                result = self.config.should_skip_dynamic_model_creation()
            self.assertTrue(result)
            # Must not be cached so the next call retries once the DB is ready.
            self.assertIsNone(nco._migrations_checked)
        finally:
            nco._migrations_checked = original_checked

    def test_should_skip_returns_true_on_operational_error(self):
        """
        should_skip_dynamic_model_creation() must return True when the migration
        infrastructure raises OperationalError (e.g. django_migrations missing).
        """
        original_checked = nco._migrations_checked
        nco._migrations_checked = None
        try:
            with patch(
                'netbox_custom_objects.MigrationLoader',
                side_effect=OperationalError('no such table: django_migrations'),
            ):
                result = self.config.should_skip_dynamic_model_creation()
            self.assertTrue(result)
            self.assertIsNone(nco._migrations_checked)
        finally:
            nco._migrations_checked = original_checked

    def test_dynamic_model_creation_unsafe_excludes_test(self):
        """_dynamic_model_creation_unsafe() must not treat 'test' in sys.argv as unsafe."""
        original_argv = sys.argv[:]
        original_checked = nco._migrations_checked
        try:
            sys.argv = ['manage.py', 'test']
            nco._migrations_checked = None
            self.assertTrue(self.config.should_skip_dynamic_model_creation())
            nco._migrations_checked = None
            self.assertFalse(self.config._dynamic_model_creation_unsafe())

            sys.argv = ['manage.py', 'migrate']
            nco._migrations_checked = None
            self.assertTrue(self.config._dynamic_model_creation_unsafe())
        finally:
            sys.argv = original_argv
            nco._migrations_checked = original_checked


class _BranchingImportBlocker(importlib.abc.MetaPathFinder):
    """Fail on any import of netbox_branching."""

    def find_spec(self, name, path, target=None):
        if name == 'netbox_branching' or name.startswith('netbox_branching.'):
            raise AssertionError(f'{name} was imported while netbox-branching is not enabled')
        return


class BranchingInstalledNotEnabledTestCase(CustomObjectsTestCase, TestCase):
    """
    Nothing imports netbox-branching when it's installed but not in PLUGINS: importing it
    registers its request processor, which then fails on every request and job.
    """

    def setUp(self):
        super().setUp()
        # The plugin's migration check builds a MigrationLoader, which imports every installed
        # app's migrations, netbox_branching's included when it's enabled. A real process runs
        # the check once at startup, so run (and cache) it before blocking the import.
        nco.CustomObjectsPluginConfig._dynamic_model_creation_unsafe()
        is_installed = django_apps.is_installed
        patcher = patch.object(
            django_apps, 'is_installed', side_effect=lambda name: name != 'netbox_branching' and is_installed(name)
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        # Unload netbox_branching (restored afterwards) so any import of it reaches the blocker.
        modules = patch.dict(sys.modules)
        modules.start()
        self.addCleanup(modules.stop)
        for name in [m for m in sys.modules if m == 'netbox_branching' or m.startswith('netbox_branching.')]:
            del sys.modules[name]
        blocker = _BranchingImportBlocker()
        sys.meta_path.insert(0, blocker)
        self.addCleanup(sys.meta_path.remove, blocker)

    def test_hooks_and_branch_context_helpers(self):
        with patch.object(nco, '_branching_hooks_registered', False):
            nco._register_branching_hooks_once()
            self.assertFalse(nco._branching_hooks_registered)
        self.assertIs(nco_models._get_schema_connection(), connection)
        self.assertIsNone(CustomObjectType._active_branch_id())
        self.assertIsNone(_active_branch_key())

    def test_field_rename_and_type_delete(self):
        cot = self.create_custom_object_type(name='NotBranched', slug='not-branched')
        field = self.create_custom_object_type_field(cot, name='label', type='text')
        field = CustomObjectTypeField.objects.get(pk=field.pk)
        field.name = 'title'
        field.save()
        cot.delete()
        self.assertFalse(CustomObjectType.objects.filter(pk=cot.pk).exists())
