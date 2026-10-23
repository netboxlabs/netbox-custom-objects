"""
The combined "Custom Objects" tab, requested over HTTP.

Custom-object hosts use the tab's real route, which the plugin injects from ``ready()``.
``ready()`` returns early under the test runner, so ``CombinedTabTestCase`` injects it
for the class and removes it afterwards.  Built-in hosts are routed by a test URLconf
(see ``combined_tab_urls``).
"""
import re

from django.test import TestCase, override_settings
from django.urls import clear_url_caches, reverse

from core.models import ObjectType
from dcim.models import Site
from extras.models import Tag
from users.models import ObjectPermission

import netbox_custom_objects.urls as co_urls
from netbox_custom_objects.related_tabs.registry import CO_COMBINED_URL_NAME, _inject_co_urls

from .base import CustomObjectsTestCase

TAB_URL_NAME = f"plugins:netbox_custom_objects:{CO_COMBINED_URL_NAME}"
PARTIAL_TEMPLATE = "netbox_custom_objects/related_tabs/combined/tab_partial.html"
FULL_TEMPLATE = "netbox_custom_objects/related_tabs/combined/tab.html"


def _name_field(cls, cot):
    cls.create_custom_object_type_field(cot, name="name", label="Name", type="text", primary=True)


class CombinedTabMixin:

    def _rows(self, response):
        """Return each row's object, in table order, read from its Object cell (the second default column)."""
        body = re.search(r"<tbody>(.*?)</tbody>", response.content.decode(), re.S)
        if body is None:
            return []
        rows = []
        for row in re.findall(r"<tr>(.*?)</tr>", body.group(1), re.S):
            cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
            rows.append(re.sub(r"<[^>]+>", "", cells[1]).strip())
        return rows

    def assertTabLink(self, response, url, badge, active=False):
        """Assert the page's tab strip links to the combined tab with *badge*."""
        nav_class = "nav-link active" if active else "nav-link"
        self.assertRegex(
            response.content.decode(),
            rf'href="{re.escape(url)}" class="{nav_class}">\s*Custom Objects\s*<span class="badge[^"]*">{badge}</span>',
        )

    def _grant_view(self, *models):
        perm = ObjectPermission.objects.create(name="view", actions=["view"])
        perm.users.add(self.user)
        perm.object_types.add(*(ObjectType.objects.get_for_model(model) for model in models))


