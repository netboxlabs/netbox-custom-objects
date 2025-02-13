from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from core.choices import ObjectChangeActionChoices
from netbox.api.exceptions import SerializerNotFound
from netbox.api.fields import ChoiceField, ContentTypeField
from netbox.api.serializers import NetBoxModelSerializer
# from netbox_branching.choices import BranchEventTypeChoices, BranchStatusChoices
from netbox_service_mappings.models import ServiceMapping, ServiceMappingType
from users.api.serializers import UserSerializer
from utilities.api import get_serializer_for_model

__all__ = (
    'ServiceMappingTypeSerializer',
    'ServiceMappingSerializer',
)


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
            'id', 'url', 'name', 'description', 'tags', 'custom_fields', 'created', 'last_updated',
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
            'id', 'url', 'name', 'type', 'tags', 'custom_fields', 'created', 'last_updated',
        ]
        brief_fields = ('id', 'url', 'name', 'type',)

    def create(self, validated_data):
        """
        Record the user who created the Service Mapping as its owner.
        """
        # validated_data['owner'] = self.context['request'].user
        return super().create(validated_data)
