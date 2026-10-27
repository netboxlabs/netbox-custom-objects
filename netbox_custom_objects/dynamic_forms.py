import logging

from django.utils.translation import gettext_lazy as _
from extras.choices import CustomFieldFilterLogicChoices, CustomFieldUIEditableChoices
from netbox.forms import NetBoxModelFilterSetForm
from netbox.forms.mixins import OwnerFilterMixin as OwnerFilterFormMixin
from utilities.forms.fields import TagFilterField
from utilities.forms.rendering import FieldSet
from . import field_types


logger = logging.getLogger('netbox_custom_objects.dynamic_forms')


def build_filterset_form_class(model):
    """
    Dynamically build a FilterSetForm class for a custom object model.

    This is the shared implementation used by both CustomObjectListView and the
    ObjectSelectorView patch so that the two stay in sync.
    """
    custom_object_type = model.custom_object_type
    attrs = {
        'model': model,
        '__module__': 'database.filterset_forms',
        'tag': TagFilterField(model),
    }
    # Form field names per field group. Fields are ordered by group name, so ungrouped ('') comes first.
    groups = {}
    for field in custom_object_type.fields.all():
        # Like core custom fields: skip fields hidden from the UI, and those the filterset doesn't filter.
        if field.ui_editable == CustomFieldUIEditableChoices.HIDDEN:
            continue
        if field.filter_logic == CustomFieldFilterLogicChoices.FILTER_DISABLED:
            continue
        field_type = field_types.FIELD_TYPE_CLASS[field.type]()
        try:
            form_field = field_type.get_filterform_field(field)
            if isinstance(form_field, dict):
                # Polymorphic fields return one form field per allowed type.
                attrs.update(form_field)
                names = list(form_field)
            elif form_field is not None:
                attrs[field.name] = form_field
                names = [field.name]
            else:
                continue
        except NotImplementedError:
            logger.debug('build_filterset_form_class: {} field is not supported'.format(field.name))
            continue
        groups.setdefault(field.group_name or '', []).extend(names)

    attrs['fieldsets'] = (
        FieldSet('q', 'filter_id', 'tag'),
        *(FieldSet(*names, name=group_name or custom_object_type.display_name) for group_name, names in groups.items()),
        FieldSet('owner_group_id', 'owner_id', name=_('Ownership')),
    )

    return type(
        f'{model._meta.object_name}FilterForm',
        (
            OwnerFilterFormMixin,
            NetBoxModelFilterSetForm,
        ),
        attrs,
    )
