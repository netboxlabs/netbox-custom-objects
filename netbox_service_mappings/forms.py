from netbox_service_mappings.models import ServiceMapping, ServiceMappingType

from netbox.forms import NetBoxModelForm
from utilities.forms.fields import CommentField
from utilities.forms.rendering import FieldSet

__all__ = (
    'ServiceMappingTypeForm',
    'ServiceMappingType',
)


class ServiceMappingTypeForm(NetBoxModelForm):
    fieldsets = (
        FieldSet('name', 'description', 'schema', 'tags'),
    )
    comments = CommentField()

    class Meta:
        model = ServiceMappingType
        fields = ('name', 'description', 'comments', 'schema', 'tags')


class ServiceMappingForm(NetBoxModelForm):
    fieldsets = (
        FieldSet('name', 'type', 'data', 'tags'),
    )
    comments = CommentField()

    class Meta:
        model = ServiceMapping
        fields = ('name', 'type', 'comments', 'data', 'tags')
