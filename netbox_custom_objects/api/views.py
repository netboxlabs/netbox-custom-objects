from django.http import Http404
from rest_framework.routers import APIRootView
from rest_framework.viewsets import ModelViewSet

from netbox_custom_objects.models import CustomObjectType, CustomObjectTypeField

from . import serializers


class RootView(APIRootView):
    def get_view_name(self):
        return "CustomObjects"


class CustomObjectTypeViewSet(ModelViewSet):
    queryset = CustomObjectType.objects.all()
    serializer_class = serializers.CustomObjectTypeSerializer


class CustomObjectViewSet(ModelViewSet):
    serializer_class = serializers.CustomObjectSerializer
    model = None

    def get_view_name(self):
        if self.model:
            return self.model.custom_object_type.name
        return 'Custom Object'

    def get_serializer_class(self):
        return serializers.get_serializer_class(self.model)

    def get_queryset(self):
        try:
            custom_object_type = CustomObjectType.objects.get(
                name__iexact=self.kwargs["custom_object_type"]
            )
        except CustomObjectType.DoesNotExist:
            raise Http404
        self.model = custom_object_type.get_model()
        return self.model.objects.all()

    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)


class CustomObjectTypeFieldViewSet(ModelViewSet):
    queryset = CustomObjectTypeField.objects.all()
    serializer_class = serializers.CustomObjectTypeFieldSerializer
