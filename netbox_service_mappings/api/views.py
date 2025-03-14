from rest_framework.routers import APIRootView
from rest_framework.viewsets import ModelViewSet

from netbox_service_mappings.models import ServiceMapping, ServiceMappingType, MappingTypeField, MappingRelation
from . import serializers


class RootView(APIRootView):
    def get_view_name(self):
        return 'ServiceMappings'


class ServiceMappingTypeViewSet(ModelViewSet):
    queryset = ServiceMappingType.objects.all()
    serializer_class = serializers.ServiceMappingTypeSerializer
    # filterset_class = filtersets.BranchFilterSet


class ServiceMappingViewSet(ModelViewSet):
    queryset = ServiceMapping.objects.all()
    serializer_class = serializers.ServiceMappingSerializer
    # filterset_class = filtersets.BranchFilterSet


class MappingTypeFieldViewSet(ModelViewSet):
    queryset = MappingTypeField.objects.all()
    serializer_class = serializers.MappingTypeFieldSerializer


class MappingRelationViewSet(ModelViewSet):
    queryset = MappingRelation.objects.all()
    serializer_class = serializers.MappingRelationSerializer
