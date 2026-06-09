"""
Dynamic GraphQL type generation for custom object models.

Each ``CustomObjectType`` is backed by a real, runtime-generated Django model
(``Table<id>Model``).  This module builds a Strawberry GraphQL type for each of
those models so that custom objects can be queried through NetBox's GraphQL API
exactly like first-class NetBox models.

Because the underlying models are generated at runtime, the GraphQL types are
also generated at runtime.  They are rebuilt per-request by
:mod:`netbox_custom_objects.graphql.live` (installed via the view patch in
``__init__.py``) whenever the set of custom object types or their fields changes,
so a type created *after* startup appears without a NetBox restart.

Scalar fields are mapped to their natural GraphQL scalar types.  Object and
multi-object (relationship) fields — including polymorphic ones — are exposed
through a single shared ``CustomObjectRelatedObjectType`` so that references to
*any* NetBox model or other custom object resolve uniformly.
"""

import datetime
import decimal
import logging
import threading
from typing import List, Optional

import strawberry
import strawberry_django
from core.graphql.mixins import ChangelogMixin
from extras.choices import CustomFieldTypeChoices
from extras.graphql.mixins import TagsMixin
from netbox.graphql.types import BaseObjectType
from strawberry.scalars import JSON

logger = logging.getLogger("netbox_custom_objects.graphql")

__all__ = (
    "CustomObjectObjectType",
    "CustomObjectRelatedObjectType",
    "build_object_type",
    "clear_type_cache",
)

# Per-process cache of built GraphQL types, keyed by (cot id, cache_timestamp).
# A COT's cache_timestamp is bumped (auto_now, plus an explicit save() on every
# field add/edit/delete) whenever the type or any of its fields changes, so a
# cached entry can never go stale: a structural change changes the key and forces
# a rebuild.  This lets a schema rebuild triggered by one COT reuse the
# already-built types of every other COT instead of re-running build_object_type
# (and its per-COT fields query) for all of them.
_type_cache = {}
_type_cache_lock = threading.RLock()


def clear_type_cache():
    """Drop all cached GraphQL types (used by tests)."""
    with _type_cache_lock:
        _type_cache.clear()


# Mapping of custom-field scalar types to the Python annotation Strawberry should
# use.  Relationship types (OBJECT / MULTIOBJECT) are handled separately via
# resolvers, so they are intentionally absent here.
SCALAR_TYPE_MAP = {
    CustomFieldTypeChoices.TYPE_TEXT: str,
    CustomFieldTypeChoices.TYPE_LONGTEXT: str,
    CustomFieldTypeChoices.TYPE_URL: str,
    CustomFieldTypeChoices.TYPE_SELECT: str,
    CustomFieldTypeChoices.TYPE_INTEGER: int,
    CustomFieldTypeChoices.TYPE_DECIMAL: decimal.Decimal,
    CustomFieldTypeChoices.TYPE_BOOLEAN: bool,
    CustomFieldTypeChoices.TYPE_DATE: datetime.date,
    CustomFieldTypeChoices.TYPE_DATETIME: datetime.datetime,
    CustomFieldTypeChoices.TYPE_JSON: JSON,
    CustomFieldTypeChoices.TYPE_MULTISELECT: List[str],
}

RELATIONSHIP_TYPES = (
    CustomFieldTypeChoices.TYPE_OBJECT,
    CustomFieldTypeChoices.TYPE_MULTIOBJECT,
)

_field_type_coverage_checked = False


def _warn_on_unmapped_field_types():
    """
    Warn once if a registered custom field type has no GraphQL mapping.

    SCALAR_TYPE_MAP and RELATIONSHIP_TYPES together re-enumerate the field types
    owned by ``field_types.FIELD_TYPE_CLASS``.  If a new field type is registered
    there without a corresponding entry here, it would be silently omitted from
    the GraphQL schema (see :func:`build_object_type`).  Surface that drift loudly
    rather than leaving the field quietly missing.
    """
    global _field_type_coverage_checked
    if _field_type_coverage_checked:
        return
    _field_type_coverage_checked = True
    try:
        from netbox_custom_objects.field_types import FIELD_TYPE_CLASS
    except Exception:  # noqa: BLE001 - never break schema build over a self-check
        return
    unmapped = set(FIELD_TYPE_CLASS) - set(SCALAR_TYPE_MAP) - set(RELATIONSHIP_TYPES)
    if unmapped:
        logger.warning(
            "Custom field type(s) %s have no GraphQL mapping and will be omitted from "
            "the GraphQL schema; add them to SCALAR_TYPE_MAP or RELATIONSHIP_TYPES in "
            "netbox_custom_objects/graphql/types.py.",
            sorted(unmapped),
        )


@strawberry.type
class CustomObjectRelatedObjectType:
    """
    A lightweight, uniform representation of an object referenced by a custom
    object's relationship field.

    Relationship fields can point at any NetBox model, another custom object, or
    (for polymorphic fields) a mix of types, so a single concrete Strawberry type
    per target is not feasible.  This shared type exposes the information common
    to every referenced object.
    """

    id: int
    object_type: str
    display: str
    url: Optional[str]


