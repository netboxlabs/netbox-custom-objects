from netbox.plugins import PluginConfig
from django.apps import apps

# Plugin Configuration
class CustomObjectsPluginConfig(PluginConfig):
    name = "netbox_custom_objects"
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
        from django.contrib.contenttypes.models import ContentType
        from django.contrib.contenttypes.management import create_contenttypes
        
        # Ensure all dynamic models are created and registered during startup
        # This prevents ContentType race conditions with Bookmark operations
        try:
            from .models import CustomObjectType
            from .constants import APP_LABEL
            
            # Only run this after the database is ready
            if apps.is_installed('django.contrib.contenttypes'):
                for custom_object_type in CustomObjectType.objects.all():
                    try:
                        # Get or create the model
                        model = custom_object_type.get_model()
                        
                        # Ensure the model is registered
                        try:
                            apps.get_model(APP_LABEL, model._meta.model_name)
                        except LookupError:
                            apps.register_model(APP_LABEL, model)
                        
                        # Ensure ContentType exists
                        content_type_name = custom_object_type.get_table_model_name(custom_object_type.id).lower()
                        try:
                            ContentType.objects.get(
                                app_label=APP_LABEL, 
                                model=content_type_name
                            )
                        except ContentType.DoesNotExist:
                            # Create the ContentType
                            app_config = apps.get_app_config(APP_LABEL)
                            create_contenttypes(app_config)
                            
                    except Exception as e:
                        # Log but don't fail startup
                        print(f"Warning: Could not initialize model for CustomObjectType {custom_object_type.id}: {e}")
        except Exception as e:
            # Don't fail plugin startup if there are issues
            print(f"Warning: Could not initialize custom object models: {e}")
            
        super().ready()


config = CustomObjectsPluginConfig
