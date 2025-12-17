import sys
import warnings

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import transaction
from django.db.utils import DatabaseError, OperationalError, ProgrammingError
from netbox.plugins import PluginConfig

from .constants import APP_LABEL as APP_LABEL


# Plugin Configuration
class CustomObjectsPluginConfig(PluginConfig):
    name = "netbox_custom_objects"
    verbose_name = "Custom Objects"
    description = "A plugin to manage custom objects in NetBox"
    version = "0.4.3"
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
    def _is_running_migration():
        """
        Check if the code is currently running during a Django migration.
        """
        # Check if 'makemigrations' or 'migrate' command is in sys.argv
        return any(cmd in sys.argv for cmd in ["makemigrations", "migrate"])

    @staticmethod
    def _is_running_test():
        """
        Check if the code is currently running during Django tests.
        """
        # Check if 'test' command is in sys.argv
        return "test" in sys.argv

    @staticmethod
    def _all_migrations_applied():
        """
        Check if all migrations for this app are applied.
        Returns True if all migrations are applied, False otherwise.
        """
        try:
            call_command(
                "migrate",
                APP_LABEL,
                check=True,
                interactive=False,
                verbosity=0,
            )
            return True
        except CommandError:
            # CommandError is raised when there are unapplied migrations
            return False

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

            # Skip database calls if running during migration or tests, or if not all migrations have been applied yet
            if (
                self._is_running_migration()
                or self._is_running_test()
                or not self._all_migrations_applied()
            ):
                super().ready()
                return

            try:
                with transaction.atomic():
                    qs = CustomObjectType.objects.all()
                    for obj in qs:
                        model = obj.get_model()
                        get_serializer_class(model)
            except (DatabaseError, OperationalError, ProgrammingError):
                # Only suppress exceptions during tests when schema may not match model
                # During normal operation, re-raise to alert of actual problems
                if self._is_running_test():
                    # The transaction.atomic() block will automatically rollback
                    pass
                else:
                    raise

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

            # Skip custom object type model loading if running during migration or tests,
            # or if not all migrations have been applied yet
            if (
                self._is_running_migration()
                or self._is_running_test()
                or not self._all_migrations_applied()
            ):
                return

            # Add custom object type models
            from .models import CustomObjectType

            try:
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
            except (DatabaseError, OperationalError, ProgrammingError):
                # Only suppress exceptions during tests when schema may not match model
                # (e.g., cache_timestamp column doesn't exist yet during test setup)
                # During normal operation, re-raise to alert of actual problems
                if self._is_running_test():
                    # The transaction.atomic() block will automatically rollback
                    pass
                else:
                    raise


config = CustomObjectsPluginConfig
