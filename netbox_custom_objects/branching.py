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


def _collect_co_refs(model_class, data, model_label=None):
    """Return ``(app.model, pk)`` refs from CO-specific shapes in *data*.

    Covers:
      * Local M2M target lists (squash's default ``_get_fk_references`` only
        walks FK / GFK fields, so M2M targets — including self-referential
        ones — are invisible to it).
      * Every ``CustomObjectTypeField`` on the CO's model.  A CO INSERT needs
        the field's column (scalar) or through table (M2M) to exist first;
        without these edges squash may apply the CO CREATE before the field
        CREATEs.  Pulled from the model class's ``_field_objects`` plus the
        polymorphic ``POLY_M2M_SIDECAR_KEY`` (which carries field PKs in the
        ObjectChange payload even when ``_field_objects`` isn't available).

    ``model_label`` — the ``"{app_label}.{model_name}"`` key from
    ``CollapsedChange.key``.  Provided when ``model_class`` is ``None``
    (dynamic CO models that aren't yet registered in ``apps.all_models``
    during the squash dep-graph phase).  Used as the ref label for the
    self-referential M2M fallback (see below).
    """
    from .constants import APP_LABEL
    from .models import POLY_M2M_SIDECAR_KEY

    refs = set()
    if not data:
        return refs

    # Primary pass: walk M2M fields declared on the model class.
    m2m_field_names = set()
    meta = getattr(model_class, '_meta', None)
    for field in getattr(meta, 'local_many_to_many', ()):
        m2m_field_names.add(field.name)
        values = data.get(field.name)
        if not values:
            continue
        rel_meta = field.related_model._meta
        label = f'{rel_meta.app_label}.{rel_meta.model_name}'
        for pk in values:
            if isinstance(pk, int):
                refs.add((label, pk))

    # Fallback for dynamically-generated CO models whose class isn't yet
    # registered in apps.all_models at dep-graph time (model_class is None).
    # The only CO field type that stores a plain list of integers in
    # postchange_data is a direct (non-polymorphic) M2M.  When such a field
    # is self-referential the refs point to the same model label, so we can
    # add the dep edge without knowing the concrete model class.
    # Cross-COT M2M would produce a wrong label, but those refs won't appear
    # in creates_map for the source model and are silently ignored.
    if model_label and model_label.startswith(f'{APP_LABEL}.'):
        for key, value in data.items():
            if key in (POLY_M2M_SIDECAR_KEY, 'tags') or key in m2m_field_names:
                continue
            if isinstance(value, list) and value and all(isinstance(v, int) for v in value):
                for pk in value:
                    refs.add((model_label, pk))

    field_label = f'{APP_LABEL}.customobjecttypefield'
    for fo in (getattr(model_class, '_field_objects', None) or {}).values():
        cotf = fo.get('field') if isinstance(fo, dict) else None
        if cotf is not None and getattr(cotf, 'pk', None) is not None:
            refs.add((field_label, cotf.pk))

    entries = data.get(POLY_M2M_SIDECAR_KEY) or ()
    for entry in entries:
        if isinstance(entry, dict) and entry.get('pk') is not None:
            refs.add((field_label, entry['pk']))

    return refs


def add_custom_object_dependencies(sender, collapsed_changes, **kwargs):
    """Extend squash's dependency graph with CO-specific edges.

    Walks every collapsed change for a CO model and mirrors squash's four
    edge-direction rules (UPDATE→DELETE, UPDATE→CREATE, CREATE→CREATE,
    DELETE→DELETE) using ``_collect_co_refs`` instead of the FK/GFK walker.

    The signal's ``operation`` kwarg ('merge' or 'revert') is intentionally
    ignored: these edges express physical "must exist before" relationships,
    and revert reverses the topological order so the same edges produce the
    correct undo sequence.
    """
    from .constants import APP_LABEL

    deletes_map = {}
    updates_map = {}
    creates_map = {}
    for key, cc in collapsed_changes.items():
        action = cc.final_action.value if cc.final_action else None
        if action == 'create':
            creates_map[key] = cc
        elif action == 'update':
            updates_map[key] = cc
        elif action == 'delete':
            deletes_map[key] = cc

    for cc in collapsed_changes.values():
        meta = getattr(cc.model_class, '_meta', None)
        # Detect CO models even when model_class is None (dynamically-generated
        # CO models aren't registered in apps.all_models until their COT CREATE
        # is applied, so ContentType.model_class() returns None during the
        # squash dep-graph phase — the meta is None guard would silently skip
        # them).  Fall back to inspecting cc.key[0] which is always set.
        model_label = cc.key[0] if isinstance(cc.key, tuple) else None
        is_co_model = (
            meta is not None and meta.app_label == APP_LABEL
        ) or (
            meta is None
            and model_label is not None
            and model_label.startswith(f'{APP_LABEL}.')
        )
        if not is_co_model:
            continue
        action = cc.final_action.value if cc.final_action else None

        if action == 'update':
            for ref in _collect_co_refs(cc.model_class, cc.prechange_data, model_label=model_label):
                if ref in deletes_map:
                    deletes_map[ref].depends_on.add(cc.key)
                    cc.depended_by.add(ref)
            for ref in _collect_co_refs(cc.model_class, cc.postchange_data, model_label=model_label):
                if ref in creates_map:
                    cc.depends_on.add(ref)
                    creates_map[ref].depended_by.add(cc.key)
        elif action == 'create':
            for ref in _collect_co_refs(cc.model_class, cc.postchange_data, model_label=model_label):
                if ref != cc.key and ref in creates_map:
                    cc.depends_on.add(ref)
                    creates_map[ref].depended_by.add(cc.key)
        elif action == 'delete':
            for ref in _collect_co_refs(cc.model_class, cc.prechange_data, model_label=model_label):
                if ref != cc.key and ref in deletes_map:
                    deletes_map[ref].depends_on.add(cc.key)
                    cc.depended_by.add(ref)
