from netbox_custom_objects.models import CustomObject, CustomObjectType, CustomObjectTypeField

from netbox.forms import NetBoxModelForm
from utilities.forms.fields import CommentField
from utilities.forms.rendering import FieldSet

__all__ = (
    'CustomObjectTypeForm',
    'CustomObjectTypeFieldForm',
    'CustomObjectType',
)


class CustomObjectTypeForm(NetBoxModelForm):
    fieldsets = (
        FieldSet('name', 'description', 'schema', 'tags'),
    )
    comments = CommentField()

    class Meta:
        model = CustomObjectType
        fields = ('name', 'description', 'comments', 'schema', 'tags')


class CustomObjectTypeFieldForm(NetBoxModelForm):
    fieldsets = (
        FieldSet('name', 'label', 'custom_object_type', 'field_type',),
    )
    comments = CommentField()

    class Meta:
        model = CustomObjectTypeField
        fields = ('name', 'label', 'custom_object_type', 'field_type',)


class CustomObjectForm(NetBoxModelForm):
    fieldsets = (
        FieldSet('name', 'custom_object_type', 'data', 'tags'),
    )
    comments = CommentField()

    class Meta:
        model = CustomObject
        fields = ('name', 'custom_object_type', 'comments', 'data', 'tags')
