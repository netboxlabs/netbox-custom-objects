from core.models import ObjectType
from django import template
from django.contrib.contenttypes.models import ContentType
from django.urls import NoReverseMatch, reverse
from extras.models import Bookmark, ExportTemplate, Subscription
from netbox.models.features import NotificationsMixin
from utilities.querydict import prepare_cloned_fields

from netbox_custom_objects.utilities import get_viewname

__all__ = (
    "custom_object_add_button",
    "custom_object_bookmark_button",
    "custom_object_bulk_delete_button",
    "custom_object_bulk_edit_button",
    "custom_object_clone_button",
    "custom_object_delete_button",
    "custom_object_edit_button",
    "custom_object_export_button",
    "custom_object_import_button",
    "custom_object_subscribe_button",
    "custom_object_sync_button",
)

register = template.Library()


#
# Instance buttons
#


@register.inclusion_tag("buttons/bookmark.html", takes_context=True)
def custom_object_bookmark_button(context, instance):
    try:

        # Check if this user has already bookmarked the object
        content_type = ContentType.objects.get_for_model(instance)
        instance.custom_object_type.get_model()

        # Verify that the ContentType is properly accessible
        try:
            # This will test if the ContentType can be used to retrieve the model
            content_type.model_class()
        except Exception:
            # If we can't get the model class, don't show the bookmark button
            return {}

        bookmark = Bookmark.objects.filter(
            object_type=content_type, object_id=instance.pk, user=context["request"].user
        ).first()

        # Compile form URL & data
        if bookmark:
            form_url = reverse("extras:bookmark_delete", kwargs={"pk": bookmark.pk})
            form_data = {
                "confirm": "true",
            }
        else:
            form_url = reverse("extras:bookmark_add")
            form_data = {
                "object_type": content_type.pk,
                "object_id": instance.pk,
            }

        return {
            "bookmark": bookmark,
            "form_url": form_url,
            "form_data": form_data,
            "return_url": instance.get_absolute_url(),
        }
    except Exception:
        # If we can't get the content type, don't show the bookmark button
        return {}


@register.inclusion_tag("buttons/clone.html")
def custom_object_clone_button(instance):
    viewname = get_viewname(instance, "add")
    url = reverse(
        viewname,
        kwargs={"custom_object_type": instance.custom_object_type.name.lower()}
    )

    # Populate cloned field values
    param_string = prepare_cloned_fields(instance).urlencode()
    if param_string:
        url = f"{url}?{param_string}"

    return {
        "url": url,
    }


@register.inclusion_tag("buttons/edit.html")
def custom_object_edit_button(instance):
    viewname = get_viewname(instance, "edit")
    url = reverse(
        viewname,
        kwargs={
            "pk": instance.pk,
            "custom_object_type": instance.custom_object_type.name.lower(),
        },
    )

    return {
        "url": url,
        "label": "Edit",
    }


@register.inclusion_tag("buttons/custom_objects_delete.html")
def custom_object_delete_button(instance):
    viewname = get_viewname(instance, "delete")
    url = reverse(
        viewname, kwargs={
            "pk": instance.pk,
            "custom_object_type": instance.custom_object_type.name.lower(),
        },
    )

    return {
        "url": url,
    }


@register.inclusion_tag("buttons/subscribe.html", takes_context=True)
def custom_object_subscribe_button(context, instance):
    # Skip for objects which don't support notifications
    if not (issubclass(instance.__class__, NotificationsMixin)):
        return {}

    try:
        # Check if this user has already subscribed to the object
        content_type = ContentType.objects.get_for_model(instance)

        # Verify that the ContentType is properly accessible
        try:
            # This will test if the ContentType can be used to retrieve the model
            content_type.model_class()
        except Exception:
            # If we can't get the model class, don't show the subscribe button
            return {}

        subscription = Subscription.objects.filter(
            object_type=content_type, object_id=instance.pk, user=context["request"].user
        ).first()

        # Compile form URL & data
        if subscription:
            form_url = reverse("extras:subscription_delete", kwargs={"pk": subscription.pk})
            form_data = {
                "confirm": "true",
            }
        else:
            form_url = reverse("extras:subscription_add")
            form_data = {
                "object_type": content_type.pk,
                "object_id": instance.pk,
            }

        return {
            "subscription": subscription,
            "form_url": form_url,
            "form_data": form_data,
            "return_url": instance.get_absolute_url(),
        }
    except Exception:
        # If we can't get the content type, don't show the subscribe button
        return {}


@register.inclusion_tag("buttons/sync.html")
def custom_object_sync_button(instance):
    viewname = get_viewname(instance, "sync")
    url = reverse(viewname, kwargs={"pk": instance.pk})

    return {
        "label": "Sync",
        "url": url,
    }


#
# List buttons
#


@register.inclusion_tag("buttons/add.html")
def custom_object_add_button(model, custom_object_type, action="add"):
    try:
        viewname = get_viewname(model, action)
        url = reverse(
            viewname, kwargs={"custom_object_type": custom_object_type.name.lower()}
        )
    except NoReverseMatch:
        url = None

    return {
        "label": "Add",
        "url": url,
    }


@register.inclusion_tag("buttons/import.html")
def custom_object_import_button(model, action="bulk_import"):
    try:
        url = reverse(get_viewname(model, action))
    except NoReverseMatch:
        url = None

    return {
        "label": "Import",
        "url": url,
    }


@register.inclusion_tag("buttons/export.html", takes_context=True)
def custom_object_export_button(context, model):
    object_type = ObjectType.objects.get_for_model(model)
    user = context["request"].user

    # Determine if the "all data" export returns CSV or YAML
    data_format = "YAML" if hasattr(object_type.model_class(), "to_yaml") else "CSV"

    # Retrieve all export templates for this model
    export_templates = ExportTemplate.objects.restrict(user, "view").filter(
        object_types=object_type
    )

    return {
        "label": "Export",
        "perms": context["perms"],
        "object_type": object_type,
        "url_params": (
            context["request"].GET.urlencode() if context["request"].GET else ""
        ),
        "export_templates": export_templates,
        "data_format": data_format,
    }


@register.inclusion_tag("buttons/bulk_edit.html", takes_context=True)
def custom_object_bulk_edit_button(
    context, model, custom_object_type, action="bulk_edit", query_params=None
):
    try:
        viewname = get_viewname(model, action)
        url = reverse(
            viewname, kwargs={"custom_object_type": custom_object_type.name.lower()}
        )

        if query_params:
            url = f"{url}?{query_params.urlencode()}"
    except NoReverseMatch:
        url = None

    return {
        "label": "Bulk Edit",
        "htmx_navigation": context.get("htmx_navigation"),
        "url": url,
    }


@register.inclusion_tag("buttons/bulk_delete.html", takes_context=True)
def custom_object_bulk_delete_button(
    context, model, custom_object_type, action="bulk_delete", query_params=None
):
    try:
        viewname = get_viewname(model, action)
        url = reverse(
            viewname, kwargs={"custom_object_type": custom_object_type.name.lower()}
        )
        if query_params:
            url = f"{url}?{query_params.urlencode()}"
    except NoReverseMatch:
        url = None

    return {
        "label": "Bulk Delete",
        "htmx_navigation": context.get("htmx_navigation"),
        "url": url,
    }
