import django_filters
from dataclasses import dataclass
from typing import Any, Dict, Optional, Type

from django.contrib.postgres.fields import ArrayField
from django.db.models import JSONField, QuerySet, Q

from extras.choices import CustomFieldTypeChoices
from netbox.filtersets import NetBoxModelFilterSet

from .models import CustomObjectType

__all__ = (
    "CustomObjectTypeFilterSet",
    "get_filterset_class",
)


@dataclass
class FilterSpec:
    """
    Declarative specification describing how a custom field type
    should be translated into a django-filter Filter instance.
    """
    filter_class: Type[django_filters.Filter]
    lookup_expr: Optional[str] = None
    extra_kwargs: Optional[Dict[str, Any]] = None

    def build(self, field_name: str, label: str, queryset: Optional[QuerySet] = None, **kwargs) -> django_filters.Filter:
        """
        Instantiate and return a django-filter Filter.
        Allows overriding defaults via **kwargs.
        """
        filter_kwargs = {
            "field_name": field_name,
            "label": label,
        }

        if self.lookup_expr:
            filter_kwargs["lookup_expr"] = self.lookup_expr

        if queryset is not None:
            filter_kwargs["queryset"] = queryset

        # Apply defaults from the spec
        if self.extra_kwargs:
            filter_kwargs.update(self.extra_kwargs)

        # Apply dynamic overrides (e.g. resolved choices)
        filter_kwargs.update(kwargs)

        return self.filter_class(**filter_kwargs)


FIELD_TYPE_FILTERS = {
    CustomFieldTypeChoices.TYPE_TEXT: FilterSpec(django_filters.CharFilter, lookup_expr="icontains"),
    CustomFieldTypeChoices.TYPE_LONGTEXT: FilterSpec(django_filters.CharFilter, lookup_expr="icontains"),
    CustomFieldTypeChoices.TYPE_INTEGER: FilterSpec(django_filters.NumberFilter, lookup_expr="exact"),
    CustomFieldTypeChoices.TYPE_DECIMAL: FilterSpec(django_filters.NumberFilter, lookup_expr="exact"),
    CustomFieldTypeChoices.TYPE_BOOLEAN: FilterSpec(django_filters.BooleanFilter),
    CustomFieldTypeChoices.TYPE_DATE: FilterSpec(django_filters.DateFilter, lookup_expr="exact"),
    CustomFieldTypeChoices.TYPE_DATETIME: FilterSpec(django_filters.DateTimeFilter, lookup_expr="exact"),
    CustomFieldTypeChoices.TYPE_URL: FilterSpec(django_filters.CharFilter, lookup_expr="icontains"),
    CustomFieldTypeChoices.TYPE_JSON: FilterSpec(django_filters.CharFilter, lookup_expr="icontains"),
    CustomFieldTypeChoices.TYPE_SELECT: FilterSpec(
        django_filters.ChoiceFilter,
        extra_kwargs={"choices": lambda f: f.choices}
    ),
    CustomFieldTypeChoices.TYPE_MULTISELECT: FilterSpec(
        django_filters.MultipleChoiceFilter,
        extra_kwargs={"choices": lambda f: f.choices}
    ),
    CustomFieldTypeChoices.TYPE_OBJECT: FilterSpec(django_filters.ModelChoiceFilter),
    CustomFieldTypeChoices.TYPE_MULTIOBJECT: FilterSpec(django_filters.ModelMultipleChoiceFilter),
}


class CustomObjectTypeFilterSet(NetBoxModelFilterSet):
    class Meta:
        model = CustomObjectType
        fields = (
            "id",
            "name",
        )


def build_filter_for_field(field) -> Optional[django_filters.Filter]:
    spec = FIELD_TYPE_FILTERS.get(field.type)
    if not spec:
        return None

    queryset = None
    if field.type in (
        CustomFieldTypeChoices.TYPE_OBJECT,
        CustomFieldTypeChoices.TYPE_MULTIOBJECT,
    ):
        queryset = field.related_object_type.model_class().objects.all()

    extra_kwargs = {}
    if spec.extra_kwargs:
        for key, value in spec.extra_kwargs.items():
            extra_kwargs[key] = value(field) if callable(value) else value

    return spec.build(
        field_name=field.name,
        label=field.label,
        queryset=queryset,
        **extra_kwargs,
    )


def get_filterset_class(model):
    """
    Create and return a filterset class for the given custom object model.
    """
    # Get standard fields from the model
    fields = [field.name for field in model._meta.fields]

    meta = type(
        "Meta",
        (),
        {
            "model": model,
            "fields": fields,
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
        "__module__": "netbox_custom_objects.filtersets",
        "search": search,
    }

    # For each custom field, add a corresponding filter
    for field in model.custom_object_type.fields.all():
        filter_instance = build_filter_for_field(field)
        if filter_instance:
            attrs[field.name] = filter_instance

    return type(
        f"{model._meta.object_name}FilterSet",
        (NetBoxModelFilterSet,),
        attrs,
    )
