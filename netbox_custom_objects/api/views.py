from django.http import Http404
from drf_spectacular.utils import extend_schema_view, extend_schema
from rest_framework.routers import APIRootView
from rest_framework.viewsets import ModelViewSet

from netbox_custom_objects.filtersets import get_filterset_class
from netbox_custom_objects.models import CustomObjectType, CustomObjectTypeField

from . import serializers


class RootView(APIRootView):
    def get_view_name(self):
        return "CustomObjects"


class CustomObjectTypeViewSet(ModelViewSet):
    queryset = CustomObjectType.objects.all()
    serializer_class = serializers.CustomObjectTypeSerializer


# TODO: Need to remove this for now, check if work-around in the future.
# There is a catch-22 spectacular get the queryset and serializer class without
# params at startup.  The suggested workaround is to return the model empty
# queryset, but we can't get the model without params at startup.
@extend_schema_view(
    list=extend_schema(exclude=True),
    retrieve=extend_schema(exclude=True),
    create=extend_schema(exclude=True),
    update=extend_schema(exclude=True),
    partial_update=extend_schema(exclude=True),
    destroy=extend_schema(exclude=True)
)
class CustomObjectViewSet(ModelViewSet):
    serializer_class = serializers.CustomObjectSerializer
    model = None

    def get_view_name(self):
        if self.model:
            return self.model.custom_object_type.display_name
        return 'Custom Object'

    def get_serializer_class(self):
        return serializers.get_serializer_class(self.model)

    def get_queryset(self):
        try:
            custom_object_type = CustomObjectType.objects.get(
                slug=self.kwargs["custom_object_type"]
            )
        except CustomObjectType.DoesNotExist:
            raise Http404
        self.model = custom_object_type.get_model_with_serializer()
        return self.model.objects.all()

    @property
    def filterset_class(self):
        return get_filterset_class(self.model)

    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)


class CustomObjectTypeFieldViewSet(ModelViewSet):
    queryset = CustomObjectTypeField.objects.all()
    serializer_class = serializers.CustomObjectTypeFieldSerializer
