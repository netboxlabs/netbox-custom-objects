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

Scalar fields are mapped to their natural GraphQL scalar types (the mapping
lives on each ``FieldType`` in ``field_types.py``).  Object and multi-object
(relationship) fields resolve to the *native* NetBox GraphQL type of their
target — a field pointing at a Site resolves to NetBox's ``SiteType`` and is
fully traversable.  Polymorphic relationship fields, which may point at several
model types, resolve to a Strawberry union of those native types (mirroring how
NetBox exposes ``assigned_object``/cable terminations).  When a target model has
no registered GraphQL type, the field falls back to a lightweight, uniform
``CustomObjectRelatedObjectType`` stub so the field is never silently dropped.
"""

import logging
import re
import threading
from typing import Annotated, List, Optional, Union

import strawberry
import strawberry_django
from core.graphql.mixins import ChangelogMixin
from extras.choices import CustomFieldTypeChoices
from extras.graphql.mixins import TagsMixin
from netbox.graphql.types import BaseObjectType
from strawberry.types import Info

from netbox_custom_objects.constants import APP_LABEL
from netbox_custom_objects.utilities import extract_cot_id_from_model_name

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

# Tracks the COT ids whose GraphQL type is being built on the current thread, so
# that a relationship between two custom objects (A -> B -> A) does not recurse
# forever: a back-reference to a type still under construction falls back to the
# flat stub instead of rebuilding it.
_building = threading.local()

# Lazily-built map of Django model class -> its registered NetBox strawberry
# GraphQL type.  The app-defined types are static for the life of the process, so
# this is computed once.
_model_type_registry = None
_registry_lock = threading.RLock()


def clear_type_cache():
    """Drop all cached GraphQL types (used by tests)."""
    with _type_cache_lock:
        _type_cache.clear()


RELATIONSHIP_TYPES = (
    CustomFieldTypeChoices.TYPE_OBJECT,
    CustomFieldTypeChoices.TYPE_MULTIOBJECT,
)


@strawberry.type
class CustomObjectRelatedObjectType:
    """
    Fallback representation of an object referenced by a relationship field whose
    target model has no registered NetBox GraphQL type.

    Most relationship targets resolve to their native GraphQL type and are fully
    traversable; this uniform stub is only used when no such type exists, so the
    field still exposes the basics rather than disappearing from the schema.
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


def _in_progress_set():
    ids = getattr(_building, "cot_ids", None)
    if ids is None:
        ids = set()
        _building.cot_ids = ids
    return ids


def _request_user(info):
    """Best-effort extraction of the requesting user from the GraphQL info context."""
    request = getattr(getattr(info, "context", None), "request", None)
    return getattr(request, "user", None)


def _user_can_view(user, obj):
    """
    Return whether ``user`` has NetBox 'view' permission for ``obj``.

    The top-level query restricts the custom objects themselves, but the objects
    reached through their relationship fields are *not* covered by that check, so
    each one must be gated individually or the field would leak objects the user
    cannot see.
    """
    if obj is None or user is None:
        return False
    if getattr(user, "is_superuser", False):
        return True
    manager = getattr(type(obj), "_default_manager", None)
    if manager is None or not hasattr(manager, "restrict"):
        # Target model isn't permission-aware; nothing to enforce.
        return True
    return manager.restrict(user, "view").filter(pk=obj.pk).exists()


def _related_repr(obj):
    """Convert a referenced model instance into a ``CustomObjectRelatedObjectType``."""
    if obj is None:
        return None
    try:
        url = obj.get_absolute_url()
    except Exception:  # noqa: BLE001 - URL resolution is best-effort
        url = None
    return CustomObjectRelatedObjectType(
        id=obj.pk,
        object_type=obj._meta.label_lower,
        display=str(obj),
        url=url,
    )


