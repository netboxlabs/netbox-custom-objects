from copy import deepcopy
from django.urls import include, path, NoReverseMatch
from rest_framework.response import Response
from rest_framework.reverse import reverse
from rest_framework.views import APIView
from netbox.api.routers import NetBoxRouter
from netbox_custom_objects.models import CustomObjectType

from . import views

custom_object_list = views.CustomObjectViewSet.as_view(
    {"get": "list", "post": "create"}
)
custom_object_detail = views.CustomObjectViewSet.as_view(
    {"get": "retrieve", "put": "update", "patch": "partial_update", "delete": "destroy"}
)


class CustomObjectsAPIRootView(APIView):
    """
    This is the root of the NetBox Custom Objects plugin API. Custom Object Types defined at application startup
    are listed by lowercased name; e.g. `/api/plugins/custom-objects/cat/`.
    """
    def get_view_name(self):
        return "Custom Objects API Root"

    _ignore_model_permissions = True
    schema = None  # exclude from schema
    api_root_dict = None

    # This logic is copied from stock DRF APIRootView
    def get(self, request, *args, **kwargs):
        # Return a plain {"name": "hyperlink"} response.
        ret = {}
        namespace = request.resolver_match.namespace
        for key, url_name in self.api_root_dict.items():
            if namespace:
                url_name = namespace + ':' + url_name
            try:
                ret[key] = reverse(
                    url_name,
                    args=args,
                    kwargs=kwargs,
                    request=request,
                    format=kwargs.get('format')
                )
            except NoReverseMatch:
                # Don't bail out if eg. no list routes exist, only detail routes.
                continue

        # Extra logic to populate roots for custom object type lists
        for custom_object_type in CustomObjectType.objects.all():
            local_kwargs = deepcopy(kwargs)
            cot_name = custom_object_type.name.lower()
            url_name = 'customobject-list'
            local_kwargs['custom_object_type'] = cot_name
            if namespace:
                url_name = namespace + ':' + url_name
            ret[cot_name] = reverse(
                url_name,
                args=args,
                kwargs=local_kwargs,
                request=request,
                format=local_kwargs.get('format')
            )

        return Response(ret)


router = NetBoxRouter()
router.APIRootView = CustomObjectsAPIRootView
router.register("custom-object-types", views.CustomObjectTypeViewSet)
router.register("custom-object-type-fields", views.CustomObjectTypeFieldViewSet)

urlpatterns = [
    path("", include(router.urls)),
    path("<str:custom_object_type>/", custom_object_list, name="customobject-list"),
    path(
        "<str:custom_object_type>/<int:pk>/",
        custom_object_detail,
        name="customobject-detail",
    ),
]
