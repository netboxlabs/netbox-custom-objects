import sys
import warnings

from django.core.exceptions import AppRegistryNotReady
from django.db import transaction
from django.db.utils import DatabaseError, OperationalError, ProgrammingError
from netbox.plugins import PluginConfig


def is_running_migration():
    """
    Check if the code is currently running during a Django migration.
    """
    # Check if 'makemigrations' or 'migrate' command is in sys.argv
    if any(cmd in sys.argv for cmd in ["makemigrations", "migrate"]):
        return True

    return False


def check_custom_object_type_table_exists():
    """
    Check if the CustomObjectType table exists in the database.
    Returns True if the table exists, False otherwise.
    """
    from .models import CustomObjectType

    try:
        # Try to query the model - if the table doesn't exist, this will raise an exception
        # this check and the transaction.atomic() is only required when running tests as the
        # migration check doesn't work correctly in the test environment
        with transaction.atomic():
            # Force immediate execution by using first()
            CustomObjectType.objects.first()
        return True
    except (OperationalError, ProgrammingError, DatabaseError):
        # Catch database-specific errors (table doesn't exist, permission issues, etc.)
        return False


# Plugin Configuration
class CustomObjectsPluginConfig(PluginConfig):
    name = "netbox_custom_objects"
    verbose_name = "Custom Objects"
    description = "A plugin to manage custom objects in NetBox"
    version = "0.2.0"
    base_url = "custom-objects"
    min_version = "4.4.0"
    default_settings = {}
    required_settings = []
    template_extensions = "template_content.template_extensions"

    def get_model(self, model_name, require_ready=True):
        try:
            # if the model is already loaded, return it
            return super().get_model(model_name, require_ready)
        except LookupError:
            try:
                self.apps.check_apps_ready()
            except AppRegistryNotReady:
                raise

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

            # Skip custom object type model loading if running during migration
            if is_running_migration() or not check_custom_object_type_table_exists():
                return

            # Add custom object type models
            from .models import CustomObjectType

            custom_object_types = CustomObjectType.objects.all()
            for custom_type in custom_object_types:
                # Only yield already cached models during discovery
                if CustomObjectType.is_model_cached(custom_type.id):
                    model = CustomObjectType.get_cached_model(custom_type.id)
                    if model:
                        yield model


config = CustomObjectsPluginConfig
