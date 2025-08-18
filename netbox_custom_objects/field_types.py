import json

import django_tables2 as tables
from django import forms
from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.contrib.postgres.fields import ArrayField
from django.core.validators import RegexValidator
from django.db import models
from django.db.models.fields.related import ManyToManyDescriptor
from django.db.models.manager import Manager
from django.utils.html import escape
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _
from extras.choices import CustomFieldTypeChoices, CustomFieldUIEditableChoices
from utilities.api import get_serializer_for_model
from utilities.forms.fields import (CSVChoiceField, CSVMultipleChoiceField,
                                    DynamicChoiceField,
                                    DynamicMultipleChoiceField, JSONField,
                                    LaxURLField)
from utilities.forms.utils import add_blank_choice
from utilities.forms.widgets import (APISelect, APISelectMultiple, DatePicker,
                                     DateTimePicker)
from utilities.templatetags.builtins.filters import linkify, render_markdown

from netbox_custom_objects.constants import APP_LABEL


class FieldType:

    def get_display_value(self, instance, field_name):
        return getattr(instance, field_name)

    def get_model_field(self, field, **kwargs):
        raise NotImplementedError

    def get_serializer_field(self, field, **kwargs):
        raise NotImplementedError

    def get_filterform_field(self, field, **kwargs):
        raise NotImplementedError

    def get_form_field(self, field, **kwargs):
        raise NotImplementedError

    def get_annotated_form_field(self, field, enforce_visibility=True, **kwargs):
        form_field = self.get_form_field(field, **kwargs)
        form_field.model = field
        form_field.label = str(field)
        if field.description:
            form_field.help_text = render_markdown(field.description)

        # Annotate read-only fields
        if enforce_visibility and field.ui_editable != CustomFieldUIEditableChoices.YES:
            form_field.disabled = True

        return form_field

    def get_table_column_field(self, field, **kwargs):
        raise NotImplementedError

    def after_model_generation(self, instance, model, field_name): ...

    def create_m2m_table(self, instance, model, field_name): ...


