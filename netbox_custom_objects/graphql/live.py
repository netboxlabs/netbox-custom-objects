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

GraphQL resolves against whatever branch netbox-branching activated for the
request — the ``X-NetBox-Branch`` header, the ``?_branch=`` query param, or the
``active_branch`` cookie — exactly like the REST API and the rest of the UI (the
plugin imposes no GraphQL-specific branch policy of its own); with no branch active
it is main.  The schema cache is keyed per branch (see :func:`_active_branch_key`)
so each branch's custom object types are reflected independently, and
:func:`get_live_schema` builds the schema for whichever branch is active so the
schema and the data the query returns always agree.

The view patch in ``__init__.py`` calls :func:`get_live_schema` per request and
assigns the result to the view before it executes the operation.
"""

import logging
import threading

from django.apps import apps as django_apps

logger = logging.getLogger("netbox_custom_objects.graphql")

# Single-flight rebuild lock.  Two roles:
#   1. Correctness (required): build_query_classes() clears and repopulates the
#      *process-global* type cache in graphql.types (clear_type_cache()).  Two
#      rebuilds running at once — e.g. for different branches — would race on that
#      cache, one wiping the other's freshly built entries mid-build.  This single
#      lock (global, not per-branch) serialises ALL rebuilds so that can't happen.
#   2. Performance: concurrent requests that find a stale schema serve it instead
#      of blocking on the (potentially expensive) rebuild a peer is already doing.
_rebuild_lock = threading.Lock()
# Per-branch cache of (signature, schema), keyed by branch identifier (None = main).
# GraphQL reflects whichever branch netbox-branching activated for the request, so
# each branch gets its own schema reflecting that branch's custom object types.  Each
# value is an atomically swapped (signature, schema) tuple, so a lockless reader can
# never see a schema that doesn't match its signature.
_schema_cache = {}
# Signature cache keys this process has populated, so reset_cache (tests) can clear
# every per-branch entry.
_signature_keys_seen = set()


def _active_branch_key():
    """
    Identifier for the active branch (``None`` for main), used to key the per-branch
    schema and signature caches.  ``None`` when netbox-branching is not installed.
    """
    try:
        from netbox_branching.contextvars import active_branch
    except ImportError:
        return None
    branch = active_branch.get()
    return branch.pk if branch is not None else None


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


def _signature_cache_key(branch_key):
    """Per-branch cache key for the schema signature (``None`` = main)."""
    if branch_key is None:
        return _SIGNATURE_CACHE_KEY
    return f"{_SIGNATURE_CACHE_KEY}:{branch_key}"


def cached_schema_signature(branch_key=None):
    """
    Return the active branch's schema signature, memoised in NetBox's shared cache.

    :func:`schema_signature` is two aggregate queries; running them on every
    GraphQL request is a needless per-request DB tax when the schema almost never
    changes.  The cached value is invalidated event-driven by the receivers in
    :func:`connect_signature_invalidation`, so a change in any worker is reflected
    everywhere on the next request — the same freshness guarantee as polling, at
    one cache read instead of two DB round-trips.  The key includes the branch
    identifier so a branch's signature never shadows main's, and the aggregates run
    under the caller's active branch context.  Falls back to a direct DB read
    whenever the cache is unavailable.
    """
    from django.core.cache import cache

    key = _signature_cache_key(branch_key)
    _signature_keys_seen.add(key)
    try:
        cached = cache.get(key)
    except Exception:  # noqa: BLE001 - cache down: fall back to the DB
        cached = None
    if cached is not None:
        # Cache backends may round-trip the tuple as a list; normalise so the
        # equality check against the stored signature stays type-stable.
        return tuple(cached)

    signature = schema_signature()
    try:
        cache.set(key, signature, _SIGNATURE_CACHE_TIMEOUT)
    except Exception:  # noqa: BLE001 - cache down: just skip memoisation
        pass
    return signature


def _invalidate_signature_cache(**kwargs):
    """
    Drop the memoised schema signature for the branch the change occurred in, so the
    next request for that branch recomputes it.  The receiver fires inside whatever
    branch context performed the save/delete, so the active branch is the one to
    invalidate.
    """
    from django.core.cache import cache

    try:
        cache.delete(_signature_cache_key(_active_branch_key()))
    except Exception:  # noqa: BLE001 - cache down: the TTL backstop still bounds staleness
        logger.debug("Could not invalidate GraphQL schema signature cache", exc_info=True)


def _evict_branch_schema(sender, instance, **kwargs):
    """
    Drop a deleted branch's cached schema and signature.

    A branch's schema is cached under its pk in :data:`_schema_cache`; once the
    branch is gone that entry can never be served again (a request can't reference a
    deleted branch), so evict it rather than leak it for the life of the process.
    """
    branch_key = instance.pk
    _schema_cache.pop(branch_key, None)  # atomic dict op; no lock needed

    from django.core.cache import cache

    key = _signature_cache_key(branch_key)
    _signature_keys_seen.discard(key)
    try:
        cache.delete(key)
    except Exception:  # noqa: BLE001 - cache down: the TTL backstop still bounds it
        logger.debug("Could not evict deleted branch's schema signature", exc_info=True)


def connect_signature_invalidation():
    """
    Connect the receivers that invalidate the cached schema signature.

    Called once from ``CustomObjectsPluginConfig.ready()``.  Creating, deleting,
    or editing any custom object type or field changes the signature; deleting the
    cache key on those events keeps :func:`cached_schema_signature` correct without
    polling the database per request.  Deleting a branch evicts that branch's cached
    schema so it can't leak.  ``dispatch_uid`` makes repeat ``ready()`` calls
    idempotent.
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

    # Evict a branch's cached schema when the branch itself is deleted.  No-op when
    # netbox-branching is not installed or not in INSTALLED_APPS.
    if not django_apps.is_installed('netbox_branching'):
        return
    from netbox_branching.models import Branch
    post_delete.connect(
        _evict_branch_schema,
        sender=Branch,
        dispatch_uid="nco_graphql_evict_branch",
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

    # Every rebuild creates fresh classes with names ("Query", "Table<id>ModelType",
    # …) that were used by previous rebuilds' schemas.  This relies on Strawberry
    # building a *per-schema* type map at strawberry.Schema(...) time — same-named
    # types in different Schema objects don't collide; only duplicates *within* one
    # schema do (which build_query_classes avoids by uniquifying field/type names).
    # That behaviour is version-dependent and Strawberry isn't pinned here (it's
    # whatever NetBox ships); it's exercised by the multi-rebuild tests
    # (test_live_schema_drops_deleted_type, test_get_live_schema_rebuilds_on_new_type).
    # If a future Strawberry raised on cross-schema name reuse, get_live_schema would
    # log the rebuild failure and keep serving the prior schema (not silently break).
    query_cls = strawberry.type(type("Query", bases, {}))
    return strawberry.Schema(
        query=query_cls,
        config=config,
        # Built fresh per rebuild on purpose — unlike ``config`` (a read-only
        # settings object), get_schema_extensions() returns extension *instances*
        # that strawberry mutates per request (``extension.execution_context = …``).
        # Each Schema must own its own set; sharing one set across our several live
        # schemas (main + per branch, plus an old schema still serving in-flight
        # requests during a rebuild) would let concurrent requests on different
        # schemas clobber each other's execution context.  This mirrors what NetBox
        # gets for its own schema, and rebuilds are rare, so the cost is irrelevant.
        extensions=ngs.get_schema_extensions(),
    )


def get_live_schema():
    """
    Return the schema for the current request's active branch, rebuilding it if the
    database has changed since this process last built it for that branch.

    netbox-branching has already scoped the active branch for the request (from the
    X-NetBox-Branch header, the ``?_branch=`` query param, or the active_branch
    cookie; main if none).  The signature check, the rebuild, and the query's data
    resolution therefore all run against that same branch.

    Returns ``None`` when dynamic models are unavailable (migrations/tests) or if
    the very first build fails — the caller then falls back to NetBox's static
    schema.
    """
    from netbox_custom_objects import CustomObjectsPluginConfig

    if CustomObjectsPluginConfig.should_skip_dynamic_model_creation():
        return None

    branch_key = _active_branch_key()

    try:
        signature = cached_schema_signature(branch_key)
    except Exception:  # noqa: BLE001 - DB hiccup
        logger.warning("Could not compute GraphQL schema signature", exc_info=True)
        cached = _schema_cache.get(branch_key)
        if cached is not None and cached[1] is not None:
            # We already have a (possibly slightly stale) schema for this branch.
            return cached[1]
        # First build for this branch and even the signature query failed.  Rather
        # than fall back to NetBox's static schema (which has no custom_objects_*
        # fields and would reject otherwise-valid queries), make a best-effort first
        # build under a sentinel signature so the next request re-checks once the DB
        # recovers.
        signature = _UNKNOWN_SIGNATURE

    # Hot path: structure unchanged since this process last built for this branch —
    # no lock, no rebuild, just return the cached schema.
    entry = _schema_cache.get(branch_key)
    sig, schema = entry if entry is not None else (None, None)
    if schema is not None and sig == signature:
        return schema

    # Structure changed (or first build).  Single-flight: one thread rebuilds while
    # concurrent requests keep serving the existing schema rather than blocking on
    # the (potentially expensive) rebuild.  On the *first* build there is no schema
    # to serve, so a loser must block until the rebuild completes — otherwise it
    # would fall back to the custom-object-less static schema and spuriously reject
    # custom_objects_* queries that do resolve.
    blocking = schema is None
    if not _rebuild_lock.acquire(blocking=blocking):
        # Another thread is already rebuilding and we have a valid (one signature
        # behind) schema to serve in the meantime.
        return schema

    try:
        # Re-check: a prior holder may have just published a matching schema for
        # this branch (always true for the first-build blocker that just waited).
        entry = _schema_cache.get(branch_key)
        sig, schema = entry if entry is not None else (None, None)
        if schema is not None and sig == signature:
            return schema
        try:
            new_schema = build_full_schema()
        except Exception:  # noqa: BLE001 - never break the endpoint
            logger.exception("Failed to rebuild live GraphQL schema")
            return schema
        _schema_cache[branch_key] = (signature, new_schema)
        return new_schema
    finally:
        _rebuild_lock.release()


def reset_cache():
    """Clear the cached schemas, signatures, per-type cache, and build state (tests)."""
    global _schema_cache
    _schema_cache = {}

    from django.core.cache import cache

    for key in list(_signature_keys_seen):
        try:
            cache.delete(key)
        except Exception:  # noqa: BLE001 - cache down: nothing to clear
            pass
    _signature_keys_seen.clear()

    from .types import clear_type_cache, reset_build_state

    clear_type_cache()
    reset_build_state()
