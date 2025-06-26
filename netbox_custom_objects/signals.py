from django.dispatch import receiver
from django.db.models.signals import pre_init, post_init, pre_save
from django.apps import apps
from django.core.exceptions import ObjectDoesNotExist
from extras.models import Bookmark
from .models import CustomObjectType
from .constants import APP_LABEL


@receiver(pre_init, sender=Bookmark)
def bookmark_pre_init(sender, **kwargs):
    """
    Ensure all dynamic models are created and registered before any Bookmark operations.
    This prevents the "RelatedObjectDoesNotExist" error when accessing object_type.
    """
    # Import Django models only when needed to avoid AppRegistryNotReady errors
    from django.contrib.contenttypes.models import ContentType
    from django.contrib.contenttypes.management import create_contenttypes
    
    # Ensure all custom object types have their models created and registered
    for custom_object_type in CustomObjectType.objects.all():
        try:
            # Use the new method to ensure ContentType exists
            custom_object_type.ensure_content_type_exists()
        except Exception as e:
            # Log the error but don't fail the bookmark operation
            print(f"Warning: Could not ensure model for CustomObjectType {custom_object_type.id}: {e}")

