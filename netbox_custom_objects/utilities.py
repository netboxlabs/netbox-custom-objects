import re
import warnings

from django.apps import apps

from netbox_custom_objects.constants import APP_LABEL

__all__ = (
    "AppsProxy",
    "extract_cot_id_from_model_name",
    "generate_model",
    "get_viewname",
    "is_in_branch",
)

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

        # Temporarily suppress apps.clear_cache during model type() creation to
        # avoid invalidating the app registry cache on every dynamic model build.
        _original_clear_cache = apps.clear_cache
        apps.clear_cache = lambda: None
        try:
            model = type(*args, **kwargs)
        finally:
            apps.clear_cache = _original_clear_cache

    return model


def is_in_branch():
    """
    Check if currently operating within a branch.

    Returns:
        bool: True if currently in a branch, False otherwise.
    """
    try:
        from netbox_branching.contextvars import active_branch
        return active_branch.get() is not None
    except ImportError:
        # Branching plugin not installed
        return False
