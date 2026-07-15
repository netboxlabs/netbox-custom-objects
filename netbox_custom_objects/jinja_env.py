"""
Jinja integration for netbox-custom-objects.

Provides:
  - ``filters``: a dict registered with NetBox's plugin ``jinja_filters`` hook.
  - ``CustomObjectsNamespace``: a lazy attribute-access namespace injected into
    every ConfigTemplate/ExportTemplate render context as ``custom_objects``.

Usage in a config template
--------------------------

Attribute access (via context injection)::

    {% for iface in custom_objects.OSPFInterface.filter(device=device) %}
        interface {{ iface.name }}
            ip ospf area {{ iface.area }}
    {% endfor %}

Filter syntax (via the registered ``custom_objects`` filter)::

    {% for iface in 'OSPFInterface' | custom_objects %}
        ...
    {% endfor %}

Both resolve the Custom Object Type by **name** at access time, so templates
remain valid even if the COT's internal table ID changes.

Both of these hooks (the ``jinja_filters`` plugin resource and
``PluginConfig.get_jinja_context()``) require NetBox 4.7+. On older NetBox,
this module is simply never consulted by core, so it degrades to a no-op
(see ``CustomObjectsPluginConfig.ready()`` for the startup log message).
"""
import logging

logger = logging.getLogger(__name__)


class CustomObjectsNamespace:
    """
    Lazy namespace injected into the Jinja context as ``custom_objects``.

    Attribute access triggers a COT lookup by name and returns the model's
    default manager, allowing queryset operations directly in templates::

        custom_objects.OSPFInterface.filter(device=device)

    Lookups are intentionally deferred so that importing this module at startup
    does not touch the database.
    """

    def __getattr__(self, name):
        # Avoid intercepting Python internal attribute lookups (e.g. __deepcopy__).
        if name.startswith('_'):
            raise AttributeError(name)
        from netbox_custom_objects.models import CustomObjectType
        try:
            cot = CustomObjectType.objects.get(name=name)
        except CustomObjectType.DoesNotExist:
            raise AttributeError(
                f"No Custom Object Type named {name!r}. "
                "Check the name in NetBox under Plugins → Custom Objects."
            )
        return cot.get_model().objects

    def __repr__(self):
        return 'custom_objects'


def custom_objects_filter(type_name):
    """
    Jinja filter: resolve a Custom Object Type by name and return a queryset
    of all its instances.

    Example::

        {% for iface in 'OSPFInterface' | custom_objects %}
    """
    from netbox_custom_objects.models import CustomObjectType
    try:
        cot = CustomObjectType.objects.get(name=type_name)
    except CustomObjectType.DoesNotExist:
        logger.warning("custom_objects filter: no Custom Object Type named %r", type_name)
        return []
    return cot.get_model().objects.all()


# Registered with NetBox via the jinja_filters plugin hook.
filters = {
    'custom_objects': custom_objects_filter,
}
