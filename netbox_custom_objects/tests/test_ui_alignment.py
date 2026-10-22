"""
Custom-object pages follow core NetBox's markup and behaviour.
"""

import re
from types import SimpleNamespace
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils.safestring import mark_safe
from django_htmx.middleware import HtmxDetails
from extras.choices import (
    CustomFieldFilterLogicChoices,
    CustomFieldUIEditableChoices,
    CustomFieldUIVisibleChoices,
)

from core.models import ObjectType
from dcim.models import Site
from users.models import ObjectPermission

from netbox_custom_objects.dynamic_forms import build_filterset_form_class
from netbox_custom_objects.forms import CustomObjectTypeFilterForm
from netbox_custom_objects.related_tabs.views.combined import _make_tab_view
from netbox_custom_objects.templatetags.custom_object_buttons import custom_object_clone_button
from netbox_custom_objects.templatetags.custom_object_utils import sticky_actions_supported
from netbox_custom_objects.views import CustomObjectDeleteView, CustomObjectListView

from .base import CustomObjectsTestCase

URL_PREFIX = "plugins:netbox_custom_objects"


class CustomObjectPageTestCase(CustomObjectsTestCase, TestCase):

    def setUp(self):
        super().setUp()
        self.user.is_superuser = True
        self.user.save()
        self.site = Site.objects.create(name="HQ", slug="hq")
        self.cot = self.create_custom_object_type(name="zone", slug="zones")
        self.create_custom_object_type_field(
            self.cot, name="name", label="Name", type="text", primary=True, required=True,
        )
        self.create_custom_object_type_field(self.cot, name="code", label="Code", type="text")
        self.create_custom_object_type_field(
            self.cot, name="notes", label="Notes", type="text", ui_visible=CustomFieldUIVisibleChoices.IF_SET,
        )
        self.create_custom_object_type_field(
            self.cot, name="secret", label="Secret", type="text", ui_editable=CustomFieldUIEditableChoices.HIDDEN,
        )
        self.create_custom_object_type_field(
            self.cot, name="unfiltered", label="Unfiltered", type="text",
            filter_logic=CustomFieldFilterLogicChoices.FILTER_DISABLED,
        )
        self.create_custom_object_type_field(
            self.cot, name="site", label="Site", type="object",
            related_object_type=self.get_site_object_type(), group_name="Details",
        )
        self.model = self.cot.get_model()
        self.obj = self.model.objects.create(name="Alpha", site=self.site)

    def _url(self, action, **kwargs):
        return reverse(f"{URL_PREFIX}:customobject_{action}", kwargs={"custom_object_type": "zones", **kwargs})

    def _get(self, url, **kwargs):
        response = self.client.get(url, **kwargs)
        self.assertEqual(response.status_code, 200)
        return response


class DetailPageTestCase(CustomObjectPageTestCase):

    def test_subtitle_and_no_last_activity(self):
        response = self._get(self.obj.get_absolute_url())
        self.assertContains(response, "mdi-calendar-plus")
        self.assertNotContains(response, "Last activity")

    def test_type_page_has_no_last_activity(self):
        self.assertNotContains(self._get(self.cot.get_absolute_url()), "Last activity")

    def test_plugin_buttons_rendered(self):
        target = "utilities.templatetags.plugins._get_registered_content"
        with mock.patch(target, return_value=mark_safe("<b>plugin-button</b>")) as registered:
            response = self._get(self.obj.get_absolute_url())
        self.assertContains(response, "<b>plugin-button</b>")
        self.assertIn("buttons", [call.args[1] for call in registered.call_args_list])

    def test_delete_button_opens_modal(self):
        response = self._get(self.obj.get_absolute_url())
        self.assertContains(response, f'hx-get="{self._url("delete", pk=self.obj.pk)}"')
        self.assertContains(response, 'data-bs-target="#htmx-modal"')


class CloneButtonTestCase(CustomObjectPageTestCase):

    def test_hidden_without_cloneable_fields(self):
        self.assertIsNone(custom_object_clone_button(self.obj)["url"])

    def test_carries_cloneable_values(self):
        self.cot.fields.filter(name="code").update(is_cloneable=True)
        self.obj.code = "AL"
        self.assertEqual(custom_object_clone_button(self.obj)["url"], f"{self._url('add')}?code=AL")


class DeleteReturnURLTestCase(CustomObjectPageTestCase):

    def _return_url(self, obj, query=""):
        view = CustomObjectDeleteView()
        view.kwargs = {}
        return view.get_return_url(RequestFactory().get(f"/delete/{query}"), obj)

    def test_honours_safe_return_url(self):
        self.assertEqual(self._return_url(self.obj, "?return_url=/dcim/sites/"), "/dcim/sites/")

    def test_ignores_unsafe_return_url(self):
        self.assertEqual(self._return_url(self.obj, "?return_url=https://example.com/"), self.obj.get_absolute_url())

    def test_falls_back_to_object_then_list(self):
        self.assertEqual(self._return_url(self.obj), self.obj.get_absolute_url())
        self.obj.pk = None
        self.assertEqual(self._return_url(self.obj), self._url("list"))

    def test_full_page_cancel_goes_back_to_object(self):
        response = self._get(self._url("delete", pk=self.obj.pk))
        self.assertContains(response, f'<a href="{self.obj.get_absolute_url()}" class="btn btn-outline-secondary">')


