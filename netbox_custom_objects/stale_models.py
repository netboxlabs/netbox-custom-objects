"""
Evict models of deleted Custom Object Types and fields from every worker process.

Deleting a Custom Object Type unregisters its generated model only in the process
that handles the delete (``CustomObjectType.delete()``).  Other worker processes
keep it registered, so it stays in other models' ``_meta.related_objects``, and a
later delete of a tag, owner or related object there queries the dropped table.
Changed types don't have this problem: ``get_model()`` notices a changed
``cache_timestamp`` the next time it's called, but a deleted type's model is never
looked up again.  Deleting or renaming a multi-object field has the same problem
with the field's old through model: regenerating the type's model doesn't
unregister it.

So once the change commits, the process that made it marks the type's ID, or the
field's old through model, in NetBox's shared cache and writes a fresh token
there.  Each worker compares the token with the last one it saw at the start
of every request and job.  When it changes, the worker unregisters the marked
models it has registered.  Only marked models are evicted: a type or field another
thread is creating, but hasn't committed yet, is missing from the database too,
and its models must stay registered.
"""

import logging
import uuid

from django.apps import apps
from django.core.cache import cache
from django.db.utils import OperationalError, ProgrammingError

from netbox_custom_objects.constants import APP_LABEL
from netbox_custom_objects.utilities import extract_cot_id_from_model_name

logger = logging.getLogger('netbox_custom_objects.stale_models')

__all__ = (
    'connect_stale_model_eviction',
    'evict_deleted_models',
    'signal_custom_object_type_deleted',
    'signal_through_model_removed',
)

_TOKEN_CACHE_KEY = 'netbox_custom_objects.deleted_types_token'
_DELETED_TYPE_CACHE_KEY = 'netbox_custom_objects.deleted_type.{}'
_REMOVED_THROUGH_MODEL_CACHE_KEY = 'netbox_custom_objects.removed_through_model.{}'

# The token this process last acted on.  A random token rather than a counter, so a
# cache flush or restart can't make a later delete look like one already seen.
_seen_token = None


def signal_custom_object_type_deleted(cot_id):
    """Tell every worker that the Custom Object Type with this ID was deleted."""
    try:
        # Mark the type before changing the token, so a worker that sees the new
        # token also sees the mark.
        cache.set(_DELETED_TYPE_CACHE_KEY.format(cot_id), True, None)
        cache.set(_TOKEN_CACHE_KEY, uuid.uuid4().hex, None)
    except Exception:  # cache down: other workers keep the stale model until restart
        logger.warning('Could not signal a Custom Object Type deletion to other workers', exc_info=True)


def signal_through_model_removed(cot_id, through_model_name):
    """
    Tell every worker that this through model of the Custom Object Type with this
    ID is gone, because its multi-object field was deleted or renamed.
    """
    try:
        cache.set(_REMOVED_THROUGH_MODEL_CACHE_KEY.format(through_model_name.lower()), cot_id, None)
        cache.set(_TOKEN_CACHE_KEY, uuid.uuid4().hex, None)
    except Exception:  # cache down: other workers keep the stale model until restart
        logger.warning('Could not signal a removed through model to other workers', exc_info=True)


def evict_deleted_models(**kwargs):
    """
    Unregister generated models of Custom Object Types, and through models of
    multi-object fields, removed (in any process) since this process last checked.
    """
    global _seen_token
    try:
        token = cache.get(_TOKEN_CACHE_KEY)
    except Exception:  # cache down: nothing to act on
        return
    if token is None or token == _seen_token:
        return

    # Either can fail while the cache or database is unavailable; try again next time.
    if _evict_deleted_types() and _evict_removed_through_models():
        _seen_token = token


def _evict_deleted_types():
    from netbox_custom_objects.models import CustomObjectType

    registered = {}
    for model_name, model in list(apps.all_models.get(APP_LABEL, {}).items()):
        cot_id = extract_cot_id_from_model_name(model_name)
        if cot_id is not None:
            registered[int(cot_id)] = (model_name, model)
    try:
        marked = cache.get_many([_DELETED_TYPE_CACHE_KEY.format(cot_id) for cot_id in registered])
    except Exception:
        return False
    deleted = {cot_id for cot_id in registered if _DELETED_TYPE_CACHE_KEY.format(cot_id) in marked}

    if deleted:
        # A mark outlives a database restore, after which the ID can be reused.
        try:
            deleted -= set(CustomObjectType.objects.filter(pk__in=deleted).values_list('pk', flat=True))
        except (OperationalError, ProgrammingError):
            return False  # database not ready (e.g. before migrations)

    for cot_id in deleted:
        model_name, model = registered[cot_id]
        CustomObjectType.clear_model_cache(cot_id, all_branches=True)
        CustomObjectType.unregister_model(model)
        logger.debug('Unregistered model %s of a deleted Custom Object Type', model_name)
    return True


def _evict_removed_through_models():
    from netbox_custom_objects.models import CustomObjectType

    registry = apps.all_models.get(APP_LABEL, {})
    keys = {
        _REMOVED_THROUGH_MODEL_CACHE_KEY.format(model_name): model_name
        for model_name in list(registry)
        if model_name.startswith('through_')
    }
    try:
        marked = cache.get_many(list(keys))
    except Exception:
        return False
    if not marked:
        return True
    if apps.get_app_config(APP_LABEL)._dynamic_model_creation_unsafe():
        return False

    # Regenerate the types' models first: the registered ones may still have the
    # fields, so they stay in the target models' _meta.related_objects too.  A
    # field added again since, under the same name, gets a through model of that
    # name again; only the regenerated model's own through models are current.
    current = set()
    failed = set()
    try:
        cots = list(CustomObjectType.objects.filter(pk__in=set(marked.values())))
    except (OperationalError, ProgrammingError):
        return False
    for cot in cots:
        try:
            current.update(cot.get_model()._through_models)
        except (OperationalError, ProgrammingError):
            return False
        except Exception:
            # Not worth retrying on every request; leave this type's through models.
            logger.warning(
                'Could not regenerate the model of Custom Object Type %s to evict its removed through models',
                cot.pk,
                exc_info=True,
            )
            failed.add(cot.pk)

    stale = [
        keys[key] for key, cot_id in marked.items() if cot_id not in failed and registry.get(keys[key]) not in current
    ]
    if stale:
        # Only main's: changes in a branch don't signal, and a branch's through
        # model registered under a marked name belongs to a field of the branch.
        CustomObjectType._unregister_model_names(stale, main_only=True)
        logger.debug('Unregistered removed through models %s', stale)
    return True


def connect_stale_model_eviction():
    """
    Check for removed types and through models at the start of every request and job.

    ``request_started`` fires before any middleware, so no netbox-branching branch
    is active yet and the existence check reads main, where the generated models
    registered in ``apps.all_models`` belong.  Called once from
    ``CustomObjectsPluginConfig.ready()``; ``dispatch_uid`` makes repeat calls
    idempotent.
    """
    from django.core.signals import request_started

    from core.signals import job_start

    request_started.connect(
        evict_deleted_models,
        dispatch_uid='nco_evict_deleted_types_request',
        weak=False,
    )
    job_start.connect(
        evict_deleted_models,
        dispatch_uid='nco_evict_deleted_types_job',
        weak=False,
    )
