from django.urls import include, path

from utilities.urls import get_model_urls
from . import views

urlpatterns = [
    path('mapping_types/', views.ServiceMappingTypeListView.as_view(), name='service_mapping_type_list'),
    # Branches
    path('mappings/', views.ServiceMappingListView.as_view(), name='service_mapping_list'),
    # path('mappings/add/', views.BranchEditView.as_view(), name='service_mapping_add'),
    # path('mappings/import/', views.BranchBulkImportView.as_view(), name='service_mapping_bulk_import'),
    # path('mappings/edit/', views.BranchBulkEditView.as_view(), name='service_mapping_bulk_edit'),
    # path('mappings/delete/', views.BranchBulkDeleteView.as_view(), name='service_mapping_bulk_delete'),
    # path('mappings/<int:pk>/', include(get_model_urls('netbox_service_mappings', 'service_mapping'))),
]
