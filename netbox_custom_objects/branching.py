"""netbox-branching integration hooks.

Registered from ``__init__.ready()`` only when netbox-branching is installed.
"""

import logging
from contextvars import ContextVar
from functools import partial

from django.apps import apps
from django.db import connection
from django.db.utils import OperationalError, ProgrammingError

logger = logging.getLogger(__name__)

# The branch this process is merging, if any; see merging_branch_model().
_merging_branch = ContextVar('netbox_custom_objects_merging_branch', default=None)


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


def _constraint_field_refs(data, fields_by_schema_id):
    """Return the field refs a constraint's *data* covers or tests, for fields changed in the same squash.

    Constraints reference fields by ``schema_id`` (an integer list that the generic
    walker would misread as self-references), so map them to field keys explicitly.
    """
    if not data:
        return set()
    cot_id = data.get('custom_object_type')
    schema_ids = list(data.get('field_schema_ids') or ())
    for predicate in [*(data.get('condition') or ()), *(data.get('rule') or ())]:
        if isinstance(predicate, dict):
            schema_ids.append(predicate.get('field'))
            value = predicate.get('value')
            if isinstance(value, dict):
                schema_ids.append(value.get('field'))
    return {fields_by_schema_id[(cot_id, sid)] for sid in schema_ids if (cot_id, sid) in fields_by_schema_id}


def _created_field_refs(data, model_label, created_fields_by_column):
    """Return the refs of fields created in the same squash that an object's *data* writes.

    The object's model class predates those fields, so a walk over its fields can't find them.
    """
    from .utilities import extract_cot_id_from_model_name

    if not data or not model_label:
        return set()
    cot_id_str = extract_cot_id_from_model_name(model_label.split('.', 1)[-1])
    if cot_id_str is None:
        return set()
    cot_id = int(cot_id_str)
    return {created_fields_by_column[(cot_id, key)] for key in data if (cot_id, key) in created_fields_by_column}


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

    field_label = f'{APP_LABEL}.customobjecttypefield'
    constraint_label = f'{APP_LABEL}.customobjecttypeconstraint'

    deletes_map = {}
    updates_map = {}
    creates_map = {}
    fields_by_schema_id = {}
    for key, cc in collapsed_changes.items():
        if isinstance(key, tuple) and key[0] == field_label:
            data = cc.postchange_data or cc.prechange_data or {}
            if data.get('schema_id') is not None:
                fields_by_schema_id[(data.get('custom_object_type'), data['schema_id'])] = key
        action = cc.final_action.value if cc.final_action else None
        if action == 'create':
            creates_map[key] = cc
        elif action == 'update':
            updates_map[key] = cc
        elif action == 'delete':
            deletes_map[key] = cc

    from .models import _occupied_columns

    created_fields_by_column = {}
    for key, cc in creates_map.items():
        if not (isinstance(key, tuple) and key[0] == field_label):
            continue
        data = cc.postchange_data or {}
        if data.get('custom_object_type') is None or not data.get('name'):
            continue
        for column in _occupied_columns(data['name'], data.get('type')):
            created_fields_by_column[(data['custom_object_type'], column)] = key

    for cc in collapsed_changes.values():
        meta = getattr(cc.model_class, '_meta', None)
        # Detect CO models even when model_class is None (dynamically-generated
        # CO models aren't registered in apps.all_models until their COT CREATE
        # is applied, so ContentType.model_class() returns None during the
        # squash dep-graph phase — the meta is None guard would silently skip
        # them).  Fall back to inspecting cc.key[0] which is always set.
        model_label = cc.key[0] if isinstance(cc.key, tuple) else None
        is_co_model = (meta is not None and meta.app_label == APP_LABEL) or (
            meta is None and model_label is not None and model_label.startswith(f'{APP_LABEL}.')
        )
        if not is_co_model:
            continue
        action = cc.final_action.value if cc.final_action else None

        if model_label == constraint_label:

            def collect_refs(data):
                return _constraint_field_refs(data, fields_by_schema_id)
        else:

            def collect_refs(data, model_class=cc.model_class, model_label=model_label):
                return _collect_co_refs(model_class, data, model_label=model_label)

        if action == 'update':
            for ref in collect_refs(cc.prechange_data):
                if ref in deletes_map:
                    deletes_map[ref].depends_on.add(cc.key)
                    cc.depended_by.add(ref)
            post_refs = collect_refs(cc.postchange_data) | _created_field_refs(
                cc.postchange_data, model_label, created_fields_by_column
            )
            for ref in post_refs:
                if ref in creates_map:
                    cc.depends_on.add(ref)
                    creates_map[ref].depended_by.add(cc.key)
        elif action == 'create':
            for ref in collect_refs(cc.postchange_data):
                if ref != cc.key and ref in creates_map:
                    cc.depends_on.add(ref)
                    creates_map[ref].depended_by.add(cc.key)
        elif action == 'delete':
            for ref in collect_refs(cc.prechange_data):
                if ref != cc.key and ref in deletes_map:
                    deletes_map[ref].depends_on.add(cc.key)
                    cc.depended_by.add(ref)


