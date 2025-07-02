from netbox.plugins import PluginConfig


# Plugin Configuration
class CustomObjectsPluginConfig(PluginConfig):
    name = "netbox_custom_objects"
    verbose_name = "Custom Objects"
    description = "A plugin to manage custom objects in NetBox"
    version = "0.1"
    base_url = "custom-objects"
    min_version = "4.2.0"
    default_settings = {}
    required_settings = []
    template_extensions = "template_content.template_extensions"

    # def get_model(self, model_name, require_ready=True):
    #     if require_ready:
    #         self.apps.check_models_ready()
    #     else:
    #         self.apps.check_apps_ready()
    #
    #     if model_name.lower() in self.models:
    #         return self.models[model_name.lower()]
    #
    #     from .models import CustomObjectType
    #     if "table" not in model_name.lower() or "model" not in model_name.lower():
    #         raise LookupError(
    #             "App '%s' doesn't have a '%s' model." % (self.label, model_name)
    #         )
    #
    #     custom_object_type_id = int(model_name.replace("table", "").replace("model", ""))
    #
    #     try:
    #         obj = CustomObjectType.objects.get(pk=custom_object_type_id)
    #     except CustomObjectType.DoesNotExist:
    #         raise LookupError(
    #             "App '%s' doesn't have a '%s' model." % (self.label, model_name)
    #         )
    #     return obj.get_model()


config = CustomObjectsPluginConfig
