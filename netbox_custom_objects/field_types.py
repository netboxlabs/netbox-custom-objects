import django_tables2 as tables

from django.contrib.contenttypes.models import ContentType
from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.db.models.fields.related import (
    ManyToManyDescriptor,
    ManyToManyField,
)
from django.db.models.manager import Manager
from django import forms
from django.apps import apps
from rest_framework import serializers

from extras.choices import CustomFieldTypeChoices
from utilities.forms.widgets import DatePicker, DateTimePicker


class FieldType:

    def get_model_field(self, field, **kwargs):
        raise NotImplementedError

    def get_serializer_field(self, field, **kwargs):
        raise NotImplementedError

    def get_filterform_field(self, field, **kwargs):
        raise NotImplementedError

    def get_form_field(self, field, **kwargs):
        raise NotImplementedError

    def get_bulk_edit_form_field(self, field, **kwargs):
        raise NotImplementedError

    def get_table_column_field(self, field, **kwargs):
        raise NotImplementedError

    def after_model_generation(self, instance, model, field_name):
        ...

    def create_m2m_table(self, instance, model, field_name):
        ...


class TextFieldType(FieldType):

    def get_model_field(self, field, **kwargs):
        kwargs.update({'max_length': field.default})
        return models.CharField(null=True, **kwargs)

    def get_serializer_field(self, field, **kwargs):
        required = kwargs.get("required", False)
        validators = kwargs.pop("validators", None) or []
        # validators.append(self.validator)
        return serializers.CharField(
            **{
                "required": required,
                "allow_null": not required,
                "allow_blank": not required,
                "validators": validators,
                **kwargs,
            }
        )

    def get_bulk_edit_form_field(self, field, **kwargs):
        return forms.CharField(
            max_length=200,
            required=False,
        )

    def get_filterform_field(self, field, **kwargs):
        return forms.CharField(
            label=field,
            max_length=100,
            required=False,
        )


class LongTextFieldType(FieldType):
    def get_model_field(self, field, **kwargs):
        return models.TextField(null=True, **kwargs)


class IntegerFieldType(FieldType):

    def get_model_field(self, field, **kwargs):
        # TODO: handle all args for IntegerField
        kwargs.update({'default': field.default})
        return models.IntegerField(null=True, **kwargs)

    def get_filterform_field(self, field, **kwargs):
        return forms.IntegerField(
            label=field,
            required=False,
        )

class DecimalFieldType(FieldType):
    def get_model_field(self, field, **kwargs):
        return models.DecimalField(
            null=True,
            max_digits=8,
            decimal_places=2,
            **kwargs,
        )


class BooleanFieldType(FieldType):
    def get_model_field(self, field, **kwargs):
        return models.BooleanField(null=True, **kwargs)


class DateFieldType(FieldType):
    def get_model_field(self, field, **kwargs):
        return models.DateField(null=True, **kwargs)

    def get_form_field(self, field, **kwargs):
        return forms.DateField(
            required=False,
            widget=DatePicker()
        )


class DateTimeFieldType(FieldType):
    def get_model_field(self, field, **kwargs):
        return models.DateTimeField(null=True, **kwargs)

    def get_form_field(self, field, **kwargs):
        return forms.DateTimeField(
            required=False,
            widget=DateTimePicker()
        )


class URLFieldType(FieldType):
    def get_model_field(self, field, **kwargs):
        return models.URLField(null=True, **kwargs)


class JSONFieldType(FieldType):
    def get_model_field(self, field, **kwargs):
        return models.JSONField(null=True, **kwargs)


class SelectFieldType(FieldType):
    def get_model_field(self, field, **kwargs):
        return models.CharField(
            max_length=100,
            choices=field.choices,
            null=True,
            **kwargs,
        )


class MultiSelectFieldType(FieldType):
    def get_model_field(self, field, **kwargs):
        return ArrayField(
            base_field=models.CharField(max_length=50, choices=field.choices),
            null=True,
            **kwargs,
        )

    def get_form_field(self, field, **kwargs):
        return forms.MultipleChoiceField(choices=field.choices, **kwargs)


class ObjectFieldType(FieldType):
    def get_model_field(self, field, **kwargs):
        # return models.IntegerField(**kwargs)
        # content_type = ContentType.objects.get_for_model(instance)
        content_type = ContentType.objects.get(pk=field.related_object_type_id)
        to_model = content_type.model

        # TODO: Handle pointing to object of same type (avoid infinite loop)
        if content_type.app_label == 'netbox_custom_objects':
            from netbox_custom_objects.models import CustomObjectType
            custom_object_type_id = content_type.model.replace('table', '').replace('model', '')
            custom_object_type = CustomObjectType.objects.get(pk=custom_object_type_id)
            model = custom_object_type.get_model()
        else:
            # to_model = content_type.model_class()._meta.object_name
            to_ct = f'{content_type.app_label}.{to_model}'
            model = apps.get_model(to_ct)
        f = models.ForeignKey(model, null=True, blank=True, on_delete=models.CASCADE)
        return f

    def get_filterform_field(self, field, **kwargs):
        return None


