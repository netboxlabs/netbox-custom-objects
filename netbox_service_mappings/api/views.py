from django.core.exceptions import PermissionDenied
from django.http import HttpResponseBadRequest
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action
from rest_framework.mixins import ListModelMixin, RetrieveModelMixin
from rest_framework.response import Response
from rest_framework.routers import APIRootView
from rest_framework.viewsets import ModelViewSet

from core.api.serializers import JobSerializer
from netbox.api.viewsets import BaseViewSet, NetBoxReadOnlyModelViewSet
from netbox_branching import filtersets
from netbox_branching.jobs import MergeBranchJob, RevertBranchJob, SyncBranchJob
from netbox_service_mappings.models import ServiceMapping, ServiceMappingType
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
