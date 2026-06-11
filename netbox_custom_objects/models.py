import contextvars
import decimal
import logging
import re
import threading
from datetime import date, datetime

from packaging.version import Version, InvalidVersion

import django_filters
from core.choices import ObjectChangeActionChoices
from core.models import ObjectType, ObjectChange
from core.models.object_types import ObjectTypeManager
from django.apps import apps
from django.conf import settings

# from django.contrib.contenttypes.management import create_contenttypes
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import FieldDoesNotExist
from django.core.validators import RegexValidator, ValidationError
from django.db import DEFAULT_DB_ALIAS, connection, connections, IntegrityError, models, transaction
from django.db.utils import OperationalError, ProgrammingError
from django.db.models import Q
from django.db.models.fields.related import ForeignKey, ManyToManyField
from django.db.models.functions import Lower
from django.db.models.signals import m2m_changed, pre_delete, post_save
from django.dispatch import receiver
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from core.signals import handle_deleted_object
from extras.choices import (
    CustomFieldFilterLogicChoices,
    CustomFieldTypeChoices,
    CustomFieldUIEditableChoices,
    CustomFieldUIVisibleChoices,
)
from extras.models import CustomField
from extras.models.customfields import SEARCH_TYPES
from extras.utils import is_taggable, run_validators
from netbox.config import get_config
from netbox.models import ChangeLoggedModel, NetBoxModel
from netbox.models.features import (
    BookmarksMixin,
    ChangeLoggingMixin,
    CloningMixin,
    ContactsMixin,
    CustomLinksMixin,
    CustomValidationMixin,
    EventRulesMixin,
    ExportTemplatesMixin,
    JournalingMixin,
    NotificationsMixin,
    TagsMixin,
    get_model_features,
)
from netbox.plugins import get_plugin_config
from netbox.registry import registry
from netbox.search import SearchIndex
from utilities import filters
from utilities.data import get_config_value_ci
from utilities.datetime import datetime_from_timestamp
from utilities.object_types import object_type_name
from utilities.querysets import RestrictedQuerySet
from utilities.serialization import deserialize_object as _deserialize_object
from utilities.string import title
from utilities.validators import validate_regex

from netbox_custom_objects.choices import ObjectFieldOnDeleteChoices
from netbox_custom_objects.constants import APP_LABEL, RESERVED_FIELD_NAMES
from netbox_custom_objects.field_types import FIELD_TYPE_CLASS, LazyForeignKey, safe_table_name
from netbox_custom_objects.jobs import ReindexCustomObjectTypeJob
from netbox_custom_objects.utilities import (
    _suppress_clear_cache,
    extract_cot_id_from_model_name,
    generate_model,
)

logger = logging.getLogger(__name__)


class UniquenessConstraintTestError(Exception):
    """Custom exception used to signal successful uniqueness constraint test."""

    pass


def _table_exists(table_name, conn=None):
    """Return True if *table_name* exists in the database reachable via *conn*.

    Defaults to the global ``connection`` (main schema).  When the caller is
    operating inside a branch context, pass the branch's connection so the
    lookup runs against the active branch's PostgreSQL schema.
    """
    if conn is None:
        conn = connection
    return table_name in conn.introspection.table_names()


USER_TABLE_DATABASE_NAME_PREFIX = "custom_objects_"

# Per-context storage for CO field values deferred during squash merge.
# Using ContextVar instead of a class-level dict so that concurrent merges
# (different threads or coroutines) each get an isolated copy.
# Shape: {db_table: {co_pk: {'data': {field_name: value}}}}
_deferred_co_field_data: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    '_deferred_co_field_data', default=None
)

# Sidecar key listing polymorphic-M2M fields in a serialized CO dict, as
# ``[{'name': ..., 'pk': ...}, ...]``.  ``pk`` lets the squash dependency
# resolver in ``branching.py`` produce a CO → field CREATE edge without
# looking the field up in main (it isn't there yet during a branch-only
# merge).  ``name`` lets ``deserialize_object`` map rows to through tables.
POLY_M2M_SIDECAR_KEY = '__nco_poly_m2m_fields__'

# Serializes the save/restore of TM.post_through_setup in get_model().  The
# patch needs to be in place during type() class creation, so the lock has to
# span the whole generate_model() — that serialises unrelated COT generations.
# Acceptable for now: the bottleneck is bounded to startup and squash merges.
_taggable_manager_patch_lock = threading.Lock()


def _apply_poly_m2m_rows(schema_conn, through_table, co_pk, rows):
    """Insert polymorphic M2M *rows* into *through_table* on *schema_conn*,
    set-style (clear existing for *co_pk* first).  ContentType resolved by
    natural key, also via *schema_conn*.

    *through_table* originates from ``CustomObjectTypeField.through_table_name``,
    which is derived from validated identifiers (the COT id and a field name
    matching ``^[a-z0-9]+(_[a-z0-9]+)*$``) — safe to interpolate directly
    into SQL.
    """
    alias = schema_conn.alias
    inserted = 0
    dropped = 0
    with schema_conn.cursor() as cursor:
        cursor.execute(
            f'DELETE FROM "{through_table}" WHERE source_id = %s', [co_pk],
        )
        for row in rows:
            ct_label = row.get('content_type')
            obj_id = row.get('object_id')
            if not ct_label or obj_id is None:
                dropped += 1
                continue
            try:
                app_label, model_name = ct_label.split('.', 1)
                ct = ContentType.objects.using(alias).get(
                    app_label=app_label, model=model_name,
                )
            except (ValueError, ContentType.DoesNotExist) as exc:
                logger.warning(
                    'poly M2M replay: ct %r unresolved (%s) for %s pk=%s',
                    ct_label, exc, through_table, co_pk,
                )
                dropped += 1
                continue
            cursor.execute(
                f'INSERT INTO "{through_table}" '
                '(source_id, content_type_id, object_id) VALUES (%s, %s, %s)',
                [co_pk, ct.pk, obj_id],
            )
            inserted += 1
    # All rows dropped — flag it.  A CO with poly-M2M data should land with at
    # least one row; zero inserts means the replay silently lost data.
    if rows and inserted == 0 and dropped > 0:
        logger.warning(
            'poly M2M replay: all %d row(s) dropped for %s pk=%s — '
            'CO will land with empty %s', dropped, through_table, co_pk, through_table,
        )


def _get_schema_connection():
    """Active branch's connection if any, else the default — so DDL targets the right schema."""
    try:
        from netbox_branching.contextvars import active_branch
        branch = active_branch.get()
        if branch is not None:
            return connections[branch.connection_name]
    except ImportError:
        pass
    return connection


def _historical_names_for_field(field_pk):
    """All names this field has ever held, from its ObjectChange UPDATE history.

    Used so a deferred CO entry recorded under the field's old name still
    matches when the field is created in the target schema under its new name.
    """
    names = set()
    rows = ObjectChange.objects.filter(
        changed_object_type__app_label='netbox_custom_objects',
        changed_object_type__model='customobjecttypefield',
        changed_object_id=field_pk,
        action=ObjectChangeActionChoices.ACTION_UPDATE,
    ).values_list('prechange_data', 'postchange_data')
    for pre, post in rows:
        for blob in (pre, post):
            if not blob:
                continue
            n = blob.get('name')
            if n:
                names.add(n)
    return names


def _apply_deferred_co_field(field_instance):
    """Apply deferred CO field values via raw UPDATE after the column is added.

    Squash merge fix: when a CO CREATE replays before its field's CREATE, the
    field values stash in ``_deferred_co_field_data`` (shape:
    ``{db_table: {co_pk: {'data': {field_name: value}}}}``); when the field
    finally lands we UPDATE those rows.  TYPE_OBJECT data key is ``{name}``
    but the column is ``{name}_id``; TYPE_MULTIOBJECT has no parent-table
    column so it's skipped.  Historical names (rename history) are accepted
    as matches so a renamed field still picks up its pre-rename data.
    """
    # No deferred data at all — fast path.
    deferred = _deferred_co_field_data.get()
    if not deferred:
        return

    cot = field_instance.custom_object_type
    table_name = cot.get_database_table_name()
    per_table = deferred.get(table_name)
    if not per_table:
        return

    # M2M has no column on the main table — nothing to UPDATE.
    if field_instance.type == CustomFieldTypeChoices.TYPE_MULTIOBJECT:
        return

    # For TYPE_OBJECT the data key is the field name but the DB column ends with _id.
    if field_instance.type == CustomFieldTypeChoices.TYPE_OBJECT:
        col_name = f'{field_instance.name}_id'
    else:
        col_name = field_instance.name

    # Candidate data keys: current name + all historical names.  pk may be None
    # when called before the field row is persisted; in that case we skip the
    # history lookup and only match by current name.
    candidate_keys = {field_instance.name}
    if field_instance.pk is not None:
        candidate_keys.update(_historical_names_for_field(field_instance.pk))

    schema_conn = _get_schema_connection()

    with schema_conn.cursor() as cursor:
        for co_pk, entry in per_table.items():
            data = entry['data']
            # Distinguish "key absent" from "key present with NULL" — explicit
            # None is a legitimate write and must reach the column.
            matched = next((k for k in candidate_keys if k in data), None)
            if matched is None:
                continue
            value = data[matched]
            # table_name / col_name come from validated identifiers
            # (^[a-z0-9_]+$ on field.name) — safe to interpolate.
            cursor.execute(
                f'UPDATE "{table_name}" SET "{col_name}" = %s WHERE id = %s',
                [value, co_pk],
            )
            # Pop consumed keys immediately so a mid-loop failure leaves
            # un-applied rows intact for retry but doesn't re-apply
            # rows that already succeeded.
            for k in candidate_keys:
                data.pop(k, None)

    exhausted = [pk for pk, entry in per_table.items() if not entry['data']]
    for pk in exhausted:
        del per_table[pk]
    if not per_table:
        del deferred[table_name]
    if not deferred:
        _deferred_co_field_data.set(None)


def _schema_add_field(fi, model, schema_editor, schema_conn):
    """``add_field`` against *schema_conn*; idempotent (skips if column exists).

    Creates the through table for MULTIOBJECT.  Deferred CO field data is NOT
    applied here — call ``_apply_deferred_co_field`` separately after.
    """
    ft = FIELD_TYPE_CLASS[fi.type]()
    mf = ft.get_model_field(fi)
    mf.contribute_to_class(model, fi.name)

    with schema_conn.cursor() as cursor:
        existing_cols = {
            col.name
            for col in schema_conn.introspection.get_table_description(cursor, model._meta.db_table)
        }
    if mf.column in existing_cols:
        logger.debug('_schema_add_field: %r already exists on %s, skipping', mf.column, model._meta.db_table)
        return

    # LazyForeignKey starts with a string remote_field.model.  Django's
    # lazy_related_operation fires immediately when the target is in
    # apps.all_models, but tearDown() cleanup between tests can remove
    # the target model from the registry.  Resolve it directly here —
    # bypassing the app-config's skip guard — so that schema_editor
    # .add_field() always sees a model class, not a string.
    if isinstance(mf, LazyForeignKey) and isinstance(mf.remote_field.model, str):
        _app_label, _model_name = mf._to_model_name.rsplit('.', 1)
        _cot_id_str = extract_cot_id_from_model_name(_model_name.lower())
        if _cot_id_str is not None:
            try:
                _cot = CustomObjectType.objects.get(pk=int(_cot_id_str))
                _actual = _cot.get_model()
                mf.remote_field.model = _actual
                mf.to = _actual
            except (CustomObjectType.DoesNotExist, OperationalError, ProgrammingError):
                logger.warning(
                    "Could not resolve LazyForeignKey target %r before add_field; "
                    "schema_editor.add_field may fail",
                    mf._to_model_name,
                )

    schema_editor.add_field(model, mf)
    if fi.type == CustomFieldTypeChoices.TYPE_MULTIOBJECT:
        ft.create_m2m_table(fi, model, fi.name, schema_conn=schema_conn)


def _schema_remove_field(fi, model, schema_editor, schema_conn=None, existing_tables=None):
    """``remove_field`` against *schema_conn*; idempotent.

    For MULTIOBJECT, drops the through table (skipped if already gone).
    For scalar fields, flushes DEFERRABLE FK triggers before ALTER TABLE so
    PostgreSQL doesn't reject the call with "pending trigger events".
    *existing_tables* optionally short-circuits the per-call introspection.
    """
    ft = FIELD_TYPE_CLASS[fi.type]()
    mf = ft.get_model_field(fi)
    mf.contribute_to_class(model, fi.name)

    if fi.type == CustomFieldTypeChoices.TYPE_MULTIOBJECT:
        through_table = fi.through_table_name
        if existing_tables is None:
            conn = schema_conn if schema_conn is not None else connection
            with conn.cursor() as cursor:
                existing_tables = set(conn.introspection.table_names(cursor))
        if through_table in existing_tables:
            through_meta = type(
                'Meta', (),
                {'db_table': through_table, 'app_label': APP_LABEL, 'managed': True},
            )
            temp_name = f'_TempThrough_{through_table}'
            through_model = type(
                temp_name,
                (models.Model,),
                {'Meta': through_meta, '__module__': 'netbox_custom_objects.models'},
            )
            try:
                schema_editor.delete_model(through_model)
            finally:
                # ModelBase.__new__ registered the temp class in apps.all_models;
                # drop it so repeated remove/re-add cycles don't leak entries.
                apps.all_models.get(APP_LABEL, {}).pop(temp_name.lower(), None)
        # M2M has no column on the parent table — nothing further to remove.
        return

    # Flush any pending DEFERRABLE FK trigger events before ALTER TABLE;
    # otherwise PostgreSQL raises "pending trigger events" when removing a FK field.
    schema_editor.execute('SET CONSTRAINTS ALL IMMEDIATE')
    schema_editor.remove_field(model, mf)


def _schema_alter_field(old_fi, new_fi, model, schema_editor, schema_conn, existing_tables=None):
    """``alter_field`` from *old_fi* to *new_fi*; idempotent across replays.

    M2M renames go through ``_rename_or_create_m2m_through`` first.  When
    neither old nor new column exists (rename conflict — branch A→X vs main
    A→Y), looks up the live field record to find the actual current column.
    MULTIOBJECT↔scalar type changes are unsupported — caller must remove+add.
    """
    old_is_m2m = old_fi.type == CustomFieldTypeChoices.TYPE_MULTIOBJECT
    new_is_m2m = new_fi.type == CustomFieldTypeChoices.TYPE_MULTIOBJECT

    if old_is_m2m != new_is_m2m:
        logger.warning(
            '_schema_alter_field: skipping unsupported type change %r→%r on %s '
            '(MULTIOBJECT ↔ scalar changes require remove+add, not alter)',
            old_fi.type, new_fi.type, model._meta.db_table,
        )
        return

    old_mf = FIELD_TYPE_CLASS[old_fi.type]().get_model_field(old_fi)
    new_mf = FIELD_TYPE_CLASS[new_fi.type]().get_model_field(new_fi)
    old_mf.contribute_to_class(model, old_fi.name)
    new_mf.contribute_to_class(model, new_fi.name)

    # M2M has no parent-table column — schema work happens on the through table.
    if new_is_m2m:
        if old_fi.name != new_fi.name:
            _rename_or_create_m2m_through(
                old_fi, new_fi, model, schema_editor, schema_conn, existing_tables,
            )
        return

    with schema_conn.cursor() as cursor:
        existing_cols = {
            col.name
            for col in schema_conn.introspection.get_table_description(cursor, model._meta.db_table)
        }
    if old_mf.column not in existing_cols:
        if new_mf.column in existing_cols:
            logger.debug(
                '_schema_alter_field: %r already renamed to %r on %s, skipping',
                old_mf.column, new_mf.column, model._meta.db_table,
            )
            return
        # Both source and target columns absent → independent rename in this
        # schema; look up the live column and converge on the merge target.
        logger.warning(
            '_schema_alter_field: rename conflict on %s — neither %r nor %r '
            'exists; resolving via live field pk=%d',
            model._meta.db_table, old_mf.column, new_mf.column, new_fi.pk,
        )
        try:
            live_fi = CustomObjectTypeField.objects.using(schema_conn.alias).get(pk=new_fi.pk)
        except CustomObjectTypeField.DoesNotExist:
            logger.debug(
                '_schema_alter_field: field pk=%d not found in %s; skipping',
                new_fi.pk, schema_conn.alias,
            )
            return
        live_mf = FIELD_TYPE_CLASS[live_fi.type]().get_model_field(live_fi)
        live_mf.contribute_to_class(model, live_fi.name)
        if live_mf.column not in existing_cols:
            logger.debug(
                '_schema_alter_field: live column %r also absent on %s; skipping',
                live_mf.column, model._meta.db_table,
            )
            return
        schema_editor.alter_field(model, live_mf, new_mf)
        return

    schema_editor.alter_field(model, old_mf, new_mf)


