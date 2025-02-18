from django import template
from netbox_service_mappings.models import MappingTypeField

__all__ = (
    'get_field_value',
)

register = template.Library()


@register.filter(name="get_field_value")
def get_field_value(obj, field: MappingTypeField) -> str:
    return str(obj.data.get(field.name))
