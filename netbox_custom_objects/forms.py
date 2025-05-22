import json

from django import forms
from django.utils.translation import gettext_lazy as _

from netbox_custom_objects.models import (
    CustomObject, CustomObjectType, CustomObjectTypeField, CustomObjectRelation, CustomObjectObjectType
)
from netbox.forms import NetBoxModelForm
from core.models import ObjectType
from extras.choices import CustomFieldTypeChoices, CustomFieldUIEditableChoices
from extras.forms import CustomFieldForm
from utilities.forms.fields import CommentField, ContentTypeChoiceField, DynamicModelChoiceField
from utilities.forms.rendering import FieldSet
from utilities.object_types import object_type_name

__all__ = (
    'CustomObjectTypeForm',
    'CustomObjectTypeFieldForm',
    'CustomObjectType',
)


class CustomObjectTypeForm(NetBoxModelForm):
    fieldsets = (
        FieldSet('name', 'description', 'tags'),
    )
    comments = CommentField()

    class Meta:
        model = CustomObjectType
        fields = ('name', 'description', 'comments', 'tags')


# class CustomObjectTypeFieldForm(NetBoxModelForm):
#     fieldsets = (
#         FieldSet('name', 'label', 'custom_object_type', 'field_type',),
#     )
#     comments = CommentField()
#
#     class Meta:
#         model = CustomObjectTypeField
#         fields = ('name', 'label', 'custom_object_type', 'field_type',)


class CustomContentTypeChoiceField(ContentTypeChoiceField):

    def label_from_instance(self, obj):
        if obj.app_label == 'netbox_custom_objects':
            try:
                custom_object_type = CustomObjectType.objects.get(content_type=obj)
                return f'Custom Objects > {custom_object_type.name}'
            except CustomObjectType.DoesNotExist:
                pass
        try:
            return object_type_name(obj)
        except AttributeError:
            return super().label_from_instance(obj)


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
    related_object_type = CustomContentTypeChoiceField(
        label=_('Related object type'),
        queryset=CustomObjectObjectType.objects.public(),
        help_text=_("Type of the related object (for object/multi-object fields only)")
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Disable changing the custom object type of a field
        if self.instance.pk:
            self.fields['custom_object_type'].disabled = True


class CustomObjectForm(NetBoxModelForm):
    fieldsets = (
        FieldSet('name', 'custom_object_type', 'tags'),
    )
    comments = CommentField()

    class Meta:
        model = CustomObject
        fields = ('name', 'custom_object_type', 'comments', 'tags')

    def _get_custom_fields(self, content_type):
        if self.instance.pk is None:
            return CustomObjectTypeField.objects.none()
        return CustomObjectTypeField.objects.filter(custom_object_type=self.instance.custom_object_type).exclude(
            ui_editable=CustomFieldUIEditableChoices.HIDDEN
        )

    def clean(self):

        # Save custom field data on instance
        new_data = {}
        for cf_name, customfield in self.custom_fields.items():
            if cf_name not in self.fields:
                # Custom fields may be absent when performing bulk updates via import
                continue
            key = cf_name[3:]  # Strip "cf_" from field name
            value = self.cleaned_data.get(cf_name)

            # Convert "empty" values to null
            if value in self.fields[cf_name].empty_values:
                new_data[key] = None
            else:
                if customfield.type == CustomFieldTypeChoices.TYPE_JSON and type(value) is str:
                    value = json.loads(value)
                new_data[key] = customfield.serialize(value)

            self.cleaned_data['data'] = new_data

        return super().clean()

    def _save_m2m(self):
        for cf_name, customfield in self.custom_fields.items():
            key = cf_name[3:]
            if customfield.type == CustomFieldTypeChoices.TYPE_OBJECT:
                CustomObjectRelation.objects.filter(custom_object=self.instance, field__name=key).delete()
                if object_id := self.cleaned_data['data'][key]:
                    CustomObjectRelation.objects.create(custom_object=self.instance, field=customfield, object_id=object_id)
            elif customfield.type == CustomFieldTypeChoices.TYPE_MULTIOBJECT:
                CustomObjectRelation.objects.filter(custom_object=self.instance, field__name=key).delete()
                for object_id in self.cleaned_data['data'].get(key) or []:
                    CustomObjectRelation.objects.create(custom_object=self.instance, field=customfield, object_id=object_id)
        return super()._save_m2m()

    def save(self, commit=True):
        return super().save(commit=commit)
