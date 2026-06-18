import re
import threading
import warnings
from contextlib import contextmanager

from django.apps import apps

from netbox_custom_objects.constants import APP_LABEL

__all__ = (
    "AppsProxy",
    "extract_cot_id_from_model_name",
    "generate_model",
    "get_viewname",
    "install_clear_cache_suppressor",
    "restrict_to_viewable",
)

# ---------------------------------------------------------------------------
# Thread-safe apps.clear_cache suppression
# ---------------------------------------------------------------------------
# Django's apps.register_model() calls apps.clear_cache(), which triggers our
# get_models() override → get_model() for every known COT → potential infinite
# recursion and spurious cache invalidations during dynamic model registration.
#
# Rather than monkey-patching the module-level apps.clear_cache attribute on
# every call (which is not thread-safe), we install *one* wrapper at startup
# and use a thread-local depth counter to suppress clear_cache per-thread.
# This means concurrent requests on other threads always see real cache
# invalidation behaviour — only the thread doing model registration is
# suppressed, and only for the duration of the critical window.
# ---------------------------------------------------------------------------

_suppress_tl = threading.local()   # thread-local suppression depth counter
_real_clear_cache = None           # set by install_clear_cache_suppressor()


def _wrapped_clear_cache():
    """apps.clear_cache replacement — skips if this thread is suppressing."""
    if getattr(_suppress_tl, "depth", 0) > 0:
        return
    _real_clear_cache()


def install_clear_cache_suppressor():
    """Install the thread-aware wrapper on apps.clear_cache (idempotent).

    Must be called once from AppConfig.ready() before any dynamic model is
    registered.  Safe to call multiple times — subsequent calls are no-ops.

    Note: the idempotency check (``apps.clear_cache is _wrapped_clear_cache``)
    and the two-step assignment that follows are not atomic, so a concurrent
    second caller could store ``_wrapped_clear_cache`` itself in
    ``_real_clear_cache``, silently breaking suppression.  This is not a
    real-world risk because ``AppConfig.ready()`` is called by Django during
    single-threaded startup before any request threads are spawned.  If this
    function is ever moved out of ``ready()`` a proper lock will be needed.
    """
    global _real_clear_cache
    if apps.clear_cache is _wrapped_clear_cache:
        return  # already installed
    _real_clear_cache = apps.clear_cache
    apps.clear_cache = _wrapped_clear_cache


@contextmanager
def _suppress_clear_cache():
    """Context manager: suppress apps.clear_cache() in the current thread.

    Reentrant — uses a depth counter so nested calls don't prematurely
    re-enable the real clear_cache before the outermost block exits.

    Private: callers outside this module should use generate_model() or
    install_clear_cache_suppressor() rather than reaching for this directly.
    """
    _suppress_tl.depth = getattr(_suppress_tl, "depth", 0) + 1
    try:
        yield
    finally:
        _suppress_tl.depth -= 1


# Internal model names for custom object types follow the pattern "table<id>model"
# (e.g. "table3model" for CustomObjectType with pk=3).
_COT_MODEL_RE = re.compile(r"^table(\d+)model$")


class AppsProxy:

    def __init__(self, dynamic_models=None, app_label=None):
        self.dynamic_models = dynamic_models or {}
        self.dynamic_app_label = app_label or "database_table"

    def get_models(self, *args, **kwargs):
        return apps.get_models(*args, **kwargs) + list(self.dynamic_models.values())

    def register_model(self, app_label, model):
        with self._lock:
            model_name = model._meta.model_name.lower()
            if not hasattr(model, "_generated_table_model"):
                if not hasattr(self, "dynamic_models"):
                    self.dynamic_models = model._meta.auto_created.dynamic_models

            self.dynamic_models[model_name] = model
            self.do_all_pending_operations()
            self._clear_dynamic_models_cache()

            try:
                del apps.all_models[self.dynamic_app_label]
            except KeyError:
                pass

    def _clear_dynamic_models_cache(self):
        for model in self.dynamic_models.values():
            model._meta._expire_cache()

    def do_all_pending_operations(self):
        max_iterations = 3
        for _ in range(max_iterations):
            pending_operations_for_app_label = [
                (app_label, model_name)
                for app_label, model_name in list(apps._pending_operations.keys())
                if app_label == self.dynamic_app_label
            ]
            for _, model_name in list(pending_operations_for_app_label):
                model = self.dynamic_models[model_name]
                apps.do_pending_operations(model)

            if not pending_operations_for_app_label:
                break

    def __getattr__(self, attr):
        return getattr(apps, attr)


