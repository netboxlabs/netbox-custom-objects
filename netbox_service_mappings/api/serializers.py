from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from core.choices import ObjectChangeActionChoices
from netbox.api.exceptions import SerializerNotFound
from netbox.api.fields import ChoiceField, ContentTypeField
from netbox.api.serializers import NetBoxModelSerializer
# from netbox_branching.choices import BranchEventTypeChoices, BranchStatusChoices
from netbox_service_mappings.models import ServiceMapping, ServiceMappingType, MappingTypeField, MappingRelation
from users.api.serializers import UserSerializer
from utilities.api import get_serializer_for_model

__all__ = (
    'ServiceMappingTypeSerializer',
    'ServiceMappingSerializer',
)

from utilities.templatetags.mptt import nested_tree


class ServiceMappingTypeSerializer(NetBoxModelSerializer):
    url = serializers.HyperlinkedIdentityField(
        view_name='plugins-api:netbox_service_mappings-api:servicemappingtype-detail'
    )
    # owner = UserSerializer(
    #     nested=True,
    #     read_only=True
    # )
    # merged_by = UserSerializer(
    #     nested=True,
    #     read_only=True
    # )
    # status = ChoiceField(
    #     choices=BranchStatusChoices
    # )

    class Meta:
        model = ServiceMappingType
        fields = [
            'id', 'url', 'name', 'description', 'tags', 'created', 'last_updated', 'fields',
        ]
        brief_fields = ('id', 'url', 'name', 'description')

    def create(self, validated_data):
        """
        Record the user who created the Service Mapping Type as its owner.
        """
        # validated_data['owner'] = self.context['request'].user
        return super().create(validated_data)


class ServiceMappingSerializer(NetBoxModelSerializer):
    url = serializers.HyperlinkedIdentityField(
        view_name='plugins-api:netbox_service_mappings-api:servicemapping-detail'
    )
    data = serializers.SerializerMethodField(
        read_only=True
    )
    # owner = UserSerializer(
    #     nested=True,
    #     read_only=True
    # )
    # merged_by = UserSerializer(
    #     nested=True,
    #     read_only=True
    # )
    # status = ChoiceField(
    #     choices=BranchStatusChoices
    # )

    class Meta:
        model = ServiceMapping
        fields = [
            'id', 'url', 'name', 'mapping_type', 'tags', 'created', 'last_updated', 'data',
        ]
        brief_fields = ('id', 'url', 'name', 'type',)

    def create(self, validated_data):
        """
        Record the user who created the Service Mapping as its owner.
        """
        # validated_data['owner'] = self.context['request'].user
        return super().create(validated_data)

    def get_data(self, obj):
        result = {}
        for field_name, value in obj.fields.items():
            field = obj.mapping_type.fields.get(name=field_name)
            if field.field_type == 'object':
                serializer = get_serializer_for_model(field.model_class)
                context = {'request': self.context['request']}
                result[field.name] = serializer(value, nested=True, context=context, many=field.many).data
                continue
            result[field_name] = obj.get_field_value(field_name)
        return result


class MappingTypeFieldSerializer(NetBoxModelSerializer):
    url = serializers.HyperlinkedIdentityField(
        view_name='plugins-api:netbox_service_mappings-api:mappingtypefield-detail'
    )
    content_type = serializers.SerializerMethodField(
        read_only=True
    )

    class Meta:
        model = MappingTypeField
        fields = ('id', 'url', 'name', 'label', 'field_type', 'content_type', 'many',)

    def create(self, validated_data):
        """
        Record the user who created the Service Mapping as its owner.
        """
        # validated_data['owner'] = self.context['request'].user
        return super().create(validated_data)

    def get_content_type(self, obj):
        if obj.content_type:
            return dict(
                id=obj.content_type.id,
                app_label=obj.content_type.app_label,
                model=obj.content_type.model,
            )


class MappingRelationSerializer(NetBoxModelSerializer):
    url = serializers.HyperlinkedIdentityField(
        view_name='plugins-api:netbox_service_mappings-api:mappingrelation-detail'
    )
    instance = serializers.SerializerMethodField(
        read_only=True
    )
    field = serializers.SerializerMethodField(
        read_only=True
    )

    class Meta:
        model = MappingRelation
        fields = ('mapping', 'field', 'object_id', 'instance',)

    def get_field(self, obj):
        return MappingTypeFieldSerializer(obj.field).data

    def get_instance(self, obj):
        if obj.instance:
            serializer = get_serializer_for_model(obj.instance)
            context = {'request': self.context['request']}
            return serializer(obj.instance, nested=True, context=context).data
