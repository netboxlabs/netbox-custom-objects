"""
Live (restart-free) GraphQL schema for custom objects.

NetBox builds its GraphQL schema once at startup and binds it to the GraphQL
view.  Custom object types, however, are created and deleted at runtime, and the
plugin runs across multiple worker processes — so a schema built once at boot
goes stale and a signal-based rebuild in one process can't reach the others.

This module makes the schema reflect the current database on every request, in
every process, without a restart:

- :func:`schema_signature` computes a cheap fingerprint of the custom object
  types and their fields (counts + most-recent change timestamps).  One small
  aggregate query pair per request.
- :func:`get_live_schema` caches the assembled schema per process and rebuilds
  it only when the signature changes.  Because each process checks the signature
  independently, a type created by a request handled in one process becomes
  visible to every process on its next request — no restart, no cross-process
  messaging.

The view patch in ``__init__.py`` calls :func:`get_live_schema` per request and
assigns the result to the view before it executes the operation.
"""

import logging
import threading

logger = logging.getLogger("netbox_custom_objects.graphql")

# Single-flight rebuild lock: only one thread rebuilds at a time; concurrent
# requests serve the current (possibly slightly stale) schema instead of blocking.
_rebuild_lock = threading.Lock()
# Atomically-swapped (signature, schema) pair.  Read without a lock on the hot
# path — a single reference read/assignment is atomic in CPython, and pairing the
# signature with the schema in one tuple means a reader can never see a schema
# that doesn't match its signature.
_current = (None, None)


def schema_signature():
    """
    Return a cheap, comparable fingerprint of the custom object type schema.

    Captures both custom object types and their fields so that creating,
    deleting, or editing either invalidates the cached GraphQL schema:

    - count detects additions and removals,
    - max timestamp detects edits (``cache_timestamp`` / ``last_updated`` are
      ``auto_now`` fields).
    """
    from django.db.models import Count, Max

    from netbox_custom_objects.models import CustomObjectType, CustomObjectTypeField

    cot = CustomObjectType.objects.aggregate(n=Count("id"), t=Max("cache_timestamp"))
    fields = CustomObjectTypeField.objects.aggregate(n=Count("id"), t=Max("last_updated"))
    return (cot["n"], cot["t"], fields["n"], fields["t"])


def build_full_schema():
    """
    Assemble a complete NetBox GraphQL schema with the current custom object types.

    Reuses NetBox's own ``Query`` base classes (all core apps plus every other
    plugin) so the result is identical to NetBox's startup schema except that our
    custom object query is rebuilt from the live database.  Our previously
    contributed query class is identified by the ``_nco_query`` marker and
    replaced.
    """
    import strawberry
    from strawberry.schema.config import StrawberryConfig

    import netbox.graphql.schema as ngs
    from netbox.graphql.scalars import BigInt, BigIntScalar

    from .schema import build_query_classes

    # Start from NetBox's canonical Query bases, dropping any stale custom-object
    # query class we contributed previously, then graft a freshly built one on.
    bases = tuple(b for b in ngs.Query.__bases__ if not getattr(b, "_nco_query", False))
    bases += tuple(build_query_classes())

    query_cls = strawberry.type(type("Query", bases, {}))
    return strawberry.Schema(
        query=query_cls,
        config=StrawberryConfig(
            auto_camel_case=False,
            scalar_map={BigInt: BigIntScalar},
        ),
        extensions=ngs.get_schema_extensions(),
    )


def get_live_schema():
    """
    Return the schema for the current request, rebuilding it if the database has
    changed since this process last built it.

    Returns ``None`` when dynamic models are unavailable (migrations/tests) or if
    the very first build fails — the caller then falls back to NetBox's static
    schema.
    """
    global _current

    from netbox_custom_objects import CustomObjectsPluginConfig

    if CustomObjectsPluginConfig.should_skip_dynamic_model_creation():
        return None

    try:
        signature = schema_signature()
    except Exception:  # noqa: BLE001 - DB hiccup: serve whatever we already have
        logger.debug("Could not compute GraphQL schema signature", exc_info=True)
        return _current[1]

    # Hot path: structure unchanged since this process last built — no lock, no
    # rebuild, just return the cached schema.
    sig, schema = _current
    if schema is not None and sig == signature:
        return schema

    # Structure changed (or first build).  Single-flight: one thread rebuilds
    # while concurrent requests keep serving the existing schema rather than
    # blocking on the (potentially expensive) rebuild.
    if not _rebuild_lock.acquire(blocking=False):
        # Another thread is already rebuilding; serve the current schema — valid,
        # just one signature behind — or None (→ static fallback) on first build.
        return schema

    try:
        # Re-check: a prior holder may have just published a matching schema.
        sig, schema = _current
        if schema is not None and sig == signature:
            return schema
        try:
            new_schema = build_full_schema()
        except Exception:  # noqa: BLE001 - never break the endpoint
            logger.exception("Failed to rebuild live GraphQL schema")
            return schema
        _current = (signature, new_schema)
        return new_schema
    finally:
        _rebuild_lock.release()


def reset_cache():
    """Clear the cached schema and per-type cache (used by tests)."""
    global _current
    _current = (None, None)

    from .types import clear_type_cache

    clear_type_cache()