def _rename_or_create_m2m_through(old_fi, new_fi, model, schema_editor, schema_conn, existing_tables):
    """Rename the through-table for a renamed M2M field, or create the new one
    if the old table is absent (sync/merge against a schema that never had it).
    """
    old_through = old_fi.through_table_name
    new_through = new_fi.through_table_name

    tables = existing_tables
    if tables is None:
        with schema_conn.cursor() as cursor:
            tables = schema_conn.introspection.table_names(cursor)

    if old_through in tables:
        old_through_meta = type(
            'Meta', (),
            {'db_table': old_through, 'app_label': APP_LABEL, 'managed': True},
        )
        temp_name = f'_TempOldThrough_{old_through}'
        old_through_model = generate_model(
            temp_name,
            (models.Model,),
            {
                '__module__': 'netbox_custom_objects.models',
                'Meta': old_through_meta,
                'id': models.AutoField(primary_key=True),
                'source': models.ForeignKey(
                    model, on_delete=models.CASCADE, db_column='source_id', related_name='+',
                ),
                'target': models.ForeignKey(
                    model, on_delete=models.CASCADE, db_column='target_id', related_name='+',
                ),
            },
        )
        try:
            schema_editor.alter_db_table(old_through_model, old_through, new_through)
        finally:
            # generate_model() registered the temp class in apps.all_models;
            # drop it so repeated renames don't leak entries.
            apps.all_models.get(APP_LABEL, {}).pop(temp_name.lower(), None)
    else:
        # Old through table absent — create the new one from scratch
        ft = FIELD_TYPE_CLASS[new_fi.type]()
        ft.create_m2m_table(new_fi, model, new_fi.name, schema_conn=schema_conn)


def _translate_renamed_field_name(cot, attr, rename_map=None):
    """Resolve *attr* to the current name of one of *cot*'s fields via its
    ObjectChange rename history.  Returns ``None`` on ambiguity (caller falls
    back to raw key) so we never silently overwrite the wrong column.  Pass
    *rename_map* (built once via ``_build_rename_map``) to skip the DB query.
    """
    if rename_map is not None:
        return rename_map.get(attr)
    candidate_pks = set(
        ObjectChange.objects.filter(
            changed_object_type__app_label='netbox_custom_objects',
            changed_object_type__model='customobjecttypefield',
            action=ObjectChangeActionChoices.ACTION_UPDATE,
        ).filter(
            Q(postchange_data__name=attr) | Q(prechange_data__name=attr)
        ).values_list('changed_object_id', flat=True)
    )
    if not candidate_pks:
        return None
    fields = list(
        cot.fields.filter(pk__in=candidate_pks).values_list('name', flat=True)
    )
    if len(fields) == 1:
        return fields[0]
    return None


def _build_rename_map(cot, attrs):
    """
    Return ``{old_name: current_name}`` for those entries in *attrs* that
    resolve to exactly one of *cot*'s fields via rename history.

    One ObjectChange query covers all candidates.  Ambiguous mappings (same
    historical name appearing in multiple fields' history) are omitted so the
    caller falls back to preserving the raw key — matching the
    abstain-on-ambiguity behaviour of ``_translate_renamed_field_name``.
    """
    attrs = [a for a in attrs if a]
    if not attrs:
        return {}
    rows = ObjectChange.objects.filter(
        changed_object_type__app_label='netbox_custom_objects',
        changed_object_type__model='customobjecttypefield',
        action=ObjectChangeActionChoices.ACTION_UPDATE,
    ).filter(
        Q(postchange_data__name__in=attrs) | Q(prechange_data__name__in=attrs)
    ).values_list('changed_object_id', 'prechange_data', 'postchange_data')

    # attr → {field_pks that have this name anywhere in their history}
    attr_to_field_pks: dict[str, set[int]] = {}
    for field_pk, pre, post in rows:
        for blob in (pre, post):
            if not blob:
                continue
            name = blob.get('name')
            if name in attrs:
                attr_to_field_pks.setdefault(name, set()).add(field_pk)

    if not attr_to_field_pks:
        return {}

    # Resolve the field pks we collected to their current names in one query.
    pk_to_name = dict(
        cot.fields.filter(
            pk__in={pk for pks in attr_to_field_pks.values() for pk in pks}
        ).values_list('pk', 'name')
    )
    result = {}
    for attr, field_pks in attr_to_field_pks.items():
        matched = [pk_to_name[pk] for pk in field_pks if pk in pk_to_name]
        if len(matched) == 1:
            result[attr] = matched[0]
    return result


def _set_with_collision_preference(result, key, value):
    """Set ``result[key] = value``; on collision prefer the non-None side.

    Squash-merge can map both the old and new name of a renamed field to the
    same canonical key, with the new-side often carrying a sentinel ``None``
    from ``deep_compare_dict``.  Preferring non-None keeps the real write.
    """
    if key in result and value is None and result[key] is not None:
        return
    result[key] = value


class CustomObject(
    BookmarksMixin,
    ChangeLoggingMixin,
    CloningMixin,
    ContactsMixin,
    CustomLinksMixin,
    CustomValidationMixin,
    ExportTemplatesMixin,
    JournalingMixin,
    NotificationsMixin,
    EventRulesMixin,
    TagsMixin,
):
    """
    Base class for dynamically generated custom object models.

    This abstract model serves as the foundation for all custom object types created
    through the CustomObjectType system. When a CustomObjectType is created, a concrete
    model class is dynamically generated that inherits from this base class and includes
    the specific fields defined in the CustomObjectType's schema.

    This class should not be used directly - instead, use CustomObjectType.get_model()
    to create concrete model classes for specific custom object types.

    Custom validation
    -----------------
    NetBox's CUSTOM_VALIDATORS setting is supported. Use the COT slug as the key:

        CUSTOM_VALIDATORS = {
            "netbox_custom_objects.<cot-slug>": [
                {"<field_name>": {"min_length": 5}},
            ],
        }

    Attributes:
        _generated_table_model (property): Indicates this is a generated table model
    """

    objects = RestrictedQuerySet.as_manager()

    class Meta:
        abstract = True

    @classmethod
    def resolve_field_aliases(cls, data):
        """Rewrite *data* keys to this model's current field names.

        Called by netbox-branching's ``update_object()`` and
        ``ChangeDiff._update_conflicts()`` when a CustomObjectTypeField has
        been renamed between an ObjectChange recording and its replay.  Walks
        each field's rename history via ObjectChange records.  Unknown keys
        are preserved as-is; on collision the non-None value wins (see
        ``_set_with_collision_preference``).

        Only top-level keys are rewritten.  All current field-name carriers
        (scalar columns, FK ``_id`` keys, M2M target lists, polymorphic
        ``{content_type, object_id}`` payloads) appear at the top level, so
        this is sufficient today.  A future field type that stores user-field
        names as nested-dict keys would need a recursive walk.
        """
        if not data:
            return data

        cot_id_str = extract_cot_id_from_model_name(cls.__name__.lower())
        if cot_id_str is None:
            return data
        cot_id = int(cot_id_str)
        try:
            cot = CustomObjectType.objects.get(pk=cot_id)
        except CustomObjectType.DoesNotExist:
            return data

        field_names = {f.name for f in cls._meta.get_fields()}

        # Collect the keys that don't match a current field name so we can do
        # one batched ObjectChange query instead of one per unknown key.
        unknown_keys = []
        for raw_key in data:
            key = 'custom_field_data' if raw_key == 'custom_fields' else raw_key
            if key not in field_names:
                unknown_keys.append(key)
        rename_map = _build_rename_map(cot, unknown_keys) if unknown_keys else {}

        result = {}
        for raw_key, value in data.items():
            # Honour custom_fields → custom_field_data the same way update_object
            # used to, so this hook is a true superset of the previous behaviour.
            key = 'custom_field_data' if raw_key == 'custom_fields' else raw_key
            if key in field_names:
                _set_with_collision_preference(result, key, value)
                continue
            translated = _translate_renamed_field_name(cot, key, rename_map=rename_map)
            if translated and translated in field_names:
                _set_with_collision_preference(result, translated, value)
            else:
                # Unknown key (e.g. removed field) — preserve raw key so callers
                # that inspect the dict for non-field metadata can still see it.
                _set_with_collision_preference(result, raw_key, value)
        return result

    @classmethod
    def deserialize_object(cls, data, pk=None):
        """ObjectChange.apply() hook for CREATE actions.

        Builds against the context-aware ``fresh_model`` (not Django's default
        ``apps.get_model`` lookup, which would return main's class with the
        wrong column set inside a branch).  Stashes ``data`` in
        ``_deferred_co_field_data`` so squash-ordering — a CO CREATE replayed
        before its field's CREATE — can apply the values via raw UPDATE once
        each column is added.
        """
        cot_id_str = extract_cot_id_from_model_name(cls.__name__.lower())
        if cot_id_str is None:
            return _deserialize_object(cls, data, pk=pk)
        cot_id = int(cot_id_str)

        # In the squash case the cache may still point to a zero-field model.
        CustomObjectType.clear_model_cache(cot_id)
        try:
            cot = CustomObjectType.objects.get(pk=cot_id)
            fresh_model = cot.get_model()
        except CustomObjectType.DoesNotExist:
            fresh_model = cls

        resolved = fresh_model.resolve_field_aliases(data)

        obj = fresh_model()
        if pk is not None:
            obj.pk = pk
        m2m_data = {}
        # Polymorphic M2M data: keys named by serialize_object's sidecar
        # (POLY_M2M_SIDECAR_KEY) — explicit so we don't rely on _field_objects
        # (empty when squash replays the CO CREATE before its field CREATE)
        # or value-shape guessing.
        poly_m2m_field_names = {
            entry['name']
            for entry in (resolved.get(POLY_M2M_SIDECAR_KEY) or ())
            if isinstance(entry, dict) and entry.get('name')
        }
        poly_m2m_data = {}
        field_names = {f.name for f in fresh_model._meta.get_fields()}

        for attr, value in resolved.items():
            if attr == POLY_M2M_SIDECAR_KEY:
                continue
            # Tags via the standard NetBox path (Tag rows are looked up by name).
            if attr == 'tags' and is_taggable(fresh_model):
                tag_model = apps.get_model('extras', 'Tag')
                m2m_data['tags'] = list(tag_model.objects.filter(name__in=value or []))
                continue
            if attr in poly_m2m_field_names:
                poly_m2m_data[attr] = value or []
                continue
            if attr not in field_names:
                # Unknown attribute (likely a removed field) — preserve it as a
                # Python attribute so downstream code (e.g. _deferred_co_field_data)
                # can still see it.
                setattr(obj, attr, value)
                continue
            try:
                f = fresh_model._meta.get_field(attr)
            except FieldDoesNotExist:
                setattr(obj, attr, value)
                continue
            if isinstance(f, ManyToManyField):
                m2m_data[attr] = value
            elif isinstance(f, ForeignKey):
                # FK values arrive as the related PK; assign via the _id column.
                setattr(obj, f.attname, value)
            else:
                # Coerce via to_python() for datetimes etc; fall back to raw on parse failure.
                try:
                    setattr(obj, attr, f.to_python(value))
                except (ValidationError, ValueError, TypeError):
                    setattr(obj, attr, value)

        table_name = fresh_model._meta.db_table
        full_data = dict(data)

        class _Deserialized:
            object = obj

            def save(self, using=None, **_kwargs):
                _using = using or DEFAULT_DB_ALIAS
                models.Model.save_base(obj, using=_using, raw=True)
                obj_pk = obj.pk  # captures auto-assigned PK
                # Re-apply M2M relations.  Skipped quietly when the through
                # table or column isn't present yet (squash ordering — the
                # field's own CREATE replays later).  hasattr() narrows the
                # except below to genuine DB-state mismatches.
                for accessor, related_pks in m2m_data.items():
                    if not hasattr(obj, accessor):
                        logger.debug(
                            'deserialize_object: deferred M2M %r on %s pk=%s (descriptor unbound)',
                            accessor, table_name, obj_pk,
                        )
                        continue
                    manager = getattr(obj, accessor)
                    try:
                        manager.set(related_pks)
                    except (ProgrammingError, OperationalError):
                        logger.debug(
                            'deserialize_object: deferred M2M %r on %s pk=%s (table absent)',
                            accessor, table_name, obj_pk, exc_info=True,
                        )
                # Replay polymorphic M2M directly via the through, pinned to
                # _using.  Bypasses manager.add() → m2m_changed →
                # handle_changed_object, which can route across DB aliases.
                schema_conn_local = connections[_using]
                for field_name, rows in poly_m2m_data.items():
                    through_table = (
                        f'{USER_TABLE_DATABASE_NAME_PREFIX}{cot_id}_{field_name}'
                    )
                    _apply_poly_m2m_rows(schema_conn_local, through_table, obj_pk, rows)
                # Stash full data for deferred column updates (squash ordering fix).
                deferred = _deferred_co_field_data.get()
                if deferred is None:
                    deferred = {}
                    _deferred_co_field_data.set(deferred)
                if table_name not in deferred:
                    deferred[table_name] = {}
                deferred[table_name][obj_pk] = {'data': full_data}

        return _Deserialized()

    def __str__(self):
        # Find the field with primary=True and return that field's "name" as the name of the object
        primary_field = self._field_objects.get(self._primary_field_id, None)
        primary_field_value = None
        if primary_field:
            field_type = FIELD_TYPE_CLASS[primary_field["field"].type]()
            try:
                primary_field_value = field_type.get_display_value(
                    self, primary_field["name"]
                )
            except AttributeError:
                primary_field_value = None
        if not primary_field_value:
            return f"{self.custom_object_type.display_name} {self.id}"
        return str(primary_field_value) or str(self.id)

    def clean(self):
        super().clean()
        # CustomValidationMixin.clean() (called above) fires the post_clean signal whose
        # receiver looks up validators under 'netbox_custom_objects.table{id}model' — an
        # internal name users cannot discover.  Also run validators under the slug key so
        # users can write: CUSTOM_VALIDATORS = {"netbox_custom_objects.my-slug": [...]}
        slug_key = f'{APP_LABEL}.{self.custom_object_type.slug}'
        validators = get_config_value_ci(get_config().CUSTOM_VALIDATORS, slug_key, default=[])
        if validators:
            run_validators(self, validators)

    def serialize_object(self, exclude=None):
        """Standard serialization plus polymorphic-field metadata.

        For polymorphic MULTIOBJECT fields, also appends
        ``[{content_type, object_id}, ...]`` per field (Django's serializer
        skips them — the descriptor isn't on ``_meta``).  For both polymorphic
        OBJECT and MULTIOBJECT, emits a sidecar of ``[{name, pk}, ...]`` so
        the squash dependency resolver in ``branching.py`` can order the
        field's CREATE before the CO's CREATE — without it, squash would
        apply the CO before the columns/through exist.
        """
        data = super().serialize_object(exclude=exclude)
        field_objects = getattr(type(self), '_field_objects', None) or {}
        poly_entries = []
        for fo in field_objects.values():
            field = fo['field']
            if not field.is_polymorphic:
                continue
            if field.type not in (
                CustomFieldTypeChoices.TYPE_OBJECT,
                CustomFieldTypeChoices.TYPE_MULTIOBJECT,
            ):
                continue
            if exclude and field.name in exclude:
                continue
            poly_entries.append({'name': field.name, 'pk': field.pk})
            if field.type != CustomFieldTypeChoices.TYPE_MULTIOBJECT:
                continue
            # MULTIOBJECT-only: append the through-table rows.
            try:
                manager = getattr(self, field.name)
                through = manager._get_through_model()
            except (AttributeError, LookupError):
                continue
            rows = list(
                through.objects.filter(source_id=self.pk)
                .values('content_type_id', 'object_id')
            )
            if not rows:
                data[field.name] = []
                continue
            ct_ids = {r['content_type_id'] for r in rows}
            ct_map = {
                ct.id: f'{ct.app_label}.{ct.model}'
                for ct in ContentType.objects.filter(id__in=ct_ids)
            }
            data[field.name] = [
                {'content_type': ct_map.get(r['content_type_id']), 'object_id': r['object_id']}
                for r in rows
                if r['content_type_id'] in ct_map
            ]
        if poly_entries:
            data[POLY_M2M_SIDECAR_KEY] = poly_entries
        return data

    @property
    def _generated_table_model(self):
        # An indication that the model is a generated table model.
        return True

    def delete(self, *args, **kwargs):
        # Two prep steps before super() so the deletion collector doesn't
        # raise traversing reverse FKs from through models:
        #   1. Realign each through's ``source`` FK to ``type(self)`` —
        #      isinstance(instance, fk.related_model) otherwise sees two
        #      Table*Model classes and fails.
        #   2. Temporarily unregister throughs whose physical table is gone
        #      (squash revert drops field-CREATEs before CO-CREATEs, so the
        #      CO's collector hits ``relation does not exist`` otherwise).
        cls = type(self)
        prefix = f'through_{cls._meta.db_table}'
        registry = apps.all_models.get(APP_LABEL, {})
        # Branch contexts may have through tables only in the branch schema, so
        # introspect via the active schema's connection, not the main one.
        existing_tables = _get_schema_connection().introspection.table_names()
        hidden = {}
        for name, through in list(registry.items()):
            if not name.startswith(prefix):
                continue
            if through._meta.db_table not in existing_tables:
                hidden[name] = registry.pop(name)
                continue
            for fk_name in ('source', 'target'):
                try:
                    field = through._meta.get_field(fk_name)
                except FieldDoesNotExist:
                    continue
                remote_meta = getattr(field.remote_field.model, '_meta', None)
                if remote_meta is None or remote_meta.label != cls._meta.label:
                    continue
                field.remote_field.model = cls
                field.__dict__.pop('related_model', None)
        try:
            return super().delete(*args, **kwargs)
        finally:
            registry.update(hidden)

    @property
    def clone_fields(self):
        """
        Return a tuple of field names that should be cloned when this object is cloned.
        This property dynamically determines which fields to clone based on the
        is_cloneable flag on the associated CustomObjectTypeField instances.
        """
        if not hasattr(self, "custom_object_type_id"):
            return ()

        # Get all field names where is_cloneable=True for this custom object type
        cloneable_fields = self.custom_object_type.fields.filter(
            is_cloneable=True
        ).values_list("name", flat=True)

        return tuple(cloneable_fields)

    def get_absolute_url(self):
        return reverse(
            "plugins:netbox_custom_objects:customobject",
            kwargs={
                "pk": self.pk,
                "custom_object_type": self.custom_object_type.slug,
            },
        )

    def get_list_url(self):
        return reverse(
            "plugins:netbox_custom_objects:customobject_list",
            kwargs={"custom_object_type": self.custom_object_type.slug},
        )

    @classmethod
    def _get_viewname(cls, action=None, rest_api=False):
        if rest_api:
            return f"plugins-api:netbox_custom_objects-api:customobject-{action}"
        return f"plugins:netbox_custom_objects:customobject_{action}"

    @classmethod
    def _get_action_url(cls, action=None, rest_api=False, kwargs=None):
        if kwargs is None:
            kwargs = {}
        kwargs["custom_object_type"] = cls.custom_object_type.slug
        return reverse(cls._get_viewname(action, rest_api), kwargs=kwargs)


