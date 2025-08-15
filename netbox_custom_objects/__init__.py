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


def is_in_clear_cache():
    """
    Check if the code is currently being called from Django's clear_cache() method.

    TODO: This is fairly ugly, but in models.CustomObjectType.get_model() we call
    meta = type() which calls clear_cache on the model which causes a call to
    get_models() which in-turn calls get_model and therefore recurses.

    This catches the specific case of a recursive call to get_models() from
    clear_cache() which is the only case we care about, so should be relatively
    safe.  An alternative should be found for this.
    """
    import inspect

    frame = inspect.currentframe()
    try:
        # Walk up the call stack to see if we're being called from clear_cache
        while frame:
            if (
                frame.f_code.co_name == "clear_cache"
                and "django/apps/registry.py" in frame.f_code.co_filename
            ):
                return True
            frame = frame.f_back
        return False
    finally:
        # Clean up the frame reference
        del frame


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
    version = "0.1.0"
    base_url = "custom-objects"
    min_version = "4.2.0"
    default_settings = {}
    required_settings = []
    template_extensions = "template_content.template_extensions"
    _in_get_models = False  # Recursion guard

    def ready(self):
        super().ready()
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

            from .models import CustomObjectType

            custom_object_types = CustomObjectType.objects.all()
            for custom_object_type in custom_object_types:
                # Synthesize SearchIndex classes for all CustomObjectTypes
                custom_object_type.register_custom_object_search_index()

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

        # Prevent recursion
        if self._in_get_models and is_in_clear_cache():
            # Skip dynamic model creation if we're in a recursive get_models call
            return

        self._in_get_models = True
        try:
            # Suppress warnings about database calls during model loading
            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "ignore", category=RuntimeWarning, message=".*database.*"
                )
                warnings.filterwarnings(
                    "ignore", category=UserWarning, message=".*database.*"
                )

                # Skip custom object type model loading if running during migration
                if (
                    is_running_migration()
                    or not check_custom_object_type_table_exists()
                ):
                    return

                # Add custom object type models
                from .models import CustomObjectType

                custom_object_types = CustomObjectType.objects.all()
                for custom_type in custom_object_types:
                    model = custom_type.get_model()
                    if model:
                        yield model
        finally:
            # Clean up the recursion guard
            self._in_get_models = False


config = CustomObjectsPluginConfig
