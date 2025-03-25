from django.urls import include, path

from utilities.urls import get_model_urls
from . import views

urlpatterns = [
    path('custom_object_types/', views.CustomObjectTypeListView.as_view(), name='customobjecttype_list'),
    path('custom_object_types/add/', views.CustomObjectTypeEditView.as_view(), name='customobjecttype_add'),
    path('custom_object_types/<int:pk>/', include(get_model_urls('netbox_service_mappings', 'customobjecttype'))),
    path('custom_objects/', views.CustomObjectListView.as_view(), name='customobject_list'),
    path('custom_objects/add/', views.CustomObjectEditView.as_view(), name='customobject_add'),
    path('custom_objects/<int:pk>/', include(get_model_urls('netbox_service_mappings', 'customobject'))),
    path('custom_object_type_fields/', include(get_model_urls('netbox_service_mappings', 'customobjecttypefield', detail=False))),
    path('custom_object_type_fields/add/', views.CustomObjectTypeFieldEditView.as_view(), name='customobjecttypefield_add'),
]
