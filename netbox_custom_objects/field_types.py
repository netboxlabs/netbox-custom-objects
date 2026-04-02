import hashlib
import json
import logging

import django_tables2 as tables
from django import forms
from django.apps import apps
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import FieldDoesNotExist
from django.core.validators import RegexValidator
from django.db import connection, models
from django.db.models.fields.related import ForeignKey, ManyToManyDescriptor
from django.db.models.manager import Manager
from django.utils.html import escape
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _
from extras.choices import CustomFieldTypeChoices, CustomFieldUIEditableChoices
from utilities.api import get_serializer_for_model
from utilities.forms.fields import (
    CSVChoiceField, CSVModelChoiceField,
    CSVModelMultipleChoiceField, CSVMultipleChoiceField,
    DynamicChoiceField, DynamicModelChoiceField,
    DynamicModelMultipleChoiceField,
    DynamicMultipleChoiceField, JSONField,
    LaxURLField,
)
from utilities.forms.utils import add_blank_choice
from utilities.forms.widgets import (
    APISelect, APISelectMultiple, DatePicker,
    DateTimePicker,
)
from utilities.templatetags.builtins.filters import linkify, render_markdown
from netbox.tables.columns import BooleanColumn

from netbox_custom_objects.constants import APP_LABEL
from netbox_custom_objects.utilities import extract_cot_id_from_model_name, generate_model

logger = logging.getLogger(__name__)

# PostgreSQL's hard limit for identifier names is 63 bytes.
_PG_MAX_IDENTIFIER_LEN = 63


def _safe_index_name(full_name: str) -> str:
    """
    Return a DB-safe index name that fits within PostgreSQL's 63-char identifier limit.

    If the full name fits, it is returned unchanged.  If it is too long, the name is
    truncated and an 8-character MD5 digest of the *full* name is appended so that
    different long names with a shared prefix do not collide.
    """
    if len(full_name) <= _PG_MAX_IDENTIFIER_LEN:
        return full_name
    digest = hashlib.md5(full_name.encode()).hexdigest()[:8]
    # Reserve 9 chars for "_" + 8-char digest; strip trailing underscores from the prefix.
    prefix = full_name[:_PG_MAX_IDENTIFIER_LEN - 9].rstrip("_")
    return f"{prefix}_{digest}"


class LazyForeignKey(ForeignKey):
    """
    A ForeignKey field that can handle lazy model references.
    The target model is resolved after the model is fully generated.
    """

    def __init__(self, to_model_name, *args, **kwargs):
        self._to_model_name = to_model_name

        # Filter out our custom parameters before passing to Django's ForeignKey
        field_kwargs = {k: v for k, v in kwargs.items() if not k.startswith('_') and k != 'generating_models'}

        super().__init__(to_model_name, *args, **field_kwargs)

    def contribute_to_class(self, cls, name, **kwargs):
        super().contribute_to_class(cls, name, **kwargs)
        # Mark this field for later resolution
        setattr(cls, f"_resolve_{name}_model", self._resolve_model)

    def _resolve_model(self, model):
        """Resolve the lazy reference to the actual model class."""
        # Get the actual model class from the app registry
        from django.apps import apps
        actual_model = apps.get_model(self._to_model_name)
        # Update the field's references
        self.remote_field.model = actual_model
        self.to = actual_model


class FieldType:

    def get_display_value(self, instance, field_name):
        """
        This value is used as the object title in the Custom Object detail view.
        """
        return getattr(instance, field_name)

    def get_model_field(self, field, **kwargs):
        raise NotImplementedError

    def get_serializer_field(self, field, **kwargs):
        raise NotImplementedError

    def get_filterform_field(self, field, **kwargs):
        raise NotImplementedError

    def get_form_field(self, field, **kwargs):
        raise NotImplementedError

    def _safe_kwargs(self, **kwargs):
        """
        Create a safe kwargs dict that can be passed to Django field constructors.
        This method automatically filters out any custom parameters.
        """
        return {k: v for k, v in kwargs.items()
                if not k.startswith('_') and k != 'generating_models'}

    def get_annotated_form_field(self, field, enforce_visibility=True, **kwargs):
        form_field = self.get_form_field(field, **kwargs)
        form_field.model = field
        form_field.label = str(field)
        # Set the field name so Django can properly bind it to the instance
        form_field.name = field.name

        if field.description:
            form_field.help_text = render_markdown(field.description)

        # Annotate read-only fields
        if enforce_visibility and field.ui_editable != CustomFieldUIEditableChoices.YES:
            form_field.disabled = True

        return form_field

    def get_table_column_field(self, field, **kwargs):
        raise NotImplementedError

    def render_table_column_linkified(self, record):
        return linkify(record)

    def after_model_generation(self, instance, model, field_name): ...

    def create_m2m_table(self, instance, model, field_name): ...


class TextFieldType(FieldType):

    def get_model_field(self, field, **kwargs):
        field_kwargs = self._safe_kwargs(**kwargs)
        field_kwargs.update({"default": field.default, "unique": field.unique})
        return models.CharField(null=True, blank=True, **field_kwargs)

    def get_form_field(self, field, **kwargs):
        validators = []
        if field.validation_regex:
            validators = [
                RegexValidator(
                    regex=field.validation_regex,
                    message=mark_safe(
                        _("Values must match this regex: <code>{regex}</code>").format(
                            regex=escape(field.validation_regex)
                        )
                    ),
                )
            ]
        return forms.CharField(
            required=field.required, initial=field.default, validators=validators
        )

    def get_filterform_field(self, field, **kwargs):
        return forms.CharField(
            label=field,
            max_length=100,
            required=False,
        )


