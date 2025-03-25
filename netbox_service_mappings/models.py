import jsonschema

from django.db import models
from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
from netbox.models import NetBoxModel
from extras.choices import CustomFieldTypeChoices
# from .choices import MappingFieldTypeChoices


class CustomObjectType(NetBoxModel):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    schema = models.JSONField(blank=True, default=dict)

    class Meta:
        verbose_name = 'Custom Object Type'

    def __str__(self):
        return self.name

    @property
    def formatted_schema(self):
        result = '<ul>'
        for field_name, field in self.schema.items():
            field_type = field.get('type')
            if field_type in ['object', 'multiobject']:
                content_type = ContentType.objects.get(app_label=field['app_label'], model=field['model'])
                field = content_type
            result += f"<li>{field_name}: {field}</li>"
        result += '</ul>'
        return result

    def get_absolute_url(self):
        return reverse('plugins:netbox_service_mappings:customobjecttype', args=[self.pk])


class CustomObject(NetBoxModel):
    custom_object_type = models.ForeignKey(CustomObjectType, on_delete=models.CASCADE, related_name="custom_objects")
    name = models.CharField(max_length=100, unique=True)
    data = models.JSONField(blank=True, default=dict)

    class Meta:
        verbose_name = 'Custom Object'

    def __str__(self):
        return self.name

    @property
    def formatted_data(self):
        result = '<ul>'
        for field_name, field in self.custom_object_type.schema.items():
            value = self.data.get(field_name)
            field_type = field.get('type')
            if field_type in ['object', 'multiobject']:
                content_type = ContentType.objects.get(app_label=field['app_label'], model=field['model'])
                model_class = content_type.model_class()
                if field_type == 'object':
                    instance = model_class.objects.get(pk=value['object_id'])
                    url = instance.get_absolute_url()
                    result += f'<li>{field_name}: <a href="{url}">{instance}</a></li>'
                    continue
                if field_type == 'multiobject':
                    result += f'<li>{field_name}: <ul>'
                    for item in value:
                        instance = model_class.objects.get(pk=item['object_id'])
                        url = instance.get_absolute_url()
                        result += f'<li><a href="{url}">{instance}</a></li>'
                    result += '</ul></li>'
                    continue
            result += f"<li>{field_name}: {value}</li>"
        result += '</ul>'
        return result

    @property
    def fields(self):
        result = {}
        for field in self.custom_object_type.fields.all():
            result[field.name] = self.get_field_value(field.name)
        return result

    def get_field_value(self, field_name):
        custom_object_type_field = self.custom_object_type.fields.get(name=field_name)
        if custom_object_type_field.field_type == 'object':
            object_ids = CustomObjectRelation.objects.filter(
                custom_object=self, field=custom_object_type_field
            ).values_list('object_id', flat=True)
            field_objects = custom_object_type_field.model_class.objects.filter(pk__in=object_ids)
            return field_objects if custom_object_type_field.many else field_objects.first()
        return self.data.get(field_name)

    def get_absolute_url(self):
        return reverse('plugins:netbox_service_mappings:customobject', args=[self.pk])


class CustomObjectTypeField(NetBoxModel):
    name = models.CharField(max_length=100, unique=True)
    label = models.CharField(max_length=100, unique=True)
    custom_object_type = models.ForeignKey(CustomObjectType, on_delete=models.CASCADE, related_name="fields")
    field_type = models.CharField(max_length=100, choices=CustomFieldTypeChoices)

    # For non-object fields, other field attribs (such as choices, length, required) should be added here as a
    # superset, or stored in a JSON field
    options = models.JSONField(blank=True, default=dict)

    content_type = models.ForeignKey(ContentType, null=True, blank=True, on_delete=models.CASCADE)
    many = models.BooleanField(default=False)

    def __str__(self):
        return self.name

    @property
    def model_class(self):
        return apps.get_model(self.content_type.app_label, self.content_type.model)

    @property
    def is_single_value(self):
        return self.field_type != 'object' or not self.many

    def get_child_relations(self, instance):
        return self.relations.filter(custom_object=instance)

    def get_absolute_url(self):
        return reverse('plugins:netbox_service_mappings:customobjecttype', args=[self.custom_object_type.pk])


class CustomObjectRelation(models.Model):
    custom_object = models.ForeignKey(CustomObject, on_delete=models.CASCADE)
    field = models.ForeignKey(CustomObjectTypeField, on_delete=models.CASCADE, related_name="relations")
    object_id = models.PositiveIntegerField(db_index=True)

    @property
    def instance(self):
        model_class = self.field.content_type.model_class()
        return model_class.objects.get(pk=self.object_id)
