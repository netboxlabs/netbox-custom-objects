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
