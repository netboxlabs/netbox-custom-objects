from netbox.registry import registry
from netbox.search import SearchIndex, register_search
from extras.choices import CustomFieldTypeChoices
from . import models, constants


@register_search
class CustomObjectTypeIndex(SearchIndex):
    model = models.CustomObjectType
    fields = (
        ('name', 100),
        ('description', 500),
        ('comments', 5000),
    )
    display_attrs = ('description', 'description')


def register_custom_object_search_index(custom_object_type):
    fields = []
    for field in custom_object_type.fields.all():
        if field.primary or field.type == CustomFieldTypeChoices.TYPE_TEXT:
            fields.append((field.name, 100))

    model = custom_object_type.get_model()
    attrs = {
        "model": model,
        "fields": tuple(fields),
        "display_attrs": tuple(),
    }
    search_index = type(
        f"{custom_object_type.name}SearchIndex",
        (SearchIndex,),
        attrs,
    )
    label = f"{constants.APP_LABEL}.{custom_object_type.get_table_model_name(custom_object_type.id).lower()}"
    registry["search"][label] = search_index