def _build_model_type_registry():
    """
    Map every Django model to its registered NetBox strawberry GraphQL type.

    NetBox (and other plugins) declare their per-model types in
    ``<app>.graphql.types``.  Each such type carries a strawberry-django
    definition naming its model, so importing those modules and indexing by model
    gives a model -> type lookup that relationship fields use to resolve their
    target to a native, traversable type.
    """
    from importlib import import_module

    from django.apps import apps as django_apps
    from strawberry_django.utils.typing import get_django_definition

    registry = {}
    for app_config in django_apps.get_app_configs():
        if app_config.label == APP_LABEL:
            # Custom-object types are resolved dynamically, not from a module.
            continue
        module_name = f"{app_config.name}.graphql.types"
        try:
            module = import_module(module_name)
        except ModuleNotFoundError:
            continue
        except Exception:  # noqa: BLE001 - a broken app module must not break the schema
            logger.debug("Could not import %s for the GraphQL type registry", module_name, exc_info=True)
            continue
        for value in vars(module).values():
            if not isinstance(value, type):
                continue
            definition = get_django_definition(value)
            if definition is None or definition.model is None:
                continue
            # Only index GraphQL *output* object types. Filters and inputs also
            # carry a django definition (and a model), but using one as a field's
            # type would break the schema.
            sb_definition = getattr(value, "__strawberry_definition__", None)
            if sb_definition is None or sb_definition.is_input or sb_definition.is_interface:
                continue
            registry.setdefault(definition.model, value)
    return registry


def _get_model_type_registry():
    global _model_type_registry
    if _model_type_registry is not None:
        return _model_type_registry
    with _registry_lock:
        if _model_type_registry is None:
            _model_type_registry = _build_model_type_registry()
    return _model_type_registry


def _custom_object_graphql_type(model_name):
    """Resolve a custom-object target (``table<id>model``) to its GraphQL type."""
    from netbox_custom_objects.models import CustomObjectType

    try:
        cot_id = extract_cot_id_from_model_name(model_name)
    except Exception:  # noqa: BLE001
        return None
    if cot_id is None or cot_id in _in_progress_set():
        # No id, or a back-reference to a type still being built — fall back to
        # the flat stub for this edge to avoid infinite recursion.
        return None
    cot = CustomObjectType.objects.filter(pk=cot_id).first()
    if cot is None:
        return None
    try:
        return build_object_type(cot)
    except Exception:  # noqa: BLE001
        logger.warning(
            "Failed to build related custom-object GraphQL type for %r", model_name, exc_info=True
        )
        return None


def _graphql_type_for_content_type(content_type):
    """Return the native strawberry GraphQL type for ``content_type``, or ``None``."""
    model = content_type.model_class()
    if model is None:
        return None
    if content_type.app_label == APP_LABEL:
        return _custom_object_graphql_type(content_type.model)
    return _get_model_type_registry().get(model)


def _field_target_content_types(field):
    """Return the ContentType(s) a relationship field may point at."""
    if field.is_polymorphic:
        return list(field.related_object_types.all())
    if field.related_object_type_id:
        return [field.related_object_type]
    return []


def _resolve_relationship_members(field):
    """
    Return ``(members, native_models)`` for a relationship field.

    ``members`` is the list of GraphQL types the field can resolve to — the
    native type of each target that has one, plus the flat stub when at least one
    target has no native type (or there are no targets at all).  ``native_models``
    is the set of Django model classes that resolve to a native type, used at
    resolve time to decide whether to return the raw instance or wrap it.
    """
    members = []
    native_models = set()
    needs_stub = False
    for content_type in _field_target_content_types(field):
        gql_type = _graphql_type_for_content_type(content_type)
        model = content_type.model_class()
        if gql_type is not None and model is not None:
            if gql_type not in members:
                members.append(gql_type)
            native_models.add(model)
        else:
            needs_stub = True
    if needs_stub or not members:
        if CustomObjectRelatedObjectType not in members:
            members.append(CustomObjectRelatedObjectType)
    return members, native_models


