from django.urls import include, path

from utilities.urls import get_model_urls
from . import views

urlpatterns = [
    path('custom_object_types/', views.CustomObjectTypeListView.as_view(), name='customobjecttype_list'),
    path('custom_object_types/add/', views.CustomObjectTypeEditView.as_view(), name='customobjecttype_add'),
    path('custom_object_types/<int:pk>/', include(get_model_urls('netbox_custom_objects', 'customobjecttype'))),
    # path('custom_objects/', views.CustomObjectListView.as_view(), name='customobject_list'),
    # path('custom_objects/add/', views.CustomObjectEditView.as_view(), name='customobject_add'),
    path('custom_objects/<int:pk>/', include(get_model_urls('netbox_custom_objects', 'customobject'))),

    path('custom_object_type_fields/<int:pk>/', include(get_model_urls('netbox_custom_objects', 'customobjecttypefield'))),
    path('custom_object_type_fields/add/', views.CustomObjectTypeFieldEditView.as_view(), name='customobjecttypefield_add'),
    path('<str:custom_object_type>/', views.CustomObjectListView.as_view(), name='customobject_list'),
    path('<str:custom_object_type>/add/', views.CustomObjectEditView.as_view(), name='customobject_add'),
    path('<str:custom_object_type>/bulk_edit/', views.CustomObjectBulkEditView.as_view(), name='customobject_bulk_edit'),
    path('<str:custom_object_type>/bulk_delete/', views.CustomObjectBulkDeleteView.as_view(), name='customobject_bulk_delete'),
    path('<str:custom_object_type>/<int:pk>/', include(get_model_urls('netbox_custom_objects', 'customobject'))),
]
