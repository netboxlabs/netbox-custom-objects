import django_filters
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional, Type

from django import forms as django_forms
from django.db.models import QuerySet, Q
from django.utils.dateparse import parse_date, parse_datetime

from extras.choices import CustomFieldTypeChoices
from netbox.filtersets import NetBoxModelFilterSet

from .models import CustomObjectType

__all__ = (
    "ArrayContainsFilter",
    "CustomObjectTypeFilterSet",
    "PolymorphicMultiObjectFilter",
    "PolymorphicObjectFilter",
    "get_filterset_class",
)


class PolymorphicObjectFilter(django_filters.Filter):
    """
    Filter for one allowed type of a polymorphic GFK object field.

    A polymorphic GFK field stores its value as two concrete columns:
    ``{name}_content_type_id`` (FK to ContentType) and ``{name}_object_id``
    (integer PK).  One instance is created per allowed content type; it
    matches rows where both columns equal the fixed content-type ID and the
    submitted object PK.

    Inherits from ``django_filters.Filter`` (not ``ModelChoiceFilter``) so that
    ``NetBoxModelFilterSet.get_additional_lookups()`` does not attempt to validate
    the virtual filter name against real model fields and raise ``ValueError``.
    """

    field_class = django_forms.ModelChoiceField

    def __init__(self, *, content_type_id, gfk_field_name, queryset, **kwargs):
        self.content_type_id = content_type_id
        self.gfk_field_name = gfk_field_name
        super().__init__(queryset=queryset, **kwargs)

    def filter(self, qs, value):
        if value in (None, "", [], ()):
            return qs
        return qs.filter(**{
            f"{self.gfk_field_name}_content_type_id": self.content_type_id,
            f"{self.gfk_field_name}_object_id": value.pk,
        })


class PolymorphicMultiObjectFilter(django_filters.Filter):
    """
    Filter for one allowed type of a polymorphic GFK multiobject field.

    The through table has (source_id, content_type_id, object_id) columns.
    One instance is created per allowed content type; submitting one or more
    PKs returns all source objects that reference any of those PKs via this
    type (OR semantics, no duplicates).

    Inherits from ``django_filters.Filter`` (not ``ModelMultipleChoiceFilter``)
    so that ``NetBoxModelFilterSet.get_additional_lookups()`` does not attempt
    to validate the virtual filter name against real model fields.
    """

    field_class = django_forms.ModelMultipleChoiceField

    def __init__(self, *, content_type_id, through_model_name, queryset, **kwargs):
        self.content_type_id = content_type_id
        self.through_model_name = through_model_name
        super().__init__(queryset=queryset, **kwargs)

    def filter(self, qs, value):
        if not value:
            return qs
        from django.apps import apps as django_apps
        from netbox_custom_objects.constants import APP_LABEL
        try:
            through = django_apps.get_model(APP_LABEL, self.through_model_name)
        except LookupError:
            return qs.none()
        source_ids = through.objects.filter(
            content_type_id=self.content_type_id,
            object_id__in=[obj.pk for obj in value],
        ).values_list("source_id", flat=True)
        return qs.filter(pk__in=source_ids).distinct()


class ArrayContainsFilter(django_filters.MultipleChoiceFilter):
    """
    Filter for ArrayField (TYPE_MULTISELECT): checks if the array contains any
    of the selected values using OR semantics with PostgreSQL array containment.

    Standard MultipleChoiceFilter uses ``exact`` lookup, which compares the
    entire array rather than checking membership. This class uses
    ``__contains=[v]`` instead, matching Django's array containment operator.
    """

    def filter(self, qs, value):
        if not value:
            return qs
        q = Q()
        for v in value:
            q |= Q(**{f"{self.field_name}__contains": [v]})
        return qs.filter(q).distinct()


@dataclass
class FilterSpec:
    """
    Declarative specification describing how a custom field type
    should be translated into a django-filter Filter instance.
    """
    filter_class: Type[django_filters.Filter]
    lookup_expr: Optional[str] = None
    extra_kwargs: Optional[Dict[str, Any]] = None

    def build(
        self, field_name: str, label: str, queryset: Optional[QuerySet] = None, **kwargs
        ) -> django_filters.Filter:
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

        # Callers (build_filter_for_field) resolve extra_kwargs callables and pass
        # the results here as **kwargs; merge them directly.
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
        django_filters.MultipleChoiceFilter,
        extra_kwargs={"choices": lambda f: f.choices}
    ),
    CustomFieldTypeChoices.TYPE_MULTISELECT: FilterSpec(
        ArrayContainsFilter,
        extra_kwargs={"choices": lambda f: f.choices}
    ),
    CustomFieldTypeChoices.TYPE_OBJECT: FilterSpec(django_filters.ModelChoiceFilter),
    # CustomManyToManyField inherits ManyToManyField, so Django's ORM translates
    # `field__in=values` to a JOIN through the through table at the SQL level.
    # The custom descriptor/manager only affects instance-level access (e.g.
    # `instance.field.all()`), not queryset filtering, so ModelMultipleChoiceFilter
    # is correct here without needing explicit through-table queries.
    CustomFieldTypeChoices.TYPE_MULTIOBJECT: FilterSpec(django_filters.ModelMultipleChoiceFilter),
}


