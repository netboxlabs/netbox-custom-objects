import contextlib

from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from rest_framework import serializers

from netbox_custom_objects.validators import AllowedSchemesURLValidator

__all__ = ('LaxURLSerializerField',)


class LaxURLSerializerField(serializers.URLField):
    """DRF's URLField, validated and normalized like the form."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Replace the Django URLValidator that URLField adds.
        self.validators = [v for v in self.validators if type(v) is not URLValidator]
        self.validators.append(AllowedSchemesURLValidator(message=self.error_messages['invalid']))

    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        # Add https:// to a value without a scheme, as the form does.  A malformed value is left
        # for the validators to report.
        if value:
            with contextlib.suppress(ValidationError):
                value = forms.URLField(assume_scheme='https').to_python(value)
        return value
