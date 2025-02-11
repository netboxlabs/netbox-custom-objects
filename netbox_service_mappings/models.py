import jsonschema

from django.db import models
from django.core.exceptions import ValidationError
from django.db.models.base import DEFERRED
from netbox.models import NetBoxModel

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
ELEMENTS_SCHEMA = [
    {"name": "str_field", "type": "string", "max_length": 100, "choices": ["A", "B", "C"]},
    {"name": "int_field", "type": "integer", "min": 0, "max": 1000, "required": False},
    {"name": "bool_field", "type": "bool", "default": True},
    # {"type": "dict"},
    {"name": "object_field", "type": "object", "content_type_id": 10, "object_id": 123},
    {"name": "object_list_field", "type": "object_list", "content_type_id": 10, "object_id": 123},
]


class ServiceMappingType(NetBoxModel):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    version = models.CharField(max_length=10, unique=True)
    description = models.TextField(blank=True)
    schema = models.JSONField()

    def __str__(self):
        return self.name


class ServiceMapping(NetBoxModel):
    type = models.ForeignKey(ServiceMappingType, on_delete=models.CASCADE, related_name="mappings")
    name = models.CharField(max_length=100, unique=True)
    data = models.JSONField()

    def __init__(self, *args, **kwargs):
        cls = self.__class__
        opts = self._meta
        _setattr = setattr
        _DEFERRED = DEFERRED

        # fields_iter = iter(opts.concrete_fields)
        # for val, field in zip(args, fields_iter):
        #     print(val, field)
        #     if val is _DEFERRED:
        #         continue
        #     _setattr(self, field.attname, val)
        for field_spec in ELEMENTS_SCHEMA:
            print(field_spec)
            if field_spec["type"] == "string":
                field = models.CharField(max_length=field_spec["max_length"], choices=field_spec["choices"])
                _setattr(self, field_spec["name"], field)
        super().__init__()

    def clean(self):
        """Validate JSON field against schema."""
        try:
            jsonschema.validate(instance=self.data, schema=SCHEMA)
        except jsonschema.ValidationError as e:
            raise ValidationError(f"Invalid JSON data: {e.message}")

    def __str__(self):
        return f"[{self.id}] {self.type.name}"
