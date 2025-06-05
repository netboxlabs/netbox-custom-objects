from rest_framework.urlpatterns import format_suffix_patterns
from django.urls import path, include

from netbox.api.routers import NetBoxRouter
from . import views


custom_object_list = views.CustomObjectViewSet.as_view({
    'get': 'list',
    'post': 'create'
})
custom_object_detail = views.CustomObjectViewSet.as_view({
    'get': 'retrieve',
    'put': 'update',
    'patch': 'partial_update',
    'delete': 'destroy'
})

router = NetBoxRouter()
router.APIRootView = views.RootView
router.register('custom-object-types', views.CustomObjectTypeViewSet)
# router.register('custom-objects', views.CustomObjectViewSet)
router.register('custom-object-type-fields', views.CustomObjectTypeFieldViewSet)
# router.register('mapping-relations', views.MappingRelationViewSet)

# urlpatterns = router.urls

urlpatterns = [
    path('', include(router.urls)),
    path('<str:custom_object_type>/', custom_object_list, name='custom-object-list'),
    path('<str:custom_object_type>/<int:pk>/', custom_object_detail, name='custom-object-detail'),
]
# urlpatterns = format_suffix_patterns(urlpatterns, allowed=['json', 'html'])