class CombinedTabTestCase(CombinedTabMixin, CustomObjectsTestCase, TestCase):

    @classmethod
    def setUpClass(cls):
        injected = CO_COMBINED_URL_NAME not in {getattr(p, "name", None) for p in co_urls.urlpatterns}
        _inject_co_urls()
        clear_url_caches()
        if injected:
            cls.addClassCleanup(cls._remove_tab_route)
        super().setUpClass()

    @staticmethod
    def _remove_tab_route():
        co_urls.urlpatterns[:] = [p for p in co_urls.urlpatterns if getattr(p, "name", None) != CO_COMBINED_URL_NAME]
        clear_url_caches()

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.zone_cot = cls.create_custom_object_type(name="zone", slug="zones")
        _name_field(cls, cls.zone_cot)
        zone_type = ObjectType.objects.get_for_model(cls.zone_cot.get_model())

        cls.note_cot = cls.create_custom_object_type(name="note", slug="notes")
        _name_field(cls, cls.note_cot)
        cls.create_custom_object_type_field(
            cls.note_cot, name="zone", label="Located in", type="object", related_object_type=zone_type,
        )
        cls.link_cot = cls.create_custom_object_type(name="link", slug="links")
        _name_field(cls, cls.link_cot)
        cls.create_custom_object_type_field(
            cls.link_cot, name="zones", label="Covers", type="multiobject", related_object_type=zone_type,
        )

        # Generate the referencing models before the target's (see CustomObjectType.get_model).
        note_model = cls.note_cot.get_model()
        link_model = cls.link_cot.get_model()
        cls.zone_cot.refresh_from_db()
        zone_model = cls.zone_cot.get_model()

        cls.zone = zone_model.objects.create(name="Zone One")
        cls.other_zone = zone_model.objects.create(name="Zone Two")
        red = Tag.objects.create(name="Red", slug="red")
        blue = Tag.objects.create(name="Blue", slug="blue")

        note_model.objects.create(name="Note Alpha", zone=cls.zone).tags.add(red)
        note_model.objects.create(name="Note Bravo", zone=cls.zone)
        note_model.objects.create(name="Note Charlie", zone=cls.zone).tags.add(blue)
        note_model.objects.create(name="Note Echo", zone=cls.other_zone)
        link = link_model.objects.create(name="Link Delta")
        link.zones.set([cls.zone, cls.other_zone])
        link.tags.add(red)

    def setUp(self):
        super().setUp()
        self.user.is_superuser = True
        self.user.save()
        self.url = reverse(TAB_URL_NAME, kwargs={"custom_object_type": "zones", "pk": self.zone.pk})

    def _get(self, url=None, **params):
        response = self.client.get(url or self.url, params)
        self.assertEqual(response.status_code, 200)
        return response

    def test_lists_objects_linking_to_the_host(self):
        response = self._get()
        self.assertTemplateUsed(response, FULL_TEMPLATE)
        self.assertCountEqual(self._rows(response), ["Note Alpha", "Note Bravo", "Note Charlie", "Link Delta"])
        # Value column: the multi-object field lists every target, not just the host.
        self.assertContains(response, f'href="{self.other_zone.get_absolute_url()}"')
        self.assertContains(response, "Located in")
        self.assertContains(response, "Covers")

    def test_tab_is_active_with_badge(self):
        self.assertTabLink(self._get(), self.url, 4, active=True)

    def test_detail_page_links_to_tab(self):
        self.assertTabLink(self._get(self.zone.get_absolute_url()), self.url, 4)

    def test_quick_search(self):
        for q, expected in (
            ("bravo", ["Note Bravo"]),
            # Matches the type name, then the field label.
            ("link", ["Link Delta"]),
            ("located", ["Note Alpha", "Note Bravo", "Note Charlie"]),
        ):
            with self.subTest(q=q):
                self.assertCountEqual(self._rows(self._get(q=q)), expected)

    def test_type_filter(self):
        response = self._get(type="links")
        self.assertEqual(self._rows(response), ["Link Delta"])
        self.assertContains(response, '<option value="links" selected>')
        # The choices come from every linked object, not just the filtered ones.
        self.assertContains(response, '<option value="notes">')

    def test_tag_filter(self):
        response = self._get(tag="red")
        self.assertCountEqual(self._rows(response), ["Note Alpha", "Link Delta"])
        self.assertContains(response, '<option value="red" selected>')
        self.assertContains(response, '<option value="blue">')

    def test_no_matches(self):
        response = self._get(q="nothing")
        self.assertEqual(self._rows(response), [])
        self.assertContains(response, "No custom objects match your filters.")

    def test_sort(self):
        for sort, expected in (
            ("object", ["Link Delta", "Note Alpha", "Note Bravo", "Note Charlie"]),
            ("-object", ["Note Charlie", "Note Bravo", "Note Alpha", "Link Delta"]),
            ("-field", ["Note Alpha", "Note Bravo", "Note Charlie", "Link Delta"]),
        ):
            with self.subTest(sort=sort):
                rows = self._rows(self._get(sort=sort))
                if sort == "-field":
                    # Sorting is stable, so only the field order is fixed.
                    self.assertCountEqual(rows[:3], expected[:3])
                    self.assertEqual(rows[3], expected[3])
                else:
                    self.assertEqual(rows, expected)

    def test_sort_headers_keep_filters(self):
        response = self._get(q="note", sort="object")
        self.assertContains(response, '<th class="asc orderable">')
        # The active column toggles to descending; its clear link drops only the sort.
        self.assertContains(response, 'hx-get="?q=note&amp;sort=-object"')
        self.assertContains(response, 'hx-get="?q=note" class="text-danger"')
        self.assertContains(response, 'hx-get="?q=note&amp;sort=type"')

    def test_pagination(self):
        # Core's paginator moves up to five trailing rows onto the previous page, so
        # two pages of two need at least eight rows.
        extra = [f"Note Page {i}" for i in range(1, 7)]
        note_model = self.note_cot.get_model()
        for name in extra:
            note_model.objects.create(name=name, zone=self.zone)
        for page, expected in (
            ("1", ["Link Delta", "Note Alpha"]),
            ("2", ["Note Bravo", "Note Charlie"]),
            # Out-of-range and malformed pages fall back to the first.
            ("9", ["Link Delta", "Note Alpha"]),
            ("x", ["Link Delta", "Note Alpha"]),
        ):
            with self.subTest(page=page):
                self.assertEqual(self._rows(self._get(sort="object", per_page=2, page=page)), expected)

    def test_htmx_request_renders_partial(self):
        response = self.client.get(self.url, {"q": "bravo"}, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, PARTIAL_TEMPLATE)
        self.assertTemplateNotUsed(response, FULL_TEMPLATE)
        self.assertEqual(self._rows(response), ["Note Bravo"])
        self.assertContains(response, 'id="custom_objects_list"', count=1)

    def test_unknown_host_is_404(self):
        for kwargs in (
            {"custom_object_type": "missing", "pk": self.zone.pk},
            {"custom_object_type": "zones", "pk": 0},
        ):
            with self.subTest(**kwargs):
                self.assertEqual(self.client.get(reverse(TAB_URL_NAME, kwargs=kwargs)).status_code, 404)

    def test_rows_restricted_to_viewable_types(self):
        self.user.is_superuser = False
        self.user.save()
        self._grant_view(self.zone_cot.get_model(), self.note_cot.get_model())
        response = self._get()
        self.assertCountEqual(self._rows(response), ["Note Alpha", "Note Bravo", "Note Charlie"])
        self.assertTabLink(response, self.url, 3, active=True)

    def test_no_viewable_rows(self):
        self.user.is_superuser = False
        self.user.save()
        self._grant_view(self.zone_cot.get_model())
        response = self._get()
        self.assertEqual(self._rows(response), [])
        self.assertContains(response, "No custom objects are linked to this object.")
        # The detail page hides the tab.
        self.assertNotContains(self._get(self.zone.get_absolute_url()), self.url)

    def test_host_without_view_permission_is_404(self):
        self.user.is_superuser = False
        self.user.save()
        self._grant_view(self.note_cot.get_model(), self.link_cot.get_model())
        self.assertEqual(self.client.get(self.url).status_code, 404)


