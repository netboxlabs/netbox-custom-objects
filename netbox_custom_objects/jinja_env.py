"""
Jinja integration for netbox-custom-objects.

Provides:
  - ``filters``: a dict registered with NetBox's plugin ``jinja_filters`` hook.
  - ``CustomObjectsNamespace``: a lazy attribute-access namespace injected into
    every ConfigTemplate/ExportTemplate render context as ``custom_objects``.

Usage in a config template
--------------------------

Attribute access (via context injection)::

    {% for iface in custom_objects.ospf_interface.filter(device=device) %}
        interface {{ iface.name }}
            ip ospf area {{ iface.area }}
    {% endfor %}

Filter syntax (via the registered ``custom_objects`` filter)::

    {% for iface in 'ospf_interface' | custom_objects %}
        ...
    {% endfor %}

Both resolve the Custom Object Type by **name** at access time, so templates
remain valid even if the COT's internal table ID changes. Both also fail
quietly on an unknown name: a warning is logged, and the reference resolves
to an EmptyCustomObjectsQuerySet rather than raising, so a template that
chains queryset-style calls onto either form (as in the examples above)
renders no rows instead of crashing.

Both of these hooks (the ``jinja_filters`` plugin resource and
``PluginConfig.get_jinja_context()``) require NetBox 4.7+. On older NetBox,
this module is simply never consulted by core, so it degrades to a no-op
(see ``CustomObjectsPluginConfig.ready()`` for the startup log message).
"""
import logging

logger = logging.getLogger(__name__)


class EmptyCustomObjectsQuerySet:
    """
    Stand-in returned for an unresolved Custom Object Type name.

    Mimics the read-only subset of the QuerySet/Manager interface that
    templates chain onto ``custom_objects.<name>`` or ``<name> | custom_objects``
    (``.filter()``, ``.exclude()``, ``.all()``, ``.order_by()``, iteration,
    ``len()``, ``.count()``, etc.), always yielding no results. Unlike a real
    QuerySet, it accepts arbitrary filter/exclude kwargs without validating
    them against a model, since there is no model to validate against.

    This lets a template written against a Custom Object Type that was
    renamed or deleted keep rendering (with no data) instead of raising, for
    either access pattern.
    """

    def filter(self, *args, **kwargs):
        return self

    def exclude(self, *args, **kwargs):
        return self

    def all(self):
        return self

    def none(self):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def first(self):
        return None

    def last(self):
        return None

    def count(self):
        return 0

    def exists(self):
        return False

    def __iter__(self):
        return iter(())

    def __len__(self):
        return 0

    def __bool__(self):
        return False

    def __repr__(self):
        return '<EmptyCustomObjectsQuerySet>'


def _resolve_custom_object_type(name):
    """Look up a Custom Object Type by name; return None (with a warning logged) if unresolved."""
    from netbox_custom_objects.models import CustomObjectType
    try:
        return CustomObjectType.objects.get(name=name)
    except CustomObjectType.DoesNotExist:
        logger.warning("custom_objects: no Custom Object Type named %r", name)
        return None


class CustomObjectsNamespace:
    """
    Lazy namespace injected into the Jinja context as ``custom_objects``.

    Attribute access triggers a COT lookup by name and returns the model's
    default manager, allowing queryset operations directly in templates::

        custom_objects.ospf_interface.filter(device=device)

    An unknown name resolves to an EmptyCustomObjectsQuerySet rather than
    raising, matching the custom_objects filter's behavior.

    Lookups are intentionally deferred so that importing this module at startup
    does not touch the database.
    """

    def __getattr__(self, name):
        # Avoid intercepting Python internal attribute lookups (e.g. __deepcopy__).
        if name.startswith('_'):
            raise AttributeError(name)
        cot = _resolve_custom_object_type(name)
        if cot is None:
            return EmptyCustomObjectsQuerySet()
        return cot.get_model().objects

    def __repr__(self):
        return 'custom_objects'


def custom_objects_filter(type_name):
    """
    Jinja filter: resolve a Custom Object Type by name and return a queryset
    of all its instances.

    Example::

        {% for iface in 'ospf_interface' | custom_objects %}
    """
    cot = _resolve_custom_object_type(type_name)
    if cot is None:
        return EmptyCustomObjectsQuerySet()
    return cot.get_model().objects.all()


# Registered with NetBox via the jinja_filters plugin hook.
filters = {
    'custom_objects': custom_objects_filter,
}
