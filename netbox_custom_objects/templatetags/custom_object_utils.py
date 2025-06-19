from django import template
from extras.choices import CustomFieldTypeChoices, CustomFieldUIVisibleChoices

from netbox_custom_objects.models import CustomObjectTypeField

__all__ = (
    "get_field_object_type",
    "get_field_type_verbose_name",
    "get_field_value",
    "get_field_is_ui_visible",
    "get_child_relations",
)

register = template.Library()

custom_field_type_verbose_names = {c[0]: c[1] for c in CustomFieldTypeChoices.CHOICES}


@register.filter(name="get_field_object_type")
def get_field_object_type(field: CustomObjectTypeField) -> str:
    return field.related_object_type_label


@register.filter(name="get_field_type_verbose_name")
def get_field_type_verbose_name(field: CustomObjectTypeField) -> str:
    return custom_field_type_verbose_names[field.type]


@register.filter(name="get_field_value")
def get_field_value(obj, field: CustomObjectTypeField) -> str:
    return getattr(obj, field.name)


@register.filter(name="get_field_is_ui_visible")
def get_field_is_ui_visible(obj, field: CustomObjectTypeField) -> bool:
    if field.ui_visible == CustomFieldUIVisibleChoices.ALWAYS:
        return True
    if field.type == CustomFieldTypeChoices.TYPE_MULTIOBJECT:
        field_value = getattr(obj, field.name).exists()
    else:
        field_value = getattr(obj, field.name)
    if field.ui_visible == CustomFieldUIVisibleChoices.IF_SET and field_value:
        return True
    return False


@register.filter(name="get_child_relations")
def get_child_relations(obj, field: CustomObjectTypeField):
    return getattr(obj, field.name).all()
