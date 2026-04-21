import contextvars
import decimal
import re
import threading
from datetime import date, datetime

import django_filters
from core.models import ObjectType, ObjectChange
from core.models.object_types import ObjectTypeManager
from django.apps import apps
from django.conf import settings

# from django.contrib.contenttypes.management import create_contenttypes
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import FieldDoesNotExist
from django.core.validators import RegexValidator, ValidationError
from django.db import connection, IntegrityError, models, transaction
from django.db.models import Q
from django.db.models.functions import Lower
from django.db.models.signals import pre_delete, post_save
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
from utilities.datetime import datetime_from_timestamp
from utilities.object_types import object_type_name
from utilities.querysets import RestrictedQuerySet
from utilities.string import title
from utilities.validators import validate_regex

from netbox_custom_objects.constants import APP_LABEL, RESERVED_FIELD_NAMES
from netbox_custom_objects.field_types import FIELD_TYPE_CLASS
from netbox_custom_objects.jobs import ReindexCustomObjectTypeJob
from netbox_custom_objects.utilities import _suppress_clear_cache, generate_model


class UniquenessConstraintTestError(Exception):
    """Custom exception used to signal successful uniqueness constraint test."""

    pass


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
            from django.db import connections
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
    from extras.choices import CustomFieldTypeChoices

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
            value = entry['data'].get(data_key)
            if value is None:
                continue
            # table_name comes from get_database_table_name() (controlled by our
            # code) and col_name from field.name, which is validated by the
            # ^[a-z0-9_]+$ regex — no double-quote characters are possible, so
            # the f-string interpolation is safe against SQL injection here.
            cursor.execute(
                f'UPDATE "{table_name}" SET "{col_name}" = %s WHERE id = %s',
                [value, co_pk],
            )


