from django import forms
from django.utils.translation import gettext_lazy as _

from netbox.forms import NetBoxModelFilterSetForm
from netbox_custom_objects.models import CustomObjectType
from utilities.forms.constants import BOOLEAN_WITH_BLANK_CHOICES
from utilities.forms.fields import TagFilterField
from utilities.forms.rendering import FieldSet

__all__ = ('CustomObjectTypeFilterForm',)


class CustomObjectTypeFilterForm(NetBoxModelFilterSetForm):
    model = CustomObjectType
    fieldsets = (
        FieldSet('q', 'filter_id', 'tag'),
        FieldSet('group_name', 'config_context_enabled', name=_('Attributes')),
    )
    group_name = forms.CharField(
        label=_('Group name'),
        required=False,
    )
    config_context_enabled = forms.NullBooleanField(
        label=_('Config context support'),
        required=False,
        widget=forms.Select(choices=BOOLEAN_WITH_BLANK_CHOICES),
    )
    tag = TagFilterField(model)
