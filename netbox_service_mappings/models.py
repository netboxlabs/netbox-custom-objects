import jsonschema

from django.db import models
from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
from netbox.models import NetBoxModel
from .choices import MappingFieldTypeChoices


class ServiceMappingType(NetBoxModel):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    schema = models.JSONField(blank=True, default=dict)

    def __str__(self):
        return self.name

    @property
    def formatted_schema(self):
        result = '<ul>'
        for field_name, field in self.schema.items():
            field_type = field.get('type')
            if field_type in ['object', 'object_list']:
                content_type = ContentType.objects.get(app_label=field['app_label'], model=field['model'])
                field = content_type
            result += f"<li>{field_name}: {field}</li>"
        result += '</ul>'
        return result

    def get_absolute_url(self):
        return reverse('plugins:netbox_service_mappings:servicemappingtype', args=[self.pk])


class ServiceMapping(NetBoxModel):
    mapping_type = models.ForeignKey(ServiceMappingType, on_delete=models.CASCADE, related_name="mappings")
    name = models.CharField(max_length=100, unique=True)
    data = models.JSONField(blank=True, default=dict)

    def __str__(self):
        return self.name

    @property
    def formatted_data(self):
        result = '<ul>'
        for field_name, field in self.mapping_type.schema.items():
            value = self.data.get(field_name)
            field_type = field.get('type')
            if field_type in ['object', 'object_list']:
                content_type = ContentType.objects.get(app_label=field['app_label'], model=field['model'])
                model_class = content_type.model_class()
                if field_type == 'object':
                    instance = model_class.objects.get(pk=value['object_id'])
                    url = instance.get_absolute_url()
                    result += f'<li>{field_name}: <a href="{url}">{instance}</a></li>'
                    continue
                if field_type == 'object_list':
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
        for field in self.mapping_type.fields.all():
            result[field.name] = self.get_field_value(field.name)
        return result

    def get_field_value(self, field_name):
        mapping_type_field = self.mapping_type.fields.get(name=field_name)
        if mapping_type_field.field_type == 'object':
            object_ids = MappingRelation.objects.filter(
                mapping=self, field=mapping_type_field
            ).values_list('object_id', flat=True)
            field_objects = mapping_type_field.model_class.objects.filter(pk__in=object_ids)
            return field_objects if mapping_type_field.many else field_objects.first()
        return self.data.get(field_name)

    def get_absolute_url(self):
        return reverse('plugins:netbox_service_mappings:servicemapping', args=[self.pk])


class MappingTypeField(models.Model):
    name = models.CharField(max_length=100, unique=True)
    label = models.CharField(max_length=100, unique=True)
    mapping_type = models.ForeignKey(ServiceMappingType, on_delete=models.CASCADE, related_name="fields")
    field_type = models.CharField(max_length=100, choices=MappingFieldTypeChoices)

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
        return self.relations.filter(mapping=instance)


class MappingRelation(models.Model):
    mapping = models.ForeignKey(ServiceMapping, on_delete=models.CASCADE)
    field = models.ForeignKey(MappingTypeField, on_delete=models.CASCADE, related_name="relations")
    object_id = models.PositiveIntegerField(db_index=True)

    @property
    def instance(self):
        model_class = self.field.content_type.model_class()
        return model_class.objects.get(pk=self.object_id)
