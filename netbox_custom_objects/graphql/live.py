"""
Live (restart-free) GraphQL schema for custom objects.

NetBox builds its GraphQL schema once at startup and binds it to the GraphQL
view.  Custom object types, however, are created and deleted at runtime, and the
plugin runs across multiple worker processes — so a schema built once at boot
goes stale and a signal-based rebuild in one process can't reach the others.

This module makes the schema reflect the current database on every request, in
every process, without a restart:

- :func:`schema_signature` computes a cheap fingerprint of the custom object
  types and their fields (counts + most-recent change timestamps).  The result
  is memoised in NetBox's shared cache and invalidated event-driven by the
  post_save/post_delete receivers in :func:`connect_signature_invalidation`, so
  the steady-state cost is one cache read rather than two DB queries per request.
- :func:`get_live_schema` caches the assembled schema per process and rebuilds
  it only when the signature changes.  Because each process checks the signature
  independently, a type created by a request handled in one process becomes
  visible to every process on its next request — no restart, no cross-process
  messaging.

The GraphQL schema is **main-only**: it is global to the process and reflects the
main database, never a branch (netbox-branching).  GraphQL has no concept of
branches, so custom object type changes made inside a branch must not alter the
schema — both the signature check and the rebuild run with the active branch reset
to main (see :func:`_main_branch_context`).

The view patch in ``__init__.py`` calls :func:`get_live_schema` per request and
assigns the result to the view before it executes the operation.
"""

import contextlib
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


@contextlib.contextmanager
def main_branch_context():
    """
    Run the enclosed block against the main database, ignoring any active branch.

    GraphQL is main-only: both its schema and the data it returns always reflect
    main, never a branch.  The signature check and the rebuild must not see a
    branch's custom object types (a COT created or edited inside a branch must never
    change the schema), and the request's data resolution must likewise read main.
    Delegates to netbox-branching's own ``deactivate_branch`` context manager
    (``activate_branch(None)``) so the meaning of "main" stays owned upstream
    rather than reimplemented here.  A no-op when netbox-branching is not
    installed.
    """
    try:
        from netbox_branching.utilities import deactivate_branch
    except ImportError:
        yield
        return

    with deactivate_branch():
        yield


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


# Sentinel signature for a best-effort first build made when the signature query
# itself failed (see get_live_schema): it compares unequal to every real
# signature, so the next request with a working DB rebuilds.
_UNKNOWN_SIGNATURE = object()

_SIGNATURE_CACHE_KEY = "netbox_custom_objects.graphql.schema_signature"
# Backstop TTL only — invalidation is event-driven (connect_signature_invalidation).
# It bounds staleness if an invalidation is ever lost (e.g. a cache blip during a
# write) without making the steady state poll the database.
_SIGNATURE_CACHE_TIMEOUT = 300


def cached_schema_signature():
    """
    Return the main schema signature, memoised in NetBox's shared cache.

    :func:`schema_signature` is two aggregate queries; running them on every
    GraphQL request is a needless per-request DB tax when the schema almost never
    changes.  The cached value is invalidated event-driven by the receivers in
    :func:`connect_signature_invalidation`, so a change in any worker is reflected
    everywhere on the next request — the same freshness guarantee as polling, at
    one cache read instead of two DB round-trips.  Falls back to a direct DB read
    whenever the cache is unavailable.
    """
    from django.core.cache import cache

    try:
        cached = cache.get(_SIGNATURE_CACHE_KEY)
    except Exception:  # noqa: BLE001 - cache down: fall back to the DB
        cached = None
    if cached is not None:
        # Cache backends may round-trip the tuple as a list; normalise so the
        # equality check against the stored signature stays type-stable.
        return tuple(cached)

    signature = schema_signature()
    try:
        cache.set(_SIGNATURE_CACHE_KEY, signature, _SIGNATURE_CACHE_TIMEOUT)
    except Exception:  # noqa: BLE001 - cache down: just skip memoisation
        pass
    return signature


def _invalidate_signature_cache(**kwargs):
    """Drop the memoised schema signature so the next request recomputes it."""
    from django.core.cache import cache

    try:
        cache.delete(_SIGNATURE_CACHE_KEY)
    except Exception:  # noqa: BLE001 - cache down: the TTL backstop still bounds staleness
        logger.debug("Could not invalidate GraphQL schema signature cache", exc_info=True)


