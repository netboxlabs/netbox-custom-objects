import decimal
import json
import re
import uuid
from datetime import date, datetime

import django_filters
from core.models.contenttypes import ObjectTypeManager
from django import forms
from django.apps import apps
from django.conf import settings
from django.contrib.contenttypes.management import create_contenttypes
from django.contrib.contenttypes.models import ContentType
from django.core.validators import RegexValidator, ValidationError
from django.db import connection, models
from django.db.models import F, Func, Q, Value
from django.db.models.expressions import RawSQL
from django.urls import reverse
from django.utils.html import escape
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _
from extras.choices import (CustomFieldFilterLogicChoices,
                            CustomFieldTypeChoices,
                            CustomFieldUIEditableChoices,
                            CustomFieldUIVisibleChoices)
from extras.constants import CUSTOMFIELD_EMPTY_VALUES
# from .choices import MappingFieldTypeChoices
from extras.models.customfields import SEARCH_TYPES
from netbox.models import ChangeLoggedModel, NetBoxModel
from netbox.models.features import (BookmarksMixin, ChangeLoggingMixin,
                                    CloningMixin, CustomFieldsMixin,
                                    CustomLinksMixin, CustomValidationMixin,
                                    EventRulesMixin, ExportTemplatesMixin,
                                    JournalingMixin, NotificationsMixin,
                                    TagsMixin)
from netbox.registry import registry
from utilities import filters
from utilities.datetime import datetime_from_timestamp
from utilities.forms.fields import (CSVChoiceField, CSVModelChoiceField,
                                    CSVModelMultipleChoiceField,
                                    CSVMultipleChoiceField, DynamicChoiceField,
                                    DynamicModelChoiceField,
                                    DynamicModelMultipleChoiceField,
                                    DynamicMultipleChoiceField, JSONField,
                                    LaxURLField)
from utilities.forms.utils import add_blank_choice
from utilities.forms.widgets import (APISelect, APISelectMultiple, DatePicker,
                                     DateTimePicker)
from utilities.querysets import RestrictedQuerySet
from utilities.string import title
from utilities.templatetags.builtins.filters import render_markdown
from utilities.validators import validate_regex

from netbox_custom_objects.constants import APP_LABEL
from netbox_custom_objects.field_types import FIELD_TYPE_CLASS
from netbox_custom_objects.utilities import AppsProxy

USER_TABLE_DATABASE_NAME_PREFIX = "custom_objects_"

class CustomObject(
    # BookmarksMixin,
    ChangeLoggingMixin,
    # CloningMixin,
    # CustomLinksMixin,
    # CustomValidationMixin,
    # ExportTemplatesMixin,
    # JournalingMixin,
    # NotificationsMixin,
    TagsMixin,
    # EventRulesMixin,
    models.Model,
):
    objects = RestrictedQuerySet.as_manager()