class BulkEditFooterTestCase(CustomObjectPageTestCase):

    def _page(self):
        response = self.client.post(self._url("bulk_edit"), {"pk": [self.obj.pk]})
        self.assertEqual(response.status_code, 200)
        return response

    def test_sticky_footer(self):
        response = self._page()
        self.assertContains(response, "sticky-actions-footer", count=1)
        self.assertNotContains(response, "btn-float")

    def test_legacy_footer_before_netbox_4_6(self):
        with override_settings(RELEASE=SimpleNamespace(version="4.5.2")):
            self.assertFalse(sticky_actions_supported())
            response = self._page()
        self.assertContains(response, "btn-float-group-right", count=1)
        self.assertNotContains(response, "sticky-actions-footer")

    def test_htmx_partial_leaves_footer_in_place(self):
        response = self._get(self._url("bulk_edit"), HTTP_HX_REQUEST="true")
        self.assertContains(response, 'name="changelog_message"')
        self.assertNotContains(response, 'name="_apply"')


class BulkImportPaneTestCase(CustomObjectPageTestCase):

    def test_direct_pane_active_by_default(self):
        response = self._get(self._url("bulk_import"))
        self.assertContains(response, 'class="tab-pane show active" id="import-form"')
        self.assertContains(response, 'class="tab-pane show" id="upload-form"')

    def test_failed_upload_shows_upload_pane(self):
        response = self.client.post(self._url("bulk_import"), {
            "import_method": "upload",
            "format": "auto",
            "csv_delimiter": "auto",
            "upload_file": SimpleUploadedFile("bad.csv", b"name,nonexistent\nx,y\n", content_type="text/csv"),
        })
        self.assertContains(response, 'class="tab-pane show active" id="upload-form"')
        self.assertContains(response, 'class="tab-pane show" id="import-form"')


class ListTableTestCase(CustomObjectPageTestCase):

    def test_default_and_available_columns(self):
        view = CustomObjectListView()
        view.custom_object_type = self.cot
        request = RequestFactory().get("/")
        request.user = self.user
        table = view.get_table(self.model.objects.all(), request)

        self.assertEqual(
            [name for name, _ in table.selected_columns],
            ["id", "code", "name", "secret", "unfiltered", "site", "tags"],
        )
        available = [name for name, _ in table.available_columns]
        for name in ("notes", "created", "last_updated"):
            self.assertIn(name, available)


class FilterFormTestCase(CustomObjectPageTestCase):

    def test_hidden_and_unfilterable_fields_left_out(self):
        form = build_filterset_form_class(self.model)()
        self.assertNotIn("secret", form.fields)
        self.assertNotIn("unfiltered", form.fields)
        self.assertIn("notes", form.fields)

    def test_fieldsets_group_fields(self):
        fieldsets = build_filterset_form_class(self.model).fieldsets
        self.assertEqual(
            [(fieldset.name, list(fieldset.items)) for fieldset in fieldsets],
            [
                (None, ["q", "filter_id", "tag"]),
                ("Zone", ["code", "name", "notes"]),
                ("Details", ["site"]),
                ("Ownership", ["owner_group_id", "owner_id"]),
            ],
        )

    def test_type_filter_form_offers_type_attributes(self):
        form = CustomObjectTypeFilterForm()
        self.assertIn("group_name", form.fields)
        self.assertIn("config_context_enabled", form.fields)

        other = self.create_custom_object_type(name="other", slug="others", group_name="Racks")
        response = self._get(reverse(f"{URL_PREFIX}:customobjecttype_list"), data={"group_name": "Racks"})
        self.assertContains(response, f'href="{other.get_absolute_url()}"')
        self.assertNotContains(response, f'href="{self.cot.get_absolute_url()}"')


class EditFormTestCase(CustomObjectPageTestCase):

    def test_titles_and_headings(self):
        for url, title in (
            (self._url("add"), "Add a new Zone"),
            (self._url("edit", pk=self.obj.pk), "Editing Zone Alpha"),
        ):
            with self.subTest(url=url):
                response = self._get(url)
                page_title = re.search(r'<h1 class="page-title">(.*?)</h1>', response.content.decode(), re.S)
                self.assertEqual(" ".join(page_title.group(1).split()), title)
                self.assertContains(response, '<h2 class="col-9 offset-3">Zone</h2>')
                self.assertContains(response, '<h2 class="col-9 offset-3">Details</h2>')
                self.assertNotContains(response, "btn-float")


class CombinedTabTestCase(CustomObjectPageTestCase):

    def _tab(self):
        # Tabs aren't registered under the test runner, so call the view directly.
        request = RequestFactory().get("/")
        request.user = self.user
        request.htmx = HtmxDetails(request)
        response = _make_tab_view(Site).as_view()(request, pk=self.site.pk)
        self.assertEqual(response.status_code, 200)
        return response

    def test_modals_rendered_once(self):
        self.assertContains(self._tab(), 'id="htmx-modal"', count=1)

    def test_delete_before_changelog(self):
        content = self._tab().content.decode()
        delete = content.index('mdi-trash-can-outline" aria-hidden="true"></i> Delete')
        changelog = content.index('mdi-history" aria-hidden="true"></i> Changelog')
        self.assertLess(delete, changelog)

    def test_toggle_secondary_without_edit(self):
        self.user.is_superuser = False
        self.user.save()
        perm = ObjectPermission.objects.create(name="view", actions=["view"])
        perm.users.add(self.user)
        perm.object_types.add(ObjectType.objects.get_for_model(Site), ObjectType.objects.get_for_model(self.model))
        response = self._tab()
        self.assertContains(response, "btn btn-sm btn-secondary dropdown-toggle")
        self.assertNotContains(response, "btn btn-sm btn-warning dropdown-toggle")