class LongTextFieldType(FieldType):
    def get_model_field(self, field, **kwargs):
        field_kwargs = self._safe_kwargs(**kwargs)
        field_kwargs.update({"default": field.default, "unique": field.unique})
        return models.TextField(null=True, blank=True, **field_kwargs)

    def get_form_field(self, field, **kwargs):
        widget = forms.Textarea
        validators = []
        if field.validation_regex:
            validators = [
                RegexValidator(
                    regex=field.validation_regex,
                    message=mark_safe(
                        _("Values must match this regex: <code>{regex}</code>").format(
                            regex=escape(field.validation_regex)
                        )
                    ),
                )
            ]
        return forms.CharField(
            widget=widget,
            required=field.required,
            initial=field.default,
            validators=validators,
        )

    def render_table_column(self, value):
        return render_markdown(value)


class IntegerFieldType(FieldType):

    def get_model_field(self, field, **kwargs):
        # TODO: handle all args for IntegerField
        field_kwargs = self._safe_kwargs(**kwargs)
        field_kwargs.update({"default": field.default, "unique": field.unique})
        return models.IntegerField(null=True, blank=True, **field_kwargs)

    def get_filterform_field(self, field, **kwargs):
        return forms.IntegerField(
            label=field,
            required=False,
        )

    def get_form_field(self, field, **kwargs):
        return forms.IntegerField(
            required=field.required,
            initial=field.default,
            min_value=field.validation_minimum,
            max_value=field.validation_maximum,
        )


class DecimalFieldType(FieldType):
    def get_model_field(self, field, **kwargs):
        field_kwargs = self._safe_kwargs(**kwargs)
        field_kwargs.update({"default": field.default, "unique": field.unique})
        return models.DecimalField(
            null=True,
            blank=True,
            max_digits=8,
            decimal_places=2,
            **field_kwargs
        )

    def get_form_field(self, field, **kwargs):
        return forms.DecimalField(
            required=field.required,
            initial=field.default,
            max_digits=12,
            decimal_places=4,
            min_value=field.validation_minimum,
            max_value=field.validation_maximum,
        )


class BooleanFieldType(FieldType):
    def get_model_field(self, field, **kwargs):
        field_kwargs = self._safe_kwargs(**kwargs)
        field_kwargs.update({"default": field.default, "unique": field.unique})
        return models.BooleanField(null=True, blank=True, **field_kwargs)

    def get_form_field(self, field, **kwargs):
        choices = (
            (None, "---------"),
            (True, _("True")),
            (False, _("False")),
        )
        return forms.NullBooleanField(
            required=field.required,
            initial=field.default,
            widget=forms.Select(choices=choices),
        )

    def get_table_column_field(self, field, **kwargs):
        return BooleanColumn()


class DateFieldType(FieldType):
    def get_model_field(self, field, **kwargs):
        field_kwargs = self._safe_kwargs(**kwargs)
        field_kwargs.update({"default": field.default, "unique": field.unique})
        return models.DateField(null=True, blank=True, **field_kwargs)

    def get_form_field(self, field, **kwargs):
        return forms.DateField(
            required=field.required, initial=field.default, widget=DatePicker()
        )


class DateTimeFieldType(FieldType):
    def get_model_field(self, field, **kwargs):
        field_kwargs = self._safe_kwargs(**kwargs)
        field_kwargs.update({"default": field.default, "unique": field.unique})
        return models.DateTimeField(null=True, blank=True, **field_kwargs)

    def get_form_field(self, field, **kwargs):
        return forms.DateTimeField(
            required=field.required, initial=field.default, widget=DateTimePicker()
        )


class URLFieldType(FieldType):
    def get_model_field(self, field, **kwargs):
        field_kwargs = self._safe_kwargs(**kwargs)
        field_kwargs.update({"default": field.default, "unique": field.unique})
        return models.URLField(null=True, blank=True, **field_kwargs)

    def get_form_field(self, field, **kwargs):
        return LaxURLField(
            assume_scheme="https", required=field.required, initial=field.default
        )


class JSONFieldType(FieldType):
    def get_model_field(self, field, **kwargs):
        field_kwargs = self._safe_kwargs(**kwargs)
        field_kwargs.update({"default": field.default, "unique": field.unique})
        return models.JSONField(null=True, blank=True, **field_kwargs)

    def get_form_field(self, field, **kwargs):
        return JSONField(
            required=field.required,
            initial=json.dumps(field.default) if field.default else None,
        )


class SelectFieldType(FieldType):
    def get_model_field(self, field, **kwargs):
        field_kwargs = self._safe_kwargs(**kwargs)
        field_kwargs.update({"default": field.default, "unique": field.unique})
        return models.CharField(
            max_length=100,
            choices=field.choices,
            null=True,
            blank=True,
            **field_kwargs
        )

    def get_form_field(self, field, for_csv_import=False, **kwargs):
        choices = field.choice_set.choices
        default_choice = field.default if field.default in field.choices else None

        if not field.required or default_choice is None:
            choices = add_blank_choice(choices)

        # Set the initial value to the first available choice (if any)
        initial = field.default
        if default_choice:
            initial = default_choice

        if for_csv_import:
            field_class = CSVChoiceField
            return field_class(
                choices=choices, required=field.required, initial=initial
            )
        else:
            field_class = DynamicChoiceField
            widget_class = APISelect
            return field_class(
                choices=choices,
                required=field.required,
                initial=initial,
                widget=widget_class(
                    api_url=f"/api/extras/custom-field-choice-sets/{field.choice_set.pk}/choices/"
                ),
            )


