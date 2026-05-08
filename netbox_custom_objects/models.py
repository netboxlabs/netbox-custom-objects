import contextvars
import decimal
import logging
import re
import threading
from datetime import date, datetime

from packaging.version import Version, InvalidVersion

import django_filters
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
from extras.models.customfields import SEARCH_TYPES
from extras.utils import is_taggable, run_validators
from netbox.config import get_config
from netbox.models import ChangeLoggedModel, NetBoxModel
from netbox.models.features import (
    BookmarksMixin,
    ChangeLoggingMixin,
    CloningMixin,
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
from netbox_custom_objects.field_types import FIELD_TYPE_CLASS, safe_table_name
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


def _table_exists(table_name):
    """Return True if *table_name* exists in the current database."""
    return table_name in connection.introspection.table_names()


USER_TABLE_DATABASE_NAME_PREFIX = "custom_objects_"

# Per-context storage for CO field values deferred during squash merge.
# Using ContextVar instead of a class-level dict so that concurrent merges
# (different threads or coroutines) each get an isolated copy.
# Shape: {db_table: {co_pk: {'using': alias, 'data': {field_name: value}}}}
_deferred_co_field_data: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    '_deferred_co_field_data', default=None
)


def _get_schema_connection():
    """
    Return the active branch's DB connection when called within a branch context,
    otherwise return the default (main-schema) connection.

    Used so that schema-editor operations (add/alter/remove column) target the
    correct PostgreSQL schema without requiring every call-site to be branch-aware.
    """
    try:
        from netbox_branching.contextvars import active_branch
        branch = active_branch.get()
        if branch is not None:
            return connections[branch.connection_name]
    except ImportError:
        pass
    return connection


def _apply_deferred_co_field(field_instance):
    """
    Apply any deferred CO field values after a column is added to the DB.

    Called by CustomObjectTypeField.save() after schema_editor.add_field() so that
    custom object rows inserted before their columns existed (squash merge ordering)
    receive their correct values via a raw UPDATE.

    ``_deferred_co_field_data`` (ContextVar) has the shape::

        {db_table: {co_pk: {'using': alias, 'data': {field_name: value}}}}

    For TYPE_OBJECT fields the postchange_data key is ``{name}`` but the DB column
    is ``{name}_id`` — this function maps accordingly.
    For TYPE_MULTIOBJECT fields there is no column on the main table, so they are
    skipped entirely.
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
    data_key = field_instance.name
    if field_instance.type == CustomFieldTypeChoices.TYPE_OBJECT:
        col_name = f'{field_instance.name}_id'
    else:
        col_name = field_instance.name

    schema_conn = _get_schema_connection()

    with schema_conn.cursor() as cursor:
        for co_pk, entry in per_table.items():
            # Distinguish "key absent" from "key present with NULL".  An explicit
            # None is a legitimate write and must reach the column; only a
            # missing key should be skipped.
            if data_key not in entry['data']:
                continue
            value = entry['data'][data_key]
            # table_name comes from get_database_table_name() (controlled by our
            # code) and col_name from field.name, which is validated by the
            # ^[a-z0-9_]+$ regex — no double-quote characters are possible, so
            # the f-string interpolation is safe against SQL injection here.
            cursor.execute(
                f'UPDATE "{table_name}" SET "{col_name}" = %s WHERE id = %s',
                [value, co_pk],
            )

    # Remove the consumed key from each entry so that processed field data does
    # not persist in the ContextVar beyond its useful lifetime (e.g. on a retry
    # after a partial failure, stale data from a previous attempt is avoided).
    for entry in per_table.values():
        entry['data'].pop(data_key, None)

    # Prune entries whose data dict is now exhausted.
    exhausted = [pk for pk, entry in per_table.items() if not entry['data']]
    for pk in exhausted:
        del per_table[pk]
    if not per_table:
        del deferred[table_name]
    if not deferred:
        _deferred_co_field_data.set(None)


def _schema_add_field(fi, model, schema_editor, schema_conn):
    """
    Issue ``add_field`` against the physical schema for *fi*.

    Handles through-table creation for MULTIOBJECT fields.  Does NOT apply
    deferred CO field data — callers that need that (squash merge context) must
    call ``_apply_deferred_co_field(fi)`` separately after this returns.

    Idempotent: skips the ALTER TABLE if the column already exists (e.g. when
    sync/merge replays an ObjectChange that was already applied).
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

    schema_editor.add_field(model, mf)
    if fi.type == CustomFieldTypeChoices.TYPE_MULTIOBJECT:
        ft.create_m2m_table(fi, model, fi.name, schema_conn=schema_conn)


