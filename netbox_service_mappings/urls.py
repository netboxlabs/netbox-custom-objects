from django.urls import include, path

from utilities.urls import get_model_urls
from . import views

urlpatterns = [
    path('mapping_types/', views.ServiceMappingTypeListView.as_view(), name='servicemappingtype_list'),
    path('mapping_types/add/', views.ServiceMappingTypeEditView.as_view(), name='servicemappingtype_add'),
    path('mapping_types/<int:pk>/', include(get_model_urls('netbox_service_mappings', 'servicemappingtype'))),
    path('mappings/', views.ServiceMappingListView.as_view(), name='servicemapping_list'),
    path('mappings/add/', views.ServiceMappingEditView.as_view(), name='servicemapping_add'),
    path('mappings/<int:pk>/', include(get_model_urls('netbox_service_mappings', 'servicemapping'))),
    path('mapping-type-fields/', include(get_model_urls('netbox_service_mappings', 'mappingtypefield', detail=False))),
    path('mapping-type-fields/add/', views.MappingTypeFieldEditView.as_view(), name='mappingtypefield_add'),
]