class MultiSelectFieldType(FieldType):
    def get_display_value(self, instance, field_name):
        return ", ".join(getattr(instance, field_name) or [])

    def get_model_field(self, field, **kwargs):
        field_kwargs = self._safe_kwargs(**kwargs)
        field_kwargs.update({"default": field.default, "unique": field.unique})
        return ArrayField(
            base_field=models.CharField(max_length=50, choices=field.choices),
            null=True,
            blank=True,
            **field_kwargs
        )

    def get_form_field(self, field, for_csv_import=False, **kwargs):
        choices = field.choice_set.choices
        default_choice = field.default if field.default in field.choices else None

        if not field.required or default_choice is None:
            choices = add_blank_choice(choices)

        # Set the initial value to the first available choice (if any)
        initial = field.default
        if default_choice:
            initial = default_choice

        if for_csv_import:
            field_class = CSVMultipleChoiceField
            return field_class(
                choices=choices, required=field.required, initial=initial
            )
        else:
            field_class = DynamicMultipleChoiceField
            widget_class = APISelectMultiple
            return field_class(
                choices=choices,
                required=field.required,
                initial=initial,
                widget=widget_class(
                    api_url=f"/api/extras/custom-field-choice-sets/{field.choice_set.pk}/choices/"
                ),
            )

    # TODO: Implement this
    # def get_form_field(self, field, required, label, **kwargs):
    #     return forms.MultipleChoiceField(
    #         choices=field.choices, required=required, label=label, **kwargs
    #     )

    def render_table_column(self, value):
        return ", ".join(value)


class ObjectFieldType(FieldType):
    def get_model_field(self, field, **kwargs):
        if field.is_polymorphic:
            # Polymorphic Object: two concrete columns + one virtual GFK descriptor
            ct_field_name = f"{field.name}_content_type"
            oid_field_name = f"{field.name}_object_id"
            return {
                ct_field_name: models.ForeignKey(
                    "contenttypes.ContentType",
                    null=True,
                    blank=True,
                    on_delete=models.SET_NULL,
                    related_name="+",
                    db_column=f"{field.name}_content_type_id",
                ),
                oid_field_name: models.PositiveBigIntegerField(null=True, blank=True),
                field.name: GenericForeignKey(ct_field_name, oid_field_name),
            }

        content_type = ContentType.objects.get(pk=field.related_object_type_id)
        to_model = content_type.model

        # Extract our custom parameters and keep only Django field parameters
        field_kwargs = {k: v for k, v in kwargs.items() if not k.startswith('_')}
        field_kwargs.update({"default": field.default, "unique": field.unique})

        # Handle self-referential fields by using string references
        if content_type.app_label == APP_LABEL:
            from netbox_custom_objects.models import CustomObjectType

            custom_object_type_id = extract_cot_id_from_model_name(content_type.model)
            custom_object_type = CustomObjectType.objects.get(pk=custom_object_type_id)

            # Check if this is a self-referential field
            if custom_object_type.id == field.custom_object_type.id:
                # For self-referential fields, use LazyForeignKey to defer resolution
                model_name = f"{APP_LABEL}.{custom_object_type.get_table_model_name(custom_object_type.id)}"
                # Generate a unique related_name to prevent reverse accessor conflicts
                table_model_name = field.custom_object_type.get_table_model_name(field.custom_object_type.id).lower()
                related_name = f"{table_model_name}_{field.name}_set"
                f = LazyForeignKey(
                    model_name,
                    null=True,
                    blank=True,
                    on_delete=models.CASCADE,
                    related_name=related_name,
                    **field_kwargs
                )
                return f
            else:
                # For cross-referential fields, use skip_object_fields to avoid infinite loops
                model = custom_object_type.get_model(skip_object_fields=True)
        else:
            # to_model = content_type.model_class()._meta.object_name
            to_ct = f"{content_type.app_label}.{to_model}"
            model = apps.get_model(to_ct)

        # Generate a unique related_name to prevent reverse accessor conflicts
        table_model_name = field.custom_object_type.get_table_model_name(field.custom_object_type.id).lower()
        related_name = f"{table_model_name}_{field.name}_set"
        f = models.ForeignKey(
            model, null=True, blank=True, on_delete=models.CASCADE, related_name=related_name, **field_kwargs
        )

        return f

    def get_form_field(self, field, for_csv_import=False, **kwargs):
        """
        Returns a form field for object relationships.
        For custom objects, uses CustomObjectDynamicModelChoiceField.
        For regular NetBox objects, uses DynamicModelChoiceField.
        """
        if field.is_polymorphic:
            # Polymorphic form field not yet supported in the UI; skip gracefully
            raise NotImplementedError(
                "Polymorphic object form fields are rendered by the view layer, not via this method."
            )

        content_type = ContentType.objects.get(pk=field.related_object_type_id)

        if content_type.app_label == APP_LABEL:
            # This is a custom object type
            from netbox_custom_objects.models import CustomObjectType

            custom_object_type_id = extract_cot_id_from_model_name(content_type.model)
            custom_object_type = CustomObjectType.objects.get(pk=custom_object_type_id)

            model = custom_object_type.get_model()
        else:
            # This is a regular NetBox model
            model = content_type.model_class()

        if for_csv_import:
            field_class = CSVModelChoiceField
            # For CSV import, determine to_field_name from the field configuration
            to_field_name = getattr(field, 'to_field_name', None) or 'name'
            return field_class(
                queryset=model.objects.all(),
                required=field.required,
                # Remove initial=field.default to allow Django to handle instance data properly
                to_field_name=to_field_name,
            )
        else:
            field_class = DynamicModelChoiceField
            return field_class(
                queryset=model.objects.all(),
                required=field.required,
                # Remove initial=field.default to allow Django to handle instance data properly
                query_params=(
                    field.related_object_filter
                    if hasattr(field, "related_object_filter")
                    else None
                ),
                selector=model._meta.app_label != APP_LABEL,
            )

    def get_filterform_field(self, field, **kwargs):
        if field.is_polymorphic:
            return None  # Filtering polymorphic fields not supported yet
        content_type = ContentType.objects.get(pk=field.related_object_type_id)
        if content_type.app_label == APP_LABEL:
            from netbox_custom_objects.models import CustomObjectType
            custom_object_type_id = extract_cot_id_from_model_name(content_type.model)
            custom_object_type = CustomObjectType.objects.get(pk=custom_object_type_id)
            model = custom_object_type.get_model()
        else:
            model = content_type.model_class()
        return DynamicModelChoiceField(
            queryset=model.objects.all(),
            required=False,
            label=field,
            selector=model._meta.app_label != APP_LABEL,
        )

    def render_table_column(self, value):
        return linkify(value)

    def get_serializer_field(self, field, **kwargs):
        if field.is_polymorphic:
            from netbox_custom_objects.api.serializers import PolymorphicObjectSerializerField
            allowed_ids = {ot.id for ot in field.related_object_types.all()}
            return PolymorphicObjectSerializerField(
                allowed_content_type_ids=allowed_ids,
                required=field.required,
                allow_null=not field.required,
            )
        related_model_class = field.related_object_type.model_class()
        if related_model_class._meta.app_label == APP_LABEL:
            from netbox_custom_objects.api.serializers import get_serializer_class
            serializer = get_serializer_class(related_model_class, skip_object_fields=True)
        else:
            serializer = get_serializer_for_model(related_model_class)
        return serializer(required=field.required, nested=True)

    def after_model_generation(self, instance, model, field_name):
        """
        Resolve lazy references after the model is fully generated.
        This ensures that self-referential fields point to the correct model class.
        """
        if instance.is_polymorphic:
            return  # GFK needs no post-generation resolution
        # Check if this field has a resolution method
        if resolve_method := getattr(model, f'_resolve_{field_name}_model', None):
            resolve_method(model)

    def add_polymorphic_object_columns(self, field_instance, model, schema_editor):
        """
        Add the two concrete DB columns (content_type FK + object_id) for a polymorphic
        Object field, plus a composite index on both columns.
        """
        ct_field_name = f"{field_instance.name}_content_type"
        oid_field_name = f"{field_instance.name}_object_id"

        ct_field = models.ForeignKey(
            "contenttypes.ContentType",
            null=True,
            blank=True,
            on_delete=models.SET_NULL,
            related_name="+",
            db_column=f"{field_instance.name}_content_type_id",
        )
        ct_field.contribute_to_class(model, ct_field_name)
        schema_editor.add_field(model, ct_field)

        oid_field = models.PositiveBigIntegerField(null=True, blank=True)
        oid_field.contribute_to_class(model, oid_field_name)
        schema_editor.add_field(model, oid_field)

        # Composite index as recommended in issue #31
        idx_name = _safe_index_name(
            f"co_{field_instance.custom_object_type_id}_{field_instance.name}_gfk"
        )
        idx = models.Index(fields=[ct_field_name, oid_field_name], name=idx_name)
        schema_editor.add_index(model, idx)

    def remove_polymorphic_object_columns(self, field_instance, model, schema_editor):
        """
        Remove the concrete DB columns for a polymorphic Object field.

        ``schema_editor`` must be supplied by the caller so that all DDL in a
        single operation (e.g. field deletion) runs within one schema editor
        context.  Opening a nested ``with connection.schema_editor()`` here
        would cause deferred_sql from the outer context to be flushed at the
        wrong time on some backends.
        """
        ct_field_name = f"{field_instance.name}_content_type"
        oid_field_name = f"{field_instance.name}_object_id"

        try:
            oid_field = model._meta.get_field(oid_field_name)
            schema_editor.remove_field(model, oid_field)
        except FieldDoesNotExist:
            pass  # Column already absent — nothing to remove.
        except Exception:
            logger.warning(
                "Failed to remove polymorphic object_id column %r from %r",
                oid_field_name, model._meta.db_table, exc_info=True,
            )
        try:
            ct_field = model._meta.get_field(ct_field_name)
            schema_editor.remove_field(model, ct_field)
        except FieldDoesNotExist:
            pass  # Column already absent — nothing to remove.
        except Exception:
            logger.warning(
                "Failed to remove polymorphic content_type column %r from %r",
                ct_field_name, model._meta.db_table, exc_info=True,
            )


