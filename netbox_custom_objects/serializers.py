import jsonschema

from rest_framework import serializers

from netbox_custom_objects.models import CustomObject

# Serializers (unused)
# class CustomObjectSerializer(serializers.ModelSerializer):
#     class Meta:
#         model = CustomObject
#         fields = ['id', 'name', 'data']


# class ServiceMappingElementSerializer(serializers.ModelSerializer):
#     def validate_data(self, value):
#         try:
#             jsonschema.validate(instance=value, schema=SCHEMA)
#         except jsonschema.ValidationError as e:
#             raise serializers.ValidationError(f"Invalid JSON: {e.message}")
#         return value
#
#     class Meta:
#         model = ServiceMappingElement
#         fields = ['id', 'mapping', 'data']