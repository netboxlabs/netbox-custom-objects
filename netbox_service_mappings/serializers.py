import jsonschema

from rest_framework import serializers


# Serializers
class ServiceMappingSerializer(serializers.ModelSerializer):
    class Meta:
        model = ServiceMapping
        fields = ['id', 'name', 'description']

class ServiceMappingElementSerializer(serializers.ModelSerializer):
    def validate_data(self, value):
        try:
            jsonschema.validate(instance=value, schema=SCHEMA)
        except jsonschema.ValidationError as e:
            raise serializers.ValidationError(f"Invalid JSON: {e.message}")
        return value

    class Meta:
        model = ServiceMappingElement
        fields = ['id', 'mapping', 'data']