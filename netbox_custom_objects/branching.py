"""netbox-branching integration hooks.

Registered from ``__init__.ready()`` only when netbox-branching is installed.
"""


def supports_branching_resolver(model):
    """Mark CustomObject M2M through models as branchable.

    Through models are plain ``models.Model`` subclasses (no ChangeLoggingMixin),
    so the default heuristic would route their queries to main even inside a
    branch — and the FK to the parent CO would resolve against main's rows.
    Returning ``True`` pulls them into the branch connection routing.
    """
    meta = getattr(model, '_meta', None)
    if meta is None or meta.app_label != 'netbox_custom_objects':
        return None
    name = meta.model_name or ''
    if name.startswith('through_custom_objects_'):
        return True
    return None


def objectchange_field_migrator(model, data):
    """Rewrite stale field-name keys in *data* for CustomObject models.

    Delegates to ``CustomObject.resolve_field_aliases`` (shared with
    ``deserialize_object``).  Returns ``None`` for non-CO models so other
    plugins' migrators can run.
    """
    meta = getattr(model, '_meta', None)
    if meta is None or meta.app_label != 'netbox_custom_objects':
        return None
    resolve = getattr(model, 'resolve_field_aliases', None)
    if resolve is None:
        return None
    return resolve(data)


def co_polymorphic_dependency_resolver(model, data, changed_objects):
    """Tell squash that a CO CREATE depends on its polymorphic-M2M field CREATEs.

    The polymorphic M2M lives on a ``PolymorphicM2MDescriptor`` (not a Django
    field), so squash's default FK/GFK introspection sees no edge between the
    CO's postchange_data and the ``CustomObjectTypeField`` rows.  Without it
    squash may apply the CO before the field — the through table doesn't
    exist yet.  The sidecar carries field PKs so we don't need to look them
    up in main (they aren't there yet during a branch-only merge).
    """
    from .constants import APP_LABEL
    from .models import POLY_M2M_SIDECAR_KEY

    meta = getattr(model, '_meta', None)
    if meta is None or meta.app_label != APP_LABEL:
        return ()
    entries = data.get(POLY_M2M_SIDECAR_KEY) or ()
    if not entries:
        return ()
    label = f'{APP_LABEL}.customobjecttypefield'
    return [
        (label, entry['pk'])
        for entry in entries
        if isinstance(entry, dict) and entry.get('pk') is not None
    ]