def connect_signature_invalidation():
    """
    Connect the receivers that invalidate the cached schema signature.

    Called once from ``CustomObjectsPluginConfig.ready()``.  Creating, deleting,
    or editing any custom object type or field changes the signature; deleting the
    cache key on those events keeps :func:`cached_schema_signature` correct without
    polling the database per request.  ``dispatch_uid`` makes repeat ``ready()``
    calls idempotent.
    """
    from django.db.models.signals import post_delete, post_save

    from netbox_custom_objects.models import CustomObjectType, CustomObjectTypeField

    for signal, label in ((post_save, "save"), (post_delete, "delete")):
        for model in (CustomObjectType, CustomObjectTypeField):
            signal.connect(
                _invalidate_signature_cache,
                sender=model,
                dispatch_uid=f"nco_graphql_sig_{label}_{model.__name__}",
                weak=False,
            )


# NetBox assembles its GraphQL schema once at import and never changes it for the
# life of the process — only our custom-object slice does.  Capture NetBox's
# static contribution (its per-app/plugin Query bases and its own
# ``StrawberryConfig``) once; every rebuild then regenerates only our slice and
# reassembles.  Reusing NetBox's real config — rather than a hand-copied one —
# keeps the rebuilt schema in lock-step with NetBox's own (e.g. its stored
# ``auto_camel_case`` is ``None``, not the ``False`` a copy would assume) and
# removes a source of silent drift across supported NetBox versions.
_static_query_parts = None


def _get_static_query_parts():
    """Return ``(query_bases, config)`` captured once from NetBox's startup schema."""
    global _static_query_parts
    if _static_query_parts is None:
        import netbox.graphql.schema as ngs

        # These bases never include our own contribution (our startup
        # ``graphql_schema`` export is empty and we never write back to
        # ``ngs.Query``); the ``_nco_query`` guard is belt-and-braces.
        bases = tuple(b for b in ngs.Query.__bases__ if not getattr(b, "_nco_query", False))
        _static_query_parts = (bases, ngs.schema.config)
    return _static_query_parts


def build_full_schema():
    """
    Assemble a complete NetBox GraphQL schema with the current custom object types.

    A ``strawberry.Schema`` is immutable once compiled, so adding/removing a root
    query field requires building a new schema — but only our custom-object slice
    (:func:`build_query_classes`) is rebuilt here.  NetBox's Query bases and config
    are captured once (:func:`_get_static_query_parts`); the result is identical to
    NetBox's startup schema except for the live custom-object query.
    """
    import strawberry

    import netbox.graphql.schema as ngs

    from .schema import build_query_classes

    static_bases, config = _get_static_query_parts()
    bases = static_bases + tuple(build_query_classes())

    query_cls = strawberry.type(type("Query", bases, {}))
    return strawberry.Schema(
        query=query_cls,
        config=config,
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

    # Both the signature check and the rebuild run against main: the schema is
    # main-only and must not be perturbed by branch-local custom object types.
    with main_branch_context():
        try:
            signature = cached_schema_signature()
        except Exception:  # noqa: BLE001 - DB hiccup
            logger.warning("Could not compute GraphQL schema signature", exc_info=True)
            cached_schema = _current[1]
            if cached_schema is not None:
                # We already have a (possibly slightly stale) schema — serve it.
                return cached_schema
            # First build and even the signature query failed.  Rather than fall
            # back to NetBox's static schema (which has no custom_objects_* fields
            # and would reject otherwise-valid queries), make a best-effort first
            # build under a sentinel signature so the next request re-checks once
            # the DB recovers.
            signature = _UNKNOWN_SIGNATURE

        # Hot path: structure unchanged since this process last built — no lock, no
        # rebuild, just return the cached schema.
        sig, schema = _current
        if schema is not None and sig == signature:
            return schema

        # Structure changed (or first build).  Single-flight: one thread rebuilds
        # while concurrent requests keep serving the existing schema rather than
        # blocking on the (potentially expensive) rebuild.  On the *first* build
        # there is no schema to serve, so a loser must block until the rebuild
        # completes — otherwise it would fall back to the custom-object-less static
        # schema and spuriously reject custom_objects_* queries that do resolve.
        blocking = schema is None
        if not _rebuild_lock.acquire(blocking=blocking):
            # Another thread is already rebuilding and we have a valid (one
            # signature behind) schema to serve in the meantime.
            return schema

        try:
            # Re-check: a prior holder may have just published a matching schema
            # (always true for the first-build blocker that just waited).
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
    """Clear the cached schema, signature, per-type cache, and build state (tests)."""
    global _current
    _current = (None, None)

    from django.core.cache import cache

    try:
        cache.delete(_SIGNATURE_CACHE_KEY)
    except Exception:  # noqa: BLE001 - cache down: nothing to clear
        pass

    from .types import clear_type_cache, reset_build_state

    clear_type_cache()
    reset_build_state()