# WHY CustomManyToManyManager / CustomManyToManyDescriptor / CustomManyToManyField
# exist instead of using Django's built-in ManyToManyField
# ──────────────────────────────────────────────────────────────────────────────
# Django's ManyToManyField assumes both sides of the relation are registered in
# the app registry *before* any model is instantiated.  Custom object types are
# defined at runtime by end-users and their models are generated dynamically via
# `type(...)`.  This creates two problems:
#
#   1. The through model does not exist in the app registry at import time, so
#      Django's ManyRelatedManager cannot resolve `field.remote_field.through`
#      during class construction.  Attempting to register it later causes
#      "model was already registered" RuntimeWarnings (suppressed in
#      generate_model()) and occasional stale-cache issues.
#
#   2. Django's `get_prefetch_queryset` (and the newer `get_prefetch_querysets`
#      introduced in Django 4.2) builds its result queryset from the through
#      model's manager, which requires the through model to be stable in the
#      registry.  Because our through models are regenerated on every server
#      restart (and on every schema change), the registry entry can be stale,
#      causing prefetch_related() to fetch from the wrong table.
#
# CustomManyToManyManager sidesteps both issues by resolving the through model
# directly from the field instance at access time rather than from the registry,
# and by implementing get_prefetch_queryset with explicit source/target subquery
# joins that work regardless of registry state.
#
# MAINTENANCE NOTE: get_prefetch_queryset returns a private Django tuple format.
# The six-element tuple (queryset, fk_getter, rel_obj_getter, single, cache_name,
# is_descriptor) is documented only in Django internals and may change between
# major versions.  If a Django upgrade breaks prefetch_related() for custom M2M
# fields, this is the first place to check.  The Django source to compare against
# is django/db/models/fields/related_managers.py :: ManyRelatedManager.
class CustomManyToManyManager(Manager):
    def __init__(self, instance=None, field_name=None):
        super().__init__()
        self.instance = instance
        self.field_name = field_name
        self.field = instance._meta.get_field(self.field_name)
        self.model = self.field.remote_field.model
        self.through = self.field.remote_field.through
        self.core_filters = {"source_id": instance.pk}
        self.prefetch_cache_name = self.field_name

    def get_prefetch_queryset(self, instances, queryset=None):
        if queryset is None:
            queryset = self.get_queryset()

        # Get all the target IDs for these instances in a single query
        through_queryset = self.through.objects.filter(
            source_id__in=[obj.pk for obj in instances]
        ).values_list("source_id", "target_id")

        # Build a mapping of instance PKs to their related objects
        rel_obj_cache = {source_id: [] for source_id in [obj.pk for obj in instances]}
        target_ids = set()
        for source_id, target_id in through_queryset:
            rel_obj_cache[source_id].append(target_id)
            target_ids.add(target_id)

        # Get all the related objects in a single query
        target_queryset = self.model.objects.filter(pk__in=target_ids)
        target_objects = {obj.pk: obj for obj in target_queryset}

        # Build the final cache mapping
        for source_id, target_ids in rel_obj_cache.items():
            rel_obj_cache[source_id] = [
                target_objects[target_id]
                for target_id in target_ids
                if target_id in target_objects
            ]

        return (
            target_queryset,  # queryset containing all the related objects
            lambda obj: obj.pk,  # function to get the related object ID
            lambda obj: rel_obj_cache[
                obj.pk
            ],  # function to get the list of related objects
            False,  # single related object (False for M2M)
            self.prefetch_cache_name,  # cache name
            False,  # is a descriptor (False for M2M)
        )

    def get_queryset(self):
        # Create a base queryset for the target model
        base_qs = self.model.objects.all()

        # Join through the through table using a subquery
        qs = base_qs.filter(
            pk__in=self.through.objects.filter(source_id=self.instance.pk).values_list(
                "target_id", flat=True
            )
        )

        # Add default ordering by pk
        return qs.order_by("pk")

    def add(self, *objs):
        for obj in objs:
            self.through.objects.get_or_create(
                source_id=self.instance.pk, target_id=obj.pk
            )

    def remove(self, *objs):
        for obj in objs:
            self.through.objects.filter(
                source_id=self.instance.pk, target_id=obj.pk
            ).delete()

    def clear(self):
        self.through.objects.filter(source_id=self.instance.pk).delete()

    def set(self, objs, clear=False):
        if clear:
            self.clear()
            self.add(*objs)
        else:
            # Diff-based replacement: add new, remove old.  Matches Django's
            # standard ManyRelatedManager.set(clear=False) behaviour.
            objs = tuple(objs)
            new_pks = {obj.pk for obj in objs}
            existing_pks = set(
                self.through.objects.filter(source_id=self.instance.pk)
                .values_list("target_id", flat=True)
            )
            to_add = [obj for obj in objs if obj.pk not in existing_pks]
            to_remove_pks = existing_pks - new_pks
            if to_add:
                self.add(*to_add)
            if to_remove_pks:
                self.through.objects.filter(
                    source_id=self.instance.pk,
                    target_id__in=to_remove_pks,
                ).delete()


