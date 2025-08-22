import decimal
import re
import warnings
from datetime import date, datetime

import django_filters
from core.models import ObjectType, ObjectChange
from core.models.contenttypes import ObjectTypeManager
from django.apps import apps
from django.conf import settings

# from django.contrib.contenttypes.management import create_contenttypes
from django.contrib.contenttypes.models import ContentType
from django.core.validators import RegexValidator, ValidationError
from django.db import connection, IntegrityError, models, transaction
from django.db.models import Q
from django.db.models.functions import Lower
from django.db.models.signals import pre_delete
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
from netbox.models import ChangeLoggedModel, PrimaryModel
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


class UniquenessConstraintTestError(Exception):
    """Custom exception used to signal successful uniqueness constraint test."""

    pass


USER_TABLE_DATABASE_NAME_PREFIX = "custom_objects_"


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
            return f"{self.custom_object_type.name} {self.id}"
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
                "custom_object_type": self.custom_object_type.name.lower(),
            },
        )

    def get_list_url(self):
        return reverse(
            "plugins:netbox_custom_objects:customobject_list",
            kwargs={"custom_object_type": self.custom_object_type.name.lower()},
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
        kwargs["custom_object_type"] = cls.custom_object_type.name.lower()
        return reverse(cls._get_viewname(action, rest_api), kwargs=kwargs)


class CustomObjectType(PrimaryModel):
    # Class-level cache for generated models
    _model_cache = {}
    _through_model_cache = (
        {}
    )  # Now stores {custom_object_type_id: {through_model_name: through_model}}
    name = models.CharField(max_length=100, unique=True)
    schema = models.JSONField(blank=True, default=dict)
    verbose_name_plural = models.CharField(max_length=100, blank=True)

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
        return self.name

    @classmethod
    def clear_model_cache(cls, custom_object_type_id=None):
        """
        Clear the model cache for a specific CustomObjectType or all models.

        :param custom_object_type_id: ID of the CustomObjectType to clear cache for, or None to clear all
        """
        if custom_object_type_id is not None:
            cls._model_cache.pop(custom_object_type_id, None)
            cls._through_model_cache.pop(custom_object_type_id, None)
        else:
            cls._model_cache.clear()
            cls._through_model_cache.clear()

        # Clear Django apps registry cache to ensure newly created models are recognized
        apps.get_models.cache_clear()

    @classmethod
    def get_cached_model(cls, custom_object_type_id):
        """
        Get a cached model for a specific CustomObjectType if it exists.

        :param custom_object_type_id: ID of the CustomObjectType
        :return: The cached model or None if not found
        """
        return cls._model_cache.get(custom_object_type_id)

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

    @property
    def formatted_schema(self):
        result = "<ul>"
        for field_name, field in self.schema.items():
            field_type = field.get("type")
            if field_type in ["object", "multiobject"]:
                content_type = ContentType.objects.get(
                    app_label=field["app_label"], model=field["model"]
                )
                field = content_type
            result += f"<li>{field_name}: {field}</li>"
        result += "</ul>"
        return result

    def get_absolute_url(self):
        return reverse("plugins:netbox_custom_objects:customobjecttype", args=[self.pk])

    def get_list_url(self):
        return reverse(
            "plugins:netbox_custom_objects:customobject_list",
            kwargs={"custom_object_type": self.name.lower()},
        )

    @classmethod
    def get_table_model_name(cls, table_id):
        return f"Table{table_id}Model"

    @property
    def content_type(self):
        try:
            return self.get_or_create_content_type()
        except Exception:
            # If we still can't get it, return None
            return None

    def get_or_create_content_type(self):
        """
        Get or create the ObjectType for this CustomObjectType.
        This ensures the ObjectType is immediately available in the current transaction.
        """
        content_type_name = self.get_table_model_name(self.id).lower()
        try:
            return ObjectType.objects.get(app_label=APP_LABEL, model=content_type_name)
        except Exception:
            # Create the ObjectType and ensure it's immediately available
            ct = ObjectType.objects.create(app_label=APP_LABEL, model=content_type_name)
            # Force a refresh to ensure it's available in the current transaction
            ct.refresh_from_db()
            return ct

    def _fetch_and_generate_field_attrs(
        self,
        fields,
        skip_object_fields=False,
    ):
        field_attrs = {
            "_primary_field_id": -1,
            # An object containing the table fields, field types and the chosen
            # names with the table field id as key.
            "_field_objects": {},
            "_trashed_field_objects": {},
        }
        fields_query = self.fields(manager="objects").all()

        # Create a combined list of fields that must be added and belong to the this
        # table.
        fields = list(fields) + [field for field in fields_query]

        for field in fields:
            field_type = FIELD_TYPE_CLASS[field.type]()
            if skip_object_fields:
                if field.type in [CustomFieldTypeChoices.TYPE_OBJECT, CustomFieldTypeChoices.TYPE_MULTIOBJECT]:
                    continue

            field_name = field.name

            field_attrs["_field_objects"][field.id] = {
                "field": field,
                "type": field_type,
                "name": field_name,
                "custom_object_type_id": self.id,
            }
            # TODO: Add "primary" support
            if field.primary:
                field_attrs["_primary_field_id"] = field.id

            field_attrs[field.name] = field_type.get_model_field(
                field,
            )

        return field_attrs

    def _after_model_generation(self, attrs, model):
        all_field_objects = {
            **attrs["_field_objects"],
            **attrs["_trashed_field_objects"],
        }
        for field_object in all_field_objects.values():
            field_object["type"].after_model_generation(
                field_object["field"], model, field_object["name"]
            )

    def get_collision_safe_order_id_idx_name(self):
        return f"tbl_order_id_{self.id}_idx"

    def get_database_table_name(self):
        return f"{USER_TABLE_DATABASE_NAME_PREFIX}{self.id}"

    @property
    def title_case_name_plural(self):
        return title(self.name) + "s"

    def get_verbose_name(self):
        return self.name

    def get_verbose_name_plural(self):
        return self.verbose_name_plural or self.title_case_name_plural

    @staticmethod
    def get_content_type_label(custom_object_type_id):
        custom_object_type = CustomObjectType.objects.get(pk=custom_object_type_id)
        return f"Custom Objects > {custom_object_type.name}"

    def register_custom_object_search_index(self, model):
        # model must be an instance of this CustomObjectType's get_model() generated class
        fields = []
        for field in self.fields.filter(search_weight__gt=0):
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
        fields=None,
        manytomany_models=None,
        app_label=None,
        skip_object_fields=False,
        no_cache=False,
    ):
        """
        Generates a temporary Django model based on available fields that belong to
        this table. Returns cached model if available, otherwise generates and caches it.

        :param fields: Extra table field instances that need to be added the model.
        :type fields: list
        :param manytomany_models: In some cases with related fields a model has to be
            generated in order to generate that model. In order to prevent a
            recursion loop we cache the generated models and pass those along.
        :type manytomany_models: dict
        :param app_label: In some cases with related fields, the related models must
            have the same app_label. If passed along in this parameter, then the
            generated model will use that one instead of generating a unique one.
        :type app_label: Optional[String]
        :param skip_object_fields: Don't add object or multiobject fields to the model
        :type skip_object_fields: bool
        :param no_cache: Don't cache the generated model or attempt to pull from cache
        :type no_cache: bool
        :return: The generated model.
        :rtype: Model
        """

        # Check if we have a cached model for this CustomObjectType
        if self.is_model_cached(self.id) and not no_cache:
            model = self.get_cached_model(self.id)
            # Ensure the serializer is registered even for cached models
            from netbox_custom_objects.api.serializers import get_serializer_class

            get_serializer_class(model)
            return model

        if app_label is None:
            app_label = APP_LABEL

        model_name = self.get_table_model_name(self.pk)

        if fields is None:
            fields = []

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

        field_attrs = self._fetch_and_generate_field_attrs(fields, skip_object_fields=skip_object_fields)

        attrs.update(**field_attrs)

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

        # Suppress RuntimeWarning about model already being registered
        # TODO: Remove this once we have a better way to handle model registration
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore", category=RuntimeWarning, message=".*was already registered.*"
            )

            try:
                model = type(
                    str(model_name),
                    (CustomObject, models.Model),
                    attrs,
                )
            finally:
                # Restore the original method
                TM.post_through_setup = original_post_through_setup

        # Register the main model with Django's app registry
        try:
            existing_model = apps.get_model(APP_LABEL, model_name)
            # If model exists but is different, we have a problem
            if existing_model is not model:
                # Use the existing model to avoid conflicts
                model = existing_model
        except LookupError:
            apps.register_model(APP_LABEL, model)

        if not manytomany_models:
            self._after_model_generation(attrs, model)

        # Cache the generated model
        if not no_cache:
            self._model_cache[self.id] = model

        # Register the serializer for this model
        if not manytomany_models:
            from netbox_custom_objects.api.serializers import get_serializer_class

            get_serializer_class(model)

        # Register the global SearchIndex for this model
        self.register_custom_object_search_index(model)

        return model

    def create_model(self):
        # Get the model and ensure it's registered
        model = self.get_model()

        # Ensure the ContentType exists and is immediately available
        ct = self.get_or_create_content_type()
        features = get_model_features(model)
        ct.public = True
        ct.features = features
        ct.save()

        with connection.schema_editor() as schema_editor:
            schema_editor.create_model(model)

        self.register_custom_object_search_index(model)

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
        object_type = ObjectType.objects.get_for_model(model)
        ObjectChange.objects.filter(changed_object_type=object_type).delete()
        super().delete(*args, **kwargs)

        # Temporarily disconnect the pre_delete handler to skip the ObjectType deletion
        # TODO: Remove this disconnect/reconnect after ObjectType has been exempted from handle_deleted_object
        pre_delete.disconnect(handle_deleted_object)
        object_type.delete()
        with connection.schema_editor() as schema_editor:
            schema_editor.delete_model(model)
        pre_delete.connect(handle_deleted_object)


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
                message=_("Only alphanumeric characters and underscores are allowed."),
                flags=re.IGNORECASE,
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
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._name = self.__dict__.get("name")
        self._original_name = self.name

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

        # Uniqueness can not be enforced for boolean fields
        if self.unique and self.type == CustomFieldTypeChoices.TYPE_BOOLEAN:
            raise ValidationError(
                {"unique": _("Uniqueness cannot be enforced for boolean fields")}
            )

        # Check if uniqueness constraint can be applied when changing from non-unique to unique
        if (
            self.pk
            and self.unique
            and not self.original.unique
            and not self._state.adding
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

    def save(self, *args, **kwargs):
        field_type = FIELD_TYPE_CLASS[self.type]()
        model_field = field_type.get_model_field(self)
        model = self.custom_object_type.get_model()
        model_field.contribute_to_class(model, self.name)

        with connection.schema_editor() as schema_editor:
            if self._state.adding:
                schema_editor.add_field(model, model_field)
                if self.type == CustomFieldTypeChoices.TYPE_MULTIOBJECT:
                    field_type.create_m2m_table(self, model, self.name)
            else:
                old_field = field_type.get_model_field(self.original)
                old_field.contribute_to_class(model, self._original_name)

                # Special handling for MultiObject fields when the name changes
                if (
                    self.type == CustomFieldTypeChoices.TYPE_MULTIOBJECT
                    and self.name != self._original_name
                ):
                    # For renamed MultiObject fields, we just need to rename the through table
                    old_through_table_name = self.original.through_table_name
                    new_through_table_name = self.through_table_name

                    # Check if old through table exists
                    with connection.cursor() as cursor:
                        tables = connection.introspection.table_names(cursor)
                        old_table_exists = old_through_table_name in tables

                    if old_table_exists:
                        # Create temporary models to represent the old and new through table states
                        old_through_meta = type(
                            "Meta",
                            (),
                            {
                                "db_table": old_through_table_name,
                                "app_label": APP_LABEL,
                                "managed": True,
                            },
                        )
                        old_through_model = type(
                            f"TempOld{self.original.through_model_name}",
                            (models.Model,),
                            {
                                "__module__": "netbox_custom_objects.models",
                                "Meta": old_through_meta,
                                "id": models.AutoField(primary_key=True),
                                "source": models.ForeignKey(
                                    model,
                                    on_delete=models.CASCADE,
                                    db_column="source_id",
                                    related_name="+",
                                ),
                                "target": models.ForeignKey(
                                    model,
                                    on_delete=models.CASCADE,
                                    db_column="target_id",
                                    related_name="+",
                                ),
                            },
                        )

                        new_through_meta = type(
                            "Meta",
                            (),
                            {
                                "db_table": new_through_table_name,
                                "app_label": APP_LABEL,
                                "managed": True,
                            },
                        )
                        new_through_model = type(
                            f"TempNew{self.through_model_name}",
                            (models.Model,),
                            {
                                "__module__": "netbox_custom_objects.models",
                                "Meta": new_through_meta,
                                "id": models.AutoField(primary_key=True),
                                "source": models.ForeignKey(
                                    model,
                                    on_delete=models.CASCADE,
                                    db_column="source_id",
                                    related_name="+",
                                ),
                                "target": models.ForeignKey(
                                    model,
                                    on_delete=models.CASCADE,
                                    db_column="target_id",
                                    related_name="+",
                                ),
                            },
                        )
                        new_through_model  # To silence ruff error

                        # Rename the table using Django's schema editor
                        schema_editor.alter_db_table(
                            old_through_model,
                            old_through_table_name,
                            new_through_table_name,
                        )
                    else:
                        # No old table exists, create the new through table
                        field_type.create_m2m_table(self, model, self.name)

                    # Alter the field normally (this updates the field definition)
                    schema_editor.alter_field(model, old_field, model_field)
                else:
                    # Normal field alteration
                    schema_editor.alter_field(model, old_field, model_field)

        # Clear and refresh the model cache for this CustomObjectType when a field is modified
        self.custom_object_type.clear_model_cache(self.custom_object_type.id)

        super().save(*args, **kwargs)

        # Reregister SearchIndex with new set of searchable fields
        self.custom_object_type.register_custom_object_search_index(model)

    def delete(self, *args, **kwargs):
        field_type = FIELD_TYPE_CLASS[self.type]()
        model_field = field_type.get_model_field(self)
        model = self.custom_object_type.get_model()
        model_field.contribute_to_class(model, self.name)

        with connection.schema_editor() as schema_editor:
            if self.type == CustomFieldTypeChoices.TYPE_MULTIOBJECT:
                apps = model._meta.apps
                through_model = apps.get_model(APP_LABEL, self.through_model_name)
                schema_editor.delete_model(through_model)
            schema_editor.remove_field(model, model_field)

        # Clear the model cache for this CustomObjectType when a field is deleted
        self.custom_object_type.clear_model_cache(self.custom_object_type.id)

        super().delete(*args, **kwargs)

        # Reregister SearchIndex with new set of searchable fields
        self.custom_object_type.register_custom_object_search_index(model)


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
