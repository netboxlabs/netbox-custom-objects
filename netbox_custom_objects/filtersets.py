import django_filters
from django.contrib.postgres.fields import ArrayField
from django.db.models import JSONField, Q

from extras.choices import CustomFieldTypeChoices
from netbox.filtersets import NetBoxModelFilterSet

from .models import CustomObjectType
from dataclasses import dataclass

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


@dataclass
class FilterSpec:
    """
    Declarative specification describing how a custom field type
    should be translated into a django-filter Filter instance.
    """

    filter_class: type
    lookup_expr: str | None = None
    extra_kwargs: dict | None = None

    def build(self, *, field_name, label, queryset=None):
        """
        Instantiate and return a django-filter Filter.

        Args:
            field_name (str): Model field name to filter on.
            label (str): Human-readable filter label.
            queryset (QuerySet, optional): Queryset for relational filters.
            Defaults to None.

        Returns:
            django_filters.Filter: Configured filter instance.
        """
        kwargs = {
            "field_name": field_name,
            "label": label,
        }

        if self.lookup_expr:
            kwargs["lookup_expr"] = self.lookup_expr

        if queryset is not None:
            kwargs["queryset"] = queryset

        if self.extra_kwargs:
            kwargs.update(self.extra_kwargs)

        return self.filter_class(**kwargs)


def build_filter_for_field(field):
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

    attrs = {
        "Meta": meta,
        "__module__": "netbox_custom_objects.filtersets",
    }

    # For each custom field, add a corresponding filter
    for field in model.custom_object_type.fields.all():
        filter_instance = build_filter_for_field(field)
        if filter_instance:
            attrs[field.name] = filter_instance

    return type(
        f"{model.__name__}FilterSet",
        (django_filters.FilterSet,),
        attrs,
    )