def _schema_add_field(fi, model, schema_editor, schema_conn):
    """
    Issue ``add_field`` against the physical schema for *fi*.

    Handles through-table creation for MULTIOBJECT fields.  Does NOT apply
    deferred CO field data — callers that need that (squash merge context) must
    call ``_apply_deferred_co_field(fi)`` separately after this returns.
    """
    ft = FIELD_TYPE_CLASS[fi.type]()
    mf = ft.get_model_field(fi)
    mf.contribute_to_class(model, fi.name)
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
    """
    old_mf = FIELD_TYPE_CLASS[old_fi.type]().get_model_field(old_fi)
    new_mf = FIELD_TYPE_CLASS[new_fi.type]().get_model_field(new_fi)
    old_mf.contribute_to_class(model, old_fi.name)
    new_mf.contribute_to_class(model, new_fi.name)

    if (
        new_fi.type == CustomFieldTypeChoices.TYPE_MULTIOBJECT
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
        from utilities.serialization import deserialize_object as _deserialize
        from netbox_custom_objects.utilities import extract_cot_id_from_model_name

        # Derive the COT primary key from the model class name (e.g. 'Table1Model' → 1)
        cot_id_str = extract_cot_id_from_model_name(cls.__name__.lower())
        if cot_id_str is None:
            # Not a generated model name — fall back to standard deserialization.
            return _deserialize(cls, data, pk=pk)
        cot_id = int(cot_id_str)  # regex guarantees digits-only

        # Refresh the model cache so we pick up any fields already applied to main.
        # (In the squash case the cache may still point to a zero-field model.)
        from netbox_custom_objects.models import CustomObjectType as _COT  # noqa: F401
        _COT.clear_model_cache(cot_id)
        try:
            cot = _COT.objects.get(pk=cot_id)
            fresh_model = cot.get_model()
        except _COT.DoesNotExist:
            fresh_model = cls

        inner = _deserialize(fresh_model, data, pk=pk)
        obj = inner.object
        table_name = fresh_model._meta.db_table
        full_data = dict(data)

        class _Deserialized:
            object = obj

            def save(self, using=None, **_kwargs):
                from django.db import DEFAULT_DB_ALIAS
                _using = using or DEFAULT_DB_ALIAS
                models.Model.save_base(obj, using=_using, raw=True)
                # Read pk after save_base so that auto-assigned PKs are captured.
                # (If pk was None before save_base, obj.pk is now the DB-assigned id.)
                obj_pk = obj.pk
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


class CustomObjectType(NetBoxModel):
    # Class-level cache for generated models
    _model_cache = {}
    _through_model_cache = (
        {}
    )  # Now stores {custom_object_type_id: {through_model_name: through_model}}
    _model_cache_locks = {}  # Per-model locks to prevent race conditions
    _global_lock = threading.RLock()  # Global lock for managing per-model locks
    name = models.CharField(
        max_length=100,
        unique=True,
        validators=(
            RegexValidator(
                regex=r"^[a-z0-9_]+$",
                message=_("Only lowercase alphanumeric characters and underscores are allowed."),
            ),
            RegexValidator(
                regex=r"__",
                message=_(
                    "Double underscores are not permitted in custom object object type names."
                ),
                flags=re.IGNORECASE,
                inverse_match=True,
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
    version = models.CharField(max_length=10, blank=True)
    verbose_name = models.CharField(max_length=100, blank=True)
    verbose_name_plural = models.CharField(max_length=100, blank=True)
    slug = models.SlugField(max_length=100, unique=True, db_index=True, blank=False)
    group_name = models.CharField(
        max_length=100,
        db_index=True,
        blank=True,
        help_text=_("Used to group similar custom object types in the navigation menu")
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

    @classmethod
    def clear_model_cache(cls, custom_object_type_id=None):
        """
        Clear the model cache for a specific CustomObjectType or all models.

        :param custom_object_type_id: ID of the CustomObjectType to clear cache for, or None to clear all
        """
        with cls._global_lock:
            if custom_object_type_id is not None:
                cls._model_cache.pop(custom_object_type_id, None)
                cls._through_model_cache.pop(custom_object_type_id, None)
                cls._model_cache_locks.pop(custom_object_type_id, None)
            else:
                cls._model_cache.clear()
                cls._through_model_cache.clear()
                cls._model_cache_locks.clear()

        # Clear Django apps registry cache to ensure newly created models are recognized
        apps.get_models.cache_clear()

    @classmethod
    def get_cached_model(cls, custom_object_type_id):
        """
        Get a cached model for a specific CustomObjectType if it exists.

        :param custom_object_type_id: ID of the CustomObjectType
        :return: The cached model or None if not found
        """
        cache_entry = cls._model_cache.get(custom_object_type_id)
        if cache_entry:
            # Cache stores (model, timestamp) tuples
            return cache_entry[0]
        return None

    @classmethod
    def get_cached_timestamp(cls, custom_object_type_id):
        """
        Get the timestamp of a cached model for a specific CustomObjectType.

        :param custom_object_type_id: ID of the CustomObjectType
        :return: The cached timestamp or None if not found
        """
        cache_entry = cls._model_cache.get(custom_object_type_id)
        if cache_entry:
            # Cache stores (model, timestamp) tuples
            return cache_entry[1]
        return None

    @classmethod
    def is_model_cached(cls, custom_object_type_id):
        """
        Check if a model is cached for a specific CustomObjectType.

        :param custom_object_type_id: ID of the CustomObjectType
        :return: True if the model is cached, False otherwise
        """
        return custom_object_type_id in cls._model_cache

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

            field_attrs[field.name] = field_type.get_model_field(
                field,
            )

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

        # Collect through models during after_model_generation
        through_models = []

        for field_object in all_field_objects.values():
            field_name = field_object["name"]

            # Skip fields that were skipped due to recursion
            if field_name in skipped_fields:
                continue

            # Only process fields that actually exist on the model
            # Fields might be skipped due to recursion prevention
            if hasattr(model._meta, 'get_field'):
                try:
                    field = model._meta.get_field(field_name)
                    # Field exists, process it
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

                except Exception:
                    # Field doesn't exist (likely skipped due to recursion), skip processing
                    continue

        # Store through models on the model for yielding in get_models()
        model._through_models = through_models

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
        fields = []
        for field in self.fields.filter(search_weight__gt=0):
            # Only index fields that are actually present on the generated model.
            # When the model was built with skip_object_fields=True (a lightweight
            # stub used to break cross-COT FK recursion), object-type fields are
            # intentionally absent.  Including them in the index causes
            # FieldDoesNotExist / AttributeError in the post_save search handler.
            try:
                model._meta.get_field(field.name)
            except FieldDoesNotExist:
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

        with self._global_lock:
            if self.is_model_cached(self.id) and not no_cache:
                cached_timestamp = self.get_cached_timestamp(self.id)
                # Only use cache if the timestamps are available and match
                if cached_timestamp and self.cache_timestamp and cached_timestamp == self.cache_timestamp:
                    model = self.get_cached_model(self.id)
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
            if model_name.lower() in apps.all_models[APP_LABEL]:
                # Remove the existing model from all_models before registering the new one
                del apps.all_models[APP_LABEL][model_name.lower()]

            apps.register_model(APP_LABEL, model)

            self._after_model_generation(attrs, model)

            # Cache the generated model with its timestamp (protected by lock for thread safety)
            with self._global_lock:
                self._model_cache[self.id] = (model, self.cache_timestamp)

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

    def _ensure_field_fk_constraint(self, model, field_name):
        """
        Ensure that a foreign key constraint is properly created at the database level
        for a specific OBJECT type field with ON DELETE CASCADE. This is necessary because
        models are created with managed=False, which may not properly create FK constraints
        with CASCADE behavior.

        :param model: The model containing the field
        :param field_name: The name of the field to ensure FK constraint for
        """
        table_name = self.get_database_table_name()

        # Get the model field
        try:
            model_field = model._meta.get_field(field_name)
        except Exception:
            return

        if not (hasattr(model_field, 'remote_field') and model_field.remote_field):
            return

        # Get the referenced table
        related_model = model_field.remote_field.model
        related_table = related_model._meta.db_table
        column_name = model_field.column

        with _get_schema_connection().cursor() as cursor:
            # Drop existing FK constraint if it exists
            # Query for existing constraints
            cursor.execute("""
                SELECT constraint_name
                FROM information_schema.table_constraints
                WHERE table_name = %s
                AND constraint_type = 'FOREIGN KEY'
                AND constraint_name LIKE %s
            """, [table_name, f"%{column_name}%"])

            for row in cursor.fetchall():
                constraint_name = row[0]
                cursor.execute(f'ALTER TABLE "{table_name}" DROP CONSTRAINT IF EXISTS "{constraint_name}"')

            # Create new FK constraint with ON DELETE CASCADE.
            # Not DEFERRABLE: a deferred constraint leaves pending trigger events that block
            # subsequent ALTER TABLE calls (e.g. during branch revert remove_field).
            constraint_name = f"{table_name}_{column_name}_fk_cascade"
            cursor.execute(f"""
                ALTER TABLE "{table_name}"
                ADD CONSTRAINT "{constraint_name}"
                FOREIGN KEY ("{column_name}")
                REFERENCES "{related_table}" ("id")
                ON DELETE CASCADE
            """)

    def _ensure_all_fk_constraints(self, model):
        """
        Ensure that foreign key constraints are properly created at the database level
        for ALL OBJECT type fields with ON DELETE CASCADE.

        :param model: The model to ensure FK constraints for
        """
        # Query all OBJECT type fields for this CustomObjectType
        object_fields = self.fields.filter(type=CustomFieldTypeChoices.TYPE_OBJECT)

        for field in object_fields:
            self._ensure_field_fk_constraint(model, field.name)

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
        from utilities.serialization import deserialize_object as _deserialize

        inner = _deserialize(cls, data, pk=pk)

        class _SchemaAwareDeserialized:
            def __init__(self, deserialized):
                self._inner = deserialized
                self.object = deserialized.object

            def save(self, using=None, **kwargs):
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

    def has_branch_schema_drift(self, branch) -> bool:
        """
        Return True if this custom object type's physical schema in *branch*
        differs from the current field definitions in main.

        Drift means at least one of:
        - A field exists in main but not in the branch snapshot (needs add_field)
        - A field exists in the branch snapshot but not in main (needs remove_field)
        - A field exists in both but has a different name, type, or constraint
          that affects the DB column (needs alter_field)

        Returns False when netbox-branching is not installed.
        """
        try:
            from netbox_branching.utilities import activate_branch
        except ImportError:
            return False

        from netbox_custom_objects.branching import _field_schema_key

        main_fields = {f.pk: f for f in self.fields.all()}
        with activate_branch(branch):
            branch_fields = {f.pk: f for f in self.fields.all()}

        main_pks = set(main_fields)
        branch_pks = set(branch_fields)

        if main_pks != branch_pks:
            return True

        return any(
            _field_schema_key(branch_fields[pk]) != _field_schema_key(main_fields[pk])
            for pk in main_pks
        )

    def save(self, *args, **kwargs):
        needs_db_create = self._state.adding

        super().save(*args, **kwargs)

        if needs_db_create:
            self.create_model()
        else:
            # Clear the model cache when the CustomObjectType is modified
            self.clear_model_cache(self.id)

    def delete(self, *args, **kwargs):
        # Clear the model cache for this CustomObjectType
        self.clear_model_cache(self.id)

        model = self.get_model()
        schema_conn = _get_schema_connection()
        in_branch = schema_conn is not connection

        # Delete all CustomObjectTypeFields that reference this CustomObjectType
        for field in CustomObjectTypeField.objects.filter(related_object_type=self.object_type):
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
            object_type.delete()
            pre_delete.connect(handle_deleted_object)

        with schema_conn.schema_editor() as schema_editor:
            # Drop through tables before the main table (they have FKs pointing to it).
            for through_model in getattr(model, '_through_models', []):
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

        # Re-clear the model cache to remove re-cached model from get_model.
        self.clear_model_cache(self.id)


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
        help_text=_("The type of NetBox object this field maps to (for object fields)"),
    )
    name = models.CharField(
        verbose_name=_("name"),
        max_length=50,
        help_text=_("Internal field name, e.g. \"vendor_label\""),
        validators=(
            RegexValidator(
                regex=r"^[a-z0-9_]+$",
                message=_("Only lowercase alphanumeric characters and underscores are allowed."),
            ),
            RegexValidator(
                regex=r"__",
                message=_(
                    "Double underscores are not permitted in custom object field names."
                ),
                flags=re.IGNORECASE,
                inverse_match=True,
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
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._name = self.__dict__.get("name")
        self._original_name = self.name
        self._original_type = self.type
        self._original_related_object_type_id = self.related_object_type_id

    def __str__(self):
        return self.label or self.name.replace("_", " ").capitalize()

    @property
    def model_class(self):
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
        if self.related_object_type.app_label == APP_LABEL:
            custom_object_type_id = self.related_object_type.model.replace(
                "table", ""
            ).replace("model", "")
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

        # Check for recursion in object and multiobject fields
        if (self.type in (
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

        # Check all object and multiobject fields in this custom object type
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
            return value.pk
        if self.type == CustomFieldTypeChoices.TYPE_MULTIOBJECT:
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
            model = self.related_object_type.model_class()
            return model.objects.filter(pk=value).first()
        if self.type == CustomFieldTypeChoices.TYPE_MULTIOBJECT:
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
                if type(value) is not int:
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
                    if type(id) is not int:
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
        return f"custom_objects_{self.custom_object_type_id}_{self.name}"

    @property
    def through_model_name(self):
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
        from utilities.serialization import deserialize_object as _deserialize

        inner = _deserialize(cls, data, pk=pk)

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

        model = self.custom_object_type.get_model()

        with schema_conn.schema_editor() as schema_editor:
            if self._state.adding:
                _schema_add_field(self, model, schema_editor, schema_conn)
                _apply_deferred_co_field(self)
            else:
                _schema_alter_field(self.original, self, model, schema_editor, schema_conn)

        # Ensure FK constraints are properly created for OBJECT fields with CASCADE behavior
        should_ensure_fk = False
        if self.type == CustomFieldTypeChoices.TYPE_OBJECT:
            if self._state.adding:
                should_ensure_fk = True
            else:
                # Existing field - check if type changed to OBJECT or related_object_type changed
                type_changed_to_object = (
                    self._original_type != CustomFieldTypeChoices.TYPE_OBJECT
                    and self.type == CustomFieldTypeChoices.TYPE_OBJECT
                )
                related_object_changed = (
                    self._original_type == CustomFieldTypeChoices.TYPE_OBJECT
                    and self.related_object_type_id != self._original_related_object_type_id
                )
                should_ensure_fk = type_changed_to_object or related_object_changed

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
            def ensure_constraint():
                self.custom_object_type._ensure_field_fk_constraint(model, self.name)

            transaction.on_commit(ensure_constraint)

        # Reregister SearchIndex with new set of searchable fields
        self.custom_object_type.register_custom_object_search_index(model)

        # Reindex all objects of this type if search indexing was affected
        if is_new:
            needs_reindex = self.search_weight > 0
        else:
            needs_reindex = self.search_weight != self.original.search_weight
        if needs_reindex:
            _cot_id = self.custom_object_type_id
            transaction.on_commit(lambda: ReindexCustomObjectTypeJob.enqueue(cot_id=_cot_id))

    def delete(self, *args, **kwargs):
        # Use the branch connection when operating inside a branch.
        schema_conn = _get_schema_connection()

        model = self.custom_object_type.get_model()

        with schema_conn.schema_editor() as schema_editor:
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
        Filter the base queryset to return only ContentTypes corresponding to "public" models; those which are listed
        in registry['models'] and intended for reference by other objects.
        """
        q = Q()
        for app_label, model_list in registry["models"].items():
            q |= Q(app_label=app_label, model__in=model_list)
        # Add CTs of custom object models, but not the "through" tables
        q |= Q(app_label=APP_LABEL)
        return (
            self.get_queryset()
            .filter(q)
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


@receiver(post_save, sender=CustomObjectTypeField)
def clear_cache_on_field_save(sender, instance, **kwargs):
    """
    Clear the model cache when a CustomObjectTypeField is saved.
    This ensures the parent CustomObjectType's model is regenerated.
    """
    if instance.custom_object_type_id:
        CustomObjectType.clear_model_cache(instance.custom_object_type_id)
    for pointing_field in CustomObjectTypeField.objects.filter(
        related_object_type=instance.custom_object_type.object_type
    ):
        CustomObjectType.clear_model_cache(pointing_field.custom_object_type_id)


@receiver(pre_delete, sender=CustomObjectTypeField)
def clear_cache_on_field_delete(sender, instance, **kwargs):
    """
    Clear the model cache when a CustomObjectTypeField is deleted.
    This is in addition to the manual clear in the delete() method.
    """
    if instance.custom_object_type_id:
        CustomObjectType.clear_model_cache(instance.custom_object_type_id)
