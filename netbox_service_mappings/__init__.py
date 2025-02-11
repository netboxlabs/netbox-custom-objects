from django.conf import settings

from netbox.plugins import PluginConfig
from .utilities import register_models


# Plugin Configuration
class ServiceMappingPluginConfig(PluginConfig):
    name = "netbox_service_mappings"
    verbose_name = "Service Mappings"
    description = "A plugin to manage custom service mappings in NetBox"
    version = "0.1"
    base_url = "service-mappings"
    min_version = "4.2.0"
    # max_version = "3.5.0"
    default_settings = {}
    required_settings = []

    def ready(self):
        super().ready()
        # from . import constants, events, search, signal_receivers

        # Register models which support service mappings
        # register_models()

config = ServiceMappingPluginConfig