class TextFieldType(FieldType):

    def get_model_field(self, field, **kwargs):
        kwargs.update({"default": field.default, "unique": field.unique})
        return models.CharField(null=True, blank=True, **kwargs)

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
        kwargs.update({"default": field.default, "unique": field.unique})
        return models.TextField(null=True, blank=True, **kwargs)

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
        kwargs.update({"default": field.default, "unique": field.unique})
        return models.IntegerField(null=True, blank=True, **kwargs)

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
        kwargs.update({"default": field.default, "unique": field.unique})
        return models.DecimalField(
            null=True,
            blank=True,
            max_digits=8,
            decimal_places=2,
            **kwargs,
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
        kwargs.update({"default": field.default, "unique": field.unique})
        return models.BooleanField(null=True, blank=True, **kwargs)

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


class DateFieldType(FieldType):
    def get_model_field(self, field, **kwargs):
        kwargs.update({"default": field.default, "unique": field.unique})
        return models.DateField(null=True, blank=True, **kwargs)

    def get_form_field(self, field, **kwargs):
        return forms.DateField(
            required=field.required, initial=field.default, widget=DatePicker()
        )


class DateTimeFieldType(FieldType):
    def get_model_field(self, field, **kwargs):
        kwargs.update({"default": field.default, "unique": field.unique})
        return models.DateTimeField(null=True, blank=True, **kwargs)

    def get_form_field(self, field, **kwargs):
        return forms.DateTimeField(
            required=field.required, initial=field.default, widget=DateTimePicker()
        )


class URLFieldType(FieldType):
    def get_model_field(self, field, **kwargs):
        kwargs.update({"default": field.default, "unique": field.unique})
        return models.URLField(null=True, blank=True, **kwargs)

    def get_form_field(self, field, **kwargs):
        return LaxURLField(
            assume_scheme="https", required=field.required, initial=field.default
        )


class JSONFieldType(FieldType):
    def get_model_field(self, field, **kwargs):
        kwargs.update({"default": field.default, "unique": field.unique})
        return models.JSONField(null=True, blank=True, **kwargs)

    def get_form_field(self, field, **kwargs):
        return JSONField(
            required=field.required,
            initial=json.dumps(field.default) if field.default else None,
        )


class SelectFieldType(FieldType):
    def get_model_field(self, field, **kwargs):
        kwargs.update({"default": field.default, "unique": field.unique})
        return models.CharField(
            max_length=100,
            choices=field.choices,
            null=True,
            blank=True,
            **kwargs,
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
        kwargs.update({"default": field.default, "unique": field.unique})
        return ArrayField(
            base_field=models.CharField(max_length=50, choices=field.choices),
            null=True,
            blank=True,
            **kwargs,
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
        content_type = ContentType.objects.get(pk=field.related_object_type_id)
        to_model = content_type.model
        kwargs.update({"default": field.default, "unique": field.unique})

        # TODO: Handle pointing to object of same type (avoid infinite loop)
        if content_type.app_label == APP_LABEL:
            from netbox_custom_objects.models import CustomObjectType

            custom_object_type_id = content_type.model.replace("table", "").replace(
                "model", ""
            )
            custom_object_type = CustomObjectType.objects.get(pk=custom_object_type_id)
            model = custom_object_type.get_model()
        else:
            # to_model = content_type.model_class()._meta.object_name
            to_ct = f"{content_type.app_label}.{to_model}"
            model = apps.get_model(to_ct)
        f = models.ForeignKey(
            model, null=True, blank=True, on_delete=models.CASCADE, **kwargs
        )
        return f

    def get_form_field(self, field, for_csv_import=False, **kwargs):
        """
        Returns a form field for object relationships.
        For custom objects, uses CustomObjectDynamicModelChoiceField.
        For regular NetBox objects, uses DynamicModelChoiceField.
        """
        content_type = ContentType.objects.get(pk=field.related_object_type_id)

        from utilities.forms.fields import DynamicModelChoiceField
        if content_type.app_label == APP_LABEL:
            # This is a custom object type
            from netbox_custom_objects.models import CustomObjectType

            custom_object_type_id = content_type.model.replace("table", "").replace(
                "model", ""
            )
            custom_object_type = CustomObjectType.objects.get(pk=custom_object_type_id)
            model = custom_object_type.get_model()
            field_class = DynamicModelChoiceField
        else:
            # This is a regular NetBox model
            model = content_type.model_class()

            field_class = DynamicModelChoiceField

        return field_class(
            queryset=model.objects.all(),
            required=field.required,
            initial=field.default,
            query_params=(
                field.related_object_filter
                if hasattr(field, "related_object_filter")
                else None
            ),
            selector=True,
        )

    def get_filterform_field(self, field, **kwargs):
        return None

    def render_table_column(self, value):
        return linkify(value)

    def get_serializer_field(self, field, **kwargs):
        related_model_class = field.related_object_type.model_class()
        if not related_model_class:
            raise NotImplementedError("Custom object serializers not implemented")
        serializer = get_serializer_for_model(related_model_class)
        return serializer(required=field.required, nested=True)


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
    def get_through_model(self, field, model=None):
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
        custom_object_type_id = content_type.model.replace("table", "").replace(
            "model", ""
        )
        is_self_referential = (
            content_type.app_label == APP_LABEL
            and field.custom_object_type.id == custom_object_type_id
        )

        # Use the actual model if provided, otherwise use string reference
        source_model = model if model else "netbox_custom_objects.CustomObject"

        attrs = {
            "__module__": "netbox_custom_objects.models",
            "Meta": meta,
            "id": models.AutoField(primary_key=True),
            "source": models.ForeignKey(
                source_model,
                on_delete=models.CASCADE,
                related_name="+",
                db_column="source_id",
            ),
            "target": models.ForeignKey(
                "self" if is_self_referential else "netbox_custom_objects.CustomObject",
                on_delete=models.CASCADE,
                related_name="+",
                db_column="target_id",
            ),
        }

        return type(field.through_model_name, (models.Model,), attrs)

    def get_model_field(self, field, **kwargs):
        """
        Creates the M2M field with appropriate model references
        """
        # Check if this is a self-referential M2M
        content_type = ContentType.objects.get(pk=field.related_object_type_id)
        custom_object_type_id = content_type.model.replace("table", "").replace(
            "model", ""
        )
        # TODO: Default does not auto-populate, to new or existing objects (should it?)
        kwargs.update({"default": field.default, "unique": field.unique})

        is_self_referential = (
            content_type.app_label == APP_LABEL
            and field.custom_object_type.id == custom_object_type_id
        )

        # For now, we'll create the through model with string references
        # and resolve them later in after_model_generation
        through = self.get_through_model(field)

        # For self-referential fields, use 'self' as the target
        m2m_field = CustomManyToManyField(
            to="self" if is_self_referential else "netbox_custom_objects.CustomObject",
            through=through,
            through_fields=("source", "target"),
            blank=True,
            related_name="+",
            related_query_name="+",
            **kwargs,
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
        content_type = ContentType.objects.get(pk=field.related_object_type_id)

        if content_type.app_label == APP_LABEL:
            # This is a custom object type
            from netbox_custom_objects.models import CustomObjectType

            custom_object_type_id = content_type.model.replace("table", "").replace(
                "model", ""
            )
            custom_object_type = CustomObjectType.objects.get(pk=custom_object_type_id)
            model = custom_object_type.get_model()
        else:
            # This is a regular NetBox model
            model = content_type.model_class()

        from utilities.forms.fields import DynamicModelMultipleChoiceField

        return DynamicModelMultipleChoiceField(
            queryset=model.objects.all(),
            required=field.required,
            initial=field.default,
            query_params=(
                field.related_object_filter
                if hasattr(field, "related_object_filter")
                else None
            ),
            selector=True,
        )

    def get_filterform_field(self, field, **kwargs):
        return None

    def get_table_column_field(self, field, **kwargs):
        return tables.ManyToManyColumn(linkify_item=True, orderable=False)

    def get_serializer_field(self, field, **kwargs):
        related_model_class = field.related_object_type.model_class()
        if not related_model_class:
            raise NotImplementedError("Custom object serializers not implemented")
        serializer = get_serializer_for_model(related_model_class)
        return serializer(required=field.required, nested=True, many=True)

    def after_model_generation(self, instance, model, field_name):
        """
        After both models are generated, update the field's remote model references
        """
        field = model._meta.get_field(field_name)

        # Skip model resolution for self-referential fields
        if getattr(field, "_is_self_referential", False):
            field.remote_field.model = model
            through_model = field.remote_field.through

            # Update both source and target fields to point to the same model
            source_field = through_model._meta.get_field("source")
            target_field = through_model._meta.get_field("target")
            source_field.remote_field.model = model
            source_field.related_model = model
            target_field.remote_field.model = model
            target_field.related_model = model
            return

        content_type = ContentType.objects.get(pk=instance.related_object_type_id)

        # Now we can safely resolve the target model
        if content_type.app_label == APP_LABEL:
            from netbox_custom_objects.models import CustomObjectType

            custom_object_type_id = content_type.model.replace("table", "").replace(
                "model", ""
            )
            custom_object_type = CustomObjectType.objects.get(pk=custom_object_type_id)
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

                custom_object_type_id = content_type.model.replace("table", "").replace(
                    "model", ""
                )
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
