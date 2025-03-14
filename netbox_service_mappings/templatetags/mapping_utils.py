from django import template
from django.apps import apps
from django.utils.safestring import mark_safe
from django.urls import reverse
from netbox_service_mappings.models import MappingTypeField

__all__ = (
    'get_field_object_type',
    'get_field_value',
)

register = template.Library()


@register.filter(name="get_field_object_type")
def get_field_object_type(field: MappingTypeField) -> str:
    ct = field.content_type
    model = apps.get_model(ct.app_label, ct.model)
    label = model._meta.verbose_name
    return label


@register.filter(name="get_field_value")
def get_field_value(obj, field: MappingTypeField) -> str:
    return str(obj.data.get(field.name))


@register.filter(name="get_child_relations")
def get_child_relations(field: MappingTypeField, obj):
    return field.get_child_relations(obj)