def validate_pep440(value):
    """Validate that *value* is a valid PEP 440 version string."""
    if not value:
        return
    try:
        Version(value)
    except InvalidVersion:
        raise ValidationError(
            _("'%(value)s' is not a valid version string (expected e.g. '1.0.0')."),
            params={"value": value},
        )


class CustomObjectType(NetBoxModel):
    # Class-level cache for generated models
    # Branch-aware model cache keyed by (cot_id, branch_id_or_None).  Only main's
    # class (branch_id=None) is registered in apps.all_models so that
    # content_type.model_class() resolves to a class with main's column set —
    # branches may have renamed columns that don't exist in main.
    _model_cache = {}
    # Per-(cot, branch) through-model registry: {(cot_id, branch_id): {name: through}}.
    # Each context owns its through class so the source FK is set once at
    # generation time and never mutated to follow another context's CO class.
    _through_model_cache = {}
    _global_lock = threading.RLock()
    _ON_DELETE_SQL = {
        ObjectFieldOnDeleteChoices.CASCADE: "CASCADE",
        ObjectFieldOnDeleteChoices.SET_NULL: "SET NULL",
        ObjectFieldOnDeleteChoices.PROTECT: "RESTRICT",
    }
    name = models.CharField(
        max_length=100,
        unique=True,
        validators=(
            RegexValidator(
                regex=r"^[a-z0-9]+(_[a-z0-9]+)*$",
                message=_(
                    "Only lowercase alphanumeric characters and underscores are allowed. "
                    "Names may not start or end with an underscore, and double underscores are not permitted."
                ),
            ),
        ),
    )
    description = models.CharField(
        verbose_name=_('description'),
        max_length=200,
        blank=True
    )
    comments = models.TextField(
        verbose_name=_('comments'),
        blank=True
    )
    version = models.CharField(max_length=50, blank=True, validators=[validate_pep440])
    verbose_name = models.CharField(max_length=100, blank=True)
    verbose_name_plural = models.CharField(max_length=100, blank=True)
    slug = models.SlugField(max_length=100, unique=True, db_index=True, blank=False)
    group_name = models.CharField(
        max_length=100,
        db_index=True,
        blank=True,
        help_text=_("Used to group similar custom object types in the navigation menu")
    )
    schema_document = models.JSONField(
        blank=True,
        null=True,
        help_text=_(
            "The last applied or exported schema document for this Custom Object Type. "
            "Serves as the source of truth for schema history, including tombstoned fields."
        ),
    )
    next_schema_id = models.PositiveIntegerField(
        default=0,
        editable=False,
        help_text=_(
            "Monotonically increasing counter tracking the highest schema_id ever assigned "
            "to a field on this Custom Object Type. Never decreases, even after field deletion."
        ),
    )
    cache_timestamp = models.DateTimeField(
        auto_now=True,
        help_text=_("Timestamp used for cache invalidation")
    )
    object_type = models.OneToOneField(
        ObjectType,
        on_delete=models.CASCADE,
        related_name="custom_object_types",
        null=True,
        blank=True,
        editable=False
    )

    class Meta:
        verbose_name = "Custom Object Type"
        ordering = ("name",)
        constraints = [
            models.UniqueConstraint(
                Lower("name"),
                name="%(app_label)s_%(class)s_name",
                violation_error_message=_(
                    "A Custom Object Type with this name already exists."
                ),
            ),
        ]

    def __str__(self):
        return self.display_name

    def clean(self):
        # Guard against None (can arrive via update_object during branch revert)
        if self.custom_field_data is None:
            self.custom_field_data = {}
        super().clean()

        if not self.slug:
            raise ValidationError(
                {"slug": _("Slug field cannot be empty.")}
            )

        # Enforce max number of COTs that may be created (max_custom_object_types)
        if not self.pk:
            max_cots = get_plugin_config("netbox_custom_objects", "max_custom_object_types")
            if max_cots and CustomObjectType.objects.count() >= max_cots:
                raise ValidationError(_(
                    f"Maximum number of Custom Object Types ({max_cots}) "
                    "exceeded; adjust max_custom_object_types to raise this limit"
                ))

    @staticmethod
    def _active_branch_id():
        """Active Branch id, or None for main — second component of the cache key."""
        try:
            from netbox_branching.contextvars import active_branch
        except ImportError:
            return None
        branch = active_branch.get()
        return branch.id if branch is not None else None

    @classmethod
    def clear_model_cache(cls, custom_object_type_id=None, *, all_branches=False):
        """Clear the cached generated model.

        Defaults to clearing only the current branch context's entry so the
        other context's class (which is registered in ``apps.all_models``)
        stays valid.  ``all_branches=True`` wipes every (cot, branch) entry,
        appropriate for COT deletion or full re-init.  ``custom_object_type_id=None``
        clears everything.
        """
        with cls._global_lock:
            if custom_object_type_id is not None:
                if all_branches:
                    for key in list(cls._model_cache):
                        if key[0] == custom_object_type_id:
                            cls._model_cache.pop(key, None)
                    for key in list(cls._through_model_cache):
                        if key[0] == custom_object_type_id:
                            cls._through_model_cache.pop(key, None)
                else:
                    branch_id = cls._active_branch_id()
                    cls._model_cache.pop((custom_object_type_id, branch_id), None)
                    cls._through_model_cache.pop((custom_object_type_id, branch_id), None)
            else:
                cls._model_cache.clear()
                cls._through_model_cache.clear()

        # Clear Django apps registry cache to ensure newly created models are recognized
        apps.get_models.cache_clear()

    @classmethod
    def _restore_main_through_registration(cls, cot_id, through_model_name):
        """Restore main's through to ``apps.all_models`` after a branch
        generation overwrote it (Django's metaclass auto-registers under the
        same name).  Keeps ``apps.get_model`` lookups returning main's class.
        """
        main_throughs = cls._through_model_cache.get((cot_id, None))
        if not main_throughs:
            return
        main_through = main_throughs.get(through_model_name)
        if main_through is None:
            return
        apps.all_models[APP_LABEL][through_model_name.lower()] = main_through

    @classmethod
    def get_cached_model(cls, custom_object_type_id, branch_id=None):
        """Cached model for (cot, branch), or None."""
        cache_entry = cls._model_cache.get((custom_object_type_id, branch_id))
        return cache_entry[0] if cache_entry else None

    @classmethod
    def get_cached_timestamp(cls, custom_object_type_id, branch_id=None):
        """Cached timestamp for (cot, branch), or None."""
        cache_entry = cls._model_cache.get((custom_object_type_id, branch_id))
        return cache_entry[1] if cache_entry else None

    @classmethod
    def is_model_cached(cls, custom_object_type_id, branch_id=None):
        """True if a model is cached for (cot, branch)."""
        return (custom_object_type_id, branch_id) in cls._model_cache

    @classmethod
    def get_cached_through_model(cls, custom_object_type_id, through_model_name, branch_id=None):
        """Get a cached through model for a (cot, branch) context, or None."""
        return cls._through_model_cache.get((custom_object_type_id, branch_id), {}).get(
            through_model_name
        )

    @classmethod
    def get_cached_through_models(cls, custom_object_type_id, branch_id=None):
        """Get all cached through models for a (cot, branch) context."""
        return cls._through_model_cache.get((custom_object_type_id, branch_id), {})

    def serialize_object(self, exclude=None):
        # cache_timestamp is an internal cache-invalidation field; exclude it
        # from ObjectChange records so it doesn't appear as a tracked change.
        extra = ['cache_timestamp']
        combined = list(exclude or []) + extra
        return super().serialize_object(exclude=combined)

    def get_absolute_url(self):
        return reverse("plugins:netbox_custom_objects:customobjecttype", args=[self.pk])

    def get_list_url(self):
        return reverse(
            "plugins:netbox_custom_objects:customobject_list",
            kwargs={"custom_object_type": self.slug},
        )

    @classmethod
    def get_table_model_name(cls, table_id):
        return f"Table{table_id}Model"

    def _fetch_and_generate_field_attrs(
        self,
        fields,
        skip_object_fields=False,
    ):
        field_attrs = {
            "_primary_field_id": -1,
            "_context_field_ids": [],
            # An object containing the table fields, field types and the chosen
            # names with the table field id as key.
            "_field_objects": {},
            "_trashed_field_objects": {},
            "_skipped_fields": set(),  # Track fields skipped due to recursion
        }
        fields_query = self.fields(manager="objects").all()

        # Create a combined list of fields that must be added and belong to the this
        # table.
        fields = list(fields) + [field for field in fields_query]

        for field in fields:
            if skip_object_fields:
                if field.type in [CustomFieldTypeChoices.TYPE_OBJECT, CustomFieldTypeChoices.TYPE_MULTIOBJECT]:
                    continue

            field_type = FIELD_TYPE_CLASS[field.type]()
            field_name = field.name

            try:
                model_field = field_type.get_model_field(field)
            except NotImplementedError:
                if field.related_object_type_id is None:
                    logger.debug(
                        "Skipping field %r (pk=%s) on COT %r: "
                        "related_object_type_id is NULL — field has no related type set.",
                        field.name, field.pk, self.slug,
                    )
                else:
                    logger.warning(
                        "Skipping field %r (pk=%s) on COT %r: related_object_type_id=%s "
                        "references a ContentType that no longer exists.",
                        field.name, field.pk, self.slug, field.related_object_type_id,
                    )
                continue

            if isinstance(model_field, dict):
                # Polymorphic Object field: dict of {attr_name: field_or_descriptor}
                field_attrs.update(model_field)
            else:
                field_attrs[field.name] = model_field

            # Add to field objects only if the field was successfully generated
            field_attrs["_field_objects"][field.id] = {
                "field": field,
                "type": field_type,
                "name": field_name,
                "custom_object_type_id": self.id,
            }
            # TODO: Add "primary" support
            if field.primary:
                field_attrs["_primary_field_id"] = field.id
            if field.context:
                field_attrs["_context_field_ids"].append(field.id)

        return field_attrs

    def _after_model_generation(self, attrs, model):
        all_field_objects = {}
        all_field_objects.update(attrs["_field_objects"])
        all_field_objects.update(attrs["_trashed_field_objects"])

        # Get the set of fields that were skipped due to recursion
        skipped_fields = attrs.get("_skipped_fields", set())

        # Build a lookup from field name → Django field object using plain lists that
        # don't trigger _relation_tree.  _meta.get_field() for a name that isn't in
        # _forward_fields_map (e.g. tombstoned fields in _trashed_field_objects) falls
        # through to fields_map → _relation_tree → apps.get_models() → our get_models()
        # override → get_model() for every COT → infinite recursion.
        present_fields = {
            f.name: f
            for f in list(model._meta.local_fields) + list(model._meta.local_many_to_many)
        }

        # Per-(cot, branch) through models — fresh per context so their source FK
        # is set once at generation time and never mutated to follow another
        # context's CO class.  Main's throughs stay canonical in apps.all_models;
        # branch's throughs are kept private (we restore main's registration
        # after Django's metaclass auto-registers ours).
        branch_id = self._active_branch_id()
        through_models = []

        for field_object in all_field_objects.values():
            field_name = field_object["name"]
            field_instance = field_object["field"]

            # Skip fields that were skipped due to recursion
            if field_name in skipped_fields:
                continue

            if field_instance.is_polymorphic:
                if field_instance.type == CustomFieldTypeChoices.TYPE_OBJECT:
                    # Polymorphic GFK: no through model.
                    pass
                elif field_instance.type == CustomFieldTypeChoices.TYPE_MULTIOBJECT:
                    # Reuse the apps-registered through if present; otherwise
                    # create one.  Avoids parallel through instances diverging
                    # from apps.all_models (collector class-identity mismatch).
                    _apps = model._meta.apps
                    try:
                        through_model = _apps.get_model(APP_LABEL, field_instance.through_model_name)
                        # Always update source FK to point to the current model class.
                        # get_model() may be called multiple times (e.g. cache invalidation
                        # after a field save changes cache_timestamp).  Without this update
                        # the through model's source FK would keep pointing at the old class,
                        # causing Django's Collector to raise ValueError during cascade delete:
                        # "Cannot query 'X': Must be 'TableYModel' instance."
                        source_field = through_model._meta.get_field("source")
                        source_field.remote_field.model = model
                        source_field.related_model = model
                        # Clear @cached_property so deletion collector rebuilds path from updated model.
                        source_field.__dict__.pop('path_infos', None)
                        source_field.__dict__.pop('reverse_path_infos', None)
                    except LookupError:
                        field_type_obj = FIELD_TYPE_CLASS[CustomFieldTypeChoices.TYPE_MULTIOBJECT]()
                        source_model_str = f"{APP_LABEL}.{model.__name__}"
                        through_model = field_type_obj.get_polymorphic_through_model(
                            field_instance, source_model_str
                        )
                        source_field = through_model._meta.get_field("source")
                        source_field.remote_field.model = model
                        source_field.related_model = model
                        # Clear @cached_property so deletion collector rebuilds path from updated model.
                        source_field.__dict__.pop('path_infos', None)
                        source_field.__dict__.pop('reverse_path_infos', None)
                        _apps.register_model(APP_LABEL, through_model)
                    self._register_context_through(branch_id, through_model)
                    if through_model and through_model not in through_models:
                        through_models.append(through_model)
                continue

            # Non-polymorphic: use safe present_fields lookup (avoids _relation_tree recursion).
            # Tombstoned fields (in _trashed_field_objects) won't be in present_fields.
            field = present_fields.get(field_name)
            if field is None:
                continue

            field_object["type"].after_model_generation(
                field_object["field"], model, field_name
            )

            # Collect through models from M2M fields — already fresh per CO from
            # MultiObjectFieldType.get_model_field → get_through_model.
            if hasattr(field, 'remote_field') and hasattr(field.remote_field, 'through'):
                through_model = field.remote_field.through
                if (through_model and through_model not in through_models and
                    hasattr(through_model._meta, 'app_label') and
                    through_model._meta.app_label == APP_LABEL):
                    through_models.append(through_model)
                    self._register_context_through(branch_id, through_model)

        # Store through models on the model for yielding in get_models()
        model._through_models = through_models

    def _register_context_through(self, branch_id, through_model):
        """Cache *through_model* under (self.pk, branch_id) and, for branch
        contexts, restore main's canonical registration in apps.all_models
        (Django's metaclass auto-registered this branch-side through with the
        same name, overwriting main's entry)."""
        key = (self.pk, branch_id)
        bucket = type(self)._through_model_cache.setdefault(key, {})
        bucket[through_model.__name__] = through_model
        if branch_id is not None:
            self._restore_main_through_registration(self.pk, through_model.__name__)

    @staticmethod
    def _collect_base_columns(model, user_field_names):
        """
        Return a list of dicts describing the concrete DB columns contributed by the
        CustomObject base class (mixins), excluding any user-defined field names.

        Each dict has keys:
          "name"        – DB column name (f.column, e.g. "site_id" for a FK field)
          "field_class" – Django field class name (e.g. "AutoField", "DateTimeField")
          "null"        – whether the column is nullable (bool)

        Using f.column (not f.name) so that the snapshot key matches the actual DB
        column name returned by DB introspection.  For non-FK fields f.name == f.column;
        for FK fields they differ (e.g. f.name='site', f.column='site_id').

        This snapshot is stored in schema_document["base_columns"] so that the
        post_migrate auto-heal handler (issue #391, Phase 2) can detect drift when
        NetBox upgrades add new columns to the mixin hierarchy.
        """
        return sorted(
            [
                {
                    "name": f.column,
                    "field_class": f.__class__.__name__,
                    "null": f.null,
                }
                for f in model._meta.concrete_fields
                if f.name not in user_field_names
            ],
            key=lambda e: e["name"],
        )

    def _store_base_column_snapshot(self, model):
        """
        Snapshot the current base columns into schema_document["base_columns"].

        Called immediately after the DB table is created by create_model() so that
        the snapshot reflects exactly what columns are present at birth.  Only the
        "base_columns" key is written; any existing keys in schema_document
        (e.g. "fields" written by the schema exporter) are preserved.
        """
        user_field_names = set(self.fields.values_list("name", flat=True))
        base_columns = self._collect_base_columns(model, user_field_names)
        doc = self.schema_document or {}
        doc["base_columns"] = base_columns
        CustomObjectType.objects.filter(pk=self.pk).update(schema_document=doc)
        self.schema_document = doc

    def get_collision_safe_order_id_idx_name(self):
        return f"tbl_order_id_{self.id}_idx"

    def get_database_table_name(self):
        return f"{USER_TABLE_DATABASE_NAME_PREFIX}{self.id}"

    @property
    def title_case_name(self):
        return title(self.verbose_name or self.name)

    @property
    def title_case_name_plural(self):
        return title(self.verbose_name or self.name) + "s"

    def get_verbose_name(self):
        return self.verbose_name or self.title_case_name

    def get_verbose_name_plural(self):
        return self.verbose_name_plural or self.title_case_name_plural

    @property
    def display_name(self):
        return self.get_verbose_name()

    @staticmethod
    def get_content_type_label(custom_object_type_id):
        custom_object_type = CustomObjectType.objects.get(pk=custom_object_type_id)
        return f"Custom Objects > {custom_object_type.display_name}"

    def register_custom_object_search_index(self, model):
        # Use local_fields / local_many_to_many directly — calling _meta.get_field()
        # triggers Django's lazy _relation_tree which re-enters get_models() and
        # recurses through get_model() for every COT.
        present = (
            {f.name for f in model._meta.local_fields}
            | {f.name for f in model._meta.local_many_to_many}
        )
        fields = []
        for field in self.fields.filter(search_weight__gt=0):
            if field.name not in present:
                continue
            fields.append((field.name, field.search_weight))

        attrs = {
            "model": model,
            "fields": tuple(fields),
            "display_attrs": tuple(),
        }
        search_index = type(
            f"{self.name}SearchIndex",
            (SearchIndex,),
            attrs,
        )
        label = f"{APP_LABEL}.{self.get_table_model_name(self.id).lower()}"
        registry["search"][label] = search_index

    def get_model(
        self,
        skip_object_fields=False,
        no_cache=False,
    ):
        """
        Generates a temporary Django model based on available fields that belong to
        this table. Returns cached model if available, otherwise generates and caches it.

        :param skip_object_fields: Don't add object or multiobject fields to the model
        :type skip_object_fields: bool
        :param no_cache: Force regeneration of the model, bypassing cache
        :type no_cache: bool
        :return: The generated model.
        :rtype: Model
        """

        branch_id = self._active_branch_id()

        # Lock guards the cache check, not the miss → re-cache window.  Two
        # threads can regenerate the same (cot_id, branch_id) in parallel;
        # both produce equivalent classes, so the duplication is wasteful but
        # not incorrect.  Worth it to avoid serialising all generation.
        with self._global_lock:
            if self.is_model_cached(self.id, branch_id) and not no_cache:
                cached_timestamp = self.get_cached_timestamp(self.id, branch_id)
                if cached_timestamp and self.cache_timestamp and cached_timestamp == self.cache_timestamp:
                    model = self.get_cached_model(self.id, branch_id)
                    # registry["search"] is global, not per-branch — re-bind so
                    # post_save's search-cache handler sees this context's fields.
                    self.register_custom_object_search_index(model)
                    return model
                else:
                    # Only clear the current (cot_id, branch_id) entry.  Lazy
                    # invalidation: each branch context detects its own stale
                    # timestamp on next access — main's COT save propagates the
                    # bumped cache_timestamp to branches via change-capture, so
                    # they'll re-evaluate against their own row independently.
                    self.clear_model_cache(self.id)

        # Generate the model outside the lock to avoid holding it during expensive operations
        model_name = self.get_table_model_name(self.pk)

        # TODO: Add other fields with "index" specified
        indexes = []

        meta = type(
            "Meta",
            (),
            {
                "apps": apps,
                "managed": False,
                "db_table": self.get_database_table_name(),
                "app_label": APP_LABEL,
                "ordering": ["id"],
                "indexes": indexes,
                "verbose_name": self.get_verbose_name(),
                "verbose_name_plural": self.get_verbose_name_plural(),
            },
        )

        attrs = {
            "Meta": meta,
            "__module__": "database.models",
            "custom_object_type": self,
            "custom_object_type_id": self.id,
        }

        # Pass the generating models set to field generation
        fields = []
        field_attrs = self._fetch_and_generate_field_attrs(
            fields,
            skip_object_fields=skip_object_fields,
        )

        attrs.update(**field_attrs)

        # Track which fields were skipped due to recursion for after_model_generation
        if '_skipped_fields' not in attrs:
            attrs['_skipped_fields'] = set()

        # Create the model class with a workaround for TaggableManager conflicts
        # Wrap the existing post_through_setup method to handle ValueError exceptions
        from taggit.managers import TaggableManager as TM

        # TM.post_through_setup is class-level state; serialize concurrent
        # generations so save/restore can't interleave across threads.
        with _taggable_manager_patch_lock:
            original_post_through_setup = TM.post_through_setup

            def wrapped_post_through_setup(self, cls):
                try:
                    return original_post_through_setup(self, cls)
                except ValueError:
                    pass

            TM.post_through_setup = wrapped_post_through_setup

            try:
                model = generate_model(
                    str(model_name),
                    (CustomObject, models.Model),
                    attrs,
                )
            finally:
                TM.post_through_setup = original_post_through_setup

        # Suppress clear_cache() through the _model_cache write so a re-entrant
        # get_model() inside register_model → clear_cache → get_models() can hit
        # the cache instead of recursing into another generation.
        with _suppress_clear_cache():
            # Main's class is the canonical registration in apps.all_models;
            # branch's class is cached only.  Without this, content_type.model_class()
            # would return a class with the wrong column set across contexts.
            model_key = model_name.lower()
            if branch_id is None:
                if model_key in apps.all_models[APP_LABEL]:
                    del apps.all_models[APP_LABEL][model_key]
                apps.register_model(APP_LABEL, model)
            else:
                main_class = self.get_cached_model(self.id, branch_id=None)
                if main_class is not None:
                    apps.all_models[APP_LABEL][model_key] = main_class
                # Else: branch class stays registered until main is generated —
                # self-healing on the next main-context get_model() call.

            # _after_model_generation registers through models in
            # _through_model_cache and mutates apps.all_models; hold the lock so
            # concurrent get_model() calls for the same (cot_id, branch_id) can't
            # interleave their through-model registrations.
            with self._global_lock:
                self._after_model_generation(attrs, model)

                # When this COT's model is regenerated (cache miss), non-polymorphic through
                # models owned by OTHER COTs that point to this COT as their M2M target keep
                # their target FK stale (pointing at the old model class).  Django's deletion
                # collector finds those through FKs in the new model's related_objects and
                # raises ValueError: "Cannot query X: Must be OldModel instance."
                # Fix: walk all inbound non-polymorphic multiobject fields and patch the
                # through model's target FK to the freshly generated model class.
                # (Same pattern as the existing fix for polymorphic source FKs above at
                # _after_model_generation lines 526-531.)
                for inbound_field in CustomObjectTypeField.objects.filter(
                    related_object_type=self.object_type,
                    type=CustomFieldTypeChoices.TYPE_MULTIOBJECT,
                    is_polymorphic=False,
                ).iterator():
                    try:
                        through_model = apps.get_model(APP_LABEL, inbound_field.through_model_name)
                        target_field = through_model._meta.get_field('target')
                    except (LookupError, FieldDoesNotExist):
                        continue
                    target_field.remote_field.model = model
                    target_field.related_model = model
                    # path_infos is a @cached_property on ForeignKey (see Django's
                    # related.py). Clear it so the path is rebuilt using the updated
                    # remote_field.model; stale cached path_infos would make Django's
                    # deletion collector compare obj against the old model class and
                    # raise ValueError: "Cannot query X: Must be OldModel instance."
                    target_field.__dict__.pop('path_infos', None)
                    target_field.__dict__.pop('reverse_path_infos', None)

                # Same staleness problem exists for direct FK fields (TYPE_OBJECT):
                # when this COT is regenerated, any cached model for another COT that
                # holds a LazyForeignKey pointing here still references the old class.
                # Walk inbound non-polymorphic object fields and patch them too.
                for inbound_fk_field in CustomObjectTypeField.objects.filter(
                    related_object_type=self.object_type,
                    type=CustomFieldTypeChoices.TYPE_OBJECT,
                    is_polymorphic=False,
                ).iterator():
                    owner_model = CustomObjectType.get_cached_model(inbound_fk_field.custom_object_type_id)
                    if owner_model is None:
                        continue
                    # Use local_fields list — avoids _relation_tree → get_models() recursion.
                    fk_field = next(
                        (f for f in owner_model._meta.local_fields if f.name == inbound_fk_field.name),
                        None,
                    )
                    if fk_field is None:
                        continue
                    fk_field.remote_field.model = model
                    fk_field.related_model = model
                    fk_field.to = model
                    fk_field.__dict__.pop('path_infos', None)
                    fk_field.__dict__.pop('reverse_path_infos', None)

                # Only cache fully-generated models.  Models generated with
                # skip_object_fields=True omit FK fields to other COTs; caching them
                # would permanently hide those fields if a dependent COT triggers
                # generation before this one in the startup loop (issue #408).
                if not skip_object_fields:
                    self._model_cache[(self.id, branch_id)] = (model, self.cache_timestamp)

        apps.clear_cache()
        ContentType.objects.clear_cache()

        # Register the global SearchIndex for this model
        self.register_custom_object_search_index(model)

        return model

    def get_model_with_serializer(self):
        from netbox_custom_objects.api.serializers import get_serializer_class
        model = self.get_model()
        get_serializer_class(model)
        self.register_custom_object_search_index(model)
        return model

    def _ensure_field_fk_constraint(self, model, field_name, on_delete_behavior=None):
        """Create the FK constraint for an OBJECT-type field at the DB level.

        Required because dynamic models are ``managed=False``, so Django won't
        emit the FK on its own.  ``on_delete_behavior`` defaults to the field's
        recorded value, falling back to SET_NULL.
        """
        table_name = self.get_database_table_name()

        # Get the model field
        try:
            model_field = model._meta.get_field(field_name)
        except Exception as e:
            logger.error("_ensure_field_fk_constraint: field %r not found on model %r: %s", field_name, model, e)
            return

        if not (hasattr(model_field, 'remote_field') and model_field.remote_field):
            return

        # Resolve on_delete_behavior from the CustomObjectTypeField if not provided
        if on_delete_behavior is None:
            try:
                cotf = self.fields.get(name=field_name)
                on_delete_behavior = cotf.on_delete_behavior or ObjectFieldOnDeleteChoices.SET_NULL
            except Exception:
                on_delete_behavior = ObjectFieldOnDeleteChoices.SET_NULL

        on_delete_sql = self._ON_DELETE_SQL.get(on_delete_behavior, "SET NULL")

        # Get the referenced table
        related_model = model_field.remote_field.model
        related_table = related_model._meta.db_table
        column_name = model_field.column

        schema_conn = _get_schema_connection()
        q = schema_conn.ops.quote_name
        with schema_conn.cursor() as cursor:
            # Drop existing FK constraint if it exists.
            # Join on key_column_usage so we match by actual column name, not constraint name —
            # RENAME COLUMN updates kcu but leaves the constraint name unchanged.
            cursor.execute("""
                SELECT tc.constraint_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                    ON tc.constraint_name = kcu.constraint_name
                    AND tc.table_schema = kcu.table_schema
                    AND tc.table_name = kcu.table_name
                WHERE tc.table_name = %s
                AND tc.table_schema = current_schema()
                AND tc.constraint_type = 'FOREIGN KEY'
                AND kcu.column_name = %s
            """, [table_name, column_name])

            for row in cursor.fetchall():
                constraint_name = row[0]
                cursor.execute(f'ALTER TABLE {q(table_name)} DROP CONSTRAINT IF EXISTS {q(constraint_name)}')

            # PROTECT → RESTRICT; SET NULL needs a nullable column (always true
            # for Object fields).  NOT DEFERRABLE — deferred constraints queue
            # trigger events that block subsequent ALTER TABLE calls.
            constraint_name = f"{table_name}_{column_name}_fk"
            cursor.execute(f"""
                ALTER TABLE {q(table_name)}
                ADD CONSTRAINT {q(constraint_name)}
                FOREIGN KEY ({q(column_name)})
                REFERENCES {q(related_table)} ("id")
                ON DELETE {on_delete_sql}
            """)

    def _ensure_all_fk_constraints(self, model):
        """
        Ensure that foreign key constraints are properly created at the database level
        for ALL OBJECT type fields, respecting each field's on_delete_behavior.

        :param model: The model to ensure FK constraints for
        """
        object_fields = self.fields.filter(type=CustomFieldTypeChoices.TYPE_OBJECT, is_polymorphic=False)

        for field in object_fields:
            self._ensure_field_fk_constraint(model, field.name, on_delete_behavior=field.on_delete_behavior)

    def create_model(self):
        from netbox_custom_objects.api.serializers import get_serializer_class
        # Get the model and ensure it's registered
        model = self.get_model()

        # Ensure the ContentType exists and is immediately available
        features = get_model_features(model)
        self.object_type.features = features
        self.object_type.public = True
        self.object_type.save()

        with _get_schema_connection().schema_editor() as schema_editor:
            schema_editor.create_model(model)

        self._store_base_column_snapshot(model)

        get_serializer_class(model)
        self.register_custom_object_search_index(model)

    @classmethod
    def deserialize_object(cls, data, pk=None):
        """Branching merge/revert hook — replays through the real ``save()``.

        The default ``DeserializedObject.save()`` does ``save_base(raw=True)``,
        which skips signals and our ``save()`` override, so the dynamic table
        never gets created in the destination schema.  This wrapper runs the
        full lifecycle.  ``object_type`` is nulled only when the FK doesn't
        resolve (mirrors ``clean_fields``) so normal merges don't churn the
        ContentType/ObjectType pair.
        """
        inner = _deserialize_object(cls, data, pk=pk)

        class _SchemaAwareDeserialized:
            def __init__(self, deserialized):
                self._inner = deserialized
                self.object = deserialized.object

            def save(self, using=None, **kwargs):
                # Snapshot before modifying so that diff()['pre'] records the
                # current state rather than showing all fields as None on revert.
                self.object.snapshot()
                # Only null the ObjectType FK when it doesn't resolve in the
                # destination schema; custom_object_type_post_save_handler will
                # then rebuild the link via get_or_create after INSERT.
                if (
                    self.object.object_type_id is not None
                    and not ObjectType.objects.filter(pk=self.object.object_type_id).exists()
                ):
                    self.object.object_type = None
                    self.object.object_type_id = None
                self.object.save()
                # Re-apply any M2M data (tags, etc.) that was stripped during deserialization.
                if self._inner.m2m_data:
                    for accessor_name, object_list in self._inner.m2m_data.items():
                        getattr(self.object, accessor_name).set(object_list)
                    self._inner.m2m_data = None

        return _SchemaAwareDeserialized(inner)

    def clean_fields(self, exclude=None):
        """Tolerate a stale ``object_type`` FK on revert (DELETE-undo path).

        ``delete()`` destroys the core_objecttype row to satisfy ChangeDiff's
        PROTECT FK; revert then re-inserts the COT, but full_clean would fail
        validating the dangling FK.  We null it here so validation passes;
        ``custom_object_type_post_save_handler`` rebuilds the ContentType/
        ObjectType pair via get_or_create on the resulting INSERT.  The new
        pk is intentional — the old one's audit refs were already invalidated.
        """
        if (
            self.object_type_id is not None
            and not ObjectType.objects.filter(pk=self.object_type_id).exists()
        ):
            self.object_type = None
            self.object_type_id = None
        super().clean_fields(exclude=exclude)

    def save(self, *args, **kwargs):
        needs_db_create = self._state.adding

        super().save(*args, **kwargs)

        if needs_db_create:
            self.create_model()
        else:
            # Clear the model cache when the CustomObjectType is modified
            self.clear_model_cache(self.id)

    def delete(self, *args, **kwargs):
        # COT is going away — every branch's cached class is stale.
        self.clear_model_cache(self.id, all_branches=True)

        # Regenerate against the current context so the model used for the
        # DDL drop below reflects this branch's column set, not whatever
        # stale class an earlier context cached.
        model = self.get_model()
        schema_conn = _get_schema_connection()
        in_branch = schema_conn is not connection

        # Delete all CustomObjectTypeFields that reference this CustomObjectType (non-polymorphic)
        for field in CustomObjectTypeField.objects.filter(related_object_type=self.object_type):
            field.delete()

        # Handle polymorphic fields that include this CustomObjectType among their allowed types
        for field in CustomObjectTypeField.objects.filter(
            is_polymorphic=True, related_object_types=self.object_type
        ):
            field.related_object_types.remove(self.object_type)
            if not field.related_object_types.exists():
                field.delete()

        object_type = ObjectType.objects.get_for_model(model)

        # ObjectChange and ObjectType records live in the main schema. Only clean
        # them up when operating outside a branch; inside a branch they belong to
        # main and must not be touched until the deletion is merged.
        if not in_branch:
            ObjectChange.objects.filter(changed_object_type=object_type).delete()

            # Delete any NetBox CustomField records (extras) with related_object_type pointing
            # to this COT's ObjectType. CustomField.related_object_type uses on_delete=PROTECT,
            # so these must be removed before object_type.delete() is called below.
            CustomField.objects.filter(related_object_type=object_type).delete()

        super().delete(*args, **kwargs)

        if not in_branch:
            # ChangeDiff has a PROTECT FK to ContentType/ObjectType — delete those
            # records first so object_type.delete() is not blocked.
            try:
                from netbox_branching.models import ChangeDiff
                ChangeDiff.objects.filter(object_type=object_type).delete()
            except ImportError:
                pass
            # Temporarily disconnect the pre_delete handler to skip the ObjectType deletion
            # TODO: Remove this disconnect/reconnect after ObjectType has been exempted from handle_deleted_object
            pre_delete.disconnect(handle_deleted_object)
            try:
                object_type.delete()
            finally:
                pre_delete.connect(handle_deleted_object)

        with schema_conn.schema_editor() as schema_editor:
            # Drop through tables before the main table (FKs).  Existence checks
            # use schema_conn so they target the active branch's schema.
            for through_model in getattr(model, '_through_models', []):
                if _table_exists(through_model._meta.db_table, conn=schema_conn):
                    schema_editor.delete_model(through_model)
            # Django's schema_editor.delete_model(parent) auto-recurses into
            # M2M fields' through models when their _meta.auto_created is
            # truthy — which we set deliberately in
            # MultiObjectFieldType.after_model_generation so Django's JSON
            # serializer includes M2M values.  That recursion would attempt a
            # DROP TABLE for through tables whose actual DB tables are absent
            # (e.g. when this delete runs on a COT whose branch-side through
            # was already dropped by a revert).  Clear auto_created on those
            # missing-table throughs for the duration of delete_model(parent)
            # and restore it after.
            cleared = []
            for field in model._meta.local_many_to_many:
                through = field.remote_field.through
                if through._meta.auto_created and not _table_exists(
                    through._meta.db_table, conn=schema_conn,
                ):
                    cleared.append((through, through._meta.auto_created))
                    through._meta.auto_created = False
            try:
                # Flush DEFERRABLE FK triggers so PG doesn't reject the DROP.
                schema_editor.execute('SET CONSTRAINTS ALL IMMEDIATE')
                schema_editor.delete_model(model)
            finally:
                for through, original in cleared:
                    through._meta.auto_created = original

        # Unregister from apps.all_models so cascade-delete doesn't query the
        # dropped table.  _global_lock guards against a concurrent get_model()
        # racing and re-registering mid-cleanup.
        with self._global_lock:
            model_name = model.__name__.lower()
            if model_name in apps.all_models.get(APP_LABEL, {}):
                del apps.all_models[APP_LABEL][model_name]

            for through_model in getattr(model, '_through_models', []):
                through_name = through_model.__name__.lower()
                if through_name in apps.all_models.get(APP_LABEL, {}):
                    del apps.all_models[APP_LABEL][through_name]

        apps.clear_cache()

        # Re-clear in case anything re-cached during cleanup.
        self.clear_model_cache(self.id, all_branches=True)


