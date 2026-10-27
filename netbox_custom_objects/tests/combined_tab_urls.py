"""
URLconf adding the combined "Custom Objects" tab on Site.

The plugin registers the tab on built-in models from ``ready()``, which returns early
under the test runner, so ``CombinedTabBuiltinHostTestCase`` routes the tab here.
"""

from django.urls import path

from dcim.models import Site
from netbox.urls import urlpatterns as netbox_urlpatterns
from netbox_custom_objects.related_tabs.views.combined import _make_tab_view

urlpatterns = [
    path('dcim/sites/<int:pk>/custom-objects/', _make_tab_view(Site).as_view()),
    *netbox_urlpatterns,
]