class CustomManyToManyDescriptor(ManyToManyDescriptor):
    def __init__(self, field):
        self.field = field
        self.rel = field.remote_field
        self.reverse = False
        self.cache_name = self.field.name

    def __get__(self, instance, cls=None):
        if instance is None:
            return self

        return CustomManyToManyManager(instance=instance, field_name=self.field.name)

    def get_prefetch_queryset(self, instances, queryset=None):
        manager = CustomManyToManyManager(instances[0], self.field.name)
        return manager.get_prefetch_queryset(instances, queryset)

    def is_cached(self, instance):
        """
        Returns True if the field's value has been cached for the given instance.
        """
        return hasattr(instance, self.cache_name)

    def get_cached_value(self, instance):
        return getattr(instance, self.cache_name)

    def set_cached_value(self, instance, value):
        setattr(instance, self.cache_name, value)


class CustomManyToManyField(models.ManyToManyField):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.many_to_many = True
        self.concrete = False

    def m2m_field_name(self):
        return "source_id"

    def m2m_reverse_field_name(self):
        return "target_id"

    def get_foreign_related_value(self, instance):
        """Get the related value for the instance."""
        return (instance.pk,)

    def get_attname(self):
        return f"{self.name}_id"

    def get_attname_column(self):
        return self.name, None

    def contribute_to_class(self, cls, name, **kwargs):
        super().contribute_to_class(cls, name, **kwargs)
        setattr(cls, name, CustomManyToManyDescriptor(self))

    def get_joining_columns(self, reverse_join=False):
        if reverse_join:
            return ((self.m2m_reverse_field_name(), "id"),)
        return ((self.m2m_field_name(), "id"),)


