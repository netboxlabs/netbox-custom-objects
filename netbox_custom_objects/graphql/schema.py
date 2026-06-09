"""
GraphQL schema contribution for the custom-objects plugin.

NetBox discovers this module via the plugin's ``graphql_schema`` resource path
and extends its global ``Query`` with every class in the exported ``schema``
list.  :func:`build_query_classes` builds a single ``Query`` class exposing two
fields per custom object type — ``<name>`` (single object by ``id``) and
``<name>_list`` (filtered, paginated list) — mirroring NetBox's own per-model
GraphQL fields.

The module-level ``schema`` export is intentionally empty: the live schema in
:mod:`netbox_custom_objects.graphql.live` (installed via the view patch in
``__init__.py``) rebuilds the custom-object query per request from the current
database, so runtime-created types appear without a restart.  A statically built
query here would be immediately shadowed by that live rebuild.  ``live`` reuses
:func:`build_query_classes`, so both paths share one implementation.
"""

import logging
from typing import List

import strawberry
import strawberry_django

from .types import build_object_type, clear_type_cache, graphql_safe_name, reset_build_state, set_cot_map

logger = logging.getLogger("netbox_custom_objects.graphql")

# All custom-object root query fields are namespaced with this prefix so they
# cannot collide with NetBox's own (or another plugin's) root query fields — every
# plugin's query class is mixed into the single global ``Query`` type, so bare,
# slug-derived names like ``site``/``group`` would otherwise be shadowed by core.
# The prefix mirrors the ``custom_objects_<id>`` table-naming convention.
QUERY_FIELD_PREFIX = "custom_objects_"


def _query_field_name(custom_object_type, used_names):
    """
    Derive a GraphQL-safe, unique field name from a custom object type's slug.

    The name is namespaced with :data:`QUERY_FIELD_PREFIX` (see above) so it never
    collides with core/plugin root query fields.  GraphQL names must match
    ``[_A-Za-z][_0-9A-Za-z]*``; slugs may contain hyphens.  Collisions among custom
    object types (after sanitisation) are disambiguated with the type id.
    """
    slug = graphql_safe_name((custom_object_type.slug or "").lower())
    # The prefix guarantees a valid leading character, so no digit/empty guard is
    # needed on the slug portion.
    base = f"{QUERY_FIELD_PREFIX}{slug}"

    def _taken(candidate):
        # Reserve the singular field name *and* its ``_list`` companion together.
        # Checking both prevents one type's list field from silently colliding
        # with another type's singular field — e.g. slug 'foo' yields foo/foo_list
        # while slug 'foo-list' sanitises to foo_list/foo_list_list, and the bare
        # 'foo_list' would otherwise clobber the first type's list field.
        return candidate in used_names or f"{candidate}_list" in used_names

    name = base
    if _taken(name):
        # Disambiguate with the (unique) type id.  The disambiguated name can
        # itself collide with one already reserved — another type whose slug
        # happens to end in this id — so keep extending until both the singular
        # name and its ``_list`` companion are genuinely free.  Without this loop
        # the colliding field would silently overwrite the earlier type's field.
        name = f"{base}_{custom_object_type.id}"
        counter = 2
        while _taken(name):
            name = f"{base}_{custom_object_type.id}_{counter}"
            counter += 1
    used_names.add(name)
    used_names.add(f"{name}_list")
    return name


