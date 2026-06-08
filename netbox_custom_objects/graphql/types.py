"""
Dynamic GraphQL type generation for custom object models.

Each ``CustomObjectType`` is backed by a real, runtime-generated Django model
(``Table<id>Model``).  This module builds a Strawberry GraphQL type for each of
those models so that custom objects can be queried through NetBox's GraphQL API
exactly like first-class NetBox models.

Because the underlying models are generated at runtime, the GraphQL types are
also generated at runtime — at plugin startup (``ready()``), when NetBox
assembles its GraphQL schema.  A custom object type created *after* startup will
not appear in the GraphQL schema until NetBox is restarted (the GraphQL schema,
like the Django model registry it mirrors, is built once at boot).  This mirrors
how adding a new Django model requires a restart, and differs from the REST API,
which resolves serializers per-request.

Scalar fields are mapped to their natural GraphQL scalar types.  Object and
multi-object (relationship) fields — including polymorphic ones — are exposed
through a single shared ``CustomObjectRelatedObjectType`` so that references to
*any* NetBox model or other custom object resolve uniformly.
"""

import datetime
import decimal
import logging
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
)


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


def _make_object_resolver(field_name):
    """Build a resolver returning the single related object for an OBJECT field."""

    @strawberry_django.field(description=f"Related object referenced by '{field_name}'")
    def resolver(self) -> Optional[CustomObjectRelatedObjectType]:
        return _related_repr(getattr(self, field_name, None))

    return resolver


def _make_multiobject_resolver(field_name):
    """Build a resolver returning related objects for a MULTIOBJECT field."""

    @strawberry_django.field(description=f"Related objects referenced by '{field_name}'")
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
    model = custom_object_type.get_model()
    if model is None:
        return None

    type_name = f"{model.__name__}Type"

    namespace = {
        "__doc__": f"Custom object type '{custom_object_type.name}'.",
        "__annotations__": {},
    }

    for field in custom_object_type.fields.all():
        field_name = field.name
        if field.type in RELATIONSHIP_TYPES:
            if field.type == CustomFieldTypeChoices.TYPE_OBJECT:
                namespace[field_name] = _make_object_resolver(field_name)
            else:
                namespace[field_name] = _make_multiobject_resolver(field_name)
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