def _schema_remove_field(fi, model, schema_editor, existing_tables=None):
    """
    Issue ``remove_field`` against the physical schema for *fi*.

    For MULTIOBJECT fields the through table is dropped first.  When
    *existing_tables* is a pre-fetched list only tables present in it are
    dropped; when it is ``None`` (main-schema context) the drop is always
    attempted.

    Always issues ``SET CONSTRAINTS ALL IMMEDIATE`` before ``remove_field`` to
    flush any DEFERRABLE FK trigger events that would otherwise cause PostgreSQL
    to reject the subsequent ALTER TABLE.
    """
    ft = FIELD_TYPE_CLASS[fi.type]()
    mf = ft.get_model_field(fi)
    mf.contribute_to_class(model, fi.name)

    if fi.type == CustomFieldTypeChoices.TYPE_MULTIOBJECT:
        through_table = fi.through_table_name
        if existing_tables is None or through_table in existing_tables:
            through_meta = type(
                'Meta', (),
                {'db_table': through_table, 'app_label': APP_LABEL, 'managed': True},
            )
            through_model = type(
                f'_TempThrough_{through_table}',
                (models.Model,),
                {'Meta': through_meta, '__module__': 'netbox_custom_objects.models'},
            )
            schema_editor.delete_model(through_model)

    # Flush any pending DEFERRABLE FK trigger events before ALTER TABLE;
    # otherwise PostgreSQL raises "pending trigger events" when removing a FK field.
    schema_editor.execute('SET CONSTRAINTS ALL IMMEDIATE')
    schema_editor.remove_field(model, mf)


