import logging

from netbox_custom_objects.constants import APP_LABEL

from .views.combined import COMBINED_LABEL, COMBINED_WEIGHT, make_co_combined_view, register_combined_tabs

logger = logging.getLogger('netbox_custom_objects.related_tabs')

# Action name / path / URL name for the combined tab on custom-object host pages.
# Kept in sync with the {% custom_objects_tab_link %} <li> in customobject.html
# and the custom_objects_tab_link template tag.
_CO_COMBINED_ACTION = 'custom_objects'
_CO_COMBINED_PATH = 'custom-objects'
# CustomObject._get_viewname('custom_objects') ->
# 'plugins:netbox_custom_objects:customobject_custom_objects'
CO_COMBINED_URL_NAME = f'customobject_{_CO_COMBINED_ACTION}'


def _inject_co_urls():
    """
    Inject the generic combined-tab URL for custom-object host pages into
    ``netbox_custom_objects.urls``.

    Custom-object detail pages are served by one generic view and never call
    ``get_model_urls()``, so the tab view has no URL pattern. Add ONE slug-agnostic
    route (``<str:custom_object_type>/<int:pk>/custom-objects/``) at ready() time,
    before the URLconf freezes — it reverses for any slug, including COTs created
    after startup. The URL name follows CustomObject._get_viewname():
    ``plugins:netbox_custom_objects:customobject_custom_objects``.
    """
    try:
        import netbox_custom_objects.urls as co_urls
        from django.urls import path as url_path
    except ImportError:
        return

    existing_names = {p.name for p in co_urls.urlpatterns if hasattr(p, 'name') and p.name}
    if CO_COMBINED_URL_NAME in existing_names:
        return

    full_path = f'<str:custom_object_type>/<int:pk>/{_CO_COMBINED_PATH}/'
    co_urls.urlpatterns.append(
        url_path(full_path, make_co_combined_view().as_view(), name=CO_COMBINED_URL_NAME)
    )
    logger.debug("injected URL pattern '%s'", CO_COMBINED_URL_NAME)


def _public_host_model_classes():
    """
    Return the model classes a combined "Custom Objects" tab may need to appear on.

    These are the models a CustomObjectType OBJECT/MULTIOBJECT field can target —
    ``ObjectType.objects.public()`` (the same flag the field's form picker uses)
    minus this plugin's own app (custom-object hosts use the generic injected URL,
    see ``_inject_co_urls``). Registering on all of them at startup is what makes a
    newly-referenced model's tab live without a restart (see ``register_tabs``).

    Returns ``[]`` if the database isn't usable yet (fresh install before
    ``migrate``); registration is retried on the next process start.
    """
    from core.models import ObjectType
    from django.db.utils import OperationalError, ProgrammingError

    try:
        object_types = list(ObjectType.objects.public().exclude(app_label=APP_LABEL))
    except (OperationalError, ProgrammingError):
        logger.warning('database unavailable — combined tab not registered until next start')
        return []

    seen = set()
    result = []
    for ot in object_types:
        # Log the stored app_label/model columns, not str(ot): when the model class
        # is gone, str(ot) renders as "None > None" and hides which ObjectType is the
        # problem.  The columns still point at the culprit (e.g. an uninstalled plugin).
        try:
            model = ot.model_class()
        except Exception:
            logger.exception(
                'skipping ObjectType pk=%s (%s.%s): error resolving its model class',
                ot.pk, ot.app_label, ot.model,
            )
            continue
        if model is None:
            logger.warning(
                'skipping ObjectType pk=%s (%s.%s): no installed model — likely a stale row from an '
                'uninstalled plugin or a deleted Custom Object Type',
                ot.pk, ot.app_label, ot.model,
            )
            continue
        key = (model._meta.app_label, model._meta.model_name)
        if key in seen:
            continue
        seen.add(key)
        result.append(model)

    return result


def register_tabs():
    """
    Register the combined "Custom Objects" tab.

    Called from ``CustomObjectsPluginConfig.ready()`` as a third pass, after the
    existing two-pass model + serializer registration.  Two host kinds:

    * **Built-in NetBox models** — the tab view is registered on every public
      model (``_public_host_model_classes``), so each model's per-model URL is
      baked by ``get_model_urls()`` when its app's ``urls.py`` is imported at
      URLconf freeze.  The tab shows only when its live badge is non-zero
      (``hide_if_empty``), so registering broadly is cheap and a newly-referenced
      model's tab is live on the next request — no restart.

    * **Custom-object host pages (CO→CO)** — served by a single COT-agnostic URL
      injected here (``_inject_co_urls``) plus the live ``custom_objects_tab_link``
      template tag.

    All registration must happen synchronously here: NetBox builds each model's
    URLconf (via ``get_model_urls()``) on the first ``resolve()`` call,
    snapshotting ``registry['views']`` at that moment; anything added later has no
    URL pattern.  Likewise, ``_inject_co_urls()`` mutates
    ``netbox_custom_objects.urls.urlpatterns`` and must run before the URL
    resolver populates its lookup cache against that list.
    """
    from django.urls import clear_url_caches

    try:
        # Inject the generic custom-object combined-tab URL first and
        # unconditionally.  It is a single COT-agnostic route, so it must exist at
        # startup (the URLconf freezes after ready()) to serve combined tabs on
        # custom-object host pages — including CustomObjectTypes created later
        # (CO→CO references).
        _inject_co_urls()
        register_combined_tabs(_public_host_model_classes(), COMBINED_LABEL, COMBINED_WEIGHT)
    finally:
        # Always drop URL-resolver caches once we've mutated urlpatterns / the
        # view registry — even if model enumeration raised partway through.  A
        # resolver cache built earlier in ready() (by other plugins) would
        # otherwise leave the injected CO→CO route unresolvable until a restart.
        clear_url_caches()
