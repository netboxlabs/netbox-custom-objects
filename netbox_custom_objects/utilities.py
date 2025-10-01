import warnings

from django.apps import apps

from netbox_custom_objects.constants import APP_LABEL

__all__ = (
    "AppsProxy",
    "generate_model",
    "get_viewname",
    "is_in_branch",
)


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
    # Monkey patch apps.clear_cache to do nothing
    apps.clear_cache = lambda: None

    # Suppress RuntimeWarning about model already being registered
    # TODO: Remove this once we have a better way to handle model registration
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore", category=RuntimeWarning, message=".*was already registered.*"
        )

        try:
            model = type(*args, **kwargs)
        finally:
            apps.clear_cache = apps.clear_cache

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
