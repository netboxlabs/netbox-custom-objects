from django.conf import settings

from netbox.plugins import PluginConfig
from .utilities import register_models


# Plugin Configuration
class ServiceMappingPluginConfig(PluginConfig):
    name = "netbox_service_mappings"
    verbose_name = "Custom Objects"
    description = "A plugin to manage custom objects in NetBox"
    version = "0.1"
    base_url = "custom-objects"
    min_version = "4.2.0"
    # max_version = "3.5.0"
    default_settings = {}
    required_settings = []
    template_extensions = "template_content.template_extensions"

    def ready(self):
        super().ready()
        # from . import constants, events, search, signal_receivers

        # Register models which support service mappings
        # register_models()

config = ServiceMappingPluginConfig
