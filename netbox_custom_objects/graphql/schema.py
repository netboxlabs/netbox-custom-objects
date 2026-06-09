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
import re
from typing import List

import strawberry
import strawberry_django

from .types import build_object_type

logger = logging.getLogger("netbox_custom_objects.graphql")


def _query_field_name(custom_object_type, used_names):
    """
    Derive a GraphQL-safe, unique field name from a custom object type's slug.

    GraphQL names must match ``[_A-Za-z][_0-9A-Za-z]*``; slugs may contain
    hyphens.  Collisions (after sanitisation) are disambiguated with the type id.
    """
    base = re.sub(r"[^0-9a-zA-Z_]", "_", (custom_object_type.slug or "").lower())
    if not base or base[0].isdigit():
        base = f"_{base}"
    name = base
    # Reserve the singular field name *and* its ``_list`` companion together.
    # Checking/recording both prevents one type's list field from silently
    # colliding with another type's singular field — e.g. slug 'foo' yields
    # foo/foo_list while slug 'foo-list' sanitises to foo_list/foo_list_list, and
    # the bare 'foo_list' would otherwise clobber the first type's list field.
    if name in used_names or f"{name}_list" in used_names:
        name = f"{base}_{custom_object_type.id}"
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

    from netbox_custom_objects.models import CustomObjectType

    try:
        custom_object_types = list(CustomObjectType.objects.all())
    except Exception:  # noqa: BLE001 - DB may be unavailable at import time
        logger.debug("Could not load custom object types for GraphQL schema", exc_info=True)
        return []

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