class CustomObjectType(NetBoxModel):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    schema = models.JSONField(blank=True, default=dict)
    verbose_name_plural = models.CharField(max_length=100, blank=True)

    class Meta:
        verbose_name = "Custom Object Type"
        ordering = ("name",)

    def __str__(self):
        return self.name

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

    def create_proxy_model(
        self, model_name, base_model, extra_fields=None, meta_options=None
    ):
        """Creates a dynamic proxy model."""
        name = f"{model_name}Proxy"

        attrs = {"__module__": base_model.__module__}
        if extra_fields:
            attrs.update(extra_fields)

        meta_attrs = {"proxy": True, "app_label": base_model._meta.app_label}
        if meta_options:
            meta_attrs.update(meta_options)

        attrs["Meta"] = type("Meta", (), meta_attrs)
        attrs["objects"] = ProxyManager(custom_object_type=self)

        proxy_model = type(name, (base_model,), attrs)
        return proxy_model

    @classmethod
    def get_table_model_name(cls, table_id):
        return f"Table{table_id}Model"

    @property
    def content_type(self):
        return ContentType.objects.get(
            app_label=APP_LABEL, model=self.get_table_model_name(self.id).lower()
        )

    def _fetch_and_generate_field_attrs(
        self,
        fields,
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
            # field_type = field_type_registry.get_by_model(field)
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
                # db_column=field.db_column,
                # verbose_name=field.name,
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

    def get_model(
        self,
        fields=None,
        manytomany_models=None,
        app_label=None,
    ):
        """
        Generates a temporary Django model based on available fields that belong to
        this table.

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
        :return: The generated model.
        :rtype: Model
        """

        if app_label is None:
            app_label = str(uuid.uuid4()) + "_database_table"

        model_name = self.get_table_model_name(self.pk)

        if fields is None:
            fields = []

        # TODO: Add other fields with "index" specified
        indexes = [
            models.Index(
                fields=["id"],
                name=self.get_collision_safe_order_id_idx_name(),
            )
        ]

        apps = AppsProxy(manytomany_models, app_label)
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
                # "verbose_name": self.get_verbose_name(),
                "verbose_name_plural": self.get_verbose_name_plural(),
            },
        )

        def __str__(self):
            # Find the field with primary=True and return that field's "name" as the name of the object
            primary_field = self._field_objects.get(self._primary_field_id, None)
            primary_field_value = None
            if primary_field:
                primary_field_value = getattr(self, primary_field["name"])
            if not primary_field_value:
                return f"unnamed row {self.id}"
            return str(primary_field_value) or str(self.id)

        def get_absolute_url(self):
            return reverse(
                "plugins:netbox_custom_objects:customobject",
                kwargs={
                    "pk": self.pk,
                    "custom_object_type": self.custom_object_type.name.lower(),
                },
            )

        attrs = {
            "Meta": meta,
            "__module__": "database.models",
            # An indication that the model is a generated table model.
            "_generated_table_model": True,
            "custom_object_type": self,
            "custom_object_type_id": self.id,
            "dynamic_models": apps.dynamic_models,
            # We are using our own table model manager to implement some queryset
            # helpers.
            # "objects": models.Manager(),
            "objects": RestrictedQuerySet.as_manager(),
            # "objects_and_trash": TableModelTrashAndObjectsManager(),
            "__str__": __str__,
            "get_absolute_url": get_absolute_url,
        }

        field_attrs = self._fetch_and_generate_field_attrs(fields)
        # field_attrs["name"] = models.CharField(max_length=100, unique=True)

        attrs.update(**field_attrs)

        # Create the model class.
        model = type(
            str(model_name),
            (CustomObject,),
            attrs,
        )

        # patch_meta_get_field(model._meta)

        if not manytomany_models:
            self._after_model_generation(attrs, model)

        return model

    def create_model(self):
        model = self.get_model()
        apps.register_model(APP_LABEL, model)
        app_config = apps.get_app_config(APP_LABEL)
        create_contenttypes(app_config)

        with connection.schema_editor() as schema_editor:
            schema_editor.create_model(model)

    def save(self, *args, **kwargs):
        # needs_db_create = self.pk is None
        needs_db_create = self._state.adding
        super().save(*args, **kwargs)
        if needs_db_create:
            self.create_model()

    def delete(self, *args, **kwargs):
        model = self.get_model()
        # self.content_type.delete()
        ContentType.objects.get(
            app_label=APP_LABEL, model=self.get_table_model_name(self.id).lower()
        ).delete()
        super().delete(*args, **kwargs)
        with connection.schema_editor() as schema_editor:
            schema_editor.delete_model(model)


class ProxyManager(models.Manager):
    custom_object_type = None

    def __init__(self, *args, **kwargs):
        self.custom_object_type = kwargs.pop("custom_object_type", None)
        super().__init__(*args, **kwargs)

    # TODO: make this a RestrictedQuerySet
    # def restrict(self, user, action='view'):
    #     queryset = super().restrict(user, action=action)
    #     return queryset.filter(custom_object_type=self.custom_object_type)

    def get_queryset(self):
        return super().get_queryset().filter(custom_object_type=self.custom_object_type)


