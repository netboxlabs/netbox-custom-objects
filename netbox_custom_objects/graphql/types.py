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
from netbox.graphql.scalars import BigInt
from netbox.graphql.types import BaseObjectType
from strawberry.types import Info

from netbox_custom_objects.constants import APP_LABEL
from netbox_custom_objects.utilities import extract_cot_id_from_model_name, restrict_to_viewable

logger = logging.getLogger("netbox_custom_objects.graphql")

__all__ = (
    "CustomObjectObjectType",
    "CustomObjectRelatedObjectType",
    "build_object_type",
    "clear_type_cache",
    "graphql_safe_name",
    "reset_build_state",
    "set_cot_map",
)

# Per-rebuild memoization of built GraphQL types, keyed by (cot id,
# cache_timestamp).  It lets a single schema rebuild reuse one built type across
# the many places that reference it (shared targets and recursive relationships)
# instead of re-running build_object_type for each.  It is cleared at the start of
# every rebuild (see schema.build_query_classes): a type that embeds another COT's
# type does not get its own cache_timestamp bumped when that referenced COT
# changes, so persisting entries across rebuilds could serve a stale embedded
# type.  clear_type_cache() also lets tests reset it explicitly.
_type_cache = {}
_type_cache_lock = threading.RLock()

# Per-thread build state.  ``cot_stack`` is the stack of COT ids whose GraphQL
# type is being built on the current thread, so that a relationship between two
# custom objects (A -> B -> A) does not recurse forever: a back-reference to a
# type still under construction falls back to the flat stub instead of rebuilding
# it.  ``cycle_tainted`` records the COT ids whose build had to use that stub
# fallback for a cyclic edge — those types are intentionally not cached (see
# build_object_type) so the next top-level query rebuilds them and resolves the
# related type fully from that entry point.
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


def reset_build_state():
    """
    Clear this thread's in-progress build stack and cycle-taint set.

    Called at the start of each schema rebuild (and by tests) so that a stack
    frame or taint leaked by an exception during a previous rebuild on this
    (pooled) thread cannot suppress caching or corrupt cycle detection on the
    next one.  ``build_object_type`` only clears a type's taint on the success
    path, so a build that raises after a cyclic edge tainted an ancestor would
    otherwise leave that taint set on the thread indefinitely.  Also drops the
    preloaded COT map (see :func:`set_cot_map`).
    """
    _building.cot_stack = []
    _building.cycle_tainted = set()
    _building.cot_map = None


def set_cot_map(cot_map):
    """
    Register a ``{pk: CustomObjectType}`` map for the current rebuild.

    ``build_query_classes`` preloads every custom object type once with its fields
    (and their related types) prefetched, then registers them here so a relationship
    field pointing at another custom object resolves its target from the prefetched
    instance — via :func:`_custom_object_graphql_type` — instead of issuing a fresh
    query per reference.  Cleared by :func:`reset_build_state`.
    """
    _building.cot_map = cot_map


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

    # BigInt (not int/Int): NetBox primary keys are BigAutoField and can exceed
    # the signed 32-bit range of GraphQL's Int.  Native relationship types already
    # expose their id as BigInt; the fallback stub must match.
    id: BigInt
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


def graphql_safe_name(value):
    """Replace any character not valid in a GraphQL name with an underscore."""
    return re.sub(r"[^0-9a-zA-Z_]", "_", value or "")


def _in_progress_stack():
    stack = getattr(_building, "cot_stack", None)
    if stack is None:
        stack = []
        _building.cot_stack = stack
    return stack


def _cycle_tainted_set():
    tainted = getattr(_building, "cycle_tainted", None)
    if tainted is None:
        tainted = set()
        _building.cycle_tainted = tainted
    return tainted


def _request_user(info):
    """Best-effort extraction of the requesting user from the GraphQL info context."""
    request = getattr(getattr(info, "context", None), "request", None)
    return getattr(request, "user", None)


def _filter_viewable(user, objects):
    """
    Return the subset of ``objects`` the user may view, preserving order.

    Thin GraphQL-side wrapper over the shared
    :func:`netbox_custom_objects.utilities.restrict_to_viewable` helper (also used
    by the combined related-objects tab) so the permission rule lives in one place.
    """
    return restrict_to_viewable(user, objects)


def _related_repr(obj):
    """Convert a referenced model instance into a ``CustomObjectRelatedObjectType``."""
    if obj is None:
        return None
    try:
        url = obj.get_absolute_url()
    except Exception:  # noqa: BLE001 - URL resolution is best-effort
        url = None
    try:
        display = str(obj)
    except Exception:  # noqa: BLE001 - a broken __str__ must not fail the whole field
        # Degrade to a stable identifier so one unrenderable object becomes a
        # placeholder rather than erroring the entire (possibly multi-object) field;
        # pk and _meta are safe even when __str__ raises.
        display = f"{obj._meta.label_lower}:{obj.pk}"
    return CustomObjectRelatedObjectType(
        id=obj.pk,
        object_type=obj._meta.label_lower,
        display=display,
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

    cot_id = extract_cot_id_from_model_name(model_name)
    if cot_id is None:
        return None
    # extract_cot_id_from_model_name returns the id as a str; the in-progress
    # stack holds ints, so coerce before the membership test or it never matches.
    cot_id = int(cot_id)
    stack = _in_progress_stack()
    if cot_id in stack:
        # Back-reference to a type still being built — fall back to the flat stub
        # for this edge to avoid infinite recursion, and taint every type currently
        # under construction so none of them is cached with this temporary stub
        # frozen in (see build_object_type).  Tainting only the immediate parent
        # (stack[-1]) would still cache the outer types of a cycle longer than two
        # (A -> B -> C -> A), permanently freezing the stub into their subtree.
        _cycle_tainted_set().update(stack)
        return None
    # Resolve from the rebuild's preloaded (prefetched) map when available, so a
    # cross-COT reference doesn't issue a query per edge; fall back to a lookup for
    # direct callers (e.g. tests) that didn't register a map.
    cot_map = getattr(_building, "cot_map", None)
    cot = cot_map.get(cot_id) if cot_map else None
    if cot is None:
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
    base = graphql_safe_name(field.name)
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
                for obj in _filter_viewable(user, related)
            ]
        if value is None:
            return None
        viewable = _filter_viewable(user, [value])
        if not viewable:
            return None
        return _coerce_related(viewable[0], native_models)

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

    stack = _in_progress_stack()
    stack.append(custom_object_type.id)
    try:
        gql_type = _build_object_type(custom_object_type, model)
    finally:
        stack.pop()

    # A type whose build had to break a relationship cycle with the flat stub (a
    # related custom object was still under construction) must not be cached: the
    # stub edge is an artefact of *this* build order, and caching it would freeze
    # that degraded edge forever.  Leaving it uncached lets a later top-level
    # query rebuild it and resolve the related type fully from that entry point.
    tainted = _cycle_tainted_set()
    if custom_object_type.id in tainted:
        tainted.discard(custom_object_type.id)
        return gql_type

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
