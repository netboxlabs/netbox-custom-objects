import django_tables2 as tables

from django.contrib.contenttypes.models import ContentType
from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.db.models.fields.related import ManyToManyDescriptor
from django import forms
from django.apps import apps
from rest_framework import serializers
from django.db.models.fields.related_descriptors import create_forward_many_to_many_manager
from django.db.models.manager import Manager

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
        to_model = content_type.model_class()._meta.object_name
        to_ct = f'{content_type.app_label}.{to_model}'
        model = apps.get_model(to_ct)
        f = models.ForeignKey(model, null=True, blank=True, on_delete=models.CASCADE)
        return f

    def get_filterform_field(self, field, **kwargs):
        return None


class CustomManyToManyManager(Manager):
    def __init__(self, instance=None):
        super().__init__()
        self.instance = instance
        self.model = self.instance._meta.get_field('multiobject_field').remote_field.model
        self.field = instance._meta.get_field('multiobject_field')
        self.through = self.field.remote_field.through
        self.core_filters = {'source_id': instance.pk}
        self.prefetch_cache_name = self.field.name

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
        # TODO: See if this can be optimized
        # TODO: Remove or tighten try-except
        try:
            # Get the IDs from the through table
            target_ids = self.through.objects.filter(
                source_id=self.instance.pk
            ).values_list('target_id', flat=True)
            
            # Return full model objects
            return self.model.objects.filter(id__in=target_ids)
        except Exception:
            return super().get_queryset()

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

        return CustomManyToManyManager(instance=instance)

    def get_prefetch_queryset(self, instances, queryset=None):
        manager = CustomManyToManyManager(instances[0])
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
        
        # Create the through model first
        class Meta:
            db_table = through_table_name
            app_label = 'netbox_custom_objects'

        attrs = {
            '__module__': 'netbox_custom_objects.models',
            'Meta': Meta,
            'id': models.AutoField(primary_key=True),
            'source': models.ForeignKey(
                'netbox_custom_objects.CustomObject',
                on_delete=models.CASCADE,
                related_name='+',
                db_column='source_id'
            ),
            'target': models.ForeignKey(
                to_model,
                on_delete=models.CASCADE,
                related_name='+',
                db_column='target_id'
            )
        }
        
        through = type(f'Through_{through_table_name}', (models.Model,), attrs)
        
        # Now create the M2M field using our custom field class
        m2m_field = CustomManyToManyField(
            to=to_model,
            through=through,
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
        min_reviews = tables.Column(
            # verbose_name=_('Minimum reviews')
        )
        return tables.ManyToManyColumn(
            linkify_item=True,
            orderable=False,
            # verbose_name=_('Reviewer Groups')
        )

    def after_model_generation(self, instance, model, field_name):
        model_name = model._meta.model_name
        model.baserow_models[model_name] = model


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
