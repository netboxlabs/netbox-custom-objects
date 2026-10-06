"""
Dynamic GraphQL filter classes for custom object models.

:func:`build_filter_class` builds the ``strawberry_django`` filter class each custom
object type's GraphQL type (see :mod:`.types`) is declared with, giving its list
query a ``filters:`` argument as core NetBox models have.
"""

import datetime
import decimal
from typing import Optional

import strawberry_django
from core.graphql.filter_mixins import ChangeLoggingMixin
from django.apps import apps as django_apps
from django.db.models import Q, QuerySet
from extras.choices import CustomFieldTypeChoices
from extras.graphql.filter_mixins import ConfigContextFilterMixin, JournalEntriesFilterMixin, TagsFilterMixin
from extras.models import ConfigContextModel
from netbox.graphql.filter_lookups import JSONFilter, StringArrayLookup
from netbox.graphql.filters import BaseModelFilter
from netbox.graphql.scalars import BigInt
from strawberry import ID
from strawberry.types import Info
from strawberry_django import BaseFilterLookup, ComparisonFilterLookup, DateFilterLookup, DatetimeFilterLookup
from strawberry_django.filters import lookup_name_conversion_map, process_filters
from strawberry_django.utils.typing import get_django_definition

from netbox_custom_objects.constants import APP_LABEL

try:
    from strawberry_django import StrFilterLookup
except ImportError:
    # COMPAT(netbox<4.5.4): strawberry-django doesn't export StrFilterLookup at the
    # top level. Use FilterLookup[str] as core does there: the StrFilterLookup class
    # it does have would clash with the "StrFilterLookup" name strawberry gives
    # FilterLookup[str].
    from strawberry_django import FilterLookup

    StrFilterLookup = FilterLookup

__all__ = (
    "build_filter_class",
    "coordinates_filter_annotations",
    "polymorphic_filter_fields",
    "relationship_filter_annotations",
    "scalar_filter_annotation",
)


def _lookup(lookup_cls, value_type):
    """
    Parametrize ``lookup_cls`` with ``value_type`` if it is generic.

    COMPAT(netbox<4.6.3): the string, date and datetime lookup classes are generic
    in the strawberry-django releases these NetBox versions pin, and concrete after.
    """
    if getattr(lookup_cls, "__parameters__", ()):
        return lookup_cls[value_type]
    return lookup_cls


SCALAR_FILTER_ANNOTATIONS = {
    CustomFieldTypeChoices.TYPE_TEXT: _lookup(StrFilterLookup, str),
    CustomFieldTypeChoices.TYPE_LONGTEXT: _lookup(StrFilterLookup, str),
    CustomFieldTypeChoices.TYPE_URL: _lookup(StrFilterLookup, str),
    CustomFieldTypeChoices.TYPE_SELECT: _lookup(StrFilterLookup, str),
    # Integer fields are BigIntegerFields; GraphQL Int is only 32-bit.
    CustomFieldTypeChoices.TYPE_INTEGER: ComparisonFilterLookup[BigInt],
    CustomFieldTypeChoices.TYPE_DECIMAL: ComparisonFilterLookup[decimal.Decimal],
    CustomFieldTypeChoices.TYPE_BOOLEAN: BaseFilterLookup[bool],
    CustomFieldTypeChoices.TYPE_DATE: _lookup(DateFilterLookup, datetime.date),
    CustomFieldTypeChoices.TYPE_DATETIME: _lookup(DatetimeFilterLookup, datetime.datetime),
    CustomFieldTypeChoices.TYPE_JSON: JSONFilter,
    CustomFieldTypeChoices.TYPE_MULTISELECT: StringArrayLookup,
}


def _filter_class_for_type(gql_type):
    """Return the filter class a strawberry_django output type was declared with, or ``None``."""
    definition = get_django_definition(gql_type)
    return getattr(definition, "filters", None) if definition is not None else None


def relationship_filter_annotations(field, members):
    """
    Return ``{name: annotation}`` filters for an OBJECT or MULTIOBJECT field.

    ``members`` are the GraphQL types the field resolves to (see
    ``types._resolve_relationship_members``).  The nested filter is the target
    type's own filter class; it is omitted when the target has none -- including a
    custom-object target whose type is still being built higher up a relationship
    cycle, which resolves to the flat stub.
    """
    if field.is_polymorphic:
        return {}
    annotations = {}
    if len(members) == 1:
        target_filter = _filter_class_for_type(members[0])
        if target_filter is not None:
            annotations[field.name] = target_filter
    if field.type == CustomFieldTypeChoices.TYPE_OBJECT:
        annotations[f"{field.name}_id"] = ID
    return annotations


def coordinates_filter_annotations(field):
    """Return the ``<field>_latitude`` and ``<field>_longitude`` filters of a coordinates field."""
    return {
        f"{field.name}_latitude": ComparisonFilterLookup[decimal.Decimal],
        f"{field.name}_longitude": ComparisonFilterLookup[decimal.Decimal],
    }