class CustomObjectTypeField(CloningMixin, ExportTemplatesMixin, ChangeLoggedModel):
    custom_object_type = models.ForeignKey(
        CustomObjectType, on_delete=models.CASCADE, related_name="fields"
    )
    type = models.CharField(
        verbose_name=_("type"),
        max_length=50,
        choices=CustomFieldTypeChoices,
        default=CustomFieldTypeChoices.TYPE_TEXT,
        help_text=_("The type of data this custom field holds"),
    )
    primary = models.BooleanField(
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
        help_text=_("Internal field name"),
        validators=(
            RegexValidator(
                regex=r"^[a-z0-9_]+$",
                message=_("Only alphanumeric characters and underscores are allowed."),
                flags=re.IGNORECASE,
            ),
            RegexValidator(
                regex=r"__",
                message=_(
                    "Double underscores are not permitted in custom field names."
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
            "Name of the field as displayed to users (if not provided, 'the field's name will be used)"
        ),
    )
    group_name = models.CharField(
        verbose_name=_("group name"),
        max_length=50,
        blank=True,
        help_text=_("Custom fields within the same group will be displayed together"),
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
        default=1000,
        help_text=_(
            "Weighting for search. Lower values are considered more important. Fields with a search weight of zero "
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

    clone_fields = (
        'custom_object_type',
    )

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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Cache instance's original name so we can check later whether it has changed
        self._name = self.__dict__.get("name")

    @property
    def search_type(self):
        return SEARCH_TYPES.get(self.type)

    @property
    def choices(self):
        if self.choice_set:
            return self.choice_set.choices
        return []

    def get_ui_visible_color(self):
        return CustomFieldUIVisibleChoices.colors.get(self.ui_visible)

    def get_ui_editable_color(self):
        return CustomFieldUIEditableChoices.colors.get(self.ui_editable)

    def get_choice_label(self, value):
        if not hasattr(self, "_choice_map"):
            self._choice_map = dict(self.choices)
        return self._choice_map.get(value, value)

    def populate_initial_data(self, content_types):
        """
        Populate initial custom field data upon either a) the creation of a new CustomField, or
        b) the assignment of an existing CustomField to new object types.
        """
        if self.default is None:
            # We have to convert None to a JSON null for jsonb_set()
            value = RawSQL("'null'::jsonb", [])
        else:
            value = Value(self.default, models.JSONField())
        for ct in content_types:
            ct.model_class().objects.update(
                custom_field_data=Func(
                    F("custom_field_data"),
                    Value([self.name]),
                    value,
                    function="jsonb_set",
                )
            )

    def remove_stale_data(self, content_types):
        """
        Delete custom field data which is no longer relevant (either because the CustomField is
        no longer assigned to a model, or because it has been deleted).
        """
        for ct in content_types:
            if model := ct.model_class():
                model.objects.update(
                    custom_field_data=F("custom_field_data") - self.name
                )

    def rename_object_data(self, old_name, new_name):
        """
        Called when a CustomField has been renamed. Removes the original key and inserts the new
        one, copying the value of the old key.
        """
        for ct in self.object_types.all():
            ct.model_class().objects.update(
                custom_field_data=Func(
                    F("custom_field_data") - old_name,
                    Value([new_name]),
                    Func(
                        F("custom_field_data"),
                        function="jsonb_extract_path_text",
                        template=f"to_jsonb(%(expressions)s -> '{old_name}')",
                    ),
                    function="jsonb_set",
                )
            )

    def clean(self):
        super().clean()

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

    def to_form_field(
        self,
        set_initial=True,
        enforce_required=True,
        enforce_visibility=True,
        for_csv_import=False,
    ):
        # TODO: Move all this logic to field_types.py get_form_field methods
        """
        Return a form field suitable for setting a CustomField's value for an object.

        set_initial: Set initial data for the field. This should be False when generating a field for bulk editing.
        enforce_required: Honor the value of CustomField.required. Set to False for filtering/bulk editing.
        enforce_visibility: Honor the value of CustomField.ui_visible. Set to False for filtering.
        for_csv_import: Return a form field suitable for bulk import of objects in CSV format.
        """
        initial = self.default if set_initial else None
        required = self.required if enforce_required else False

        # Integer
        if self.type == CustomFieldTypeChoices.TYPE_INTEGER:
            field = forms.IntegerField(
                required=required,
                initial=initial,
                min_value=self.validation_minimum,
                max_value=self.validation_maximum,
            )

        # Decimal
        elif self.type == CustomFieldTypeChoices.TYPE_DECIMAL:
            field = forms.DecimalField(
                required=required,
                initial=initial,
                max_digits=12,
                decimal_places=4,
                min_value=self.validation_minimum,
                max_value=self.validation_maximum,
            )

        # Boolean
        elif self.type == CustomFieldTypeChoices.TYPE_BOOLEAN:
            choices = (
                (None, "---------"),
                (True, _("True")),
                (False, _("False")),
            )
            field = forms.NullBooleanField(
                required=required, initial=initial, widget=forms.Select(choices=choices)
            )

        # Date
        elif self.type == CustomFieldTypeChoices.TYPE_DATE:
            field = forms.DateField(
                required=required, initial=initial, widget=DatePicker()
            )

        # Date & time
        elif self.type == CustomFieldTypeChoices.TYPE_DATETIME:
            field = forms.DateTimeField(
                required=required, initial=initial, widget=DateTimePicker()
            )

        # Select
        elif self.type in (
            CustomFieldTypeChoices.TYPE_SELECT,
            CustomFieldTypeChoices.TYPE_MULTISELECT,
        ):
            choices = self.choice_set.choices
            default_choice = self.default if self.default in self.choices else None

            if not required or default_choice is None:
                choices = add_blank_choice(choices)

            # Set the initial value to the first available choice (if any)
            if set_initial and default_choice:
                initial = default_choice

            if for_csv_import:
                if self.type == CustomFieldTypeChoices.TYPE_SELECT:
                    field_class = CSVChoiceField
                else:
                    field_class = CSVMultipleChoiceField
                field = field_class(choices=choices, required=required, initial=initial)
            else:
                if self.type == CustomFieldTypeChoices.TYPE_SELECT:
                    field_class = DynamicChoiceField
                    widget_class = APISelect
                else:
                    field_class = DynamicMultipleChoiceField
                    widget_class = APISelectMultiple
                field = field_class(
                    choices=choices,
                    required=required,
                    initial=initial,
                    widget=widget_class(
                        api_url=f"/api/extras/custom-field-choice-sets/{self.choice_set.pk}/choices/"
                    ),
                )

        # URL
        elif self.type == CustomFieldTypeChoices.TYPE_URL:
            field = LaxURLField(
                assume_scheme="https", required=required, initial=initial
            )

        # JSON
        elif self.type == CustomFieldTypeChoices.TYPE_JSON:
            field = JSONField(
                required=required, initial=json.dumps(initial) if initial else None
            )

        # Object
        elif self.type == CustomFieldTypeChoices.TYPE_OBJECT:
            model = self.related_object_type.model_class()
            if not model:
                custom_object_model_name = self.related_object_type.name
                custom_object_type_id = custom_object_model_name.replace(
                    "table", ""
                ).replace("model", "")
                custom_object_type = CustomObjectType.objects.get(
                    pk=custom_object_type_id
                )
                model = custom_object_type.get_model()
            field_class = (
                CSVModelChoiceField if for_csv_import else DynamicModelChoiceField
            )
            kwargs = {
                "queryset": model.objects.all(),
                "required": required,
                "initial": initial,
            }
            if not for_csv_import:
                kwargs["query_params"] = self.related_object_filter
                kwargs["selector"] = True

            field = field_class(**kwargs)

        # Multiple objects
        elif self.type == CustomFieldTypeChoices.TYPE_MULTIOBJECT:
            model = self.related_object_type.model_class()
            field_class = (
                CSVModelMultipleChoiceField
                if for_csv_import
                else DynamicModelMultipleChoiceField
            )
            kwargs = {
                "queryset": model.objects.all(),
                "required": required,
                "initial": initial,
            }
            if not for_csv_import:
                kwargs["query_params"] = self.related_object_filter
                kwargs["selector"] = True

            field = field_class(**kwargs)

        # Text
        else:
            widget = (
                forms.Textarea
                if self.type == CustomFieldTypeChoices.TYPE_LONGTEXT
                else None
            )
            field = forms.CharField(required=required, initial=initial, widget=widget)
            if self.validation_regex:
                field.validators = [
                    RegexValidator(
                        regex=self.validation_regex,
                        message=mark_safe(
                            _(
                                "Values must match this regex: <code>{regex}</code>"
                            ).format(regex=escape(self.validation_regex))
                        ),
                    )
                ]

        field.model = self
        field.label = str(self)
        if self.description:
            field.help_text = render_markdown(self.description)

        # Annotate read-only fields
        if enforce_visibility and self.ui_editable != CustomFieldUIEditableChoices.YES:
            field.disabled = True

        return field

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
        # apps.register_model(APP_LABEL, model)
        with connection.schema_editor() as schema_editor:
            if self._state.adding:
                schema_editor.add_field(model, model_field)
                if self.type == CustomFieldTypeChoices.TYPE_MULTIOBJECT:
                    field_type.create_m2m_table(self, model, self.name)
            else:
                old_field = field_type.get_model_field(self.original)
                old_field.contribute_to_class(model, self.name)
                schema_editor.alter_field(model, old_field, model_field)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        field_type = FIELD_TYPE_CLASS[self.type]()
        model_field = field_type.get_model_field(self)
        model = self.custom_object_type.get_model()
        model_field.contribute_to_class(model, self.name)
        # apps.register_model(APP_LABEL, model)
        with connection.schema_editor() as schema_editor:
            if self.type == CustomFieldTypeChoices.TYPE_MULTIOBJECT:
                apps = model._meta.apps
                through_model = apps.get_model(APP_LABEL, self.through_model_name)
                schema_editor.delete_model(through_model)
            schema_editor.remove_field(model, model_field)

        super().delete(*args, **kwargs)


class CustomObjectObjectTypeManager(ObjectTypeManager):

    def public(self):
        """
        Filter the base queryset to return only ContentTypes corresponding to "public" models; those which are listed
        in registry['models'] and intended for reference by other objects.
        """
        q = Q()
        for app_label, models in registry["models"].items():
            q |= Q(app_label=app_label, model__in=models)
        # Add CTs of custom object models, but not the "through" tables
        q |= Q(app_label=APP_LABEL)
        return (
            self.get_queryset()
            .filter(q)
            .exclude(app_label=APP_LABEL, model__startswith="through")
        )


class CustomObjectObjectType(ContentType):
    """
    Wrap Django's native ContentType model to use our custom manager.
    """

    objects = CustomObjectObjectTypeManager()

    class Meta:
        proxy = True