class MultiObjectFieldType(FieldType):
    def get_through_model(self, field, model_string):
        """
        Creates a through model with deferred model references
        """
        # TODO: Register through model in AppsProxy to avoid "model was already registered" warnings
        # app_label = str(uuid.uuid4()) + "_database_table"
        # apps = AppsProxy(dynamic_models=None, app_label=app_label)
        meta = type(
            "Meta",
            (),
            {
                "db_table": field.through_table_name,
                "app_label": APP_LABEL,
                "apps": apps,
                "managed": True,
                "unique_together": ("source", "target"),
            },
        )

        # Check if this is a self-referential M2M
        content_type = ContentType.objects.get(pk=field.related_object_type_id)
        custom_object_type_id = extract_cot_id_from_model_name(content_type.model)
        is_self_referential = (
            content_type.app_label == APP_LABEL
            and field.custom_object_type.id == custom_object_type_id
        )

        attrs = {
            "__module__": "netbox_custom_objects.models",
            "Meta": meta,
            "id": models.AutoField(primary_key=True),
            "source": models.ForeignKey(
                model_string,
                on_delete=models.CASCADE,
                related_name="+",
                db_column="source_id",
            ),
            "target": models.ForeignKey(
                "self" if is_self_referential else model_string,
                on_delete=models.CASCADE,
                related_name="+",
                db_column="target_id",
            ),
        }

        return generate_model(field.through_model_name, (models.Model,), attrs)

    def get_model_field(self, field, **kwargs):
        """
        Creates the M2M field with appropriate model references
        """
        if field.is_polymorphic:
            # Polymorphic MultiObject: return a descriptor instead of a real M2M field.
            # The descriptor manages a through table with (source_id, content_type_id, object_id).
            return PolymorphicM2MDescriptor(through_model_name=field.through_model_name)

        # Check if this is a self-referential M2M
        content_type = ContentType.objects.get(pk=field.related_object_type_id)
        custom_object_type_id = extract_cot_id_from_model_name(content_type.model)

        # Extract our custom parameters and keep only Django field parameters
        field_kwargs = {k: v for k, v in kwargs.items() if not k.startswith('_')}
        # Remove default from field_kwargs since ManyToManyField doesn't handle defaults the same way
        field_kwargs.update({"unique": field.unique})

        is_self_referential = (
            content_type.app_label == APP_LABEL
            and field.custom_object_type.id == custom_object_type_id
        )

        # For now, we'll create the through model with string references
        # and resolve them later in after_model_generation
        # TODO: Check whether later resolution of the model is actually necessary or can be passed as string
        model_string = f"{field.related_object_type.app_label}.{field.related_object_type.model}"
        through = self.get_through_model(field, model_string)

        # For self-referential fields, use 'self' as the target
        m2m_field = CustomManyToManyField(
            to="self" if is_self_referential else model_string,
            through=through,
            through_fields=("source", "target"),
            blank=True,
            related_name="+",
            related_query_name="+",
            **field_kwargs
        )

        # Store metadata for later resolution
        m2m_field._custom_object_type_id = field.related_object_type_id
        m2m_field._is_self_referential = is_self_referential

        return m2m_field

    def get_form_field(self, field, for_csv_import=False, **kwargs):
        """
        Returns a form field for multi-object relationships.
        Uses DynamicModelMultipleChoiceField for both custom objects and regular NetBox objects.
        """
        if field.is_polymorphic:
            raise NotImplementedError("Polymorphic multi-object fields are managed via the API")

        content_type = ContentType.objects.get(pk=field.related_object_type_id)

        if content_type.app_label == APP_LABEL:
            # This is a custom object type
            from netbox_custom_objects.models import CustomObjectType

            custom_object_type_id = extract_cot_id_from_model_name(content_type.model)
            custom_object_type = CustomObjectType.objects.get(pk=custom_object_type_id)

            model = custom_object_type.get_model(skip_object_fields=True)
        else:
            # This is a regular NetBox model
            model = content_type.model_class()

        if for_csv_import:
            field_class = CSVModelMultipleChoiceField
            # For CSV import, determine to_field_name from the field configuration
            to_field_name = getattr(field, 'to_field_name', None) or 'name'
            return field_class(
                queryset=model.objects.all(),
                required=field.required,
                to_field_name=to_field_name,
            )
        else:
            field_class = DynamicModelMultipleChoiceField
            return field_class(
                queryset=model.objects.all(),
                required=field.required,
                query_params=(
                    field.related_object_filter
                    if hasattr(field, "related_object_filter")
                    else None
                ),
                selector=model._meta.app_label != APP_LABEL,
            )

    def get_display_value(self, instance, field_name):
        field = getattr(instance, field_name)
        return ", ".join(str(s) for s in field.all())

    def get_table_column_field(self, field, **kwargs):
        return tables.ManyToManyColumn(linkify_item=True, orderable=False)

    def get_serializer_field(self, field, **kwargs):
        if field.is_polymorphic:
            from netbox_custom_objects.api.serializers import PolymorphicObjectSerializerField
            from rest_framework import serializers as drf_serializers
            allowed_ids = {ot.id for ot in field.related_object_types.all()}
            return drf_serializers.ListField(
                child=PolymorphicObjectSerializerField(allowed_content_type_ids=allowed_ids),
                required=field.required,
                allow_empty=True,
            )
        related_model_class = field.related_object_type.model_class()
        if related_model_class._meta.app_label == APP_LABEL:
            from netbox_custom_objects.api.serializers import get_serializer_class
            serializer = get_serializer_class(related_model_class, skip_object_fields=True)
        else:
            serializer = get_serializer_for_model(related_model_class)
        return serializer(required=field.required, nested=True, many=True)

    def get_filterform_field(self, field, **kwargs):
        if field.is_polymorphic:
            return None  # Filtering polymorphic fields not supported yet
        content_type = ContentType.objects.get(pk=field.related_object_type_id)
        if content_type.app_label == APP_LABEL:
            from netbox_custom_objects.models import CustomObjectType
            custom_object_type_id = extract_cot_id_from_model_name(content_type.model)
            custom_object_type = CustomObjectType.objects.get(pk=custom_object_type_id)
            model = custom_object_type.get_model()
        else:
            model = content_type.model_class()
        return DynamicModelMultipleChoiceField(
            queryset=model.objects.all(),
            required=False,
            label=field,
            selector=model._meta.app_label != APP_LABEL,
        )

    def after_model_generation(self, instance, model, field_name):
        """
        After both models are generated, update the field's remote model references
        """
        if instance.is_polymorphic:
            return  # PolymorphicM2MDescriptor needs no post-generation resolution

        field = model._meta.get_field(field_name)

        # Skip model resolution for self-referential fields
        if getattr(field, "_is_self_referential", False):
            field.remote_field.model = model
            through_model = field.remote_field.through

            # Update both source and target fields to point to the same model
            source_field = through_model._meta.get_field("source")
            target_field = through_model._meta.get_field("target")

            # Resolve the foreign key fields to point to the actual model
            source_field.remote_field.model = model
            source_field.related_model = model
            target_field.remote_field.model = model
            target_field.related_model = model

            # Also update the field's to attribute to point to the actual model
            field.to = model

            return

        # For non-self-referential fields, we need to resolve the target model
        # Use the instance parameter which contains the field definition
        content_type = ContentType.objects.get(pk=instance.related_object_type_id)

        # Now we can safely resolve the target model
        if content_type.app_label == APP_LABEL:
            from netbox_custom_objects.models import CustomObjectType

            custom_object_type_id = extract_cot_id_from_model_name(content_type.model)
            custom_object_type = CustomObjectType.objects.get(pk=custom_object_type_id)

            # For self-referential fields, we need to resolve them to the current model
            # This doesn't cause recursion because we're not calling get_model() again
            if custom_object_type.id == instance.custom_object_type.id:
                # Self-referential field - resolve to current model
                to_model = model
            else:
                to_model = custom_object_type.get_model()
        else:
            to_ct = f"{content_type.app_label}.{content_type.model}"
            to_model = apps.get_model(to_ct)

        # Update through model's fields
        field.remote_field.model = to_model

        # Update through model's target field
        through_model = field.remote_field.through
        source_field = through_model._meta.get_field("source")
        target_field = through_model._meta.get_field("target")

        # Source field should point to the current model
        source_field.remote_field.model = model
        source_field.related_model = model

        # Target field should point to the related model
        target_field.remote_field.model = to_model
        target_field.related_model = to_model

    def create_m2m_table(self, instance, model, field_name):
        """
        Creates the actual M2M table after models are fully generated
        """
        from django.db import connection

        # Get the field instance
        field = model._meta.get_field(field_name)

        # For self-referential fields, use the current model
        if getattr(field, "_is_self_referential", False):
            to_model = model
        else:
            content_type = ContentType.objects.get(pk=instance.related_object_type_id)
            if content_type.app_label == APP_LABEL:
                from netbox_custom_objects.models import CustomObjectType

                custom_object_type_id = extract_cot_id_from_model_name(content_type.model)
                custom_object_type = CustomObjectType.objects.get(
                    pk=custom_object_type_id
                )

                to_model = custom_object_type.get_model()
            else:
                to_model = content_type.model_class()

        # Create the through model with actual model references
        through = self.get_through_model(instance, model)

        # Update the through model's foreign key references
        source_field = through._meta.get_field("source")
        target_field = through._meta.get_field("target")

        # Source field should point to the current model
        source_field.remote_field.model = model
        source_field.remote_field.field_name = model._meta.pk.name
        source_field.related_model = model

        # Target field should point to the related model
        target_field.remote_field.model = to_model
        target_field.remote_field.field_name = to_model._meta.pk.name
        target_field.related_model = to_model

        # Register the model with Django's app registry
        apps = model._meta.apps

        try:
            through_model = apps.get_model(APP_LABEL, instance.through_model_name)
        except LookupError:
            apps.register_model(APP_LABEL, through)
            through_model = through

        # Update the M2M field's through model and target model
        field.remote_field.through = through_model
        field.remote_field.model = to_model
        field.remote_field.field_name = to_model._meta.pk.name

        # Create the through table
        with connection.schema_editor() as schema_editor:
            table_name = through_model._meta.db_table
            with connection.cursor() as cursor:
                tables = connection.introspection.table_names(cursor)
                if table_name not in tables:
                    schema_editor.create_model(through_model)

    def get_polymorphic_through_model(self, field_instance, source_model_string):
        """
        Creates a through model for a polymorphic MultiObject field.
        Columns: source_id (FK to custom object), content_type_id (FK to ContentType),
        object_id (PositiveBigIntegerField).
        """
        meta = type(
            "Meta",
            (),
            {
                "db_table": field_instance.through_table_name,
                "app_label": APP_LABEL,
                "apps": apps,
                "managed": True,
                "unique_together": (("source", "content_type", "object_id"),),
                "indexes": [
                    models.Index(
                        fields=["content_type", "object_id"],
                        name=_safe_index_name(
                            f"co_{field_instance.custom_object_type_id}"
                            f"_{field_instance.name}_pgfk"
                        ),
                    )
                ],
            },
        )

        attrs = {
            "__module__": "netbox_custom_objects.models",
            "Meta": meta,
            "id": models.AutoField(primary_key=True),
            "source": models.ForeignKey(
                source_model_string,
                on_delete=models.CASCADE,
                related_name="+",
                db_column="source_id",
            ),
            "content_type": models.ForeignKey(
                "contenttypes.ContentType",
                on_delete=models.CASCADE,
                related_name="+",
                db_column="content_type_id",
            ),
            "object_id": models.PositiveBigIntegerField(db_column="object_id"),
        }

        return generate_model(field_instance.through_model_name, (models.Model,), attrs)

    def create_polymorphic_m2m_table(self, field_instance, model):
        """
        Creates the DB table for a polymorphic MultiObject through model.
        """
        source_model_string = f"{APP_LABEL}.{model.__name__}"
        through = self.get_polymorphic_through_model(field_instance, source_model_string)

        # Update source FK to point to the actual model
        source_field = through._meta.get_field("source")
        source_field.remote_field.model = model
        source_field.related_model = model

        # Register with Django's app registry
        _apps = model._meta.apps
        try:
            through_model = _apps.get_model(APP_LABEL, field_instance.through_model_name)
        except LookupError:
            _apps.register_model(APP_LABEL, through)
            through_model = through

        with connection.schema_editor() as schema_editor:
            table_name = through_model._meta.db_table
            with connection.cursor() as cursor:
                existing_tables = connection.introspection.table_names(cursor)
                if table_name not in existing_tables:
                    schema_editor.create_model(through_model)

    def drop_polymorphic_m2m_table(self, field_instance, model, schema_editor):
        """
        Drops the DB table for a polymorphic MultiObject through model.

        ``schema_editor`` must be supplied by the caller for the same reason as
        ``remove_polymorphic_object_columns``: all DDL in a single operation
        should share one schema editor context.
        """
        _apps = model._meta.apps
        try:
            through_model = _apps.get_model(APP_LABEL, field_instance.through_model_name)
            schema_editor.delete_model(through_model)
        except LookupError:
            pass  # Already dropped or never created