class CustomManyToManyManager(Manager):
    def __init__(self, instance=None, field_name=None):
        super().__init__()
        self.instance = instance
        self.field_name = field_name
        self.field = instance._meta.get_field(self.field_name)
        self.model = self.field.remote_field.model
        self.through = self.field.remote_field.through
        self.core_filters = {'source_id': instance.pk}
        self.prefetch_cache_name = self.field_name

    def get_prefetch_queryset(self, instances, queryset=None):
        if queryset is None:
            queryset = self.get_queryset()

        # Get all the target IDs for these instances in a single query
        through_queryset = self.through.objects.filter(
            source_id__in=[obj.pk for obj in instances]
        ).values_list('source_id', 'target_id')

        # Build a mapping of instance PKs to their related objects
        rel_obj_cache = {
            source_id: []
            for source_id in [obj.pk for obj in instances]
        }
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
            lambda obj: rel_obj_cache[obj.pk],  # function to get the list of related objects
            False,  # single related object (False for M2M)
            self.prefetch_cache_name,  # cache name
            False,  # is a descriptor (False for M2M)
        )

    def get_queryset(self):
        # Create a base queryset for the target model
        base_qs = self.model.objects.all()
        
        # Join through the through table using a subquery
        qs = base_qs.filter(
            pk__in=self.through.objects.filter(
                source_id=self.instance.pk
            ).values_list('target_id', flat=True)
        )

        # Add default ordering by pk
        return qs.order_by('pk')

    def add(self, *objs):
        for obj in objs:
            self.through.objects.get_or_create(
                source_id=self.instance.pk,
                target_id=obj.pk
            )

    def remove(self, *objs):
        for obj in objs:
            self.through.objects.filter(
                source_id=self.instance.pk,
                target_id=obj.pk
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
        return 'source_id'

    def m2m_reverse_field_name(self):
        return 'target_id'

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
    def get_model_field(self, field, **kwargs):
        content_type = ContentType.objects.get(pk=field.related_object_type_id)
        model_name = field.custom_object_type.get_table_model_name(field.custom_object_type.pk).lower()
        to_model = content_type.model_class()
        through_table_name = f"custom_objects_{field.custom_object_type_id}_{field.name}"
        through_model_name = f'Through_{through_table_name}'
        
        # Store the information needed for after_model_generation
        field._through_table_name = through_table_name
        field._through_model_name = through_model_name
        field._to_model = to_model
        
        # Create a temporary through model
        class Meta:
            db_table = through_table_name
            app_label = 'netbox_custom_objects'
            managed = True

        attrs = {
            '__module__': 'netbox_custom_objects.models',
            'Meta': Meta,
            'id': models.AutoField(primary_key=True),
            'source': models.ForeignKey(
                'netbox_custom_objects.CustomObject',
                on_delete=models.CASCADE,
                db_column='source_id'
            ),
            'target': models.ForeignKey(
                to_model,
                on_delete=models.CASCADE,
                db_column='target_id'
            )
        }
        
        temp_through = type(f'{through_model_name}', (models.Model,), attrs)
        
        # Create the M2M field using our custom field class
        m2m_field = CustomManyToManyField(
            to=to_model,
            through=temp_through,
            through_fields=('source', 'target'),
            blank=True,
            related_name='+',
            related_query_name='+'
        )
        
        return m2m_field

    def get_form_field(self, field, **kwargs):
        return None

    def get_filterform_field(self, field, **kwargs):
        return None

    def get_table_column_field(self, field, **kwargs):
        return tables.ManyToManyColumn(
            linkify_item=True,
            orderable=False
        )

    def after_model_generation(self, instance, model, field_name):
        ...

    def create_m2m_table(self, instance, model, field_name):
        from django.db import connection

        model_name = model._meta.model_name
        model.baserow_models[model_name] = model

        # Get the field instance
        field = model._meta.get_field(field_name)

        # Create the through model
        class Meta:
            db_table = instance._through_table_name
            app_label = 'netbox_custom_objects'
            managed = True
            unique_together = ('source', 'target')

        attrs = {
            '__module__': 'netbox_custom_objects.models',
            'Meta': Meta,
            'id': models.AutoField(primary_key=True),
            'source': models.ForeignKey(
                model,
                on_delete=models.CASCADE,
                related_name='+',
                db_column='source_id'
            ),
            'target': models.ForeignKey(
                instance._to_model,
                on_delete=models.CASCADE,
                related_name='+',
                db_column='target_id'
            )
        }
        
        # Create and register the through model
        through = type(instance._through_model_name, (models.Model,), attrs)
        
        # Register the model with Django's app registry
        apps = model._meta.apps
        try:
            through_model = apps.get_model('netbox_custom_objects', instance._through_model_name)
        except LookupError:
            apps.register_model('netbox_custom_objects', through)
            through_model = through
        
        # Update the M2M field's through model
        field.remote_field.through = through_model
        field.remote_field.model = instance._to_model
        
        # Create the through table directly using schema editor
        with connection.schema_editor() as schema_editor:
            # Check if table exists first
            table_name = through_model._meta.db_table
            with connection.cursor() as cursor:
                tables = connection.introspection.table_names(cursor)
                if table_name not in tables:
                    schema_editor.create_model(through_model)

    # TODO: Probably not needed
    def remove_field(self, field, model, field_name):
        """
        Remove the through table when the field is deleted.
        """
        from django.db import connection

        # Recreate the through model to get its meta info
        through_table_name = f"custom_objects_{field.custom_object_type_id}_{field.name}"
        through_model_name = f'Through_{through_table_name}'
        
        class Meta:
            db_table = through_table_name
            app_label = 'netbox_custom_objects'
            managed = True

        attrs = {
            '__module__': 'netbox_custom_objects.models',
            'Meta': Meta,
            'id': models.AutoField(primary_key=True),
            'source': models.ForeignKey(
                model,
                on_delete=models.CASCADE,
                db_column='source_id'
            ),
            'target': models.ForeignKey(
                field._to_model,
                on_delete=models.CASCADE,
                db_column='target_id'
            )
        }
        
        through = type(through_model_name, (models.Model,), attrs)
        
        # Delete the through table using schema editor
        with connection.schema_editor() as schema_editor:
            # Check if table exists first
            table_name = through._meta.db_table
            with connection.cursor() as cursor:
                tables = connection.introspection.table_names(cursor)
                if table_name in tables:
                    schema_editor.delete_model(through)


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