@strawberry.type
class CustomObjectObjectType(ChangelogMixin, TagsMixin, BaseObjectType):
    """
    Base GraphQL type for all custom object models.

    ``BaseObjectType`` provides ``display``/``class_type`` and, crucially,
    ``get_queryset()`` which enforces NetBox object-level view permissions.
    ``ChangelogMixin`` and ``TagsMixin`` add change-log and tag access — both are
    supported by the ``CustomObject`` base model.  Custom fields are added per
    type by :func:`build_object_type`.
    """

    pass


def _related_repr(obj):
    """Convert a referenced model instance into a ``CustomObjectRelatedObjectType``."""
    if obj is None:
        return None
    url = None
    try:
        url = obj.get_absolute_url()
    except Exception:  # noqa: BLE001 - URL resolution is best-effort
        url = None
    return CustomObjectRelatedObjectType(
        id=obj.pk,
        object_type=f"{obj._meta.app_label}.{obj._meta.model_name}",
        display=str(obj),
        url=url,
    )


def _make_object_resolver(field):
    """Build a resolver returning the single related object for an OBJECT field."""
    field_name = field.name
    # Optimize the parent list query so each row's related object is fetched in
    # bulk rather than one query per row.  NetBox's DjangoOptimizerExtension reads
    # these hints off the field even though it has a custom resolver.  A
    # non-polymorphic OBJECT field is a plain ForeignKey (select_related); a
    # polymorphic one is a GenericForeignKey, for which only prefetch_related works.
    hint = {"prefetch_related": field_name} if field.is_polymorphic else {"select_related": field_name}

    @strawberry_django.field(description=f"Related object referenced by '{field_name}'", **hint)
    def resolver(self) -> Optional[CustomObjectRelatedObjectType]:
        return _related_repr(getattr(self, field_name, None))

    return resolver


def _make_multiobject_resolver(field):
    """Build a resolver returning related objects for a MULTIOBJECT field."""
    field_name = field.name
    # A non-polymorphic MULTIOBJECT field is a real ManyToManyField and can be
    # prefetched to avoid one query per parent row.  A polymorphic one is backed
    # by a custom descriptor (PolymorphicM2MDescriptor), not a Django relation, so
    # it can't be prefetched here and remains one query per row.
    hint = {} if field.is_polymorphic else {"prefetch_related": field_name}

    @strawberry_django.field(description=f"Related objects referenced by '{field_name}'", **hint)
    def resolver(self) -> List[CustomObjectRelatedObjectType]:
        manager = getattr(self, field_name, None)
        if manager is None:
            return []
        try:
            related = list(manager.all())
        except Exception:  # noqa: BLE001 - never let one field break the query
            logger.warning(
                "Failed to resolve multi-object GraphQL field %r", field_name, exc_info=True
            )
            return []
        return [_related_repr(obj) for obj in related if obj is not None]

    return resolver


def build_object_type(custom_object_type):
    """
    Build (or return ``None`` on failure) a Strawberry type for a single
    ``CustomObjectType``.

    The returned class is a ``strawberry_django.type`` bound to the runtime
    model, with one GraphQL field per custom field plus the inherited base
    fields (id, display, tags, changelog, created, last_updated).
    """
    _warn_on_unmapped_field_types()

    model = custom_object_type.get_model()
    if model is None:
        return None

    # Reuse the already-built type when this COT's structure is unchanged (see
    # _type_cache).  cache_timestamp moves on every structural change, so this can
    # never serve a type that no longer matches the model.
    cache_key = (custom_object_type.id, custom_object_type.cache_timestamp)
    with _type_cache_lock:
        cached = _type_cache.get(cache_key)
    if cached is not None:
        return cached

    gql_type = _build_object_type(custom_object_type, model)

    with _type_cache_lock:
        _type_cache[cache_key] = gql_type
        # Bound growth: drop now-superseded entries for this COT.
        for key in [k for k in _type_cache if k[0] == cache_key[0] and k != cache_key]:
            del _type_cache[key]
    return gql_type


def _build_object_type(custom_object_type, model):
    """Construct a fresh Strawberry type for ``custom_object_type`` (uncached)."""
    type_name = f"{model.__name__}Type"

    namespace = {
        "__doc__": f"Custom object type '{custom_object_type.name}'.",
        "__annotations__": {},
    }

    for field in custom_object_type.fields.all():
        field_name = field.name
        if field.type in RELATIONSHIP_TYPES:
            if field.type == CustomFieldTypeChoices.TYPE_OBJECT:
                namespace[field_name] = _make_object_resolver(field)
            else:
                namespace[field_name] = _make_multiobject_resolver(field)
            continue

        annotation = SCALAR_TYPE_MAP.get(field.type)
        if annotation is None:
            logger.debug(
                "Skipping custom field %r of unsupported GraphQL type %r",
                field_name,
                field.type,
            )
            continue
        # Every custom field is nullable at the database level.
        namespace["__annotations__"][field_name] = Optional[annotation]

    cls = type(type_name, (CustomObjectObjectType,), namespace)

    return strawberry_django.type(
        model,
        name=type_name,
        fields=["id", "created", "last_updated"],
        pagination=True,
    )(cls)