def extract_cot_id_from_model_name(model_name: str) -> str | None:
    """
    Extract the CustomObjectType primary key from an internal model name.

    Internal model names follow the pattern ``table<id>model`` (e.g. ``table3model``
    for CustomObjectType pk=3).  Returns the id as a string, or ``None`` if the name
    does not match the pattern.

    Use this instead of chained ``.replace("table", "").replace("model", "")`` calls,
    which corrupt model names that contain "table" or "model" as substrings.
    """
    m = _COT_MODEL_RE.match(model_name)
    return m.group(1) if m else None


def get_viewname(model, action=None, rest_api=False):
    """
    Return the view name for the given model and action, if valid.

    :param model: The model or instance to which the view applies
    :param action: A string indicating the desired action (if any); e.g. "add" or "list"
    :param rest_api: A boolean indicating whether this is a REST API view
    """
    is_plugin = True
    app_label = APP_LABEL
    model_name = "customobject"

    if rest_api:
        viewname = f"{app_label}-api:{model_name}"
        if is_plugin:
            viewname = f"plugins-api:{viewname}"
        if action:
            viewname = f"{viewname}-{action}"

    else:
        viewname = f"{app_label}:{model_name}"
        if is_plugin:
            viewname = f"plugins:{viewname}"
        if action:
            viewname = f"{viewname}_{action}"

    return viewname


def generate_model(*args, **kwargs):
    """
    Create a model.
    """
    # Suppress RuntimeWarning about model already being registered
    # TODO: Remove this once we have a better way to handle model registration
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore", category=RuntimeWarning, message=".*was already registered.*"
        )

        # Suppress apps.clear_cache during model type() creation to avoid
        # invalidating the app registry cache on every dynamic model build.
        with _suppress_clear_cache():
            model = type(*args, **kwargs)

    return model


def restrict_to_viewable(user, objects):
    """
    Return the subset of ``objects`` the user may view, preserving order.

    The top-level query restricts the custom objects themselves, but the objects
    reached through their relationship fields are *not* covered by that check, so
    each one must be gated here or the field would leak objects the user cannot
    see.  Used for both single- and multi-object fields so the permission rule
    lives in one place; batches the check to one query per distinct model rather
    than one ``.exists()`` per object (an N+1 explosion on multi-object fields):
    the related objects are grouped by model and each model's permission-restricted
    queryset is evaluated once with ``pk__in``.

    The user (anonymous or ``None`` included) is passed straight to the model
    manager's ``restrict(user, "view")``, mirroring NetBox's
    ``BaseObjectType.get_queryset`` — superuser bypass, ``EXEMPT_VIEW_PERMISSIONS``
    and anonymous handling are all left to ``restrict``.  Models whose manager has
    no ``restrict`` (not permission-aware) are treated as all-viewable.
    """
    objects = [obj for obj in objects if obj is not None]
    if not objects:
        return []
    if getattr(user, "is_superuser", False):
        return objects

    # sentinel meaning "model isn't permission-aware → all allowed".
    allowed_by_model = {}
    by_model = {}
    for obj in objects:
        by_model.setdefault(type(obj), []).append(obj)
    for model, model_objs in by_model.items():
        manager = getattr(model, "_default_manager", None)
        if manager is None or not hasattr(manager, "restrict"):
            allowed_by_model[model] = None
            continue
        pks = [obj.pk for obj in model_objs]
        allowed_by_model[model] = set(
            manager.restrict(user, "view").filter(pk__in=pks).values_list("pk", flat=True)
        )

    return [
        obj
        for obj in objects
        if (allowed_by_model[type(obj)] is None or obj.pk in allowed_by_model[type(obj)])
    ]
