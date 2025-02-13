from django.urls import include, path

from utilities.urls import get_model_urls
from . import views

urlpatterns = [
    path('mapping_types/', views.ServiceMappingTypeListView.as_view(), name='servicemappingtype_list'),
    path('mapping_types/add/', views.ServiceMappingTypeEditView.as_view(), name='servicemappingtype_add'),
    path('mapping_types/<int:pk>/', include(get_model_urls('netbox_service_mappings', 'servicemappingtype'))),
    # Branches
    path('mappings/', views.ServiceMappingListView.as_view(), name='servicemapping_list'),
    path('mappings/add/', views.ServiceMappingEditView.as_view(), name='servicemapping_add'),
    # path('mappings/import/', views.BranchBulkImportView.as_view(), name='service_mapping_bulk_import'),
    # path('mappings/edit/', views.BranchBulkEditView.as_view(), name='service_mapping_bulk_edit'),
    # path('mappings/delete/', views.BranchBulkDeleteView.as_view(), name='service_mapping_bulk_delete'),
    path('mappings/<int:pk>/', include(get_model_urls('netbox_service_mappings', 'servicemapping'))),
]
