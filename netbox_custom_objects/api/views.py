from rest_framework.routers import APIRootView
from rest_framework.viewsets import ModelViewSet

from netbox_custom_objects import filtersets
from netbox_custom_objects.models import CustomObject, CustomObjectType, CustomObjectTypeField, CustomObjectRelation
from . import serializers
from ..views import CustomObjectTypeView


class RootView(APIRootView):
    def get_view_name(self):
        return 'CustomObjects'


class CustomObjectTypeViewSet(ModelViewSet):
    queryset = CustomObjectType.objects.all()
    serializer_class = serializers.CustomObjectTypeSerializer
    # filterset_class = filtersets.BranchFilterSet


class CustomObjectViewSet(ModelViewSet):
    queryset = CustomObject.objects.all()
    serializer_class = serializers.CustomObjectSerializer
    # filterset_class = filtersets.CustomObjectFilterSet

    def get_serializer_class(self):
        return self.serializer_class

    def get_queryset(self):
        custom_object_type = CustomObjectType.objects.get(slug=self.kwargs['custom_object_type'])
        model = custom_object_type.get_model()
        return model.objects.all()


class CustomObjectTypeFieldViewSet(ModelViewSet):
    queryset = CustomObjectTypeField.objects.all()
    serializer_class = serializers.CustomObjectTypeFieldSerializer


class CustomObjectRelationViewSet(ModelViewSet):
    queryset = CustomObjectRelation.objects.all()
    serializer_class = serializers.CustomObjectRelationSerializer
