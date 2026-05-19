"""
netbox-branching integration hooks for netbox-custom-objects.

These functions plug into extension points exposed by netbox-branching so the
plugin can correctly route queries for its dynamically-generated through
models when an active branch is set, and translate field-name keys in stored
ObjectChange data when fields have been renamed at runtime.  Importing
netbox-branching is deferred / optional — the registration call sites in
``__init__.ready()`` are guarded so the plugin still works when
netbox-branching is not installed.
"""


def supports_branching_resolver(model):
    """
    Mark CustomObject M2M through-models as branchable.

    The dynamically-generated through models for ``MULTIOBJECT`` fields are
    plain ``models.Model`` subclasses (no ``ChangeLoggingMixin``), so
    netbox-branching's default heuristic would route their queries to main
    even when an active branch is set.  That breaks branch-context M2M
    assignments because the through-table FK to the parent CO model resolves
    to main's row set, which is missing branch-only CO instances.

    Returning ``True`` here pulls these through models into the same
    branching connection routing as their parent CO model.  Returning
    ``None`` for everything else lets the default heuristic run.
    """
    meta = getattr(model, '_meta', None)
    if meta is None or meta.app_label != 'netbox_custom_objects':
        return None
    name = meta.model_name or ''
    # Through models are named ``through_custom_objects_<n>_<field_name>``
    # (see CustomObjectTypeField.through_model_name).  Match anything with
    # that prefix.
    if name.startswith('through_custom_objects_'):
        return True
    return None


def objectchange_field_migrator(model, data):
    """
    Translate stale field-name keys in ``data`` for CustomObject models.

    Returns the translated dict when ``model`` is a generated ``CustomObject``
    subclass; returns ``None`` (defer) otherwise so other plugins' migrators
    can run.

    The actual translation logic lives on ``CustomObject.resolve_field_aliases``
    so the same code path can be re-used by ``CustomObject.deserialize_object``
    for CREATE replay; this function is the registration adapter that
    netbox-branching invokes.
    """
    meta = getattr(model, '_meta', None)
    if meta is None or meta.app_label != 'netbox_custom_objects':
        return None
    resolve = getattr(model, 'resolve_field_aliases', None)
    if resolve is None:
        return None
    return resolve(data)
