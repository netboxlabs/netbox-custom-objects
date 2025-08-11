from django.urls import include, path
from utilities.urls import get_model_urls

from . import views
from .constants import APP_LABEL

app_name = APP_LABEL

urlpatterns = [
    path('custom-object-types/', include(get_model_urls(APP_LABEL, 'customobjecttype', detail=False))),
    path('custom-object-types/<int:pk>/', include(get_model_urls(APP_LABEL, 'customobjecttype'))),

    # Custom Object Type Fields
    path(
        "custom-object-type-fields/<int:pk>/",
        include(get_model_urls(APP_LABEL, "customobjecttypefield")),
    ),
    path(
        "custom-object-type-fields/add/",
        views.CustomObjectTypeFieldEditView.as_view(),
        name="customobjecttypefield_add",
    ),

    # Journal Entries (must come before custom object patterns)
    path(
        "journal-entries/add/",
        views.CustomJournalEntryEditView.as_view(),
        name="custom_journalentry_add",
    ),

    # Custom Objects
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
        "<str:custom_object_type>/bulk-edit/",
        views.CustomObjectBulkEditView.as_view(),
        name="customobject_bulk_edit",
    ),
    path(
        "<str:custom_object_type>/bulk-delete/",
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
    path(
        "<str:custom_object_type>/<int:pk>/journal/",
        views.CustomObjectJournalView.as_view(),
        name="customobject_journal",
    ),
    path(
        "<str:custom_object_type>/<int:pk>/changelog/",
        views.CustomObjectChangeLogView.as_view(),
        name="customobject_changelog",
    ),
]
