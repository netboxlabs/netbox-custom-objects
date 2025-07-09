from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.contrib.contenttypes.models import ContentType
from django.contrib.contenttypes.management import create_contenttypes
from django.apps import apps
from django.core.exceptions import ObjectDoesNotExist

from .models import CustomObjectType
from .constants import APP_LABEL


@receiver(post_save, sender=CustomObjectType)
def ensure_content_type_exists(sender, instance, created, **kwargs):
    """
    Ensure ContentType exists for the custom object type after it's saved.
    This signal runs after the database transaction is committed.
    """
    if created:
        try:
            # Get the model name for this custom object type
            content_type_name = instance.get_table_model_name(instance.id).lower()
            
            # Check if ContentType already exists
            try:
                ContentType.objects.get(
                    app_label=APP_LABEL, 
                    model=content_type_name
                )
            except ObjectDoesNotExist:
                # Create the ContentType
                ContentType.objects.create(
                    app_label=APP_LABEL,
                    model=content_type_name
                )
        except Exception as e:
            # Log the error but don't fail the save operation
            print(f"Warning: Could not create ContentType for CustomObjectType {instance.id}: {e}")


@receiver(post_delete, sender=CustomObjectType)
def cleanup_content_type(sender, instance, **kwargs):
    """
    Clean up the ContentType when a CustomObjectType is deleted.
    """
    try:
        content_type_name = instance.get_table_model_name(instance.id).lower()
        ContentType.objects.filter(
            app_label=APP_LABEL,
            model=content_type_name
        ).delete()
    except Exception as e:
        # Log the error but don't fail the delete operation
        print(f"Warning: Could not delete ContentType for CustomObjectType {instance.id}: {e}") 

