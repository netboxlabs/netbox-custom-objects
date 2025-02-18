import jsonschema

from django.db import models
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db.models.base import DEFERRED
from django.urls import reverse
from netbox.models import NetBoxModel
from .choices import MappingFieldTypeChoices

# Define JSON Schema for validation
SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "description": {"type": "string"},
        "port": {"type": "integer", "minimum": 1, "maximum": 65535}
    },
    "required": ["name", "port"]
}
ELEMENTS_SCHEMA = {
    "blah": {"type": "string", "max_length": 100, "choices": ["A", "B", "C"]},
    "int_field": {"type": "integer", "min": 0, "max": 1000, "required": False},
    "bool_field": {"type": "bool", "default": True},
    # {"type": "dict"},
    "object_field": {"type": "object", "app_label": "dcim", "model": "devicetype"},
    "object_list_field": {"type": "object_list", "app_label": "dcim", "model": "device"},
}


class ServiceMappingType(NetBoxModel):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    version = models.CharField(max_length=10, unique=True)
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

    # def __init__(self, *args, **kwargs):
    #     cls = self.__class__
    #     opts = self._meta
    #     _setattr = setattr
    #     _DEFERRED = DEFERRED
    #
    #     # fields_iter = iter(opts.concrete_fields)
    #     # for val, field in zip(args, fields_iter):
    #     #     print(val, field)
    #     #     if val is _DEFERRED:
    #     #         continue
    #     #     _setattr(self, field.attname, val)
    #     for field_spec in ELEMENTS_SCHEMA:
    #         print(field_spec)
    #         if field_spec["type"] == "string":
    #             field = models.CharField(max_length=field_spec["max_length"], choices=field_spec["choices"])
    #             _setattr(self, field_spec["name"], field)
    #     super().__init__()

    def clean(self):
        """Validate JSON field against schema."""
        try:
            jsonschema.validate(instance=self.data, schema=SCHEMA)
        except jsonschema.ValidationError as e:
            raise ValidationError(f"Invalid JSON data: {e.message}")

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

    def get_absolute_url(self):
        return reverse('plugins:netbox_service_mappings:servicemapping', args=[self.pk])


class MappingTypeField(models.Model):
    name = models.CharField(max_length=100, unique=True)
    label = models.CharField(max_length=100, unique=True)
    mapping_type = models.ForeignKey(ServiceMappingType, on_delete=models.CASCADE, related_name="fields")
    field_type = models.CharField(max_length=100, choices=MappingFieldTypeChoices)
    # For non-object fields, other field attribs (such as choices, length, required) should be added here as a
    # superset, or stored in a JSON field

    content_type = models.ForeignKey(ContentType, null=True, on_delete=models.CASCADE)
    many = models.BooleanField(default=False)

    @property
    def instance(self):
        if self.many:
            return None
        if relation := self.relations.first():
            return relation.instance
        return None


class MappingRelation(models.Model):
    mapping = models.ForeignKey(ServiceMapping, on_delete=models.CASCADE)
    field = models.ForeignKey(MappingTypeField, on_delete=models.CASCADE, related_name="relations")
    object_id = models.PositiveIntegerField()

    @property
    def instance(self):
        model_class = self.field.content_type.model_class()
        return model_class.objects.get(pk=self.object_id)