def _relationship_union_name(field):
    """A schema-unique, GraphQL-safe name for a polymorphic field's union type."""
    base = re.sub(r"[^0-9a-zA-Z_]", "_", field.name)
    return f"CustomObject{field.custom_object_type_id}_{base}_Related"


def _relationship_annotation(members, is_list, union_name):
    """Build the resolver return annotation from the resolved member types."""
    if len(members) == 1:
        inner = members[0]
    else:
        inner = Annotated[Union[tuple(members)], strawberry.union(union_name)]
    return List[inner] if is_list else Optional[inner]


def _coerce_related(obj, native_models):
    """Return ``obj`` itself if its model resolves natively, else the flat stub."""
    if type(obj) in native_models or any(isinstance(obj, model) for model in native_models):
        return obj
    return _related_repr(obj)


def _make_relationship_resolver(field):
    """
    Build a resolver for an OBJECT or MULTIOBJECT relationship field.

    The resolver returns the referenced object(s) as their native GraphQL
    type(s) (or the flat stub for targets without one), filtered to those the
    requesting user may view.
    """
    field_name = field.name
    is_list = field.type == CustomFieldTypeChoices.TYPE_MULTIOBJECT

    members, native_models = _resolve_relationship_members(field)
    if not members:
        return None
    annotation = _relationship_annotation(members, is_list, _relationship_union_name(field))

    # Query-optimisation hints read by NetBox's DjangoOptimizerExtension. A
    # non-polymorphic OBJECT field is a ForeignKey (select_related); a polymorphic
    # one is a GenericForeignKey (prefetch_related only). A non-polymorphic
    # MULTIOBJECT field is a real M2M (prefetch_related); a polymorphic one is a
    # custom descriptor that can't be prefetched.
    if is_list:
        hint = {} if field.is_polymorphic else {"prefetch_related": field_name}
        description = f"Related objects referenced by '{field_name}'"
    else:
        hint = {"prefetch_related": field_name} if field.is_polymorphic else {"select_related": field_name}
        description = f"Related object referenced by '{field_name}'"

    def resolver(self, info: Info):
        user = _request_user(info)
        value = getattr(self, field_name, None)
        if is_list:
            if value is None:
                return []
            try:
                related = list(value.all())
            except Exception:  # noqa: BLE001 - never let one field break the query
                logger.warning(
                    "Failed to resolve multi-object GraphQL field %r", field_name, exc_info=True
                )
                return []
            return [
                _coerce_related(obj, native_models)
                for obj in related
                if obj is not None and _user_can_view(user, obj)
            ]
        if value is None or not _user_can_view(user, value):
            return None
        return _coerce_related(value, native_models)

    resolver.__annotations__ = {"info": Info, "return": annotation}
    return strawberry_django.field(description=description, **hint)(resolver)


def _scalar_annotation_for(field_type):
    """Return the GraphQL scalar annotation for a field type, or ``None``."""
    from netbox_custom_objects.field_types import FIELD_TYPE_CLASS

    field_type_cls = FIELD_TYPE_CLASS.get(field_type)
    if field_type_cls is None:
        return None
    return field_type_cls().get_graphql_annotation()


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

    # Reuse the already-built type when this COT's structure is unchanged (see
    # _type_cache).  cache_timestamp moves on every structural change, so this can
    # never serve a type that no longer matches the model.
    cache_key = (custom_object_type.id, custom_object_type.cache_timestamp)
    with _type_cache_lock:
        cached = _type_cache.get(cache_key)
    if cached is not None:
        return cached

    in_progress = _in_progress_set()
    in_progress.add(custom_object_type.id)
    try:
        gql_type = _build_object_type(custom_object_type, model)
    finally:
        in_progress.discard(custom_object_type.id)

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
            resolver = _make_relationship_resolver(field)
            if resolver is not None:
                namespace[field_name] = resolver
            continue

        annotation = _scalar_annotation_for(field.type)
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
