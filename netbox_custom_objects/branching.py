"""
netbox-branching integration hooks for netbox-custom-objects.

These functions plug into extension points exposed by netbox-branching so the
plugin can correctly handle changes to its dynamically-generated CustomObject
models across schema-rename history.  Importing netbox-branching is
deferred / optional — none of these functions run if netbox-branching is not
installed (the registration call sites in ``__init__.ready()`` are guarded).
"""

from core.choices import ObjectChangeActionChoices
from core.models import ObjectChange


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


def translate_renamed_field_attr(instance, attr):
    """
    Resolve a CustomObject data-attribute name to its current field name,
    walking the field's rename history when the raw name doesn't match the
    instance's current field set.

    Called by ``netbox_branching.utilities.update_object()`` when a stored
    ObjectChange / ChangeDiff data dict carries a key that does not match
    any current field on ``instance`` — the typical case is a squash-merge
    revert where the collapsed prechange dict still uses the field's old
    name (e.g. 'beta') while the model class has the field's current name
    (e.g. 'gamma').  Returning the current name lets ``update_object``
    write the value to the correct column.

    For non-CustomObject instances or unresolved names, returns ``None`` so
    other registered translators (or the default behaviour) get a chance.
    """
    if not getattr(instance, '_generated_table_model', False):
        return None  # not a CustomObject — defer to other translators

    cot = getattr(instance, 'custom_object_type', None)
    if cot is None:
        return None

    # The field we're looking for is one of this COT's CustomObjectTypeFields
    # whose history (via ObjectChanges of name) includes ``attr`` as a former
    # or current name.  We match by walking ObjectChanges whose
    # postchange_data['name'] or prechange_data['name'] equals ``attr``.
    field_ct = ObjectChange.objects.filter(
        changed_object_type__app_label='netbox_custom_objects',
        changed_object_type__model='customobjecttypefield',
    )
    candidate_pks = set()
    for oc in field_ct.filter(action=ObjectChangeActionChoices.ACTION_UPDATE):
        post = oc.postchange_data or {}
        pre = oc.prechange_data or {}
        if post.get('name') == attr or pre.get('name') == attr:
            candidate_pks.add(oc.changed_object_id)
    if not candidate_pks:
        return None

    # Filter candidate fields to those belonging to this COT.  The fields
    # have stable PKs across schemas, and current name reflects the latest
    # state in the active context.
    fields_qs = cot.fields.filter(pk__in=candidate_pks)
    fields = list(fields_qs.values_list('name', flat=True))
    if not fields:
        return None

    # If a field was renamed, ``attr`` mapped to its PK; the field's current
    # ``name`` is the translated attribute.  When more than one field has
    # ``attr`` somewhere in its history (renamed away then renamed back, or
    # multiple fields cycling through the same name) we abstain — picking
    # arbitrarily would silently overwrite the wrong column.  This is
    # vanishingly rare for normal use and produces a clear "unknown attr"
    # signal at the call site rather than data corruption.
    if len(fields) == 1:
        return fields[0]
    return None
