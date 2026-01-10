import django_filters
from django.contrib.postgres.fields import ArrayField
from django.db.models import JSONField, Q

from extras.choices import CustomFieldTypeChoices
from netbox.filtersets import NetBoxModelFilterSet

from .models import CustomObjectType

__all__ = (
    "CustomObjectTypeFilterSet",
    "get_filterset_class",
)


class CustomObjectTypeFilterSet(NetBoxModelFilterSet):
    class Meta:
        model = CustomObjectType
        fields = (
            "id",
            "name",
        )


def get_filterset_class(model):
    """
    Create and return a filterset class for the given custom object model.
    """
    fields = [field.name for field in model._meta.fields]

    meta = type(
        "Meta",
        (),
        {
            "model": model,
            "fields": fields,
            # TODO: overrides should come from FieldType
            # These are placeholders; should use different logic
            "filter_overrides": {
                JSONField: {
                    "filter_class": django_filters.CharFilter,
                    "extra": lambda f: {
                        "lookup_expr": "icontains",
                    },
                },
                ArrayField: {
                    "filter_class": django_filters.CharFilter,
                    "extra": lambda f: {
                        "lookup_expr": "icontains",
                    },
                },
            },
        },
    )

    attrs = {}
    attrs["Meta"] = meta
    attrs["__module__"] = "netbox_custom_objects.filtersets"

    # For each custom field, add a corresponding filter
    for field in model.custom_object_type.fields.all():
        # Check field type and assign appropriate filter
        if field.type in [CustomFieldTypeChoices.TYPE_TEXT, CustomFieldTypeChoices.TYPE_LONGTEXT]:
            # CharFilter for text fields
            attrs[field.name] = django_filters.CharFilter(field_name=field.name, lookup_expr='icontains', label=field.label)
        elif field.type == CustomFieldTypeChoices.TYPE_INTEGER:
            attrs[field.name] = django_filters.NumberFilter(field_name=field.name, lookup_expr="exact", label=field.label)
        elif field.type == CustomFieldTypeChoices.TYPE_BOOLEAN:
            attrs[field.name] = django_filters.BooleanFilter(field_name=field.name, label=field.label)
        elif field.type == CustomFieldTypeChoices.TYPE_DATE:
            attrs[field.name] = django_filters.DateFilter(field_name=field.name, lookup_expr='exact', label=field.label)
        elif field.type == CustomFieldTypeChoices.TYPE_URL:
            attrs[field.name] = django_filters.CharFilter(field_name=field.name, lookup_expr='icontains', label=field.label)
        # For relationships, you might want ModelChoiceFilter or MultipleChoiceFilter
        elif field.type == CustomFieldTypeChoices.TYPE_OBJECT:
            # For related objects, assuming the field's related_object_type provides the model class
            rel_model_class = field.related_object_type.model_class()
            attrs[field.name] = django_filters.ModelChoiceFilter(
                queryset=rel_model_class.objects.all(),
                field_name=field.name,
                label=field.label
            )
        # Add other field types as needed
        # Continue for other field types...
        else
            return None

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        q = Q()
        for field in model.custom_object_type.fields.all():
            if field.type in [
                CustomFieldTypeChoices.TYPE_TEXT,
                CustomFieldTypeChoices.TYPE_LONGTEXT,
                CustomFieldTypeChoices.TYPE_JSON,
                CustomFieldTypeChoices.TYPE_URL,
            ]:
                q |= Q(**{f"{field.name}__icontains": value})
        return queryset.filter(q)

    attrs = {
        "Meta": meta,
        "__module__": "database.filtersets",
        "search": search,
    }

    return type(
        f"{model._meta.object_name}FilterSet",
        (NetBoxModelFilterSet,),
        attrs,
    )
