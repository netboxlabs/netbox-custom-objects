from django import forms
from django.utils.translation import gettext_lazy as _

from extras.models import CustomField
from netbox_custom_objects.models import CustomObject, CustomObjectType, CustomObjectTypeField

from netbox.forms import NetBoxModelForm
from extras.forms import CustomFieldForm
from utilities.forms.fields import CommentField, DynamicModelChoiceField
from utilities.forms.rendering import FieldSet

__all__ = (
    'CustomObjectTypeForm',
    'CustomObjectTypeFieldForm',
    'CustomObjectType',
)


class CustomObjectTypeForm(NetBoxModelForm):
    fieldsets = (
        FieldSet('name', 'slug', 'description', 'schema', 'tags'),
    )
    comments = CommentField()

    class Meta:
        model = CustomObjectType
        fields = ('name', 'slug', 'description', 'comments', 'schema', 'tags')


# class CustomObjectTypeFieldForm(NetBoxModelForm):
#     fieldsets = (
#         FieldSet('name', 'label', 'custom_object_type', 'field_type',),
#     )
#     comments = CommentField()
#
#     class Meta:
#         model = CustomObjectTypeField
#         fields = ('name', 'label', 'custom_object_type', 'field_type',)


class CustomObjectTypeFieldForm(CustomFieldForm):
    # This field should be removed or at least "required" should be defeated
    object_types = forms.CharField(
        label=_('Object types'),
        help_text=_("The type(s) of object that have this custom field"),
        required=False,
    )
    custom_object_type = DynamicModelChoiceField(
        queryset=CustomObjectType.objects.all(),
        required=True,
        label=_('Custom object type')
    )

    fieldsets = (
        FieldSet(
            'custom_object_type', 'name', 'label', 'group_name', 'description', 'type', 'required', 'unique', 'default',
            name=_('Custom Field')
        ),
        FieldSet(
            'search_weight', 'filter_logic', 'ui_visible', 'ui_editable', 'weight', 'is_cloneable', name=_('Behavior')
        ),
    )

    class Meta:
        model = CustomObjectTypeField
        # fields = (
        #     'custom_object_type', 'name', 'label', 'type', 'validation_regex', 'validation_minimum', 'validation_maximum',
        #     'related_object_type',
        # )
        fields = '__all__'


class CustomObjectForm(NetBoxModelForm):
    fieldsets = (
        FieldSet('name', 'custom_object_type', 'data', 'tags'),
    )
    comments = CommentField()

    class Meta:
        model = CustomObject
        fields = ('name', 'custom_object_type', 'comments', 'data', 'tags')