@receiver(post_save, sender=CustomObjectType)
def custom_object_type_post_save_handler(sender, instance, created, **kwargs):
    if created:
        # If creating a new object, get or create the ObjectType
        content_type_name = instance.get_table_model_name(instance.id).lower()
        ct, created = ObjectType.objects.get_or_create(
            app_label=APP_LABEL,
            model=content_type_name
        )
        # Snapshot for the second save below (the object_type assignment).
        # Without this, its ObjectChange would mark every field as changed.
        instance.snapshot()
        instance.object_type = ct
        instance.save()


def _rename_objectchange_field_key(fi, old_name, new_name):
    """Rewrite *old_name* → *new_name* JSON keys in ObjectChange (and
    ChangeDiff when netbox-branching is installed) for this field's COT.

    Runs inside ``CustomObjectTypeField.save()``'s atomic so it rolls back
    cleanly.  JSON column names are literals and field names are validated
    against ``^[a-z0-9_]+$`` — safe to interpolate.
    """
    cot = fi.custom_object_type
    model = cot.get_model()
    ct = ContentType.objects.get_for_model(model)
    # core.ObjectChange is branched by netbox-branching (migrations are allowed
    # on the branch schema, and read routing sends ObjectChange queries to the
    # active branch — see netbox_branching.database.BranchAwareRouter).  Using
    # _get_schema_connection() therefore updates the branch's copy of
    # core_objectchange, keeping branch-context history consistent with the
    # rename.  Main's copy is updated when the rename is later merged and this
    # function runs again in main context.
    conn = _get_schema_connection()

    oc_sql = (
        'UPDATE core_objectchange '
        'SET {col} = ({col} - %s) || jsonb_build_object(%s, {col}->%s) '
        'WHERE changed_object_type_id = %s AND {col} ? %s'
    )
    # Savepoint contains the failure cleanly, then we re-raise so the outer
    # field save aborts — silently logging would leave audit data inconsistent.
    try:
        with transaction.atomic(using=conn.alias):
            with conn.cursor() as cursor:
                for json_col in ('prechange_data', 'postchange_data'):
                    cursor.execute(
                        oc_sql.format(col=json_col),
                        [old_name, new_name, old_name, ct.id, old_name],
                    )
    except ProgrammingError:
        logger.error(
            '_rename_objectchange_field_key: ObjectChange schema mismatch '
            'rewriting %r -> %r; aborting rename',
            old_name, new_name, exc_info=True,
        )
        raise

    logger.debug('_rename_objectchange_field_key: %r -> %r for %s', old_name, new_name, ct)

    try:
        from netbox_branching.models import ChangeDiff  # noqa: F401
    except ImportError:
        return

    cd_sql = (
        'UPDATE netbox_branching_changediff '
        'SET {col} = ({col} - %s) || jsonb_build_object(%s, {col}->%s) '
        'WHERE object_type_id = %s AND {col} IS NOT NULL AND {col} ? %s'
    )
    try:
        with transaction.atomic(using=conn.alias):
            with conn.cursor() as cursor:
                for json_col in ('original', 'modified', 'current'):
                    cursor.execute(
                        cd_sql.format(col=json_col),
                        [old_name, new_name, old_name, ct.id, old_name],
                    )
    except ProgrammingError:
        logger.error(
            '_rename_objectchange_field_key: ChangeDiff schema mismatch '
            'rewriting %r -> %r; aborting rename',
            old_name, new_name, exc_info=True,
        )
        raise


