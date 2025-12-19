import contextvars
import sys
import warnings

from django.db import connection, transaction
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.recorder import MigrationRecorder
from django.db.models.signals import pre_migrate, post_migrate
from netbox.plugins import PluginConfig

from .constants import APP_LABEL as APP_LABEL

# Context variable to track if we're currently running migrations
_is_migrating = contextvars.ContextVar('is_migrating', default=False)

# Cache for migration check to avoid repeated expensive filesystem/database operations
_migrations_checked = None
_checking_migrations = False


def _migration_started(sender, **kwargs):
    """Signal handler for pre_migrate - sets the migration flag."""
    _is_migrating.set(True)


def _migration_finished(sender, **kwargs):
    """Signal handler for post_migrate - clears the migration flag and cache."""
    global _migrations_checked
    _is_migrating.set(False)
    _migrations_checked = None


# Plugin Configuration
class CustomObjectsPluginConfig(PluginConfig):
    name = "netbox_custom_objects"
    verbose_name = "Custom Objects"
    description = "A plugin to manage custom objects in NetBox"
    version = "0.4.4"
    author = 'Netbox Labs'
    author_email = 'support@netboxlabs.com'
    base_url = "custom-objects"
    min_version = "4.4.0"
    max_version = "4.5.99"
    default_settings = {
        # The maximum number of Custom Object Types that may be created
        'max_custom_object_types': 50,
    }
    required_settings = []
    template_extensions = "template_content.template_extensions"

    @staticmethod
    def _should_skip_dynamic_model_creation():
        """
        Determine if dynamic model creation should be skipped.

        Returns True if dynamic models should not be created/loaded due to:
        - Currently running migrations
        - Running tests
        - All migrations not yet applied

        Returns False if it's safe to proceed with dynamic model creation.
        """
        global _migrations_checked, _checking_migrations

        # Skip if currently running migrations
        if _is_migrating.get():
            return True

        # Skip if running tests
        if "test" in sys.argv:
            return True

        # Below code is to check if the last migration is applied using the migration graph
        # However, migrations can can call into get_models() which can call into this function again
        # so we have checks to prevent recursion
        if _checking_migrations:
            return True

        # Return cached result if available
        if _migrations_checked is not None:
            return _migrations_checked

        _checking_migrations = True

        try:
            loader = MigrationLoader(connection)

            # Get all migrations for our app from the migration graph
            app_migrations = [
                key[1] for key in loader.graph.nodes
                if key[0] == APP_LABEL
            ]

            if not app_migrations:
                result = True
            else:
                # Get and check if the last migration is applied
                last_migration = sorted(app_migrations)[-1]
                recorder = MigrationRecorder(connection)
                applied_migrations = recorder.applied_migrations()

                if (APP_LABEL, last_migration) not in applied_migrations:
                    result = True
                else:
                    result = False

            # Cache the result
            _migrations_checked = result
            return result

        finally:
            # Always clear the recursion flag
            _checking_migrations = False

    def ready(self):
        from .models import CustomObjectType
        from netbox_custom_objects.api.serializers import get_serializer_class

        # Connect migration signals to track migration state
        pre_migrate.connect(_migration_started)
        post_migrate.connect(_migration_finished)

        # Suppress warnings about database calls during app initialization
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore", category=RuntimeWarning, message=".*database.*"
            )
            warnings.filterwarnings(
                "ignore", category=UserWarning, message=".*database.*"
            )

            # Skip database calls if dynamic models can't be created yet
            if self._should_skip_dynamic_model_creation():
                super().ready()
                return

            with transaction.atomic():
                qs = CustomObjectType.objects.all()
                for obj in qs:
                    model = obj.get_model()
                    get_serializer_class(model)

        super().ready()

    def get_model(self, model_name, require_ready=True):
        self.apps.check_apps_ready()
        try:
            # if the model is already loaded, return it
            return super().get_model(model_name, require_ready)
        except LookupError:
            pass

        model_name = model_name.lower()
        # only do database calls if we are sure the app is ready to avoid
        # Django warnings
        if "table" not in model_name.lower() or "model" not in model_name.lower():
            raise LookupError(
                "App '%s' doesn't have a '%s' model." % (self.label, model_name)
            )

        from .models import CustomObjectType

        custom_object_type_id = int(
            model_name.replace("table", "").replace("model", "")
        )

        try:
            obj = CustomObjectType.objects.get(pk=custom_object_type_id)
        except CustomObjectType.DoesNotExist:
            raise LookupError(
                "App '%s' doesn't have a '%s' model." % (self.label, model_name)
            )

        return obj.get_model()

    def get_models(self, include_auto_created=False, include_swapped=False):
        """Return all models for this plugin, including custom object type models."""
        # Get the regular Django models first
        for model in super().get_models(include_auto_created, include_swapped):
            yield model

        # Suppress warnings about database calls during model loading
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore", category=RuntimeWarning, message=".*database.*"
            )
            warnings.filterwarnings(
                "ignore", category=UserWarning, message=".*database.*"
            )

            # Skip custom object type model loading if dynamic models can't be created yet
            if self._should_skip_dynamic_model_creation():
                return

            # Add custom object type models
            from .models import CustomObjectType

            with transaction.atomic():
                custom_object_types = CustomObjectType.objects.all()
                for custom_type in custom_object_types:
                    model = custom_type.get_model()
                    if model:
                        yield model

                        # If include_auto_created is True, also yield through models
                        if include_auto_created and hasattr(model, '_through_models'):
                            for through_model in model._through_models:
                                yield through_model


config = CustomObjectsPluginConfig