def _target_pks(value, model, info):
    """Primary keys of the ``model`` objects matching the nested filter ``value``."""
    targets, q = process_filters(value, model.objects.all(), info)
    return targets.filter(q).values("pk")


def _resolver_filter_field(name, annotation, resolve):
    """A filter field named ``name`` whose ``resolve(info, value, prefix)`` returns a Q."""

    def resolver(self, info: Info, queryset: QuerySet, value: annotation, prefix: str):
        return queryset, resolve(info, value, prefix)

    resolver.__name__ = name
    return strawberry_django.filter_field(resolver)


def polymorphic_filter_fields(field, targets):
    """
    Return ``{name: filter field}`` for a polymorphic OBJECT or MULTIOBJECT field.

    ``targets`` pairs each allowed ContentType with its GraphQL type (or ``None``).
    Each allowed type gets ``<field>_<app_label>_<model>``, named as in the REST
    filterset, taking that type's own filter class; it is omitted when the type has
    none (e.g. a custom object type still being built higher up a relationship
    cycle).  An OBJECT field also gets ``<field>_<app_label>_<model>_id``.  Generic
    relations can't be traversed in ORM lookups, so these filter the target model
    first and match its primary keys.
    """
    fields = {}
    is_multi = field.type == CustomFieldTypeChoices.TYPE_MULTIOBJECT
    for content_type, gql_type in targets:
        model = content_type.model_class()
        if model is None:
            continue
        name = f"{field.name}_{content_type.app_label}_{content_type.model}"
        target_filter = _filter_class_for_type(gql_type) if gql_type is not None else None

        if is_multi:
            def match(pks, prefix, _ct=content_type.pk, _through=field.through_model_name):
                through = django_apps.get_model(APP_LABEL, _through)
                sources = through.objects.filter(content_type_id=_ct, object_id__in=pks).values("source_id")
                return Q(**{f"{prefix}pk__in": sources})
        else:
            def match(pks, prefix, _ct=content_type.pk, _gfk=field.name):
                lookup = "in" if isinstance(pks, QuerySet) else "exact"
                return Q(**{
                    f"{prefix}{_gfk}_content_type_id": _ct,
                    f"{prefix}{_gfk}_object_id__{lookup}": pks,
                })

        if target_filter is not None:
            fields[name] = _resolver_filter_field(
                name, Optional[target_filter],
                lambda info, value, prefix, _model=model, _match=match: _match(
                    _target_pks(value, _model, info), prefix
                ),
            )
        if not is_multi:
            fields[f"{name}_id"] = _resolver_filter_field(
                f"{name}_id", Optional[ID],
                lambda info, value, prefix, _match=match: _match(value, prefix),
            )
    return fields


def _aliased_filter_field(name, annotation):
    """
    Build a filter field that filters on ``name`` itself.

    strawberry-django treats filter fields named like its lookups (``in_list``,
    ``is_null``, ``i_contains``, ...) as those Django lookups (``in``, ``isnull``,
    ...), so a custom field with such a name would otherwise filter the wrong thing.
    """

    def resolver(self, info: Info, queryset: QuerySet, value: annotation, prefix: str):
        if hasattr(value, "__strawberry_definition__"):
            return process_filters(value, queryset, info, prefix=f"{prefix}{name}__")
        return queryset, Q(**{f"{prefix}{name}": value})

    resolver.__name__ = name
    return strawberry_django.filter_field(resolver)


def build_filter_class(model, field_annotations, filter_fields=None):
    """
    Build the strawberry_django filter class for a custom object ``model``.

    ``field_annotations`` maps filter names to their (non-optional) annotations,
    as collected by ``types._build_object_type``; ``filter_fields`` maps names to
    ready-made filter fields (see ``polymorphic_filter_fields``).
    """
    bases = [TagsFilterMixin, JournalEntriesFilterMixin, ChangeLoggingMixin, BaseModelFilter]
    if issubclass(model, ConfigContextModel):
        bases.insert(0, ConfigContextFilterMixin)

    namespace = {"__annotations__": {}}
    for name, annotation in field_annotations.items():
        if name in lookup_name_conversion_map:
            namespace[name] = _aliased_filter_field(name, annotation)
            continue
        namespace["__annotations__"][name] = Optional[annotation]
        namespace[name] = strawberry_django.filter_field()
    for name, filter_field in (filter_fields or {}).items():
        namespace.setdefault(name, filter_field)

    name = f"{model.__name__}Filter"
    cls = type(name, tuple(bases), namespace)
    return strawberry_django.filter_type(model, name=name, lookups=True)(cls)


def scalar_filter_annotation(field):
    """Return the filter annotation for a non-relationship field, or ``None``."""
    return SCALAR_FILTER_ANNOTATIONS.get(field.type)
