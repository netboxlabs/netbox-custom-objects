from enum import Enum
from django.db import models
from django import forms
from rest_framework import serializers

from extras.choices import CustomFieldTypeChoices


class FieldType:

    def get_model_field(self, field, **kwargs):
        raise NotImplementedError

    def get_serializer_field(self, field, **kwargs):
        raise NotImplementedError

    def get_filterform_field(self, field, **kwargs):
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
    ...


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
    ...


class BooleanFieldType(FieldType):
    ...


class DateFieldType(FieldType):
    ...


class DateTimeFieldType(FieldType):
    ...


class URLFieldType(FieldType):
    ...


class JSONFieldType(FieldType):
    ...


class SelectFieldType(FieldType):
    ...


class MultiSelectFieldType(FieldType):
    ...


class ObjectFieldType(FieldType):
    def get_model_field(self, field, **kwargs):
        return models.IntegerField(**kwargs)


class MultiObjectFieldType(FieldType):
    ...


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
