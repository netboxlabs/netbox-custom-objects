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