class CustomObjectTypeFilterSet(NetBoxModelFilterSet):
    class Meta:
        model = CustomObjectType
        fields = (
            "id",
            "name",
            "group_name",
        )


def _build_polymorphic_filters(field) -> dict:
    """Build one filter per allowed type for a polymorphic object/multiobject field."""
    filters = {}
    base_label = field.label or field.name
    is_multi = field.type == CustomFieldTypeChoices.TYPE_MULTIOBJECT

    for ot in field.related_object_types.all():
        model_class = ot.model_class()
        if model_class is None:
            continue
        filter_name = f"{field.name}_{ot.app_label}_{ot.model}"
        label = f"{base_label} ({model_class._meta.verbose_name})"
        queryset = model_class.objects.all()
        if is_multi:
            filters[filter_name] = PolymorphicMultiObjectFilter(
                content_type_id=ot.id,
                through_model_name=field.through_model_name,
                queryset=queryset,
                label=label,
            )
        else:
            filters[filter_name] = PolymorphicObjectFilter(
                content_type_id=ot.id,
                gfk_field_name=field.name,
                queryset=queryset,
                label=label,
            )

    return filters


def build_filter_for_field(field) -> dict:
    """
    Build django-filter Filter instances for a CustomObjectTypeField.

    Returns a mapping of filter name → Filter.  For non-polymorphic fields
    this is always ``{field.name: filter}``; for polymorphic object/multiobject
    fields one entry is emitted per allowed related type, named
    ``{field.name}_{app_label}_{model}``.
    """
    if field.is_polymorphic and field.type in (
        CustomFieldTypeChoices.TYPE_OBJECT,
        CustomFieldTypeChoices.TYPE_MULTIOBJECT,
    ):
        return _build_polymorphic_filters(field)

    spec = FIELD_TYPE_FILTERS.get(field.type)
    if not spec:
        return {}

    queryset = None
    if field.type in (
        CustomFieldTypeChoices.TYPE_OBJECT,
        CustomFieldTypeChoices.TYPE_MULTIOBJECT,
    ):
        related_object_type = getattr(field, "related_object_type", None)
        if not related_object_type:
            # Defensive guard: if data integrity is compromised and the related object type
            # is missing, skip building a filter for this field rather than raising.
            return {}
        model_class = related_object_type.model_class()
        if model_class is None:
            # ContentType exists but the model is no longer installed (e.g. stale content type).
            return {}
        queryset = model_class.objects.all()

    extra_kwargs = {}
    if spec.extra_kwargs:
        for key, value in spec.extra_kwargs.items():
            extra_kwargs[key] = value(field) if callable(value) else value

    return {
        field.name: spec.build(
            field_name=field.name,
            label=field.label or field.name,
            queryset=queryset,
            **extra_kwargs,
        )
    }


def get_filterset_class(model):
    """
    Create and return a filterset class for the given custom object model.
    """
    # fields=[] disables auto-generation; all filters are added explicitly below
    # via build_filter_for_field so there are no shadowed duplicates.
    meta = type(
        "Meta",
        (),
        {
            "model": model,
            "fields": [],
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
                CustomFieldTypeChoices.TYPE_SELECT,
            ]:
                q |= Q(**{f"{field.name}__icontains": value})
            elif field.type == CustomFieldTypeChoices.TYPE_MULTISELECT:
                # ArrayField does not support icontains; use array containment
                # to check whether the searched value is an element in the array.
                q |= Q(**{f"{field.name}__contains": [value]})
            elif field.type in [
                CustomFieldTypeChoices.TYPE_INTEGER,
                CustomFieldTypeChoices.TYPE_DECIMAL,
            ]:
                try:
                    numeric = int(value) if field.type == CustomFieldTypeChoices.TYPE_INTEGER else Decimal(value)
                    q |= Q(**{f"{field.name}__exact": numeric})
                except (ValueError, TypeError, InvalidOperation):
                    pass
            elif field.type == CustomFieldTypeChoices.TYPE_DATE:
                parsed = parse_date(value)
                if parsed is not None:
                    q |= Q(**{f"{field.name}__exact": parsed})
            elif field.type == CustomFieldTypeChoices.TYPE_DATETIME:
                parsed = parse_datetime(value)
                if parsed is not None:
                    q |= Q(**{f"{field.name}__exact": parsed})
        if not q:
            return queryset.none()
        return queryset.filter(q)

    attrs = {
        "Meta": meta,
        "__module__": "netbox_custom_objects.filtersets",
        "search": search,
    }

    # For each custom field, add a corresponding filter (dict of name → Filter).
    for field in model.custom_object_type.fields.all():
        attrs.update(build_filter_for_field(field))

    return type(
        f"{model._meta.object_name}FilterSet",
        (NetBoxModelFilterSet,),
        attrs,
    )