def build_query_classes():
    """
    Build the list of Strawberry query classes contributed to NetBox's schema.

    Returns an empty list when dynamic models are unavailable (during
    migrations, tests, or before migrations have been applied) or when no custom
    object types are defined — extending NetBox's Query with an empty/invalid
    class is avoided.

    Called on every live rebuild (:mod:`netbox_custom_objects.graphql.live`); the
    module-level ``schema`` export below deliberately does not call it (see the
    module docstring).  The returned class carries a ``_nco_query`` marker so live
    rebuilds can identify and replace a previously contributed instance.
    """
    # Import lazily to avoid import-time side effects and circular imports.
    # The skip-check must run *before* importing models: during migrations and
    # the test run the app registry may not be ready, and importing models then
    # raises "model isn't in an application in INSTALLED_APPS".
    from netbox_custom_objects import CustomObjectsPluginConfig

    if CustomObjectsPluginConfig.should_skip_dynamic_model_creation():
        return []

    from django.db.models import Prefetch

    from netbox_custom_objects.models import CustomObjectType, CustomObjectTypeField

    # Start each rebuild from an empty per-type cache.  The cache is keyed by
    # (cot id, cache_timestamp), but a type that embeds another COT's type via a
    # relationship field does NOT get its own cache_timestamp bumped when the
    # referenced COT changes — so a cached entry could embed a stale child type.
    # Clearing here makes every type fresh per rebuild (rebuilds only happen on an
    # actual structural change), while the cache still memoizes within this single
    # rebuild pass so shared and recursive references reuse one built type.
    # NOTE: this clear+repopulate of the process-global cache is why rebuilds must
    # be serialised — concurrent rebuilds (e.g. different branches) would race here.
    # live.get_live_schema holds _rebuild_lock around the whole rebuild to enforce it.
    clear_type_cache()
    # Also drop any in-progress build state (stack / cycle taint) leaked by an
    # exception during a previous rebuild on this pooled thread, so it can't
    # suppress caching or corrupt cycle detection on this rebuild.
    reset_build_state()

    try:
        # Preload every type's fields once, with each field's related type(s)
        # joined/prefetched, so the per-type build issues no per-field queries:
        # without this the inner loop hits the DB for each type's fields and again
        # for each field's related object type(s) — O(N + N·M) queries per rebuild.
        fields_qs = (
            CustomObjectTypeField.objects
            .select_related("related_object_type")
            .prefetch_related("related_object_types")
        )
        custom_object_types = list(
            CustomObjectType.objects.prefetch_related(Prefetch("fields", queryset=fields_qs))
        )
    except Exception:  # noqa: BLE001 - DB may be unavailable at import time
        logger.debug("Could not load custom object types for GraphQL schema", exc_info=True)
        return []

    # Register the preloaded types by pk so a relationship field pointing at another
    # custom object resolves its target from the prefetched instance instead of
    # re-querying it (build_object_type then reuses the per-rebuild type cache).
    set_cot_map({cot.pk: cot for cot in custom_object_types})

    annotations = {}
    attrs = {}
    used_names = set()

    for cot in custom_object_types:
        try:
            gql_type = build_object_type(cot)
        except Exception:  # noqa: BLE001 - never break the whole schema for one type
            logger.warning(
                "Failed to build GraphQL type for custom object type %r (id=%s); skipping",
                cot.name,
                cot.id,
                exc_info=True,
            )
            continue
        if gql_type is None:
            continue

        field_name = _query_field_name(cot, used_names)
        list_name = f"{field_name}_list"

        annotations[field_name] = gql_type
        attrs[field_name] = strawberry_django.field()
        annotations[list_name] = List[gql_type]
        attrs[list_name] = strawberry_django.field()

    if not attrs:
        return []

    attrs["__annotations__"] = annotations
    # The GraphQL type name must be "Query": strawberry-django only attaches the
    # single-object ``id`` lookup argument to fields whose origin type is named
    # "Query" (see strawberry_django.filters: ``is_root_query``).  NetBox names
    # every per-app query class "Query" for the same reason; our contributed class
    # is mixed into NetBox's real Query as a base, so it must do likewise.
    query_cls = strawberry.type(type("CustomObjectsQuery", (), attrs), name="Query")
    # Marker so live schema rebuilds can find and replace a stale instance of
    # this class among NetBox's Query bases.
    query_cls._nco_query = True
    return [query_cls]


# Empty by design — the live schema (graphql/live.py) owns per-request assembly so
# runtime-created types appear without a restart.  Must remain a list: NetBox calls
# ``.extend`` on it when registering plugin GraphQL schemas.
schema = []
