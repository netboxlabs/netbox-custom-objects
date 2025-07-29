from netbox.plugins import PluginConfig
from django.core.exceptions import AppRegistryNotReady
import warnings


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
        custom_object_type_id = int(model_name.replace("table", "").replace("model", ""))

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
        models = list(super().get_models(include_auto_created, include_swapped))
        
        # Suppress RuntimeWarning and UserWarning about database calls during model loading
        # These are read-only operations that are safe to perform - we also need
        # to suppress UserWarning as branching plugin will throw that as well.
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=RuntimeWarning, message=".*database.*")
            warnings.filterwarnings("ignore", category=UserWarning, message=".*database.*")
            
            # Add custom object type models
            try:
                from .models import CustomObjectType
                custom_object_types = CustomObjectType.objects.all()
                
                for custom_type in custom_object_types:
                    try:
                        model = custom_type.get_model()
                        if model:
                            models.append(model)
                    except Exception:
                        # Skip models that can't be loaded
                        continue
            except Exception:
                # If we can't load custom object types, just return the regular models
                pass
        
        return models


config = CustomObjectsPluginConfig
