from netbox.api.routers import NetBoxRouter
from . import views

router = NetBoxRouter()
router.APIRootView = views.RootView
router.register('mapping-types', views.ServiceMappingTypeViewSet)
router.register('mappings', views.ServiceMappingViewSet)
router.register('mapping-type-fields', views.MappingTypeFieldViewSet)
# router.register('mapping-relations', views.MappingRelationViewSet)

urlpatterns = router.urls