def _schema_alter_field(old_fi, new_fi, model, schema_editor, schema_conn, existing_tables=None):
    """
    Issue ``alter_field`` against the physical schema, updating *old_fi* to *new_fi*.

    For MULTIOBJECT fields whose name changes the through table is renamed before
    ``alter_field`` is called.  When the old through table is absent (e.g. the
    branch has never seen this field) the new through table is created from scratch
    instead.

    *existing_tables* — optional pre-fetched table name list from the target
    connection.  When given, through-table operations are guarded by membership
    checks.  When ``None`` (main-schema context) the schema_conn is introspected
    once on demand.

    Idempotent: skips the ALTER TABLE if the old column is already gone and the
    new column already exists (e.g. when sync/merge replays an ObjectChange that
    was already applied).

    Conflict resolution: when neither the old nor the new column exists (the field
    was independently renamed in the target schema — e.g. branch renamed A→X while
    main renamed A→Y), the live field record is looked up from the target schema to
    find the actual current column name, which is then renamed to the new target.
    A warning is logged to flag the conflict.
    """
    old_is_m2m = old_fi.type == CustomFieldTypeChoices.TYPE_MULTIOBJECT
    new_is_m2m = new_fi.type == CustomFieldTypeChoices.TYPE_MULTIOBJECT

    # A type change between MULTIOBJECT and a scalar type (or vice versa) is not
    # a simple column rename/alter — the storage representation is fundamentally
    # different (through-table vs column).  Attempting alter_field in this case
    # would fail at the DB level.  Log and skip; the caller is expected to handle
    # such changes as remove + add rather than alter.
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
        if old_is_m2m:
            # M2M fields have no physical column; the old through table is absent.
            return
        # Scalar field: neither the old nor the new column exists.  The field was
        # independently renamed in this schema (e.g. branch renamed A→X while main
        # renamed A→Y; now applying main's rename to the branch).  Look up the live
        # field record in the target schema to find the actual column and rename it.
        logger.warning(
            '_schema_alter_field: rename conflict on %s — source column %r and '
            'target column %r are both absent; field pk=%d was independently renamed '
            'in this schema; resolving by looking up live column',
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

    if (
        new_is_m2m
        and old_fi.name != new_fi.name
    ):
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
            old_through_model = generate_model(
                f'_TempOldThrough_{old_through}',
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
            schema_editor.alter_db_table(old_through_model, old_through, new_through)
        else:
            # Old through table absent — create the new one from scratch
            ft = FIELD_TYPE_CLASS[new_fi.type]()
            ft.create_m2m_table(new_fi, model, new_fi.name, schema_conn=schema_conn)

    schema_editor.alter_field(model, old_mf, new_mf)


class CustomObject(
    BookmarksMixin,
    ChangeLoggingMixin,
    CloningMixin,
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
    def deserialize_object(cls, data, pk=None):
        """
        Hook called by ObjectChange.apply() for CREATE actions.

        The squash merge strategy may apply a CO's CREATE before its
        CustomObjectTypeField rows are in main (the dependency graph has no FK
        from CO to fields).  When that happens, the standard Django
        deserialization would INSERT the CO with NULL custom-field values
        because the columns don't exist yet.

        This hook:
        1. Deserializes the CO using a fresh model (re-queried from DB).
        2. Does save_base(raw=True) as normal.
        3. Stores the full postchange_data in _deferred_co_field_data (ContextVar)
           so that CustomObjectTypeField.save() can UPDATE the row after each
           column is added (handles the squash ordering case).
        """
        # Derive the COT primary key from the model class name (e.g. 'Table1Model' → 1)
        cot_id_str = extract_cot_id_from_model_name(cls.__name__.lower())
        if cot_id_str is None:
            # Not a generated model name — fall back to standard deserialization.
            return _deserialize_object(cls, data, pk=pk)
        cot_id = int(cot_id_str)  # regex guarantees digits-only

        # Refresh the model cache so we pick up any fields already applied to main.
        # (In the squash case the cache may still point to a zero-field model.)
        CustomObjectType.clear_model_cache(cot_id)
        try:
            cot = CustomObjectType.objects.get(pk=cot_id)
            fresh_model = cot.get_model()
        except CustomObjectType.DoesNotExist:
            fresh_model = cls

        # Build the instance directly against ``fresh_model`` rather than going
        # through Django's ``serializers.deserialize('python', ...)``: that
        # helper re-resolves the model class from the data's natural_key via
        # ``apps.get_model``, which returns *main's* class (the one registered
        # in ``apps.all_models``).  In branch context, main's class can have a
        # different field set than the branch's class — e.g. main has 'alpha'
        # while branch has 'branch_alpha' after a branch-side rename — so the
        # eventual save would emit SQL with main's column names against a
        # branch table that doesn't have them, raising
        # ``column "alpha" does not exist``.
        #
        # Building directly off ``fresh_model`` (the context-aware class) keeps
        # the field set aligned with the schema we're writing to, and the attr
        # translator registered with netbox-branching (see
        # branching.translate_renamed_field_attr) lets us map the data dict's
        # old field names to the current model's field names where they
        # diverge.

        # Try the registered netbox-branching attr translator if available so
        # that data dicts carrying old field names are reshaped to the current
        # context's field names before deserialization.  Falls back gracefully
        # when the translator isn't registered (e.g. branching not installed).
        try:
            from netbox_branching.utilities import _translate_attr  # type: ignore[attr-defined]
        except ImportError:
            def _translate_attr(_inst, attr):  # noqa: D401
                return None

        obj = fresh_model()
        if pk is not None:
            obj.pk = pk
        m2m_data = {}
        field_names = {f.name for f in fresh_model._meta.get_fields()}

        for raw_attr, value in data.items():
            attr = raw_attr
            if attr == 'custom_fields':
                attr = 'custom_field_data'
            # Tags via the standard NetBox path (Tag rows are looked up by name).
            if attr == 'tags' and is_taggable(fresh_model):
                tag_model = apps.get_model('extras', 'Tag')
                m2m_data['tags'] = list(tag_model.objects.filter(name__in=value or []))
                continue
            if attr not in field_names:
                resolved = _translate_attr(obj, attr)
                if resolved and resolved in field_names:
                    attr = resolved
                else:
                    # Unknown attribute (likely a removed field) — preserve it as
                    # a Python attribute so downstream code (e.g.
                    # _deferred_co_field_data) can still see it.
                    setattr(obj, raw_attr, value)
                    continue
            try:
                f = fresh_model._meta.get_field(attr)
            except FieldDoesNotExist:
                setattr(obj, raw_attr, value)
                continue
            if isinstance(f, ManyToManyField):
                m2m_data[attr] = value
            elif isinstance(f, ForeignKey):
                # FK values arrive as the related PK; assign via the FK column
                # (``<name>_id``) to avoid an extra DB lookup.
                setattr(obj, f.attname, value)
            else:
                # Coerce via the field's to_python() to handle datetimes etc.
                # Field.to_python raises ValidationError for parse failures; the
                # raw value is also acceptable for ValueError/TypeError on
                # malformed input from older serialized data.
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
                # Read pk after save_base so that auto-assigned PKs are captured.
                # (If pk was None before save_base, obj.pk is now the DB-assigned id.)
                obj_pk = obj.pk
                # Re-apply M2M relations.  Each ``accessor`` is a manager
                # attribute name on the saved instance and ``related_pks`` is a
                # list of related PKs.  Skipped quietly when the through table
                # or its columns aren't present yet — squash ordering can defer
                # field creation, and the through-table appears when the field's
                # own CREATE ObjectChange is later applied.  We narrow the
                # except to the failure modes that signature: ``AttributeError``
                # if the manager descriptor isn't bound to ``obj`` yet, and
                # ``ProgrammingError``/``OperationalError`` if the through
                # table/column is missing in the active schema.
                for accessor, related_pks in m2m_data.items():
                    try:
                        manager = getattr(obj, accessor)
                        manager.set(related_pks)
                    except (AttributeError, ProgrammingError, OperationalError):
                        logger.debug(
                            'deserialize_object: deferred M2M %r on %s pk=%s',
                            accessor, table_name, obj_pk, exc_info=True,
                        )
                # Register full data for deferred column updates (squash ordering fix).
                deferred = _deferred_co_field_data.get()
                if deferred is None:
                    deferred = {}
                    _deferred_co_field_data.set(deferred)
                if table_name not in deferred:
                    deferred[table_name] = {}
                deferred[table_name][obj_pk] = {
                    'using': _using,
                    'data': full_data,
                }

        return _Deserialized()

    def __str__(self):
        # Find the field with primary=True and return that field's "name" as the name of the object
        primary_field = self._field_objects.get(self._primary_field_id, None)
        primary_field_value = None
        if primary_field:
            field_type = FIELD_TYPE_CLASS[primary_field["field"].type]()
            primary_field_value = field_type.get_display_value(
                self, primary_field["name"]
            )
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

    @property
    def _generated_table_model(self):
        # An indication that the model is a generated table model.
        return True

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
    # Branch-aware model cache.
    #
    # Key: (custom_object_type_id, branch_id_or_None)
    #   branch_id_or_None == None  → main schema (the canonical apps-registered class)
    #   branch_id_or_None == int   → that branch's schema (cached only, not registered)
    #
    # Branch-specific classes are NEVER inserted into Django's apps registry.
    # apps.all_models[APP_LABEL][<table_name>] always points to main's class so that
    # ``content_type.model_class()`` (used by netbox-branching's record_change_diff
    # inside `with deactivate_branch():`) resolves to a class consistent with main's
    # schema — branch's schema can have renamed columns that wouldn't exist in main.
    _model_cache = {}
    _through_model_cache = (
        {}
    )  # Now stores {custom_object_type_id: {through_model_name: through_model}}
    _model_cache_locks = {}  # Per-model locks to prevent race conditions
    _global_lock = threading.RLock()  # Global lock for managing per-model locks
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
        """Return the active netbox-branching Branch id, or None for main.

        Used as the second component of the model cache key so that branch and
        main contexts each get their own cached class, with main's class being
        the only one registered in Django's apps registry.
        """
        try:
            from netbox_branching.contextvars import active_branch
        except ImportError:
            return None
        try:
            branch = active_branch.get()
        except LookupError:
            return None
        return branch.id if branch is not None else None

    @classmethod
    def clear_model_cache(cls, custom_object_type_id=None, *, all_branches=False):
        """
        Clear the cached generated model for a CustomObjectType.

        Default behaviour clears only the **current branch context's** entry
        (main vs. a specific branch).  This preserves the other context's
        cached class — important because that's the class registered in
        ``apps.all_models`` and used by ``content_type.model_class()``.

        Pass ``all_branches=True`` to wipe every (cot, branch) entry for the
        given cot — appropriate for COT deletion or full re-init.

        :param custom_object_type_id: ID of the CustomObjectType to clear cache
            for, or None to clear *everything*.
        :param all_branches: When True with a specific cot id, clear that cot's
            cache for all branch contexts, not just the active one.
        """
        with cls._global_lock:
            if custom_object_type_id is not None:
                if all_branches:
                    for key in list(cls._model_cache):
                        if key[0] == custom_object_type_id:
                            cls._model_cache.pop(key, None)
                    cls._through_model_cache.pop(custom_object_type_id, None)
                    cls._model_cache_locks.pop(custom_object_type_id, None)
                else:
                    branch_id = cls._active_branch_id()
                    cls._model_cache.pop((custom_object_type_id, branch_id), None)
                    # Through-model cache and per-cot lock are not branch-scoped;
                    # leave them alone for context-only clears.
            else:
                cls._model_cache.clear()
                cls._through_model_cache.clear()
                cls._model_cache_locks.clear()

        # Clear Django apps registry cache to ensure newly created models are recognized
        apps.get_models.cache_clear()

    @classmethod
    def get_cached_model(cls, custom_object_type_id, branch_id=None):
        """
        Get the cached model for a CustomObjectType in the given branch context.

        :param custom_object_type_id: ID of the CustomObjectType
        :param branch_id: Branch id, or None for main (default)
        :return: The cached model or None if not found
        """
        cache_entry = cls._model_cache.get((custom_object_type_id, branch_id))
        if cache_entry:
            return cache_entry[0]
        return None

    @classmethod
    def get_cached_timestamp(cls, custom_object_type_id, branch_id=None):
        """
        Get the timestamp of a cached model for a CustomObjectType in the given branch context.

        :param custom_object_type_id: ID of the CustomObjectType
        :param branch_id: Branch id, or None for main (default)
        :return: The cached timestamp or None if not found
        """
        cache_entry = cls._model_cache.get((custom_object_type_id, branch_id))
        if cache_entry:
            return cache_entry[1]
        return None

    @classmethod
    def is_model_cached(cls, custom_object_type_id, branch_id=None):
        """
        Check if a model is cached for a CustomObjectType in the given branch context.

        :param custom_object_type_id: ID of the CustomObjectType
        :param branch_id: Branch id, or None for main (default)
        :return: True if the model is cached, False otherwise
        """
        return (custom_object_type_id, branch_id) in cls._model_cache

    @classmethod
    def get_cached_through_model(cls, custom_object_type_id, through_model_name):
        """
        Get a specific cached through model for a CustomObjectType.

        :param custom_object_type_id: ID of the CustomObjectType
        :param through_model_name: Name of the through model to retrieve
        :return: The cached through model or None if not found
        """
        if custom_object_type_id in cls._through_model_cache:
            return cls._through_model_cache[custom_object_type_id].get(
                through_model_name
            )
        return None

    @classmethod
    def get_cached_through_models(cls, custom_object_type_id):
        """
        Get all cached through models for a CustomObjectType.

        :param custom_object_type_id: ID of the CustomObjectType
        :return: Dict of through models or empty dict if not found
        """
        return cls._through_model_cache.get(custom_object_type_id, {})

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
                    logger.debug(
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

        # Collect through models during after_model_generation
        through_models = []

        for field_object in all_field_objects.values():
            field_name = field_object["name"]
            field_instance = field_object["field"]

            # Skip fields that were skipped due to recursion
            if field_name in skipped_fields:
                continue

            if field_instance.is_polymorphic:
                if field_instance.type == CustomFieldTypeChoices.TYPE_OBJECT:
                    # Polymorphic GFK: no through model, no after_model_generation needed.
                    pass
                elif field_instance.type == CustomFieldTypeChoices.TYPE_MULTIOBJECT:
                    # Ensure the polymorphic through model is in the app registry.
                    # On server restart the registry is cleared; re-register if needed.
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
                    except LookupError:
                        field_type_obj = FIELD_TYPE_CLASS[CustomFieldTypeChoices.TYPE_MULTIOBJECT]()
                        source_model_str = f"{APP_LABEL}.{model.__name__}"
                        through_model = field_type_obj.get_polymorphic_through_model(
                            field_instance, source_model_str
                        )
                        source_field = through_model._meta.get_field("source")
                        source_field.remote_field.model = model
                        source_field.related_model = model
                        _apps.register_model(APP_LABEL, through_model)
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

            # Collect through models from M2M fields
            if hasattr(field, 'remote_field') and hasattr(field.remote_field, 'through'):
                through_model = field.remote_field.through
                # Only collect custom through models, not auto-created Django ones
                if (through_model and through_model not in through_models and
                    hasattr(through_model._meta, 'app_label') and
                    through_model._meta.app_label == APP_LABEL):
                    through_models.append(through_model)

        # Store through models on the model for yielding in get_models()
        model._through_models = through_models

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
        # model must be an instance of this CustomObjectType's get_model() generated class
        # Use local_fields / local_many_to_many — plain lists populated at class-creation
        # time — instead of _meta.get_field(), which triggers Django's lazy _relation_tree
        # computation.  _relation_tree calls apps.get_models(), which re-enters our
        # get_models() override, which calls get_model() for every COT → infinite recursion.
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

        with self._global_lock:
            if self.is_model_cached(self.id, branch_id) and not no_cache:
                cached_timestamp = self.get_cached_timestamp(self.id, branch_id)
                # Only use cache if the timestamps are available and match
                if cached_timestamp and self.cache_timestamp and cached_timestamp == self.cache_timestamp:
                    model = self.get_cached_model(self.id, branch_id)
                    # Re-register the SearchIndex against this cached class.  The
                    # ``registry["search"]`` dict is global, not per-branch, so a
                    # previous get_model() call in a different context may have
                    # left it bound to a class with a different field set.
                    # Without this refresh, the next CO save in this context
                    # would fail when post_save's search-cache handler reads
                    # field names that don't exist on the active model class.
                    self.register_custom_object_search_index(model)
                    return model
                else:
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

        # Register the main model with Django's app registry.
        # _suppress_clear_cache() is used directly here (rather than going
        # through generate_model()) because we are calling apps.register_model()
        # explicitly, not type().  generate_model() wraps type() and suppresses
        # clear_cache only for that call; the suppression window needs to extend
        # through the _model_cache write that follows so the model is safely
        # cached before any re-entrant get_model() call can observe it.
        # Without suppression: register_model() → clear_cache() → get_models() →
        # get_model() → generate_model() → register_model() recurses infinitely.
        with _suppress_clear_cache():
            # Django's ModelBase metaclass auto-registers every concrete model with
            # ``Meta.app_label`` set into ``apps.all_models`` at ``type()``-creation
            # time (inside generate_model() above).  That's the right behaviour for
            # main's class, but for branch classes we must NOT leave the auto-
            # registration in place — content_type.model_class() (used by
            # netbox-branching's record_change_diff inside `with deactivate_branch():`)
            # would then return a class with branch's column set, producing
            # `column "beta" does not exist` errors when querying main.
            #
            # So: for main (branch_id is None) overwrite the registration cleanly;
            # for a branch context, restore main's previously-cached class so the
            # apps registry continues to reflect main's schema.
            model_key = model_name.lower()
            if branch_id is None:
                if model_key in apps.all_models[APP_LABEL]:
                    del apps.all_models[APP_LABEL][model_key]
                apps.register_model(APP_LABEL, model)
            else:
                main_class = self.get_cached_model(self.id, branch_id=None)
                if main_class is not None:
                    apps.all_models[APP_LABEL][model_key] = main_class
                # If main hasn't been generated yet, the branch class stays
                # registered until the next main-context get_model() call, which
                # will replace it.  This is rare and self-healing.

            self._after_model_generation(attrs, model)

            # Cache the generated model with its timestamp (protected by lock for thread safety)
            with self._global_lock:
                self._model_cache[(self.id, branch_id)] = (model, self.cache_timestamp)

        # Now that the model is in _model_cache, clear_cache() is safe:
        # re-entrant get_model() calls for this COT hit the cache immediately.
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
        """
        Ensure that a foreign key constraint is properly created at the database level
        for a specific OBJECT type field. This is necessary because models are created
        with managed=False, which may not properly create FK constraints.

        :param model: The model containing the field
        :param field_name: The name of the field to ensure FK constraint for
        :param on_delete_behavior: Override the ON DELETE behavior (ObjectFieldOnDeleteChoices value).
            If None, the value is read from the corresponding CustomObjectTypeField record.
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
            # Drop existing FK constraint if it exists
            cursor.execute("""
                SELECT constraint_name
                FROM information_schema.table_constraints
                WHERE table_name = %s
                AND table_schema = current_schema()
                AND constraint_type = 'FOREIGN KEY'
                AND constraint_name LIKE %s
            """, [table_name, f"%{column_name}%"])

            for row in cursor.fetchall():
                constraint_name = row[0]
                cursor.execute(f'ALTER TABLE {q(table_name)} DROP CONSTRAINT IF EXISTS {q(constraint_name)}')

            # PROTECT maps to RESTRICT in SQL (raises an error on delete attempt).
            # SET NULL and CASCADE map directly.  For SET NULL the column must be
            # nullable, which it always is for Object fields.
            # Not DEFERRABLE: deferred constraints queue trigger events that block
            # subsequent ALTER TABLE calls (e.g. during branch revert remove_field).
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
        """
        Custom deserialization hook for netbox-branching's merge/revert engine.

        ``ObjectChange.apply()`` normally uses ``DeserializedObject.save()``, which
        calls ``Model.save_base(raw=True)`` — bypassing our ``save()`` override and
        all ``post_save`` signals.  That means ``create_model()`` never runs and the
        physical table is never created in the destination schema.

        By implementing this classmethod the apply engine calls our version instead,
        returning a wrapper whose ``save()`` invokes the full ``CustomObjectType.save()``
        lifecycle (signals included) so that the table is created as a side effect of
        replaying the ObjectChange.

        ``object_type`` is cleared before saving so the ``custom_object_type_post_save_handler``
        can re-create and link it correctly in the destination schema, avoiding any FK
        mismatch between the branch and main ``ObjectType`` pks.
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
                # Clear the ObjectType FK — it may not exist in main yet.
                # custom_object_type_post_save_handler re-sets it after INSERT.
                self.object.object_type = None
                self.object.object_type_id = None
                self.object.save()
                # Re-apply any M2M data (tags, etc.) that was stripped during deserialization.
                if self._inner.m2m_data:
                    for accessor_name, object_list in self._inner.m2m_data.items():
                        getattr(self.object, accessor_name).set(object_list)
                    self._inner.m2m_data = None

        return _SchemaAwareDeserialized(inner)

    def save(self, *args, **kwargs):
        needs_db_create = self._state.adding

        super().save(*args, **kwargs)

        if needs_db_create:
            self.create_model()
        else:
            # Clear the model cache when the CustomObjectType is modified
            self.clear_model_cache(self.id)

    def delete(self, *args, **kwargs):
        # Clear the model cache for this CustomObjectType (across all branches —
        # the COT itself is going away, so every branch's cached class becomes stale).
        self.clear_model_cache(self.id, all_branches=True)

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
            # Drop through tables before the main table (they have FKs pointing to it).
            for through_model in getattr(model, '_through_models', []):
                if _table_exists(through_model._meta.db_table):
                    schema_editor.delete_model(through_model)
            schema_editor.delete_model(model)

        # Unregister the model and its through-models from Django's app registry so
        # that subsequent ORM operations (e.g. deleting a related device) do not try
        # to query the now-dropped table and receive a
        # "relation 'custom_objects_<id>' does not exist" error.
        # Use _global_lock to prevent a concurrent get_model() call from racing
        # against this de-registration and re-adding the model mid-cleanup.
        with self._global_lock:
            model_name = model.__name__.lower()
            if model_name in apps.all_models.get(APP_LABEL, {}):
                del apps.all_models[APP_LABEL][model_name]

            for through_model in getattr(model, '_through_models', []):
                through_name = through_model.__name__.lower()
                if through_name in apps.all_models.get(APP_LABEL, {}):
                    del apps.all_models[APP_LABEL][through_name]

        # Clear Django's internal relation/field caches so the removed model is no
        # longer discovered during cascade-delete collector traversal.
        apps.clear_cache()

        # Re-clear the model cache to remove re-cached model from get_model
        # (across all branches — see comment at the top of this method).
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
        # Snapshot before modifying so change logging records a correct pre-state.
        # Without this, diff()['pre'] would set all fields to None during branch revert.
        instance.snapshot()
        instance.object_type = ct
        instance.save()


def _rename_objectchange_field_key(fi, old_name, new_name):
    """
    Rename a JSON key in all ObjectChange records for CustomObject instances of
    this field's type, reflecting a field rename from *old_name* to *new_name*.

    Updates both ``prechange_data`` and ``postchange_data`` in the ObjectChange
    table, and ``original``/``modified``/``current`` in netbox-branching's
    ChangeDiff table when that plugin is installed.

    Field names are validated with ``^[a-z0-9_]+$`` so string formatting of the
    column names here is safe against SQL injection.

    This runs inside the same ``transaction.atomic()`` block as
    ``CustomObjectTypeField.save()``, so it rolls back cleanly if the enclosing
    transaction is aborted.
    """
    cot = fi.custom_object_type
    model = cot.get_model()
    ct = ContentType.objects.get_for_model(model)
    conn = _get_schema_connection()

    oc_sql = (
        'UPDATE core_objectchange '
        'SET {col} = ({col} - %s) || jsonb_build_object(%s, {col}->%s) '
        'WHERE changed_object_type_id = %s AND {col} ? %s'
    )
    with connections[conn.alias].cursor() as cursor:
        for json_col in ('prechange_data', 'postchange_data'):
            cursor.execute(oc_sql.format(col=json_col), [old_name, new_name, old_name, ct.id, old_name])

    logger.debug('_rename_objectchange_field_key: %r → %r for %s', old_name, new_name, ct)

    try:
        from netbox_branching.models import ChangeDiff  # noqa: F401 — presence check only
        cd_sql = (
            'UPDATE netbox_branching_changediff '
            'SET {col} = ({col} - %s) || jsonb_build_object(%s, {col}->%s) '
            'WHERE object_type_id = %s AND {col} IS NOT NULL AND {col} ? %s'
        )
        with connections[conn.alias].cursor() as cursor:
            for json_col in ('original', 'modified', 'current'):
                cursor.execute(cd_sql.format(col=json_col), [old_name, new_name, old_name, ct.id, old_name])
    except ImportError:
        pass  # netbox-branching not installed
    except Exception:
        logger.debug(
            '_rename_objectchange_field_key: ChangeDiff rename failed for %r → %r',
            old_name, new_name, exc_info=True,
        )


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
        # Skip when _original is absent (e.g. during deserialization in branch merge/revert).
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

            try:
                with transaction.atomic():
                    with connection.schema_editor() as test_schema_editor:
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
        # If we've already visited this type, we have a cycle
        if custom_object_type.id in visited:
            return True

        # Add this type to visited set
        visited.add(custom_object_type.id)

        # Check all *non-polymorphic* object and multiobject fields in this COT.
        #
        # KNOWN LIMITATION: polymorphic fields (is_polymorphic=True) store their
        # allowed target types on the related_object_types M2M, not on the
        # related_object_type FK.  This DFS therefore does not traverse edges
        # introduced by polymorphic fields.  A cycle that passes entirely through
        # polymorphic legs (e.g. A →(poly) B →(poly) A) will go undetected.
        #
        # Fixing this requires also iterating field.related_object_types.filter(
        # app_label=APP_LABEL) and recursing into each.  The check_polymorphic_recursion
        # signal already guards the direct A→B assignment, but cannot see multi-hop
        # cycles that depend on polymorphic fields already on intermediate types.
        #
        # TODO: extend this DFS to also traverse polymorphic related_object_types
        # so that multi-hop polymorphic cycles are detected at assignment time.
        related_objects_checked = set()
        for field in custom_object_type.fields.filter(
            type__in=[
                CustomFieldTypeChoices.TYPE_OBJECT,
                CustomFieldTypeChoices.TYPE_MULTIOBJECT,
            ],
            related_object_type__isnull=False,
            related_object_type__app_label=APP_LABEL
        ):
            if field.related_object_type in related_objects_checked:
                continue
            related_objects_checked.add(field.related_object_type)

            # Get the related custom object type directly from the object_type relationship
            try:
                next_custom_object_type = CustomObjectType.objects.get(object_type=field.related_object_type)
            except CustomObjectType.DoesNotExist:
                continue

            # Recursively check this dependency
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
        # return self.__class__(**self._loaded_values)

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
        """
        Custom deserialization hook for netbox-branching's merge/revert engine.

        Same problem as ``CustomObjectType.deserialize_object``: the default
        ``DeserializedObject.save(raw=True)`` bypasses ``CustomObjectTypeField.save()``,
        so the physical column is never added to the custom object table.

        This wrapper calls the real ``save()`` so that ``add_field`` runs as a side
        effect of replaying the CREATE ObjectChange during a merge.
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

        # Use the branch connection when operating inside a branch so that schema
        # editor operations target the branch schema rather than main.
        schema_conn = _get_schema_connection()

        # Auto-assign schema_id for new fields that don't have one yet.
        # Increments the monotonic counter on the parent CustomObjectType so that IDs are
        # never reused, even after a field is deleted.  The UniqueConstraint on
        # (schema_id, custom_object_type) is the safety net against races; a concurrent
        # writer would get an IntegrityError and must retry.
        # Note: bulk_create() bypasses save() entirely, so auto-assignment will NOT fire for
        # fields created via CustomObjectTypeField.objects.bulk_create(...). Always set
        # schema_id explicitly when using bulk_create.
        if self._state.adding and self.schema_id is None:
            # transaction.atomic() must target the same connection that the
            # SELECT FOR UPDATE runs against; in a branch context the branching
            # router routes CustomObjectType to the branch connection, so we
            # pin both the atomic block and the queryset to schema_conn.alias.
            with transaction.atomic(using=schema_conn.alias):
                cot = CustomObjectType.objects.using(schema_conn.alias).select_for_update().get(
                    pk=self.custom_object_type_id
                )
                new_schema_id = cot.next_schema_id + 1
                # Use update() rather than save() to avoid dispatching post_save on
                # CustomObjectType, which would clear the model cache prematurely.
                # The model cache must remain valid until this field's own save() calls
                # get_model() below (to contribute the new field and alter the DB table).
                CustomObjectType.objects.using(schema_conn.alias).filter(
                    pk=self.custom_object_type_id
                ).update(next_schema_id=new_schema_id)
                self.schema_id = new_schema_id

        field_type = FIELD_TYPE_CLASS[self.type]()
        model = self.custom_object_type.get_model()

        with schema_conn.schema_editor() as schema_editor:
            if self._state.adding:
                if self.is_polymorphic:
                    # Polymorphic Object: add content_type + object_id columns + index.
                    # Polymorphic MultiObject: create through table with content_type + object_id.
                    if self.type == CustomFieldTypeChoices.TYPE_OBJECT:
                        field_type.add_polymorphic_object_columns(self, model, schema_editor)
                    elif self.type == CustomFieldTypeChoices.TYPE_MULTIOBJECT:
                        field_type.create_polymorphic_m2m_table(self, model, schema_editor)
                else:
                    _schema_add_field(self, model, schema_editor, schema_conn)
                    _apply_deferred_co_field(self)
            else:
                # Polymorphic fields: renames and type changes are rejected by clean().
                # Non-schema attributes (label, description, …) may still change here.
                # If clean() was bypassed and a rename slipped through, raise rather
                # than silently leaving DB columns / through table out of sync.
                if self.is_polymorphic or self._original_is_polymorphic:
                    if self.name != self._original_name:
                        raise ValidationError(
                            {"name": _("Cannot rename a polymorphic field after creation.")}
                        )
                else:
                    _schema_alter_field(self.original, self, model, schema_editor, schema_conn)

        # When the field is renamed, update ObjectChange / ChangeDiff JSON keys so
        # historical audit records and branch diffs stay consistent with the new
        # name.  Combined with the netbox-branching attr translator registered
        # in branching.translate_renamed_field_attr, this lets later replays
        # (whether iterative undo or squash undo) resolve any data key — old or
        # new name — to the field's current name on the model.
        if (
            not self._state.adding
            and not self.is_polymorphic
            and self._original_name != self.name
        ):
            _rename_objectchange_field_key(self, self._original_name, self.name)

        # Ensure FK constraints are properly created for OBJECT fields
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

        # Clear and refresh the model cache for this CustomObjectType when a field is modified
        self.custom_object_type.clear_model_cache(self.custom_object_type.id)

        # Update parent's cache_timestamp to invalidate cache across all workers.
        # snapshot() must be called first so that change logging has a correct pre-state;
        # without it, diff()['pre'] would set ALL fields to None during branch revert.
        self.custom_object_type.snapshot()
        self.custom_object_type.save(update_fields=['cache_timestamp'])

        super().save(*args, **kwargs)

        # Ensure FK constraints AFTER the transaction commits to avoid "pending trigger events" errors
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

        # On rename, ``_schema_alter_field`` calls contribute_to_class for both
        # the old and new field names on the same model class, leaving the
        # _meta in a corrupt state.  Force a no_cache regeneration so the
        # next access sees a clean model.  This regeneration ALSO triggers
        # apps.clear_cache() at the end of get_model(), which cascades into
        # AppConfig.get_models() and re-caches every COT — but on rename
        # that's tolerable because the rename doesn't affect related COTs'
        # FK reverse relations.
        #
        # For non-rename changes (new field, attribute toggle, etc.) the
        # cache_timestamp bump done above is sufficient: the next get_model()
        # call detects the timestamp mismatch and regenerates lazily.  We
        # skip both the regeneration AND the search-index re-register here
        # so that the cache invalidations performed by signal handlers — most
        # importantly the eviction of related COTs in ``clear_cache_on_field_save``
        # for an OBJECT-type field — are not undone by the apps.clear_cache()
        # cascade re-caching every COT in sight.
        renamed = (
            not self._state.adding
            and not self.is_polymorphic
            and self._original_name != self.name
        )
        if renamed:
            updated_model = self.custom_object_type.get_model(no_cache=True)
            self.custom_object_type.register_custom_object_search_index(updated_model)

        # Reindex all objects of this type if search indexing was affected
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
        # Use the branch connection when operating inside a branch.
        schema_conn = _get_schema_connection()

        with schema_conn.schema_editor() as schema_editor:
            if self.is_polymorphic:
                if self.type == CustomFieldTypeChoices.TYPE_OBJECT:
                    field_type.remove_polymorphic_object_columns(self, model, schema_editor)
                elif self.type == CustomFieldTypeChoices.TYPE_MULTIOBJECT:
                    field_type.drop_polymorphic_m2m_table(self, model, schema_editor)
            else:
                _schema_remove_field(self, model, schema_editor)

        # Clear the model cache for this CustomObjectType when a field is deleted
        self.custom_object_type.clear_model_cache(self.custom_object_type.id)

        # Update parent's cache_timestamp to invalidate cache across all workers.
        # snapshot() must be called first so that change logging has a correct pre-state.
        self.custom_object_type.snapshot()
        self.custom_object_type.save(update_fields=['cache_timestamp'])

        super().delete(*args, **kwargs)

        # Regenerate and re-register the model so the app registry no longer includes
        # the removed field.  During squash revert the squash strategy may try to query
        # CO rows (model.objects.get(pk=...)) after undoing this field but before undoing
        # the CO itself.  If the stale model class is still in the app registry it will
        # include the now-absent column in its SELECT, causing ProgrammingError.
        updated_model = self.custom_object_type.get_model()

        # Reregister SearchIndex with new set of searchable fields
        self.custom_object_type.register_custom_object_search_index(updated_model)

        # Reindex all objects of this type since a searchable field was removed
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
