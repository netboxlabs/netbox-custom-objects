from django.urls import include, path
from utilities.urls import get_model_urls

from . import views
from .constants import APP_LABEL

urlpatterns = [
    path(
        "custom_object_types/",
        views.CustomObjectTypeListView.as_view(),
        name="customobjecttype_list",
    ),
    path(
        "custom_object_types/add/",
        views.CustomObjectTypeEditView.as_view(),
        name="customobjecttype_add",
    ),
    path(
        "custom_object_types/<int:pk>/",
        include(get_model_urls(APP_LABEL, "customobjecttype")),
    ),
    path(
        "custom_object_type_fields/<int:pk>/",
        include(get_model_urls(APP_LABEL, "customobjecttypefield")),
    ),
    path(
        "custom_object_type_fields/add/",
        views.CustomObjectTypeFieldEditView.as_view(),
        name="customobjecttypefield_add",
    ),
    path(
        "<str:custom_object_type>/",
        views.CustomObjectListView.as_view(),
        name="customobject_list",
    ),
    path(
        "<str:custom_object_type>/add/",
        views.CustomObjectEditView.as_view(),
        name="customobject_add",
    ),
    path(
        "<str:custom_object_type>/delete/",
        views.CustomObjectDeleteView.as_view(),
        name="customobject_delete",
    ),
    path(
        "<str:custom_object_type>/bulk_edit/",
        views.CustomObjectBulkEditView.as_view(),
        name="customobject_bulk_edit",
    ),
    path(
        "<str:custom_object_type>/bulk_delete/",
        views.CustomObjectBulkDeleteView.as_view(),
        name="customobject_bulk_delete",
    ),
    path(
        "<str:custom_object_type>/<int:pk>/",
        views.CustomObjectView.as_view(),
        name="customobject",
    ),
    path(
        "<str:custom_object_type>/<int:pk>/edit/",
        views.CustomObjectEditView.as_view(),
        name="customobject_edit",
    ),
    path(
        "<str:custom_object_type>/<int:pk>/delete/",
        views.CustomObjectDeleteView.as_view(),
        name="customobject_delete",
    ),
]
