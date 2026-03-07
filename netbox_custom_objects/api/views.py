from django.contrib.contenttypes.models import ContentType
from django.http import Http404
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_view, extend_schema
from extras.choices import CustomFieldTypeChoices
from rest_framework.response import Response
from rest_framework.routers import APIRootView
from rest_framework.views import APIView
from rest_framework.viewsets import ModelViewSet
from rest_framework.exceptions import ValidationError

from netbox_custom_objects.filtersets import get_filterset_class
from netbox_custom_objects.models import CustomObjectType, CustomObjectTypeField
from netbox_custom_objects.utilities import is_in_branch

from . import serializers

# Constants
BRANCH_ACTIVE_ERROR_MESSAGE = _("Please switch to the main branch to perform this operation.")


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

    def create(self, request, *args, **kwargs):
        if is_in_branch():
            raise ValidationError(BRANCH_ACTIVE_ERROR_MESSAGE)
        return super().create(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        if is_in_branch():
            raise ValidationError(BRANCH_ACTIVE_ERROR_MESSAGE)
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        if is_in_branch():
            raise ValidationError(BRANCH_ACTIVE_ERROR_MESSAGE)
        return super().partial_update(request, *args, **kwargs)


class CustomObjectTypeFieldViewSet(ModelViewSet):
    queryset = CustomObjectTypeField.objects.all()
    serializer_class = serializers.CustomObjectTypeFieldSerializer


class LinkedObjectsView(APIView):
    """
    Returns all custom objects that link to a specific NetBox object via an `object` or
    `multiobject` field.

    ## Query Parameters

    * **`object_type`** *(required)* — target model in `app_label.model` form, e.g. `dcim.device`
    * **`object_id`** *(required)* — primary key of the target object

    ## Example Response

        {
            "count": 1,
            "results": [
                {
                    "custom_object_type": {"id": 1, "name": "My Type", "slug": "my-type"},
                    "field_name": "device",
                    "object": {"id": 7, "display": "My Custom Object", ...}
                }
            ]
        }
    """

    # This view queries across multiple unrelated custom object type models so there is
    # no single queryset to derive object-type permissions from.  Authentication is still
    # enforced; object-level permission checks are bypassed here and delegated to the
    # individual serializers / querysets used when building the results.
    _ignore_model_permissions = True

    def get(self, request, *args, **kwargs):
        object_type_str = request.query_params.get('object_type')
        object_id = request.query_params.get('object_id')

        if not object_type_str or not object_id:
            raise ValidationError(
                _("Both 'object_type' and 'object_id' query parameters are required.")
            )

        try:
            app_label, model_name = object_type_str.split('.', 1)
        except ValueError:
            raise ValidationError(
                _("'object_type' must be in the format 'app_label.model'.")
            )

        try:
            content_type = ContentType.objects.get(app_label=app_label, model=model_name)
        except ContentType.DoesNotExist:
            raise ValidationError(
                _("Object type '%(object_type)s' does not exist.") % {'object_type': object_type_str}
            )

        model_class = content_type.model_class()
        try:
            target_obj = model_class.objects.get(pk=object_id)
        except (model_class.DoesNotExist, ValueError):
            raise Http404

        fields = CustomObjectTypeField.objects.filter(
            related_object_type=content_type,
            type__in=[CustomFieldTypeChoices.TYPE_OBJECT, CustomFieldTypeChoices.TYPE_MULTIOBJECT],
        ).select_related('custom_object_type')

        results = []
        for field in fields:
            custom_object_model = field.custom_object_type.get_model(no_cache=True)

            if field.type == CustomFieldTypeChoices.TYPE_MULTIOBJECT:
                m2m_field = custom_object_model._meta.get_field(field.name)
                through_model = m2m_field.remote_field.through
                linked_ids = through_model.objects.filter(
                    target_id=target_obj.pk
                ).values_list('source_id', flat=True)
                linked_objects = custom_object_model.objects.filter(pk__in=linked_ids)
            else:
                linked_objects = custom_object_model.objects.filter(**{field.name: target_obj})

            serializer_class = serializers.get_serializer_class(custom_object_model)
            for linked_obj in linked_objects:
                results.append({
                    'custom_object_type': serializers.CustomObjectTypeSerializer(
                        field.custom_object_type, nested=True, context={'request': request}
                    ).data,
                    'field_name': field.name,
                    'object': serializer_class(linked_obj, context={'request': request}).data,
                })

        return Response({'count': len(results), 'results': results})
