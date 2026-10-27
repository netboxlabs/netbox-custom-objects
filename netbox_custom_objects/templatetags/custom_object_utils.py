from django import template
from django.conf import settings
from packaging.version import InvalidVersion, Version

from extras.choices import CustomFieldUIVisibleChoices
from netbox_custom_objects.choices import CustomObjectFieldTypeChoices
from netbox_custom_objects.field_types import URLFieldType, format_array_value
from netbox_custom_objects.models import CustomObjectTypeField
from netbox_custom_objects.utilities import build_map_url

__all__ = (
    'get_child_relations',
    'get_coordinate_map_url',
    'get_field_is_ui_visible',
    'get_field_object_type',
    'get_field_type_verbose_name',
    'get_field_value',
    'get_url_field_html',
    'sticky_actions_supported',
)

register = template.Library()

custom_field_type_verbose_names = {c[0]: c[1] for c in CustomObjectFieldTypeChoices.CHOICES}


@register.filter(name='get_field_object_type')
def get_field_object_type(field: CustomObjectTypeField) -> str:
    return field.related_object_type_label


@register.filter(name='get_field_type_verbose_name')
def get_field_type_verbose_name(field: CustomObjectTypeField) -> str:
    return custom_field_type_verbose_names[field.type]


@register.filter(name='get_field_value')
def get_field_value(obj, field: CustomObjectTypeField):
    if field.type == CustomObjectFieldTypeChoices.TYPE_COORDINATES:
        latitude = getattr(obj, f'{field.name}_latitude', None)
        longitude = getattr(obj, f'{field.name}_longitude', None)
        if latitude is None or longitude is None:
            return None
        return f'{latitude}, {longitude}'
    if field.type == CustomObjectFieldTypeChoices.TYPE_ARRAY:
        return format_array_value(getattr(obj, field.name)) or None
    return getattr(obj, field.name)


@register.filter(name='get_coordinate_map_url')
def get_coordinate_map_url(obj, field: CustomObjectTypeField):
    """Return the external map URL for a coordinates field, or None."""
    if field.type != CustomObjectFieldTypeChoices.TYPE_COORDINATES:
        return None
    latitude = getattr(obj, f'{field.name}_latitude', None)
    longitude = getattr(obj, f'{field.name}_longitude', None)
    return build_map_url(latitude, longitude)


@register.filter(name='get_field_is_ui_visible')
def get_field_is_ui_visible(obj, field: CustomObjectTypeField) -> bool:
    if field.ui_visible == CustomFieldUIVisibleChoices.ALWAYS:
        return True
    if field.type == CustomObjectFieldTypeChoices.TYPE_MULTIOBJECT:
        field_value = getattr(obj, field.name).exists()
    elif field.type == CustomObjectFieldTypeChoices.TYPE_COORDINATES:
        field_value = (
            getattr(obj, f'{field.name}_latitude', None) is not None
            and getattr(obj, f'{field.name}_longitude', None) is not None
        )
    else:
        field_value = getattr(obj, field.name)
    if field.ui_visible == CustomFieldUIVisibleChoices.IF_SET and field_value:
        return True
    return False


@register.filter(name='get_url_field_html')
def get_url_field_html(obj, field: CustomObjectTypeField):
    """
    Render a url-type field as a safe link: the title as link text if set,
    falling back to the URL itself.  Delegates to URLFieldType.render_url_html()
    so the detail page and the list-view table column render identically.
    Returns '' when the URL itself is unset, regardless of whether a title is set.
    """
    if field.type != CustomObjectFieldTypeChoices.TYPE_URL:
        return ''
    url = getattr(obj, field.name, None)
    title = getattr(obj, f'{field.name}_title', None)
    return URLFieldType.render_url_html(url, title)


@register.filter(name='get_child_relations')
def get_child_relations(obj, field: CustomObjectTypeField):
    return getattr(obj, field.name)


@register.filter(name='dict_get')
def dict_get(d, key):
    """Look up a key in a dict from a template (e.g. ``mydict|dict_get:name``)."""
    return d.get(key)


@register.simple_tag
def sticky_actions_supported():
    """Whether core styles ``sticky-actions-footer`` (NetBox 4.6+); 4.5 has only ``btn-float-group-right``."""
    # COMPAT(netbox<4.6.0): drop this tag and the legacy footer it selects.
    try:
        return Version(settings.RELEASE.version) >= Version('4.6')
    except (AttributeError, InvalidVersion):
        return True
