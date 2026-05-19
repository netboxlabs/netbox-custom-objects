"""
netbox-branching integration hooks for netbox-custom-objects.

These functions plug into extension points exposed by netbox-branching so the
plugin can correctly route queries for its dynamically-generated through
models when an active branch is set.  Importing netbox-branching is deferred /
optional — the registration call site in ``__init__.ready()`` is guarded so
the plugin still works when netbox-branching is not installed.

Field-rename translation lives on ``CustomObject.resolve_field_aliases`` (a
model classmethod) and is invoked directly by netbox-branching from
``update_object`` and ``ChangeDiff._update_conflicts``; no registration is
required for that path.
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
