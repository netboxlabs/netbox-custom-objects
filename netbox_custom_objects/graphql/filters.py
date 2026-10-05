"""
Dynamic GraphQL filter generation for custom object models.

Each custom object type's GraphQL type (see :mod:`.types`) is given a
``strawberry_django`` filter class built here, so its ``custom_objects_<slug>_list``
root query -- and any multi-object field pointing at it -- takes a ``filters:``
argument, as core NetBox models do.

The base filters (``id``, ``tags``, ``journal_entries``, ``created``,
``last_updated`` and, when config context is enabled, ``local_context_data``)
come from core NetBox's own filter mixins.  Each custom field adds a filter
suited to its type:

* text, long text, URL and select -- string lookups (``exact``, ``i_contains``, ...)
* integer and decimal -- comparison lookups (``exact``, ``gt``, ``range``, ...)
* boolean -- ``exact``/``is_null``
* date and datetime -- comparison lookups plus date-part lookups
* JSON -- NetBox's ``JSONFilter``
* multi-select -- NetBox's ``StringArrayLookup`` (``contains``, ``overlap``, ...)
* object -- the target model's own filter (nested, e.g. ``site: {name: ...}``)
  plus ``<field>_id``, mirroring core's ``tenant``/``tenant_id`` pairs
* multi-object -- the target model's own filter (nested)

Polymorphic object/multi-object fields and coordinates fields have no filter.
"""

import datetime
import decimal
import logging
from typing import Optional

import strawberry_django
from core.graphql.filter_mixins import ChangeLoggingMixin
from extras.choices import CustomFieldTypeChoices
from extras.graphql.filter_mixins import ConfigContextFilterMixin, JournalEntriesFilterMixin, TagsFilterMixin
from extras.models import ConfigContextModel
from netbox.graphql.filter_lookups import JSONFilter, StringArrayLookup
from netbox.graphql.filters import BaseModelFilter
from strawberry import ID
from strawberry_django import BaseFilterLookup, ComparisonFilterLookup, DateFilterLookup, DatetimeFilterLookup
from strawberry_django.utils.typing import get_django_definition

try:
    from strawberry_django import StrFilterLookup
except ImportError:
    # COMPAT(netbox<4.6): strawberry-django < 0.84 doesn't export StrFilterLookup.
    # Use FilterLookup[str] as core does there: its own StrFilterLookup class would
    # clash with the "StrFilterLookup" name strawberry gives FilterLookup[str].
    from strawberry_django import FilterLookup

    StrFilterLookup = FilterLookup

logger = logging.getLogger("netbox_custom_objects.graphql")

__all__ = (
    "build_filter_class",
    "relationship_filter_annotations",
    "scalar_filter_annotation",
)


def _lookup(lookup_cls, value_type):
    """
    Parametrize ``lookup_cls`` with ``value_type`` if it is generic.

    COMPAT(netbox<4.6): every lookup class is generic in strawberry-django < 0.84;
    later releases made some of them (e.g. ``StrFilterLookup``) concrete.
    """
    if getattr(lookup_cls, "__parameters__", ()):
        return lookup_cls[value_type]
    return lookup_cls


SCALAR_FILTER_ANNOTATIONS = {
    CustomFieldTypeChoices.TYPE_TEXT: _lookup(StrFilterLookup, str),
    CustomFieldTypeChoices.TYPE_LONGTEXT: _lookup(StrFilterLookup, str),
    CustomFieldTypeChoices.TYPE_URL: _lookup(StrFilterLookup, str),
    CustomFieldTypeChoices.TYPE_SELECT: _lookup(StrFilterLookup, str),
    CustomFieldTypeChoices.TYPE_INTEGER: _lookup(ComparisonFilterLookup, int),
    CustomFieldTypeChoices.TYPE_DECIMAL: _lookup(ComparisonFilterLookup, decimal.Decimal),
    CustomFieldTypeChoices.TYPE_BOOLEAN: _lookup(BaseFilterLookup, bool),
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


def build_filter_class(model, field_annotations):
    """
    Build the strawberry_django filter class for a custom object ``model``.

    ``field_annotations`` maps filter names to their (non-optional) annotations,
    as collected by ``types._build_object_type``.
    """
    bases = [TagsFilterMixin, JournalEntriesFilterMixin, ChangeLoggingMixin, BaseModelFilter]
    if issubclass(model, ConfigContextModel):
        bases.insert(0, ConfigContextFilterMixin)

    namespace = {"__annotations__": {}}
    for name, annotation in field_annotations.items():
        namespace["__annotations__"][name] = Optional[annotation]
        namespace[name] = strawberry_django.filter_field()

    name = f"{model.__name__}Filter"
    cls = type(name, tuple(bases), namespace)
    return strawberry_django.filter_type(model, name=name, lookups=True)(cls)


def scalar_filter_annotation(field):
    """Return the filter annotation for a non-relationship field, or ``None``."""
    return SCALAR_FILTER_ANNOTATIONS.get(field.type)