class PolymorphicManyToManyManager:
    """
    Manager for polymorphic many-to-many relationships.
    Handles objects from multiple model types via a through table with
    (source_id, content_type_id, object_id) columns.
    """

    def __init__(self, instance, field_name, through_model_name):
        self.instance = instance
        self.field_name = field_name
        self.through_model_name = through_model_name

    def _get_through_model(self):
        return apps.get_model(APP_LABEL, self.through_model_name)

    def _get_objects(self):
        through = self._get_through_model()
        rows = list(
            through.objects.filter(source_id=self.instance.pk)
            .values_list("content_type_id", "object_id")
            .order_by("id")
        )

        # Group object IDs by content type so we can batch-fetch per model class
        # (one SELECT per type) rather than issuing one SELECT per row.
        by_ct: dict[int, list] = {}
        for ct_id, obj_id in rows:
            by_ct.setdefault(ct_id, []).append(obj_id)

        # Build a lookup map: (ct_id, obj_id) → object, preserving row order below.
        obj_map: dict[tuple, object] = {}
        for ct_id, obj_ids in by_ct.items():
            ct = ContentType.objects.get_for_id(ct_id)
            model_class = ct.model_class()
            if model_class is None:
                continue
            for obj in model_class.objects.filter(pk__in=obj_ids):
                obj_map[(ct_id, obj.pk)] = obj

        # Yield in original insertion order, skipping stale references.
        for ct_id, obj_id in rows:
            obj = obj_map.get((ct_id, obj_id))
            if obj is not None:
                yield obj

    def all(self):
        return list(self._get_objects())

    def count(self):
        return self._get_through_model().objects.filter(source_id=self.instance.pk).count()

    def exists(self):
        return self._get_through_model().objects.filter(source_id=self.instance.pk).exists()

    def add(self, *objs):
        through = self._get_through_model()
        for obj in objs:
            ct = ContentType.objects.get_for_model(obj)
            through.objects.get_or_create(
                source_id=self.instance.pk,
                content_type_id=ct.pk,
                object_id=obj.pk,
            )

    def remove(self, *objs):
        through = self._get_through_model()
        for obj in objs:
            ct = ContentType.objects.get_for_model(obj)
            through.objects.filter(
                source_id=self.instance.pk,
                content_type_id=ct.pk,
                object_id=obj.pk,
            ).delete()

    def clear(self):
        self._get_through_model().objects.filter(source_id=self.instance.pk).delete()

    def set(self, objs, clear=False):
        if clear:
            self.clear()
            self.add(*objs)
        else:
            # Diff-based replacement: add new, remove old.  Matches Django's
            # standard ManyRelatedManager.set(clear=False) behaviour.
            objs = tuple(objs)
            through = self._get_through_model()
            existing = {
                (ct_id, obj_id)
                for ct_id, obj_id in through.objects.filter(source_id=self.instance.pk)
                .values_list("content_type_id", "object_id")
            }
            # Pre-compute (ct_id, obj_pk) once per object to avoid duplicate CT lookups.
            new_items = [
                (ContentType.objects.get_for_model(obj).pk, obj.pk, obj) for obj in objs
            ]
            new_keys = {(ct_id, obj_pk) for ct_id, obj_pk, _ in new_items}
            to_add = [obj for ct_id, obj_pk, obj in new_items if (ct_id, obj_pk) not in existing]
            to_remove = existing - new_keys
            if to_add:
                self.add(*to_add)
            for ct_id, obj_id in to_remove:
                through.objects.filter(
                    source_id=self.instance.pk,
                    content_type_id=ct_id,
                    object_id=obj_id,
                ).delete()

    def __iter__(self):
        return self._get_objects()


