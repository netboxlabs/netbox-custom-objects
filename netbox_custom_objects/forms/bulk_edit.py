from django import forms
from django.utils.translation import gettext_lazy as _
from netbox.forms import (NetBoxModelBulkEditForm)
from utilities.forms.fields import (CommentField)
from utilities.forms.rendering import FieldSet

from netbox_custom_objects.models import (CustomObjectType)

__all__ = (
    'CustomObjectTypeBulkEditForm',
)


class CustomObjectTypeBulkEditForm(NetBoxModelBulkEditForm):
    description = forms.CharField(
        label=_("Description"), max_length=200, required=False
    )
    comments = CommentField()

    model = CustomObjectType
    fieldsets = (FieldSet("description"),)
    nullable_fields = (
        "description",
        "comments",
    )
