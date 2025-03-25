from netbox.api.routers import NetBoxRouter
from . import views

router = NetBoxRouter()
router.APIRootView = views.RootView
router.register('custom-object-types', views.CustomObjectTypeViewSet)
router.register('custom-objects', views.CustomObjectViewSet)
router.register('custom-object-type-fields', views.CustomObjectTypeFieldViewSet)
# router.register('mapping-relations', views.MappingRelationViewSet)

urlpatterns = router.urls