class PolymorphicM2MDescriptor:
    """
    Descriptor for polymorphic many-to-many fields.
    Added directly to the model's class attrs during model generation.
    """

    def __init__(self, through_model_name):
        self.through_model_name = through_model_name
        self.field_name = None

    def __set_name__(self, owner, name):
        self.field_name = name

    def contribute_to_class(self, cls, name):
        self.field_name = name
        setattr(cls, name, self)

    def __get__(self, instance, owner=None):
        if instance is None:
            return self
        return PolymorphicManyToManyManager(
            instance=instance,
            field_name=self.field_name,
            through_model_name=self.through_model_name,
        )

    def __set__(self, instance, value):
        raise AttributeError(
            f"Direct assignment to '{self.field_name}' is not supported. "
            f"Use '{self.field_name}.set(objs)' to update polymorphic M2M fields."
        )

    @property
    def many_to_many(self):
        return True

    @property
    def concrete(self):
        return False


FIELD_TYPE_CLASS = {
    CustomFieldTypeChoices.TYPE_TEXT: TextFieldType,
    CustomFieldTypeChoices.TYPE_LONGTEXT: LongTextFieldType,
    CustomFieldTypeChoices.TYPE_INTEGER: IntegerFieldType,
    CustomFieldTypeChoices.TYPE_DECIMAL: DecimalFieldType,
    CustomFieldTypeChoices.TYPE_BOOLEAN: BooleanFieldType,
    CustomFieldTypeChoices.TYPE_DATE: DateFieldType,
    CustomFieldTypeChoices.TYPE_DATETIME: DateTimeFieldType,
    CustomFieldTypeChoices.TYPE_URL: URLFieldType,
    CustomFieldTypeChoices.TYPE_JSON: JSONFieldType,
    CustomFieldTypeChoices.TYPE_SELECT: SelectFieldType,
    CustomFieldTypeChoices.TYPE_MULTISELECT: MultiSelectFieldType,
    CustomFieldTypeChoices.TYPE_OBJECT: ObjectFieldType,
    CustomFieldTypeChoices.TYPE_MULTIOBJECT: MultiObjectFieldType,
}