def mark_merging_branch(sender, branch, **kwargs):
    """netbox-branching's ``pre_merge`` receiver; see merging_branch_model()."""
    _merging_branch.set(branch)


def unmark_merging_branch(sender, **kwargs):
    """netbox-branching's ``post_merge`` receiver.

    It isn't sent when a merge fails, so merging_branch_model() also checks the branch's status.
    """
    _merging_branch.set(None)


def merging_branch_model(cot_id):
    """The model of Custom Object Type *cot_id* in the branch this process is merging, or None.

    netbox-branching's squash merge looks up the model of every changed object before it
    applies any change, so before the branch's new types exist in main.
    """
    branch = _merging_branch.get()
    if branch is None:
        return None

    from netbox_branching.choices import BranchStatusChoices
    from netbox_branching.models import Branch
    from netbox_branching.utilities import activate_branch

    from netbox_custom_objects.models import CustomObjectType

    try:
        if not Branch.objects.filter(pk=branch.pk, status=BranchStatusChoices.MERGING).exists():
            return None
        with activate_branch(branch):
            cot = CustomObjectType.objects.filter(pk=cot_id).first()
            return cot.get_model() if cot is not None else None
    except (ProgrammingError, OperationalError):
        return None


def _unmerged_type_ids(cot_ids):
    """Those of *cot_ids* that are neither in main nor in any branch."""
    from netbox_branching.models import Branch

    from netbox.plugins import get_plugin_config
    from netbox_custom_objects.models import CustomObjectType

    remaining = set(cot_ids)
    quote = connection.ops.quote_name
    schemas = [get_plugin_config('netbox_branching', 'main_schema')]
    schemas += [branch.schema_name for branch in Branch.objects.all()]
    with connection.cursor() as cursor:
        for schema in schemas:
            if not remaining:
                break
            table = f'{quote(schema)}.{quote(CustomObjectType._meta.db_table)}'
            cursor.execute('SELECT to_regclass(%s)', [table])
            if cursor.fetchone()[0] is None:
                continue
            cursor.execute(f'SELECT id FROM {table} WHERE id = ANY(%s)', [list(remaining)])
            remaining -= {row[0] for row in cursor.fetchall()}
    return remaining


def remove_unmerged_object_types(sender, instance, **kwargs):
    """``post_delete`` receiver for netbox-branching's ``Branch``: remove the object types
    of Custom Object Types that exist neither in main nor in any remaining branch.

    A type's object type is created in main even when the type is created in a branch, and
    is left behind when the branch is deleted without being merged.  The deleted branch's
    ChangeDiffs, which protect it, are gone by now.  This is clean-up, so a failure is
    logged rather than failing the branch's deletion.
    """
    from django.contrib.contenttypes.models import ContentType
    from django.db import transaction
    from django.db.models import ProtectedError
    from netbox_branching.utilities import deactivate_branch

    from core.models import ObjectType
    from netbox.context import current_request
    from netbox_custom_objects.constants import APP_LABEL
    from netbox_custom_objects.models import CustomObjectType
    from netbox_custom_objects.stale_models import signal_custom_object_type_deleted
    from netbox_custom_objects.utilities import extract_cot_id_from_model_name

    # Another branch may be active, e.g. when this one is deleted from a request made in it.
    with deactivate_branch():
        try:
            with transaction.atomic():
                candidates = {}
                for object_type in ObjectType.objects.filter(app_label=APP_LABEL):
                    cot_id = extract_cot_id_from_model_name(object_type.model)
                    if cot_id is not None:
                        candidates[int(cot_id)] = object_type
                orphan_ids = _unmerged_type_ids(candidates)
        except Exception:
            logger.exception('Could not look for object types left behind by deleting branch %s', instance)
            return

        for cot_id in sorted(orphan_ids):
            object_type = candidates[cot_id]
            # As in CustomObjectType.delete(): keep core's pre_delete handler out of it.
            request_token = current_request.set(None)
            try:
                with transaction.atomic():
                    object_type.delete()
            except ProtectedError:
                logger.warning(
                    'Not removing object type %s of a Custom Object Type that no longer exists: still referenced',
                    object_type,
                )
                continue
            except Exception:
                logger.exception(
                    'Could not remove object type %s of a Custom Object Type that no longer exists', object_type
                )
                continue
            finally:
                current_request.reset(request_token)
            model = apps.all_models.get(APP_LABEL, {}).get(object_type.model)
            if model is not None:
                CustomObjectType.unregister_model(model)
            CustomObjectType.clear_model_cache(cot_id, all_branches=True)
            # Other worker processes may have this model registered too.
            transaction.on_commit(partial(signal_custom_object_type_deleted, cot_id))
    ContentType.objects.clear_cache()
