import logging

from netbox.forms import NetBoxModelFilterSetForm
from utilities.forms.fields import TagFilterField
from . import field_types


logger = logging.getLogger("netbox_custom_objects.dynamic_forms")


def build_filterset_form_class(model):
    """
    Dynamically build a FilterSetForm class for a custom object model.

    This is the shared implementation used by both CustomObjectListView and the
    ObjectSelectorView patch so that the two stay in sync.
    """
    custom_object_type = model.custom_object_type
    attrs = {
        "model": model,
        "__module__": "database.filterset_forms",
        "tag": TagFilterField(model),
    }
    for field in custom_object_type.fields.all():
        field_type = field_types.FIELD_TYPE_CLASS[field.type]()
        try:
            form_field = field_type.get_filterform_field(field)
            if isinstance(form_field, dict):
                # Polymorphic fields return one form field per allowed type.
                attrs.update(form_field)
            elif form_field is not None:
                attrs[field.name] = form_field
        except NotImplementedError:
            logger.debug("build_filterset_form_class: {} field is not supported".format(field.name))

    return type(
        f"{model._meta.object_name}FilterForm",
        (NetBoxModelFilterSetForm,),
        attrs,
    )
