import sys
import warnings

from django.db.utils import DatabaseError, OperationalError, ProgrammingError
from netbox.plugins import PluginConfig

from .constants import APP_LABEL as APP_LABEL


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
    from django.db import connection
    from .models import CustomObjectType

    try:
        # Use raw SQL to check table existence without generating ORM errors
        with connection.cursor() as cursor:
            table_name = CustomObjectType._meta.db_table
            cursor.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_name = %s
                )
            """, [table_name])
            table_exists = cursor.fetchone()[0]
            return table_exists
    except (OperationalError, ProgrammingError, DatabaseError):
        # Catch database-specific errors (permission issues, etc.)
        return False


# Plugin Configuration
class CustomObjectsPluginConfig(PluginConfig):
    name = "netbox_custom_objects"
    verbose_name = "Custom Objects"
    description = "A plugin to manage custom objects in NetBox"
    version = "0.4.1"
    author = 'Netbox Labs'
    author_email = 'support@netboxlabs.com'
    base_url = "custom-objects"
    min_version = "4.4.0"
    default_settings = {}
    required_settings = []
    template_extensions = "template_content.template_extensions"

    def ready(self):
        from .models import CustomObjectType
        from netbox_custom_objects.api.serializers import get_serializer_class

        # Suppress warnings about database calls during app initialization
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore", category=RuntimeWarning, message=".*database.*"
            )
            warnings.filterwarnings(
                "ignore", category=UserWarning, message=".*database.*"
            )

            # Skip database calls if running during migration or if table doesn't exist
            if is_running_migration() or not check_custom_object_type_table_exists():
                super().ready()
                return

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

            # Skip custom object type model loading if running during migration
            if is_running_migration() or not check_custom_object_type_table_exists():
                return

            # Add custom object type models
            from .models import CustomObjectType

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