class CustomObjectTypeField(CloningMixin, ExportTemplatesMixin, ChangeLoggedModel):
    custom_object_type = models.ForeignKey(
        CustomObjectType, on_delete=models.CASCADE, related_name="fields"
    )
    type = models.CharField(
        verbose_name=_("type"),
        max_length=50,
        choices=CustomFieldTypeChoices,
        default=CustomFieldTypeChoices.TYPE_TEXT,
        help_text=_("The type of data this custom object field holds"),
    )
    primary = models.BooleanField(
        verbose_name=_("primary name field"),
        default=False,
        help_text=_(
            "Indicates that this field's value will be used as the object's displayed name"
        ),
    )
    context = models.BooleanField(
        verbose_name=_("context field"),
        default=False,
        help_text=_(
            "Indicates that this field's value will be shown as context when this object is referenced by other objects"
        ),
    )
    related_object_type = models.ForeignKey(
        to="core.ObjectType",
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        help_text=_("The type of NetBox object this field maps to (for non-polymorphic object fields)"),
    )
    is_polymorphic = models.BooleanField(
        default=False,
        verbose_name=_("polymorphic"),
        help_text=_(
            "When enabled, this field uses a generic foreign key and may reference objects of multiple types. "
            "Set the allowed types in 'Related object types'."
        ),
    )
    related_object_types = models.ManyToManyField(
        to="core.ObjectType",
        blank=True,
        related_name="polymorphic_custom_object_type_fields",
        verbose_name=_("related object types"),
        help_text=_("The types of objects this polymorphic field may reference (used when 'Polymorphic' is enabled)."),
    )
    name = models.CharField(
        verbose_name=_("name"),
        max_length=50,
        help_text=_("Internal field name, e.g. \"vendor_label\""),
        validators=(
            RegexValidator(
                regex=r"^[a-z0-9]+(_[a-z0-9]+)*$",
                message=_(
                    "Only lowercase alphanumeric characters and underscores are allowed. "
                    "Names may not start or end with an underscore, and double underscores are not permitted."
                ),
            ),
        ),
    )
    label = models.CharField(
        verbose_name=_("label"),
        max_length=50,
        blank=True,
        help_text=_(
            "Name of the field as displayed to users (if not provided, the field's name will be used)"
        ),
    )
    group_name = models.CharField(
        verbose_name=_("group name"),
        max_length=50,
        blank=True,
        help_text=_("Custom object fields within the same group will be displayed together"),
    )
    description = models.CharField(
        verbose_name=_("description"), max_length=200, blank=True
    )
    required = models.BooleanField(
        verbose_name=_("required"),
        default=False,
        help_text=_(
            "This field is required when creating new objects or editing an existing object."
        ),
    )
    unique = models.BooleanField(
        verbose_name=_("must be unique"),
        default=False,
        help_text=_("The value of this field must be unique for the assigned object"),
    )
    search_weight = models.PositiveSmallIntegerField(
        verbose_name=_("search weight"),
        default=500,
        help_text=_(
            "Weighting for search. Lower values are considered more important. Fields with a search weight of 0 "
            "will be ignored."
        ),
    )
    filter_logic = models.CharField(
        verbose_name=_("filter logic"),
        max_length=50,
        choices=CustomFieldFilterLogicChoices,
        default=CustomFieldFilterLogicChoices.FILTER_LOOSE,
        help_text=_(
            "Loose matches any instance of a given string; exact matches the entire field."
        ),
    )
    default = models.JSONField(
        verbose_name=_("default"),
        blank=True,
        null=True,
        help_text=_(
            'Default value for the field (must be a JSON value). Encapsulate strings with double quotes (e.g. "Foo").'
        ),
    )
    related_object_filter = models.JSONField(
        blank=True,
        null=True,
        help_text=_(
            "Filter the object selection choices using a query_params dict (must be a JSON value)."
            'Encapsulate strings with double quotes (e.g. "Foo").'
        ),
    )
    related_name = models.CharField(
        verbose_name=_("reverse relation name"),
        max_length=100,
        blank=True,
        validators=(
            RegexValidator(
                regex=r"^[a-z0-9_]+$",
                message=_("Only lowercase alphanumeric characters and underscores are allowed."),
            ),
            RegexValidator(
                regex=r"__",
                message=_(
                    "Double underscores are not permitted in the reverse relation name."
                ),
                flags=re.IGNORECASE,
                inverse_match=True,
            ),
        ),
        help_text=_(
            "Name for the reverse relation accessor on the related object (for Object and MultiObject fields only). "
            'For example, setting this to "ssl_profiles" on a Certificate\u2192SLB field allows '
            "<code>slb.ssl_profiles.all()</code> in export templates. "
            "If left blank, a unique auto-generated name is used for Object fields and reverse access is "
            "disabled for MultiObject fields."
        ),
    )
    on_delete_behavior = models.CharField(
        verbose_name=_("on delete behavior"),
        max_length=20,
        choices=ObjectFieldOnDeleteChoices,
        default=ObjectFieldOnDeleteChoices.SET_NULL,
        blank=True,
        help_text=_(
            "What happens to this Custom Object when the referenced object is deleted "
            "(applies to Object-type fields only). "
            "Set null: clear the field and keep this object. "
            "Cascade: delete this object too. "
            "Protect: prevent deletion of the referenced object."
        ),
    )
    weight = models.PositiveSmallIntegerField(
        default=100,
        verbose_name=_("display weight"),
        help_text=_("Fields with higher weights appear lower in a form."),
    )
    validation_minimum = models.BigIntegerField(
        blank=True,
        null=True,
        verbose_name=_("minimum value"),
        help_text=_("Minimum allowed value (for numeric fields)"),
    )
    validation_maximum = models.BigIntegerField(
        blank=True,
        null=True,
        verbose_name=_("maximum value"),
        help_text=_("Maximum allowed value (for numeric fields)"),
    )
    validation_regex = models.CharField(
        blank=True,
        validators=[validate_regex],
        max_length=500,
        verbose_name=_("validation regex"),
        help_text=_(
            "Regular expression to enforce on text field values. Use ^ and $ to force matching of entire string. For "
            "example, <code>^[A-Z]{3}$</code> will limit values to exactly three uppercase letters."
        ),
    )
    choice_set = models.ForeignKey(
        to="extras.CustomFieldChoiceSet",
        on_delete=models.PROTECT,
        related_name="choices_for_object_type",
        verbose_name=_("choice set"),
        blank=True,
        null=True,
    )
    ui_visible = models.CharField(
        max_length=50,
        choices=CustomFieldUIVisibleChoices,
        default=CustomFieldUIVisibleChoices.ALWAYS,
        verbose_name=_("UI visible"),
        help_text=_("Specifies whether the custom field is displayed in the UI"),
    )
    ui_editable = models.CharField(
        max_length=50,
        choices=CustomFieldUIEditableChoices,
        default=CustomFieldUIEditableChoices.YES,
        verbose_name=_("UI editable"),
        help_text=_("Specifies whether the custom field value can be edited in the UI"),
    )
    is_cloneable = models.BooleanField(
        default=False,
        verbose_name=_("is cloneable"),
        help_text=_("Replicate this value when cloning objects"),
    )
    comments = models.TextField(verbose_name=_("comments"), blank=True)
    schema_id = models.PositiveIntegerField(
        blank=True,
        null=True,
        verbose_name=_("schema ID"),
        help_text=_(
            "Stable numeric identifier for this field used during schema diffing. "
            "Auto-assigned on creation; never changes and never reused within this Custom Object Type."
        ),
    )
    deprecated = models.BooleanField(
        default=False,
        verbose_name=_("deprecated"),
        help_text=_(
            "Mark this field as deprecated. Deprecated fields remain in the database but "
            "are read-only in the UI and should not be used in new objects."
        ),
    )
    deprecated_since = models.CharField(
        max_length=50,
        blank=True,
        verbose_name=_("deprecated since"),
        help_text=_("Schema version in which this field was marked deprecated (e.g. '2.0.0')."),
        validators=[validate_pep440],
    )
    scheduled_removal = models.CharField(
        max_length=50,
        blank=True,
        verbose_name=_("scheduled removal"),
        help_text=_("Schema version in which this field is planned to be removed (e.g. '3.0.0')."),
        validators=[validate_pep440],
    )

    clone_fields = ("custom_object_type",)

    # For non-object fields, other field attribs (such as choices, length, required) should be added here as a
    # superset, or stored in a JSON field
    # options = models.JSONField(blank=True, default=dict)

    # content_type = models.ForeignKey(ContentType, null=True, blank=True, on_delete=models.CASCADE)
    # many = models.BooleanField(default=False)

    class Meta:
        ordering = ["group_name", "weight", "name"]
        verbose_name = _("custom object type field")
        verbose_name_plural = _("custom object type fields")
        constraints = (
            models.UniqueConstraint(
                fields=("name", "custom_object_type"),
                name="%(app_label)s_%(class)s_unique_name",
            ),
            models.UniqueConstraint(
                fields=("related_object_type", "related_name"),
                condition=Q(related_name__gt=""),
                name="%(app_label)s_%(class)s_unique_related_name",
            ),
            models.UniqueConstraint(
                fields=("schema_id", "custom_object_type"),
                name="%(app_label)s_%(class)s_unique_schema_id",
                condition=models.Q(schema_id__isnull=False),
            ),
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Two distinct "original" mechanisms exist on this model:
        #   * ``_original_name`` / ``_original_type`` / ``_original_*`` (here) are
        #     scalar snapshots set on every instance (including freshly constructed
        #     ones) for cheap before/after comparisons in save().
        #   * ``_original`` (set in ``from_db``) is a full instance clone from the
        #     DB row, used when more than a single attribute is needed; only
        #     DB-loaded objects have it — see ``original`` property.
        self._name = self.__dict__.get("name")
        self._original_name = self.name
        self._original_type = self.type
        self._original_related_object_type_id = self.related_object_type_id
        self._original_is_polymorphic = self.is_polymorphic
        self._original_on_delete_behavior = self.on_delete_behavior

    def __str__(self):
        return self.label or self.name.replace("_", " ").capitalize()

    @property
    def model_class(self):
        if self.is_polymorphic:
            raise ValueError("Polymorphic fields reference multiple model classes; use related_object_types instead.")
        return apps.get_model(
            self.related_object_type.app_label, self.related_object_type.model
        )

    @property
    def is_single_value(self):
        return not self.many

    @property
    def many(self):
        return self.type in ["multiobject"]

    def get_child_relations(self, instance):
        return instance.get_field_value(self)

    def get_absolute_url(self):
        return reverse(
            "plugins:netbox_custom_objects:customobjecttype",
            args=[self.custom_object_type.pk],
        )

    @property
    def docs_url(self):
        return f"{settings.STATIC_URL}docs/models/extras/customfield/"

    @property
    def search_type(self):
        return SEARCH_TYPES.get(self.type)

    @property
    def choices(self):
        if self.choice_set:
            return self.choice_set.choices
        return []

    @property
    def related_object_type_label(self):
        if self.is_polymorphic:
            labels = []
            for ot in self.related_object_types.all():
                if ot.app_label == APP_LABEL:
                    cot_id = extract_cot_id_from_model_name(ot.model)
                    if cot_id is not None:
                        try:
                            labels.append(CustomObjectType.get_content_type_label(cot_id))
                            continue
                        except CustomObjectType.DoesNotExist:
                            pass
                labels.append(object_type_name(ot, include_app=True))
            return ", ".join(labels) if labels else "—"
        if not self.related_object_type:
            return "—"
        if self.related_object_type.app_label == APP_LABEL:
            custom_object_type_id = extract_cot_id_from_model_name(self.related_object_type.model)
            return CustomObjectType.get_content_type_label(custom_object_type_id)
        return object_type_name(self.related_object_type, include_app=True)

    def clean(self):
        super().clean()

        # A field cannot serve as both the primary display name and a context field
        if self.primary and self.context:
            raise ValidationError(
                _("A field cannot be both the primary display field and a context field.")
            )

        # Check if the field name is reserved
        if self.name in RESERVED_FIELD_NAMES:
            raise ValidationError(
                {
                    "name": _(
                        'Field name "{name}" is reserved and cannot be used. Reserved names are: {reserved_names}'
                    ).format(name=self.name, reserved_names=", ".join(RESERVED_FIELD_NAMES))
                }
            )

        # Validate the field's default value (if any)
        if self.default is not None:
            try:
                if self.type in (
                    CustomFieldTypeChoices.TYPE_TEXT,
                    CustomFieldTypeChoices.TYPE_LONGTEXT,
                ):
                    default_value = str(self.default)
                else:
                    default_value = self.default
                self.validate(default_value)
            except ValidationError as err:
                raise ValidationError(
                    {
                        "default": _('Invalid default value "{value}": {error}').format(
                            value=self.default, error=err.message
                        )
                    }
                )

        # Minimum/maximum values can be set only for numeric fields
        if self.type not in (
            CustomFieldTypeChoices.TYPE_INTEGER,
            CustomFieldTypeChoices.TYPE_DECIMAL,
        ):
            if self.validation_minimum:
                raise ValidationError(
                    {
                        "validation_minimum": _(
                            "A minimum value may be set only for numeric fields"
                        )
                    }
                )
            if self.validation_maximum:
                raise ValidationError(
                    {
                        "validation_maximum": _(
                            "A maximum value may be set only for numeric fields"
                        )
                    }
                )

        # Regex validation can be set only for text fields
        regex_types = (
            CustomFieldTypeChoices.TYPE_TEXT,
            CustomFieldTypeChoices.TYPE_LONGTEXT,
            CustomFieldTypeChoices.TYPE_URL,
        )
        if self.validation_regex and self.type not in regex_types:
            raise ValidationError(
                {
                    "validation_regex": _(
                        "Regular expression validation is supported only for text and URL fields"
                    )
                }
            )

        # Uniqueness can not be enforced for boolean or multiobject fields
        if self.unique and self.type in [CustomFieldTypeChoices.TYPE_BOOLEAN, CustomFieldTypeChoices.TYPE_MULTIOBJECT]:
            raise ValidationError(
                {"unique": _("Uniqueness cannot be enforced for boolean or multiobject fields")}
            )

        # Check if uniqueness constraint can be applied when changing from non-unique to unique.
        # _original is set by from_db only; deserialized objects (branch merge/revert)
        # never load from DB and won't have it — guard before touching self.original.
        if (
            self.pk
            and self.unique
            and not self._state.adding
            and hasattr(self, '_original')
            and not self.original.unique
        ):
            field_type = FIELD_TYPE_CLASS[self.type]()
            model_field = field_type.get_model_field(self)
            model = self.custom_object_type.get_model()
            model_field.contribute_to_class(model, self.name)

            old_field = field_type.get_model_field(self.original)
            old_field.contribute_to_class(model, self._original_name)

            # Route the probe through the branch's connection so the ALTER
            # TABLE runs in the active schema.  Using the default connection
            # here would either probe main's table from a branch context or
            # fail outright if the table only exists in the branch schema.
            probe_conn = _get_schema_connection()
            try:
                with transaction.atomic(using=probe_conn.alias):
                    with probe_conn.schema_editor() as test_schema_editor:
                        test_schema_editor.alter_field(model, old_field, model_field)
                        # If we get here, the constraint was applied successfully
                        # Now raise a custom exception to rollback the test transaction
                        raise UniquenessConstraintTestError()
            except UniquenessConstraintTestError:
                # The constraint can be applied, validation passes
                pass
            except IntegrityError:
                # The constraint cannot be applied due to existing non-unique values
                raise ValidationError(
                    {
                        "unique": _(
                            "Custom objects with non-unique values already exist so this action isn't permitted"
                        )
                    }
                )
            finally:
                self.custom_object_type.clear_model_cache(self.custom_object_type.id)

        # Choice set must be set on selection fields, and *only* on selection fields
        if self.type in (
            CustomFieldTypeChoices.TYPE_SELECT,
            CustomFieldTypeChoices.TYPE_MULTISELECT,
        ):
            if not self.choice_set:
                raise ValidationError(
                    {"choice_set": _("Selection fields must specify a set of choices.")}
                )
        elif self.choice_set:
            raise ValidationError(
                {"choice_set": _("Choices may be set only on selection fields.")}
            )

        # Object fields must define an object_type; other fields must not
        if self.type in (
            CustomFieldTypeChoices.TYPE_OBJECT,
            CustomFieldTypeChoices.TYPE_MULTIOBJECT,
        ):
            if self.is_polymorphic:
                # For polymorphic fields, related_object_type must be null
                if self.related_object_type:
                    raise ValidationError(
                        {
                            "related_object_type": _(
                                "Polymorphic object fields must not define a single object type; "
                                "use 'Related object types' instead."
                            )
                        }
                    )
                # related_object_types validation happens in forms (M2M set after save)
            else:
                if not self.related_object_type:
                    raise ValidationError(
                        {
                            "related_object_type": _(
                                "Object fields must define an object type."
                            )
                        }
                    )
        elif self.related_object_type:
            raise ValidationError(
                {
                    "type": _("{type} fields may not define an object type.").format(
                        type=self.get_type_display()
                    )
                }
            )
        elif self.is_polymorphic:
            raise ValidationError(
                {
                    "is_polymorphic": _(
                        "Only Object and Multi-Object fields may be polymorphic."
                    )
                }
            )

        # Related object filter can be set only for object-type fields, and must contain a dictionary mapping (if set)
        if self.related_object_filter is not None:
            if self.type not in (
                CustomFieldTypeChoices.TYPE_OBJECT,
                CustomFieldTypeChoices.TYPE_MULTIOBJECT,
            ):
                raise ValidationError(
                    {
                        "related_object_filter": _(
                            "A related object filter can be defined only for object fields."
                        )
                    }
                )
            if type(self.related_object_filter) is not dict:
                raise ValidationError(
                    {
                        "related_object_filter": _(
                            "Filter must be defined as a dictionary mapping attributes to values."
                        )
                    }
                )

        # Prevent flipping is_polymorphic on an existing field.  The DB schema
        # (concrete GFK columns or through table) was created for the original value;
        # changing it would leave the schema in an inconsistent state.
        if self.pk and bool(self.is_polymorphic) != bool(self._original_is_polymorphic):
            raise ValidationError(
                {"is_polymorphic": _("Cannot change the polymorphic flag after field creation.")}
            )

        # Prevent renaming a polymorphic field.
        #
        # For a polymorphic GFK field the concrete DB columns are named
        # "{name}_content_type" and "{name}_object_id"; for a polymorphic
        # MultiObject field the through table is named
        # "custom_objects_{cot_id}_{name}".  The save() path currently has no
        # logic to rename these artefacts (it falls through to `pass`), so
        # allowing a rename would silently leave the DB schema out of sync with
        # the field name stored in the row — causing query failures at runtime.
        #
        # Until explicit rename logic is implemented (renaming the GFK columns
        # and/or the through table analogously to the non-polymorphic rename path
        # at save() line ~1749), we reject renames outright.
        if (
            self.pk
            and (self.is_polymorphic or self._original_is_polymorphic)
            and self.name != self._original_name
        ):
            raise ValidationError(
                {"name": _("Cannot rename a polymorphic field after creation.")}
            )

        # related_name can only be set for object-type fields
        if self.related_name and self.type not in (
            CustomFieldTypeChoices.TYPE_OBJECT,
            CustomFieldTypeChoices.TYPE_MULTIOBJECT,
        ):
            raise ValidationError(
                {
                    "related_name": _(
                        "A reverse relation name can only be set for Object and MultiObject fields."
                    )
                }
            )

        # related_name is not supported on polymorphic fields: GenericForeignKey ignores it
        # and PolymorphicM2MDescriptor never consumes it, so any value set here would be silently
        # dropped with no working reverse accessor.
        if self.related_name and self.is_polymorphic:
            raise ValidationError(
                {
                    "related_name": _(
                        "Reverse relation names are not supported for polymorphic fields."
                    )
                }
            )

        # related_name must be unique per related_object_type (when set)
        if self.related_name and self.related_object_type_id:
            conflict = CustomObjectTypeField.objects.filter(
                related_object_type_id=self.related_object_type_id,
                related_name=self.related_name,
            ).exclude(pk=self.pk).first()
            if conflict:
                raise ValidationError(
                    {
                        "related_name": _(
                            'Reverse relation name "{name}" is already used by field '
                            '"{field}" on "{object_type}".'
                        ).format(
                            name=self.related_name,
                            field=conflict.name,
                            object_type=conflict.custom_object_type,
                        )
                    }
                )

        # on_delete_behavior is only meaningful for non-polymorphic Object-type fields.
        # Polymorphic GFK fields have no real DB FK constraint to enforce (the content_type
        # column always uses SET_NULL); silently normalise to SET_NULL so stored values
        # never create a false impression of cascade/protect semantics.
        if self.type != CustomFieldTypeChoices.TYPE_OBJECT or self.is_polymorphic:
            self.on_delete_behavior = ObjectFieldOnDeleteChoices.SET_NULL

        # Check for recursion in object and multiobject fields (non-polymorphic only).
        # Polymorphic fields' allowed types are a M2M set after save(), so their recursion
        # check runs in the check_polymorphic_recursion m2m_changed signal handler instead.
        if (not self.is_polymorphic and self.type in (
            CustomFieldTypeChoices.TYPE_OBJECT,
            CustomFieldTypeChoices.TYPE_MULTIOBJECT,
        ) and self.related_object_type_id and
            self.related_object_type.app_label == APP_LABEL):
            self._check_recursion()

    def _check_recursion(self):
        """
        Check for circular references in object and multiobject fields.
        Raises ValidationError if recursion is detected.
        """
        # Check if this field points to the same custom object type (self-referential)
        if self.related_object_type_id == self.custom_object_type.object_type_id:
            return  # Self-referential fields are allowed

        # Get the related custom object type directly from the object_type relationship
        try:
            related_custom_object_type = CustomObjectType.objects.get(object_type=self.related_object_type)
        except CustomObjectType.DoesNotExist:
            return  # Not a custom object type, no recursion possible

        # Check for circular references by traversing the dependency chain
        visited = {self.custom_object_type.id}
        if self._has_circular_reference(related_custom_object_type, visited):
            raise ValidationError(
                {
                    "related_object_type": _(
                        "Circular reference detected. This field would create a circular dependency "
                        "between custom object types."
                    )
                }
            )

    def _has_circular_reference(self, custom_object_type, visited):
        """
        Recursively check if there's a circular reference by following the dependency chain.

        Args:
            custom_object_type: The CustomObjectType object to check
            visited: Set of custom object type IDs already visited in this traversal

        Returns:
            bool: True if a circular reference is detected, False otherwise
        """
        # If we've already visited this node, it's a genuine cycle only when the
        # node is the origin COT (the one that owns the field being validated).
        # Re-encountering a non-origin node that was already explored in a
        # different branch of the DFS is NOT a cycle — returning True there
        # would cause a false positive when an intermediate COT has a
        # self-referencing field (e.g. a multiobject pointing back to itself).
        if custom_object_type.id in visited:
            return custom_object_type.id == self.custom_object_type.id

        # Add this type to visited set
        visited.add(custom_object_type.id)

        # Track ContentTypes already enqueued for recursion to avoid redundant work.
        related_objects_checked = set()

        # Non-polymorphic object/multiobject fields: target stored on related_object_type FK.
        for field in custom_object_type.fields.filter(
            type__in=[
                CustomFieldTypeChoices.TYPE_OBJECT,
                CustomFieldTypeChoices.TYPE_MULTIOBJECT,
            ],
            is_polymorphic=False,
            related_object_type__isnull=False,
            related_object_type__app_label=APP_LABEL
        ):
            if field.related_object_type in related_objects_checked:
                continue
            related_objects_checked.add(field.related_object_type)
            try:
                next_custom_object_type = CustomObjectType.objects.get(object_type=field.related_object_type)
            except CustomObjectType.DoesNotExist:
                continue
            if self._has_circular_reference(next_custom_object_type, visited):
                return True

        # Polymorphic object/multiobject fields: targets stored on related_object_types M2M.
        for poly_field in custom_object_type.fields.filter(
            type__in=[
                CustomFieldTypeChoices.TYPE_OBJECT,
                CustomFieldTypeChoices.TYPE_MULTIOBJECT,
            ],
            is_polymorphic=True,
        ):
            for ot in poly_field.related_object_types.filter(app_label=APP_LABEL):
                if ot in related_objects_checked:
                    continue
                related_objects_checked.add(ot)
                try:
                    next_custom_object_type = CustomObjectType.objects.get(object_type=ot)
                except CustomObjectType.DoesNotExist:
                    continue
                if self._has_circular_reference(next_custom_object_type, visited):
                    return True

        return False

    def serialize(self, value):
        """
        Prepare a value for storage as JSON data.
        """
        if value is None:
            return value
        if self.type == CustomFieldTypeChoices.TYPE_DATE and type(value) is date:
            return value.isoformat()
        if (
            self.type == CustomFieldTypeChoices.TYPE_DATETIME
            and type(value) is datetime
        ):
            return value.isoformat()
        if self.type == CustomFieldTypeChoices.TYPE_OBJECT:
            if self.is_polymorphic and value is not None:
                ct = ContentType.objects.get_for_model(value)
                return {"content_type_id": ct.pk, "object_id": value.pk}
            return value.pk
        if self.type == CustomFieldTypeChoices.TYPE_MULTIOBJECT:
            if self.is_polymorphic:
                result = []
                for obj in value:
                    ct = ContentType.objects.get_for_model(obj)
                    result.append({"content_type_id": ct.pk, "object_id": obj.pk})
                return result or None
            return [obj.pk for obj in value] or None
        return value

    def deserialize(self, value):
        """
        Convert JSON data to a Python object suitable for the field type.
        """
        if value is None:
            return value
        if self.type == CustomFieldTypeChoices.TYPE_DATE:
            try:
                return date.fromisoformat(value)
            except ValueError:
                return value
        if self.type == CustomFieldTypeChoices.TYPE_DATETIME:
            try:
                return datetime.fromisoformat(value)
            except ValueError:
                return value
        if self.type == CustomFieldTypeChoices.TYPE_OBJECT:
            if self.is_polymorphic and isinstance(value, dict):
                try:
                    ct = ContentType.objects.get(pk=value["content_type_id"])
                    model = ct.model_class()
                    return model.objects.filter(pk=value["object_id"]).first() if model else None
                except (ContentType.DoesNotExist, KeyError):
                    return None
            if not self.related_object_type:
                return None
            model = self.related_object_type.model_class()
            return model.objects.filter(pk=value).first()
        if self.type == CustomFieldTypeChoices.TYPE_MULTIOBJECT:
            if self.is_polymorphic and isinstance(value, list):
                results = []
                for item in value:
                    if isinstance(item, dict):
                        try:
                            ct = ContentType.objects.get(pk=item["content_type_id"])
                            model = ct.model_class()
                            if model:
                                obj = model.objects.filter(pk=item["object_id"]).first()
                                if obj:
                                    results.append(obj)
                        except (ContentType.DoesNotExist, KeyError):
                            pass
                return results
            if not self.related_object_type:
                return []
            model = self.related_object_type.model_class()
            return model.objects.filter(pk__in=value)
        return value

    def to_filter(self, lookup_expr=None):
        # TODO: Move all this logic to field_types.py get_filterform_field methods
        """
        Return a django_filters Filter instance suitable for this field type.

        :param lookup_expr: Custom lookup expression (optional)
        """
        kwargs = {"field_name": f"custom_field_data__{self.name}"}
        if lookup_expr is not None:
            kwargs["lookup_expr"] = lookup_expr

        # Text/URL
        if self.type in (
            CustomFieldTypeChoices.TYPE_TEXT,
            CustomFieldTypeChoices.TYPE_LONGTEXT,
            CustomFieldTypeChoices.TYPE_URL,
        ):
            filter_class = filters.MultiValueCharFilter
            if self.filter_logic == CustomFieldFilterLogicChoices.FILTER_LOOSE:
                kwargs["lookup_expr"] = "icontains"

        # Integer
        elif self.type == CustomFieldTypeChoices.TYPE_INTEGER:
            filter_class = filters.MultiValueNumberFilter

        # Decimal
        elif self.type == CustomFieldTypeChoices.TYPE_DECIMAL:
            filter_class = filters.MultiValueDecimalFilter

        # Boolean
        elif self.type == CustomFieldTypeChoices.TYPE_BOOLEAN:
            filter_class = django_filters.BooleanFilter

        # Date
        elif self.type == CustomFieldTypeChoices.TYPE_DATE:
            filter_class = filters.MultiValueDateFilter

        # Date & time
        elif self.type == CustomFieldTypeChoices.TYPE_DATETIME:
            filter_class = filters.MultiValueDateTimeFilter

        # Select
        elif self.type == CustomFieldTypeChoices.TYPE_SELECT:
            filter_class = filters.MultiValueCharFilter

        # Multiselect
        elif self.type == CustomFieldTypeChoices.TYPE_MULTISELECT:
            filter_class = filters.MultiValueArrayFilter

        # Object
        elif self.type == CustomFieldTypeChoices.TYPE_OBJECT:
            filter_class = filters.MultiValueNumberFilter

        # Multi-object
        elif self.type == CustomFieldTypeChoices.TYPE_MULTIOBJECT:
            filter_class = filters.MultiValueNumberFilter
            kwargs["lookup_expr"] = "contains"

        # Unsupported custom field type
        else:
            return None

        filter_instance = filter_class(**kwargs)
        filter_instance.custom_field = self

        return filter_instance

    def validate(self, value):
        """
        Validate a value according to the field's type validation rules.
        """
        if value not in [None, ""]:

            # Validate text field
            if self.type in (
                CustomFieldTypeChoices.TYPE_TEXT,
                CustomFieldTypeChoices.TYPE_LONGTEXT,
            ):
                if type(value) is not str:
                    raise ValidationError(_("Value must be a string."))
                if self.validation_regex and not re.match(self.validation_regex, value):
                    raise ValidationError(
                        _("Value must match regex '{regex}'").format(
                            regex=self.validation_regex
                        )
                    )

            # Validate integer
            elif self.type == CustomFieldTypeChoices.TYPE_INTEGER:
                if type(value) is not int:
                    raise ValidationError(_("Value must be an integer."))
                if (
                    self.validation_minimum is not None
                    and value < self.validation_minimum
                ):
                    raise ValidationError(
                        _("Value must be at least {minimum}").format(
                            minimum=self.validation_minimum
                        )
                    )
                if (
                    self.validation_maximum is not None
                    and value > self.validation_maximum
                ):
                    raise ValidationError(
                        _("Value must not exceed {maximum}").format(
                            maximum=self.validation_maximum
                        )
                    )

            # Validate decimal
            elif self.type == CustomFieldTypeChoices.TYPE_DECIMAL:
                try:
                    decimal.Decimal(value)
                except decimal.InvalidOperation:
                    raise ValidationError(_("Value must be a decimal."))
                if (
                    self.validation_minimum is not None
                    and value < self.validation_minimum
                ):
                    raise ValidationError(
                        _("Value must be at least {minimum}").format(
                            minimum=self.validation_minimum
                        )
                    )
                if (
                    self.validation_maximum is not None
                    and value > self.validation_maximum
                ):
                    raise ValidationError(
                        _("Value must not exceed {maximum}").format(
                            maximum=self.validation_maximum
                        )
                    )

            # Validate boolean
            elif self.type == CustomFieldTypeChoices.TYPE_BOOLEAN and value not in [
                True,
                False,
                1,
                0,
            ]:
                raise ValidationError(_("Value must be true or false."))

            # Validate date
            elif self.type == CustomFieldTypeChoices.TYPE_DATE:
                if type(value) is not date:
                    try:
                        date.fromisoformat(value)
                    except ValueError:
                        raise ValidationError(
                            _("Date values must be in ISO 8601 format (YYYY-MM-DD).")
                        )

            # Validate date & time
            elif self.type == CustomFieldTypeChoices.TYPE_DATETIME:
                if type(value) is not datetime:
                    try:
                        datetime_from_timestamp(value)
                    except ValueError:
                        raise ValidationError(
                            _(
                                "Date and time values must be in ISO 8601 format (YYYY-MM-DD HH:MM:SS)."
                            )
                        )

            # Validate selected choice
            elif self.type == CustomFieldTypeChoices.TYPE_SELECT:
                if value not in self.choice_set.values:
                    raise ValidationError(
                        _(
                            "Invalid choice ({value}) for choice set {choiceset}."
                        ).format(value=value, choiceset=self.choice_set)
                    )

            # Validate all selected choices
            elif self.type == CustomFieldTypeChoices.TYPE_MULTISELECT:
                if not set(value).issubset(self.choice_set.values):
                    raise ValidationError(
                        _(
                            "Invalid choice(s) ({value}) for choice set {choiceset}."
                        ).format(value=value, choiceset=self.choice_set)
                    )

            # Validate selected object
            elif self.type == CustomFieldTypeChoices.TYPE_OBJECT:
                if self.is_polymorphic:
                    # Polymorphic value is {"content_type_id": int, "object_id": int}
                    if not isinstance(value, dict) or not isinstance(
                        value.get("content_type_id"), int
                    ) or not isinstance(value.get("object_id"), int):
                        raise ValidationError(
                            _(
                                "Polymorphic object value must be a dict with integer "
                                "content_type_id and object_id keys, not {type}."
                            ).format(type=type(value).__name__)
                        )
                elif type(value) is not int:
                    raise ValidationError(
                        _("Value must be an object ID, not {type}").format(
                            type=type(value).__name__
                        )
                    )

            # Validate selected objects
            elif self.type == CustomFieldTypeChoices.TYPE_MULTIOBJECT:
                if type(value) is not list:
                    raise ValidationError(
                        _("Value must be a list of object IDs, not {type}").format(
                            type=type(value).__name__
                        )
                    )
                for id in value:
                    if self.is_polymorphic:
                        # Each polymorphic entry is {"content_type_id": int, "object_id": int}
                        if not isinstance(id, dict) or not isinstance(
                            id.get("content_type_id"), int
                        ) or not isinstance(id.get("object_id"), int):
                            raise ValidationError(
                                _(
                                    "Each polymorphic multiobject value must be a dict with "
                                    "integer content_type_id and object_id keys."
                                )
                            )
                    elif type(id) is not int:
                        raise ValidationError(
                            _("Found invalid object ID: {id}").format(id=id)
                        )

        elif self.required:
            raise ValidationError(_("Required field cannot be empty."))

    @classmethod
    def from_db(cls, db, field_names, values):
        instance = super().from_db(db, field_names, values)

        # save original values, when model is loaded from database,
        # in a separate attribute on the model
        instance._loaded_values = dict(zip(field_names, values))
        instance._original = cls(**instance._loaded_values)
        return instance

    @property
    def original(self):
        return self._original

    @property
    def through_table_name(self):
        # STABILITY CONTRACT — do not change this formula without a data migration.
        #
        # The table name is computed from (custom_object_type_id, name) and is
        # never stored in the database.  It is used as the physical PostgreSQL
        # table name for polymorphic M2M through tables and as part of the
        # in-memory Django model name returned by through_model_name.
        #
        # Consequences of changing the formula:
        #   • Existing through tables in live databases would be orphaned (the
        #     new name would not match any table on disk).
        #   • Any serialised reference to the through model (e.g. in cached app
        #     state or migration history) would become unresolvable.
        #
        # If the formula must change, write a data migration that renames every
        # affected table with ALTER TABLE … RENAME TO before deploying the new
        # code, and update through_model_name to match.
        raw = f"custom_objects_{self.custom_object_type_id}_{self.name}"
        return safe_table_name(raw)

    @property
    def through_model_name(self):
        # Derived directly from through_table_name; see its stability contract above.
        # The "Through_" prefix ensures the in-memory model name is unique within
        # the app registry and does not collide with user-visible model names.
        return f"Through_{self.through_table_name}"

    @classmethod
    def deserialize_object(cls, data, pk=None):
        """Branching merge/revert hook — replays through the real ``save()``.

        Same shape as ``CustomObjectType.deserialize_object``: the default
        ``DeserializedObject.save(raw=True)`` skips our ``save()``, so the
        column never gets added.  This wrapper runs the full lifecycle.
        """
        inner = _deserialize_object(cls, data, pk=pk)

        class _SchemaAwareDeserialized:
            def __init__(self, deserialized):
                self._inner = deserialized
                self.object = deserialized.object

            def save(self, using=None, **kwargs):
                self.object.save()
                if self._inner.m2m_data:
                    for accessor_name, object_list in self._inner.m2m_data.items():
                        getattr(self.object, accessor_name).set(object_list)
                    self._inner.m2m_data = None

        return _SchemaAwareDeserialized(inner)

    def save(self, *args, **kwargs):
        is_new = self._state.adding

        schema_conn = _get_schema_connection()

        # Auto-assign schema_id from the parent's monotonic counter.  IDs are
        # never reused, even after a field is deleted; the
        # UniqueConstraint(schema_id, custom_object_type) is the race safety net.
        # bulk_create() bypasses save() — callers must set schema_id explicitly.
        if self._state.adding and self.schema_id is None:
            # Atomic and queryset pinned to schema_conn — the branching router
            # routes CustomObjectType writes to the branch connection.
            with transaction.atomic(using=schema_conn.alias):
                cot = CustomObjectType.objects.using(schema_conn.alias).select_for_update().get(
                    pk=self.custom_object_type_id
                )
                new_schema_id = cot.next_schema_id + 1
                # update() avoids post_save → clear_model_cache; the cache must
                # remain valid until this field's own get_model() below.
                CustomObjectType.objects.using(schema_conn.alias).filter(
                    pk=self.custom_object_type_id
                ).update(next_schema_id=new_schema_id)
                self.schema_id = new_schema_id

        field_type = FIELD_TYPE_CLASS[self.type]()
        model = self.custom_object_type.get_model()

        # Schema mutation + audit-key rewrite + cache bump + parent save() share
        # one atomic so a failure between DDL and row save can't leave audit
        # data rewritten but the field record un-persisted (or vice versa).
        with transaction.atomic(using=schema_conn.alias):
            with schema_conn.schema_editor() as schema_editor:
                if self._state.adding:
                    if self.is_polymorphic:
                        if self.type == CustomFieldTypeChoices.TYPE_OBJECT:
                            field_type.add_polymorphic_object_columns(self, model, schema_editor)
                        elif self.type == CustomFieldTypeChoices.TYPE_MULTIOBJECT:
                            field_type.create_polymorphic_m2m_table(self, model, schema_editor)
                    else:
                        _schema_add_field(self, model, schema_editor, schema_conn)
                        _apply_deferred_co_field(self)
                else:
                    # Polymorphic renames/type changes are rejected in clean();
                    # raise here if one slipped through to avoid silent column drift.
                    if self.is_polymorphic or self._original_is_polymorphic:
                        if self.name != self._original_name:
                            raise ValidationError(
                                {"name": _("Cannot rename a polymorphic field after creation.")}
                            )
                    else:
                        _schema_alter_field(self.original, self, model, schema_editor, schema_conn)

            # Rewrite historical audit-data keys so any future replay can
            # resolve old or new name to the current field name.
            if (
                not self._state.adding
                and not self.is_polymorphic
                and self._original_name != self.name
            ):
                _rename_objectchange_field_key(self, self._original_name, self.name)

            # FK-constraint decision inside the atomic so a rollback discards it too.
            should_ensure_fk = False
            if self.type == CustomFieldTypeChoices.TYPE_OBJECT and not self.is_polymorphic:
                if self._state.adding:
                    should_ensure_fk = True
                else:
                    type_changed_to_object = (
                        self._original_type != CustomFieldTypeChoices.TYPE_OBJECT
                        and self.type == CustomFieldTypeChoices.TYPE_OBJECT
                    )
                    related_object_changed = (
                        self._original_type == CustomFieldTypeChoices.TYPE_OBJECT
                        and self.related_object_type_id != self._original_related_object_type_id
                    )
                    on_delete_changed = (
                        self._original_type == CustomFieldTypeChoices.TYPE_OBJECT
                        and self.on_delete_behavior != self._original_on_delete_behavior
                    )
                    should_ensure_fk = type_changed_to_object or related_object_changed or on_delete_changed

            self.custom_object_type.clear_model_cache(self.custom_object_type.id)

            # Bump cache_timestamp to invalidate other workers.  snapshot() first
            # so change logging records a correct pre-state.
            self.custom_object_type.snapshot()
            self.custom_object_type.save(update_fields=['cache_timestamp'])

            super().save(*args, **kwargs)

        # FK constraint runs AFTER commit to avoid "pending trigger events".
        if should_ensure_fk:
            _on_delete = self.on_delete_behavior
            _field_name = self.name

            def ensure_constraint():
                try:
                    self.custom_object_type._ensure_field_fk_constraint(
                        model, _field_name, on_delete_behavior=_on_delete
                    )
                except Exception as e:
                    logger.error(
                        "Failed to ensure FK constraint for field %r on COT %r: %s",
                        _field_name, self.custom_object_type_id, e,
                    )

            transaction.on_commit(ensure_constraint)

        # On rename, _schema_alter_field calls contribute_to_class twice on the
        # same class — force a no_cache regeneration so _meta is clean.  Non-
        # rename changes lean on cache_timestamp for lazy invalidation; we skip
        # the apps.clear_cache() cascade so signal-driven cache evictions (e.g.
        # clear_cache_on_field_save for OBJECT fields) survive.
        renamed = (
            not self._state.adding
            and not self.is_polymorphic
            and self._original_name != self.name
        )
        if renamed:
            updated_model = self.custom_object_type.get_model(no_cache=True)
            self.custom_object_type.register_custom_object_search_index(updated_model)

        # Reindex all objects of this type if search indexing was affected.
        # self.original (backed by _original) is only set by from_db; the
        # `not is_new` branch implies _state.adding is False, which implies
        # the row came from the DB, so _original is guaranteed to exist.
        if is_new:
            needs_reindex = self.search_weight > 0
        else:
            needs_reindex = self.search_weight != self.original.search_weight
        if needs_reindex:
            _cot_id = self.custom_object_type_id
            transaction.on_commit(lambda: ReindexCustomObjectTypeJob.enqueue(cot_id=_cot_id))

    def delete(self, *args, **kwargs):
        field_type = FIELD_TYPE_CLASS[self.type]()
        model = self.custom_object_type.get_model()
        schema_conn = _get_schema_connection()

        with schema_conn.schema_editor() as schema_editor:
            if self.is_polymorphic:
                if self.type == CustomFieldTypeChoices.TYPE_OBJECT:
                    field_type.remove_polymorphic_object_columns(self, model, schema_editor)
                elif self.type == CustomFieldTypeChoices.TYPE_MULTIOBJECT:
                    field_type.drop_polymorphic_m2m_table(self, model, schema_editor)
            else:
                _schema_remove_field(self, model, schema_editor, schema_conn=schema_conn)

        self.custom_object_type.clear_model_cache(self.custom_object_type.id)

        # snapshot() first so change logging records a correct pre-state.
        self.custom_object_type.snapshot()
        self.custom_object_type.save(update_fields=['cache_timestamp'])

        super().delete(*args, **kwargs)

        # Regenerate so the apps registry no longer holds a class with the
        # removed column — squash revert may SELECT via the model class between
        # field-undo and CO-undo, and a stale class would emit ProgrammingError.
        updated_model = self.custom_object_type.get_model()

        self.custom_object_type.register_custom_object_search_index(updated_model)

        if self.search_weight > 0:
            _cot_id = self.custom_object_type_id
            transaction.on_commit(lambda: ReindexCustomObjectTypeJob.enqueue(cot_id=_cot_id))


class CustomObjectObjectTypeManager(ObjectTypeManager):

    def public(self):
        """
        Return ObjectTypes for public models plus all custom object models (excluding through tables).

        NetBox marks models as public via the ObjectType.public boolean field (set when the
        ObjectType row is created from model_is_public()).  Dynamic custom object models are
        included unconditionally by app_label so they appear in object-type pickers even before
        their ObjectType rows have been lazily created.
        """
        return (
            self.get_queryset()
            .filter(Q(public=True) | Q(app_label=APP_LABEL))
            .exclude(app_label=APP_LABEL, model__startswith="through")
        )


class CustomObjectObjectType(ObjectType):
    """
    Wrap Django's native ContentType model to use our custom manager.
    """

    objects = CustomObjectObjectTypeManager()

    class Meta:
        proxy = True


# Signal handlers to clear model cache when definitions change


@receiver(post_save, sender=CustomObjectType)
def clear_cache_on_custom_object_type_save(sender, instance, **kwargs):
    """
    Clear the model cache when a CustomObjectType is saved.
    """
    CustomObjectType.clear_model_cache(instance.id)


@receiver(m2m_changed, sender=CustomObjectTypeField.related_object_types.through)
def check_polymorphic_recursion(sender, instance, action, pk_set, **kwargs):
    """
    Prevent circular references in polymorphic field allowed-type lists.

    clean() cannot check this because related_object_types is a M2M that is set
    after the instance is saved.  m2m_changed fires on pre_add, which lets us abort
    the operation before any rows are written.
    """
    if action != "pre_add" or not pk_set:
        return

    own_object_type_id = instance.custom_object_type.object_type_id

    for ot_pk in pk_set:
        if ot_pk == own_object_type_id:
            # Self-reference is permitted (same pattern as non-polymorphic check).
            continue
        try:
            related_cot = CustomObjectType.objects.get(object_type_id=ot_pk)
        except CustomObjectType.DoesNotExist:
            continue  # Native NetBox type — no COT dependency chain to traverse.
        visited = {instance.custom_object_type_id}
        if instance._has_circular_reference(related_cot, visited):
            raise ValidationError(
                _(
                    "Circular reference detected: one of the selected object types would "
                    "create a circular dependency between custom object types."
                )
            )


@receiver(post_save, sender=CustomObjectTypeField)
def clear_cache_on_field_save(sender, instance, **kwargs):
    """
    Clear the model cache when a CustomObjectTypeField is saved.
    This ensures the parent CustomObjectType's model is regenerated.
    """
    if instance.custom_object_type_id:
        CustomObjectType.clear_model_cache(instance.custom_object_type_id)
    # Clear caches for non-polymorphic fields pointing to this custom object type
    for pointing_field in CustomObjectTypeField.objects.filter(
        related_object_type=instance.custom_object_type.object_type
    ):
        CustomObjectType.clear_model_cache(pointing_field.custom_object_type_id)
    # Clear caches for polymorphic fields that include this custom object type
    for pointing_field in CustomObjectTypeField.objects.filter(
        is_polymorphic=True,
        related_object_types=instance.custom_object_type.object_type,
    ):
        CustomObjectType.clear_model_cache(pointing_field.custom_object_type_id)

    # When a TYPE_OBJECT field is saved, the FK's on_delete behavior is contributed as
    # a reverse relation to the related model's _meta.related_objects. If the related
    # model is a custom object type, bump its cache_timestamp so that all workers
    # regenerate its model and pick up the correct on_delete behavior. Without this,
    # a worker with a stale cached related model will still see the old on_delete value
    # and may bypass a PROTECT or RESTRICT constraint via Django's pre-delete SET NULL.
    if instance.type == CustomFieldTypeChoices.TYPE_OBJECT and instance.related_object_type_id:
        try:
            related_cot = CustomObjectType.objects.get(object_type_id=instance.related_object_type_id)
            CustomObjectType.clear_model_cache(related_cot.id)
            related_cot.snapshot()
            related_cot.save(update_fields=['cache_timestamp'])
        except CustomObjectType.DoesNotExist:
            pass


@receiver(pre_delete, sender=CustomObjectTypeField)
def clear_cache_on_field_delete(sender, instance, **kwargs):
    """
    Clear the model cache when a CustomObjectTypeField is deleted.
    This is in addition to the manual clear in the delete() method.
    """
    if instance.custom_object_type_id:
        CustomObjectType.clear_model_cache(instance.custom_object_type_id)
