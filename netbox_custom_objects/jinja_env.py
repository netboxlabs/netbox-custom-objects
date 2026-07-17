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
quietly on an unknown name: a warning is logged (once per name, per process,
to avoid log spam across a bulk render), and the reference resolves to an
EmptyCustomObjectsQuerySet rather than raising, so a template that chains
queryset-style calls onto either form (as in the examples above) renders no
rows instead of crashing.

Both of these hooks (the ``jinja_filters`` plugin resource and
``PluginConfig.get_jinja_context()``) require NetBox 4.7+. On older NetBox,
this module is simply never consulted by core, so it degrades to a no-op
(see ``CustomObjectsPluginConfig.ready()`` for the startup log message).
"""
import logging

from jinja2 import pass_context

logger = logging.getLogger(__name__)

# Names for which the "no Custom Object Type named ..." warning has already been
# logged, so a template typo rendered against many objects (e.g. a bulk device
# config export) logs once per process rather than once per render. If a type is
# later created under a previously-warned name, resolution still succeeds
# immediately -- only the warning is suppressed, not the lookup itself.
_warned_unknown_names = set()


class EmptyCustomObjectsQuerySet:
    """
    Stand-in returned for an unresolved Custom Object Type name.

    Mimics the read-only subset of the QuerySet/Manager interface that
    templates commonly chain onto ``custom_objects.<name>`` or
    ``<name> | custom_objects`` (``.filter()``, ``.exclude()``, ``.all()``,
    ``.order_by()``, ``.values()``, ``.values_list()``, ``.select_related()``,
    ``.prefetch_related()``, ``.distinct()``, ``.annotate()``, slicing,
    iteration, ``len()``, ``.count()``, etc.), always yielding no results.
    Unlike a real QuerySet, it accepts arbitrary kwargs without validating
    them against a model, since there is no model to validate against.

    This lets a template written against a Custom Object Type that was
    renamed or deleted keep rendering (with no data) instead of raising, for
    either access pattern, as long as only the queryset methods listed above
    are chained onto the result.
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

    def values(self, *args, **kwargs):
        return self

    def values_list(self, *args, **kwargs):
        return self

    def select_related(self, *args, **kwargs):
        return self

    def prefetch_related(self, *args, **kwargs):
        return self

    def distinct(self, *args, **kwargs):
        return self

    def annotate(self, *args, **kwargs):
        return self

    def get(self, *args, **kwargs):
        # Matches real QuerySet.get() semantics: "no matching object" is a
        # genuine, expected condition to raise on, not something to paper over.
        raise LookupError("No matching object (Custom Object Type is unresolved).")

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

    def __getitem__(self, item):
        if isinstance(item, slice):
            return self
        raise IndexError("EmptyCustomObjectsQuerySet index out of range")

    def __repr__(self):
        return '<EmptyCustomObjectsQuerySet>'


def _resolve_custom_object_type(name):
    """Look up a Custom Object Type by name; return None (warning logged once per name) if unresolved."""
    from netbox_custom_objects.models import CustomObjectType
    try:
        return CustomObjectType.objects.get(name=name)
    except CustomObjectType.DoesNotExist:
        if name not in _warned_unknown_names:
            logger.warning("custom_objects: no Custom Object Type named %r", name)
            _warned_unknown_names.add(name)
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
    does not touch the database. Resolved results are cached per-instance (not
    across renders, since a new CustomObjectsNamespace is created for every
    render via get_jinja_context()) so a template referencing the same name
    multiple times issues only one lookup per render.
    """

    def __init__(self):
        self._cache = {}

    def __getattr__(self, name):
        # Avoid intercepting Python internal attribute lookups (e.g. __deepcopy__).
        if name.startswith('_'):
            raise AttributeError(name)
        if name not in self._cache:
            cot = _resolve_custom_object_type(name)
            self._cache[name] = cot.get_model().objects if cot is not None else EmptyCustomObjectsQuerySet()
        return self._cache[name]

    def __repr__(self):
        return 'custom_objects'


@pass_context
def custom_objects_filter(_context, type_name):
    """
    Jinja filter: resolve a Custom Object Type by name and return a queryset
    of all its instances.

    Example::

        {% for iface in 'ospf_interface' | custom_objects %}

    Marked with @pass_context (unused beyond the signature) so Jinja treats
    this as context-dependent and never constant-folds a call whose argument
    is a string literal -- which would otherwise resolve the Custom Object
    Type (and run a database query) once at template compile time instead of
    at render time.
    """
    cot = _resolve_custom_object_type(type_name)
    if cot is None:
        return EmptyCustomObjectsQuerySet()
    return cot.get_model().objects.all()


# Registered with NetBox via the jinja_filters plugin hook.
filters = {
    'custom_objects': custom_objects_filter,
}
