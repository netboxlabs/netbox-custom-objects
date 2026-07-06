from django import template
from extras.choices import CustomFieldUIVisibleChoices

from netbox_custom_objects.choices import CustomObjectFieldTypeChoices
from netbox_custom_objects.models import CustomObjectTypeField
from netbox_custom_objects.utilities import build_map_url

__all__ = (
    "get_field_object_type",
    "get_field_type_verbose_name",
    "get_field_value",
    "get_field_is_ui_visible",
    "get_child_relations",
    "get_coordinate_map_url",
)

register = template.Library()

custom_field_type_verbose_names = {c[0]: c[1] for c in CustomObjectFieldTypeChoices.CHOICES}


@register.filter(name="get_field_object_type")
def get_field_object_type(field: CustomObjectTypeField) -> str:
    return field.related_object_type_label


@register.filter(name="get_field_type_verbose_name")
def get_field_type_verbose_name(field: CustomObjectTypeField) -> str:
    return custom_field_type_verbose_names[field.type]


@register.filter(name="get_field_value")
def get_field_value(obj, field: CustomObjectTypeField):
    if field.type == CustomObjectFieldTypeChoices.TYPE_COORDINATES:
        latitude = getattr(obj, f"{field.name}_latitude", None)
        longitude = getattr(obj, f"{field.name}_longitude", None)
        if latitude is None or longitude is None:
            return None
        return f"{latitude}, {longitude}"
    return getattr(obj, field.name)


@register.filter(name="get_coordinate_map_url")
def get_coordinate_map_url(obj, field: CustomObjectTypeField):
    """Return the external map URL for a coordinates field, or None."""
    if field.type != CustomObjectFieldTypeChoices.TYPE_COORDINATES:
        return None
    latitude = getattr(obj, f"{field.name}_latitude", None)
    longitude = getattr(obj, f"{field.name}_longitude", None)
    return build_map_url(latitude, longitude)


@register.filter(name="get_field_is_ui_visible")
def get_field_is_ui_visible(obj, field: CustomObjectTypeField) -> bool:
    if field.ui_visible == CustomFieldUIVisibleChoices.ALWAYS:
        return True
    if field.type == CustomObjectFieldTypeChoices.TYPE_MULTIOBJECT:
        field_value = getattr(obj, field.name).exists()
    elif field.type == CustomObjectFieldTypeChoices.TYPE_COORDINATES:
        field_value = (
            getattr(obj, f"{field.name}_latitude", None) is not None
            and getattr(obj, f"{field.name}_longitude", None) is not None
        )
    else:
        field_value = getattr(obj, field.name)
    if field.ui_visible == CustomFieldUIVisibleChoices.IF_SET and field_value:
        return True
    return False


@register.filter(name="get_child_relations")
def get_child_relations(obj, field: CustomObjectTypeField):
    return getattr(obj, field.name)


@register.filter(name="dict_get")
def dict_get(d, key):
    """Look up a key in a dict from a template (e.g. ``mydict|dict_get:name``)."""
    return d.get(key)
