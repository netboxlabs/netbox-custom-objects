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
    _ignore_model_permissions = True
    schema = None  # exclude from schema
    api_root_dict = None

    def get(self, request, *args, **kwargs):
        # Return a plain {"name": "hyperlink"} response.
        ret = {}
        namespace = request.resolver_match.namespace
        for key, url_name in self.api_root_dict.items():
            local_kwargs = deepcopy(kwargs)
            if isinstance(url_name, tuple):
                url_name, cot_name = url_name
                local_kwargs['custom_object_type'] = cot_name
            if namespace:
                url_name = namespace + ':' + url_name
            try:
                ret[key] = reverse(
                    url_name,
                    args=args,
                    kwargs=local_kwargs,
                    request=request,
                    format=local_kwargs.get('format')
                )
            except NoReverseMatch:
                # Don't bail out if eg. no list routes exist, only detail routes.
                continue

        return Response(ret)


class CustomObjectsRouter(NetBoxRouter):
    """
    Extends NetBoxRouter to populate the root level of the plugin in the browseable API with list views
    of all dynamically generated custom object types
    """
    def get_api_root_view(self, api_urls=None):
        """
        Wrap DRF's DefaultRouter to return an alphabetized list of endpoints.
        """
        api_root_dict = {}
        list_name = self.routes[0].name
        for prefix, viewset, basename in sorted(self.registry, key=lambda x: x[0]):
            api_root_dict[prefix] = list_name.format(basename=basename)

        for custom_object_type in CustomObjectType.objects.all():
            cot_name = custom_object_type.name.lower()
            api_root_dict[cot_name] = (list_name.format(basename='customobject'), cot_name)

        return self.APIRootView.as_view(api_root_dict=api_root_dict)


router = CustomObjectsRouter()
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
