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
            "group_name",
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

    # Add filters for M2M (multiobject) fields, which are not in model._meta.fields.
    # By the time get_filterset_class() is called (at request time), after_model_generation()
    # will have already resolved m2m_field.remote_field.model and .through to actual model
    # classes. Calling this during app startup (before model generation) would fail.
    for m2m_field in model._meta.many_to_many:
        field_name = m2m_field.name
        through_model = m2m_field.remote_field.through
        related_model = m2m_field.remote_field.model

        def make_m2m_filter(through, fname):
            def filter_m2m(self, queryset, name, value):
                if not value:
                    return queryset
                ids = [v.pk for v in value]
                source_ids = through.objects.filter(
                    target_id__in=ids
                ).values_list("source_id", flat=True)
                return queryset.filter(pk__in=source_ids)
            filter_m2m.__name__ = f"filter_{fname}"
            return filter_m2m

        method_name = f"filter_{field_name}"
        attrs[method_name] = make_m2m_filter(through_model, field_name)
        attrs[field_name] = django_filters.ModelMultipleChoiceFilter(
            queryset=related_model.objects.all(),
            method=method_name,
            label=field_name,
        )

    return type(
        f"{model._meta.object_name}FilterSet",
        (NetBoxModelFilterSet,),
        attrs,
    )
