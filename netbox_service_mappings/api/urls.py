from netbox.api.routers import NetBoxRouter
from . import views

router = NetBoxRouter()
router.APIRootView = views.RootView
router.register('mapping-types', views.ServiceMappingTypeViewSet)
router.register('mappings', views.ServiceMappingViewSet)
# router.register('branch-events', views.BranchEventViewSet)
# router.register('changes', views.ChangeDiffViewSet)

urlpatterns = router.urls