@override_settings(ROOT_URLCONF="netbox_custom_objects.tests.combined_tab_urls")
class CombinedTabBuiltinHostTestCase(CombinedTabMixin, CustomObjectsTestCase, TestCase):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cot = cls.create_custom_object_type(name="site_note", slug="site-notes")
        _name_field(cls, cot)
        cls.create_custom_object_type_field(
            cot, name="site", label="Site", type="object", related_object_type=ObjectType.objects.get_for_model(Site),
        )
        cls.note_model = cot.get_model()
        cls.site = Site.objects.create(name="HQ", slug="hq")
        other = Site.objects.create(name="Branch", slug="branch")
        cls.note_model.objects.create(name="Note Alpha", site=cls.site)
        cls.note_model.objects.create(name="Note Bravo", site=other)
        cls.url = f"/dcim/sites/{cls.site.pk}/custom-objects/"

    def test_lists_objects_linking_to_the_host(self):
        self.user.is_superuser = True
        self.user.save()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, FULL_TEMPLATE)
        self.assertEqual(self._rows(response), ["Note Alpha"])

    def test_htmx_request_renders_partial(self):
        self.user.is_superuser = True
        self.user.save()
        response = self.client.get(self.url, HTTP_HX_REQUEST="true")
        self.assertTemplateUsed(response, PARTIAL_TEMPLATE)
        self.assertTemplateNotUsed(response, FULL_TEMPLATE)
        self.assertEqual(self._rows(response), ["Note Alpha"])

    def test_rows_restricted_to_viewable_types(self):
        self._grant_view(Site)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._rows(response), [])

    def test_host_without_view_permission_is_404(self):
        self._grant_view(self.note_model)
        self.assertEqual(self.client.get(self.url).status_code, 404)
