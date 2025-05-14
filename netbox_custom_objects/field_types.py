from enum import Enum
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django import forms
from django.apps import apps
from rest_framework import serializers

from extras.choices import CustomFieldTypeChoices
from utilities.forms.widgets import DatePicker, DateTimePicker


class FieldType:

    def get_model_field(self, field, **kwargs):
        raise NotImplementedError

    def get_serializer_field(self, field, **kwargs):
        raise NotImplementedError

    def get_filterform_field(self, field, **kwargs):
        raise NotImplementedError

    def get_form_field(self, field, **kwargs):
        raise NotImplementedError

    def get_bulk_edit_form_field(self, field, **kwargs):
        raise NotImplementedError


class TextFieldType(FieldType):

    def get_model_field(self, field, **kwargs):
        kwargs.update({'max_length': field.default})
        return models.CharField(null=True, **kwargs)

    def get_serializer_field(self, field, **kwargs):
        required = kwargs.get("required", False)
        validators = kwargs.pop("validators", None) or []
        # validators.append(self.validator)
        return serializers.CharField(
            **{
                "required": required,
                "allow_null": not required,
                "allow_blank": not required,
                "validators": validators,
                **kwargs,
            }
        )

    def get_bulk_edit_form_field(self, field, **kwargs):
        return forms.CharField(
            max_length=200,
            required=False,
        )

    def get_filterform_field(self, field, **kwargs):
        return forms.CharField(
            label=field,
            max_length=100,
            required=False,
        )


class LongTextFieldType(FieldType):
    def get_model_field(self, field, **kwargs):
        return models.TextField(null=True, **kwargs)


class IntegerFieldType(FieldType):

    def get_model_field(self, field, **kwargs):
        # TODO: handle all args for IntegerField
        kwargs.update({'default': field.default})
        return models.IntegerField(null=True, **kwargs)

    def get_filterform_field(self, field, **kwargs):
        return forms.IntegerField(
            label=field,
            required=False,
        )

class DecimalFieldType(FieldType):
    def get_model_field(self, field, **kwargs):
        return models.DecimalField(
            null=True,
            max_digits=8,
            decimal_places=2,
            **kwargs,
        )


class BooleanFieldType(FieldType):
    def get_model_field(self, field, **kwargs):
        return models.BooleanField(null=True, **kwargs)


class DateFieldType(FieldType):
    def get_model_field(self, field, **kwargs):
        return models.DateField(null=True, **kwargs)

    def get_form_field(self, field, **kwargs):
        return forms.DateField(
            required=False,
            widget=DatePicker()
        )


class DateTimeFieldType(FieldType):
    def get_model_field(self, field, **kwargs):
        return models.DateTimeField(null=True, **kwargs)

    def get_form_field(self, field, **kwargs):
        return forms.DateTimeField(
            required=False,
            widget=DateTimePicker()
        )


class URLFieldType(FieldType):
    def get_model_field(self, field, **kwargs):
        return models.URLField(null=True, **kwargs)


class JSONFieldType(FieldType):
    def get_model_field(self, field, **kwargs):
        return models.JSONField(null=True, **kwargs)


class SelectFieldType(FieldType):
    def get_model_field(self, field, **kwargs):
        return models.CharField(
            max_length=100,
            choices=field.choices,
            null=True,
            **kwargs,
        )


class MultiSelectFieldType(FieldType):
    ...


class ObjectFieldType(FieldType):
    def get_model_field(self, field, **kwargs):
        # return models.IntegerField(**kwargs)
        # content_type = ContentType.objects.get_for_model(instance)
        content_type = ContentType.objects.get(pk=field.related_object_type_id)
        to_model = content_type.model_class()._meta.object_name
        to_ct = f'{content_type.app_label}.{to_model}'
        model = apps.get_model(to_ct)
        f = models.ForeignKey(model, null=True, blank=True, on_delete=models.CASCADE)
        return f

    def get_filterform_field(self, field, **kwargs):
        return None


class MultiObjectFieldType(FieldType):
    def get_model_field(self, field, **kwargs):
        content_type = ContentType.objects.get(pk=field.related_object_type_id)
        to_model = content_type.model_class()._meta.object_name
        to_ct = f'{content_type.app_label}.{to_model}'
        model = apps.get_model(to_ct)
        f = models.ManyToManyField(model, related_name=field.custom_object_type.name.lower() + 's')
        # f = models.ManyToOneRel(model, related_name=field.custom_object_type.name.lower() + 's')
        return f

    def get_filterform_field(self, field, **kwargs):
        return None


FIELD_TYPE_CLASS = {
    CustomFieldTypeChoices.TYPE_TEXT: TextFieldType,
    CustomFieldTypeChoices.TYPE_LONGTEXT: LongTextFieldType,
    CustomFieldTypeChoices.TYPE_INTEGER: IntegerFieldType,
    CustomFieldTypeChoices.TYPE_DECIMAL: DecimalFieldType,
    CustomFieldTypeChoices.TYPE_BOOLEAN: BooleanFieldType,
    CustomFieldTypeChoices.TYPE_DATE: DateFieldType,
    CustomFieldTypeChoices.TYPE_DATETIME: DateTimeFieldType,
    CustomFieldTypeChoices.TYPE_URL: URLFieldType,
    CustomFieldTypeChoices.TYPE_JSON: JSONFieldType,
    CustomFieldTypeChoices.TYPE_SELECT: SelectFieldType,
    CustomFieldTypeChoices.TYPE_MULTISELECT: MultiSelectFieldType,
    CustomFieldTypeChoices.TYPE_OBJECT: ObjectFieldType,
    CustomFieldTypeChoices.TYPE_MULTIOBJECT: MultiObjectFieldType,
}
