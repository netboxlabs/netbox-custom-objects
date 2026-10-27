"""
Tests for how each field type's values are read, written and validated through the REST API.
"""
from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from utilities.testing import TestCase as NetBoxTestCase, create_test_user
from rest_framework import status
from rest_framework.test import APIClient

from netbox_custom_objects.models import CustomObjectType
from ..base import CustomObjectsTestCase, create_token
from core.models import ObjectType
from users.models import ObjectPermission


class ValidationRegexAPITestCase(CustomObjectsTestCase, TestCase):
    """validation_regex must be enforced on REST API writes, not just in the UI form."""

    def setUp(self):
        self.user = create_test_user('regexapiuser')
        self.client = APIClient()
        token_key = create_token(self.user)
        self.header = {'HTTP_AUTHORIZATION': f'Token {token_key}'}

        self.cot = CustomObjectsTestCase.create_custom_object_type(name='RegexAPITest', slug='regex-api-test')
        for name, field_type, regex in (
            ('code', 'text', r'^[A-Z]{3}$'),
            ('notes', 'longtext', r'^ok'),
            ('website', 'url', r'^https://'),
        ):
            CustomObjectsTestCase.create_custom_object_type_field(
                self.cot, name=name, label=name.title(), type=field_type, validation_regex=regex,
            )
        self.model = self.cot.get_model()

        perm = ObjectPermission(name='Regex API perm', actions=['add', 'change'])
        perm.save()
        perm.users.add(self.user)
        perm.object_types.add(ObjectType.objects.get_for_model(self.model))

    def _list_url(self):
        return reverse(
            'plugins-api:netbox_custom_objects-api:customobject-list',
            kwargs={'custom_object_type': self.cot.slug},
        )

    def test_create_rejects_values_not_matching_regex(self):
        for field_name, value in (('code', 'abc'), ('notes', 'not ok'), ('website', 'http://example.com')):
            with self.subTest(field=field_name):
                response = self.client.post(
                    self._list_url(), {field_name: value}, format='json', **self.header
                )
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.content)
                self.assertIn(field_name, response.json())
        self.assertFalse(self.model.objects.exists())

    def test_create_accepts_values_matching_regex(self):
        data = {'code': 'ABC', 'notes': 'ok then', 'website': 'https://example.com'}
        response = self.client.post(self._list_url(), data, format='json', **self.header)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.content)

    def test_update_rejects_value_not_matching_regex(self):
        instance = self.model.objects.create(code='ABC')
        url = reverse(
            'plugins-api:netbox_custom_objects-api:customobject-detail',
            kwargs={'pk': instance.pk, 'custom_object_type': self.cot.slug},
        )
        response = self.client.patch(url, {'code': 'abcd'}, format='json', **self.header)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.content)
        instance.refresh_from_db()
        self.assertEqual(instance.code, 'ABC')


class CoordinatesFieldAPITestCase(CustomObjectsTestCase, NetBoxTestCase):
    """REST API behaviour for the coordinates field type."""

    @classmethod
    def setUpTestData(cls):
        cls.cot = CustomObjectType.objects.create(
            name="GeoObject",
            verbose_name_plural="Geo Objects",
            slug="geo-objects",
        )
        cls.create_custom_object_type_field(
            cls.cot, name="name", type="text", primary=True, required=True
        )
        cls.create_custom_object_type_field(cls.cot, name="location", type="coordinates")
        cls.model = cls.cot.get_model()

    def setUp(self):
        super().setUp()
        self.user = create_test_user("geouser")
        self.client = APIClient()
        token_key = create_token(self.user)
        self.header = {"HTTP_AUTHORIZATION": f"Token {token_key}"}
        perm = ObjectPermission(
            name="geo all", actions=["view", "add", "change", "delete"]
        )
        perm.save()
        perm.users.add(self.user)
        perm.object_types.add(ObjectType.objects.get_for_model(self.model))

    def _list_url(self):
        return reverse(
            "plugins-api:netbox_custom_objects-api:customobject-list",
            kwargs={"custom_object_type": self.cot.slug},
        )

    def _detail_url(self, instance):
        return reverse(
            "plugins-api:netbox_custom_objects-api:customobject-detail",
            kwargs={"pk": instance.pk, "custom_object_type": self.cot.slug},
        )

    def test_serializer_exposes_flat_latitude_longitude(self):
        """The coordinate columns are serialized as two flat fields (NetBox convention)."""
        obj = self.model.objects.create(
            name="Box",
            location_latitude=Decimal("40.712800"),
            location_longitude=Decimal("-74.006000"),
        )
        response = self.client.get(self._detail_url(obj), **self.header)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(Decimal(response.data["location_latitude"]), Decimal("40.712800"))
        self.assertEqual(Decimal(response.data["location_longitude"]), Decimal("-74.006000"))
        self.assertNotIn("location", response.data)

    def test_create_with_coordinates(self):
        """Creating an object via the API persists both coordinate columns."""
        data = {
            "name": "Created box",
            "location_latitude": "41.000000",
            "location_longitude": "-75.000000",
        }
        response = self.client.post(self._list_url(), data, format="json", **self.header)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        obj = self.model.objects.get(pk=response.data["id"])
        self.assertEqual(obj.location_latitude, Decimal("41.000000"))
        self.assertEqual(obj.location_longitude, Decimal("-75.000000"))

    def test_create_half_populated_pair_rejected(self):
        """Setting only one of latitude/longitude is rejected, keyed to the empty field."""
        data = {"name": "Bad box", "location_latitude": "41.000000"}
        response = self.client.post(self._list_url(), data, format="json", **self.header)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.data)
        # The error is pinned to the missing field, not non_field_errors.
        self.assertIn("location_longitude", response.data)
        self.assertNotIn("non_field_errors", response.data)

    def test_create_out_of_range_rejected(self):
        """Latitude beyond ±90 is rejected by the model field validators."""
        data = {
            "name": "Out of range",
            "location_latitude": "120.000000",
            "location_longitude": "10.000000",
        }
        response = self.client.post(self._list_url(), data, format="json", **self.header)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.data)

    def test_patch_clearing_one_half_rejected(self):
        """A PATCH that clears only one coordinate of a populated pair is rejected."""
        obj = self.model.objects.create(
            name="Box",
            location_latitude=Decimal("40.712800"),
            location_longitude=Decimal("-74.006000"),
        )
        response = self.client.patch(
            self._detail_url(obj),
            {"location_latitude": None},
            format="json",
            **self.header,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.data)
        self.assertIn("location_latitude", response.data)
        # The DB row is left untouched.
        obj.refresh_from_db()
        self.assertEqual(obj.location_latitude, Decimal("40.712800"))
        self.assertEqual(obj.location_longitude, Decimal("-74.006000"))

    def test_patch_clearing_both_halves_allowed(self):
        """A PATCH clearing both coordinates at once is accepted."""
        obj = self.model.objects.create(
            name="Box",
            location_latitude=Decimal("40.712800"),
            location_longitude=Decimal("-74.006000"),
        )
        response = self.client.patch(
            self._detail_url(obj),
            {"location_latitude": None, "location_longitude": None},
            format="json",
            **self.header,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        obj.refresh_from_db()
        self.assertIsNone(obj.location_latitude)
        self.assertIsNone(obj.location_longitude)


class ArrayFieldAPITestCase(CustomObjectsTestCase, NetBoxTestCase):
    """REST API behaviour for the array field type."""

    @classmethod
    def setUpTestData(cls):
        cls.cot = CustomObjectType.objects.create(name="Zone", verbose_name_plural="Zones", slug="zones")
        cls.create_custom_object_type_field(cls.cot, name="name", type="text", primary=True, required=True)
        cls.create_custom_object_type_field(
            cls.cot, name="coordinates", type="array", array_element_type="integer", array_tuple_size=3,
            array_min_length=2, validation_minimum=0,
        )
        cls.create_custom_object_type_field(cls.cot, name="days", type="array", array_element_type="date")
        cls.model = cls.cot.get_model()

    def setUp(self):
        super().setUp()
        self.user = create_test_user("zoneuser")
        self.user.is_superuser = True
        self.user.save()
        self.client = APIClient()
        self.header = {"HTTP_AUTHORIZATION": f"Token {create_token(self.user)}"}

    def _list_url(self):
        return reverse(
            "plugins-api:netbox_custom_objects-api:customobject-list",
            kwargs={"custom_object_type": self.cot.slug},
        )

    def test_create_and_read(self):
        data = {"name": "Zone 1", "coordinates": [[0, 0, 0], [10, 0, 5]], "days": ["2026-01-01"]}
        response = self.client.post(self._list_url(), data, format="json", **self.header)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["coordinates"], [[0, 0, 0], [10, 0, 5]])
        self.assertEqual(response.data["days"], ["2026-01-01"])
        obj = self.model.objects.get(pk=response.data["id"])
        self.assertEqual(obj.coordinates, [[0, 0, 0], [10, 0, 5]])
        self.assertEqual(obj.days, [date(2026, 1, 1)])

    def test_invalid_values_rejected(self):
        cases = {
            "wrong tuple size": [[0, 0, 0], [1, 1]],
            "too few items": [[0, 0, 0]],
            "below the minimum": [[0, 0, 0], [-1, 0, 0]],
            "not integers": [[0, 0, 0], ["a", 0, 0]],
            "not a list": "0,0,0",
            "a fraction": [[0, 0, 0], [1, 1, 1.5]],
            "a boolean": [[0, 0, 0], [1, 1, True]],
        }
        for label, value in cases.items():
            with self.subTest(label):
                response = self.client.post(
                    self._list_url(), {"name": label, "coordinates": value}, format="json", **self.header
                )
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.data)
                self.assertIn("coordinates", response.data)
        self.assertFalse(self.model.objects.exists())

    def test_empty_list_is_null(self):
        # As in the form and CSV: stored as null, and the minimum length applies only to a non-empty list.
        data = {"name": "Empty", "coordinates": [], "days": []}
        response = self.client.post(self._list_url(), data, format="json", **self.header)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual((response.data["coordinates"], response.data["days"]), (None, None))
        obj = self.model.objects.get(pk=response.data["id"])
        self.assertEqual((obj.coordinates, obj.days), (None, None))

    def test_create_array_field(self):
        url = reverse("plugins-api:netbox_custom_objects-api:customobjecttypefield-list")
        data = {
            "custom_object_type": self.cot.pk, "name": "readings", "type": "array",
            "array_element_type": "decimal", "array_max_length": 10,
        }
        response = self.client.post(url, data, format="json", **self.header)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(
            (response.data["array_element_type"], response.data["array_tuple_size"], response.data["array_max_length"]),
            ("decimal", None, 10),
        )

        response = self.client.post(url, {**data, "name": "untyped", "array_element_type": ""}, format="json",
                                    **self.header)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.data)
        self.assertIn("array_element_type", response.data)


class ColorFieldAPITestCase(CustomObjectsTestCase, NetBoxTestCase):
    """REST API behaviour for the color field type."""

    @classmethod
    def setUpTestData(cls):
        cls.cot = CustomObjectType.objects.create(name="Paint", verbose_name_plural="Paints", slug="paints")
        cls.create_custom_object_type_field(cls.cot, name="name", type="text", primary=True, required=True)
        cls.create_custom_object_type_field(cls.cot, name="shade", type="color")
        cls.model = cls.cot.get_model()

    def setUp(self):
        super().setUp()
        self.user = create_test_user("paintuser")
        self.client = APIClient()
        self.header = {"HTTP_AUTHORIZATION": f"Token {create_token(self.user)}"}
        perm = ObjectPermission(name="paint all", actions=["view", "add", "change", "delete"])
        perm.save()
        perm.users.add(self.user)
        perm.object_types.add(ObjectType.objects.get_for_model(self.model))

    def _list_url(self):
        return reverse(
            "plugins-api:netbox_custom_objects-api:customobject-list",
            kwargs={"custom_object_type": self.cot.slug},
        )

    def test_create_and_read(self):
        response = self.client.post(self._list_url(), {"name": "Red", "shade": "f44336"}, format="json", **self.header)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["shade"], "f44336")
        self.assertEqual(self.model.objects.get(pk=response.data["id"]).shade, "f44336")

    def test_null_allowed(self):
        response = self.client.post(self._list_url(), {"name": "None", "shade": None}, format="json", **self.header)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertIsNone(response.data["shade"])

    def test_invalid_color_rejected(self):
        for value in ("F44336", "#f44336", "red", "f443361"):
            with self.subTest(value=value):
                response = self.client.post(
                    self._list_url(), {"name": "Bad", "shade": value}, format="json", **self.header
                )
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn("shade", response.data)


class MACAddressFieldAPITestCase(CustomObjectsTestCase, NetBoxTestCase):
    """REST API behaviour for the MAC address field type."""

    @classmethod
    def setUpTestData(cls):
        cls.cot = CustomObjectType.objects.create(name="Port", verbose_name_plural="Ports", slug="ports")
        cls.create_custom_object_type_field(cls.cot, name="name", type="text", primary=True, required=True)
        cls.create_custom_object_type_field(cls.cot, name="mac", type="mac_address")
        cls.model = cls.cot.get_model()

    def setUp(self):
        super().setUp()
        self.user = create_test_user("portuser")
        self.client = APIClient()
        self.header = {"HTTP_AUTHORIZATION": f"Token {create_token(self.user)}"}
        perm = ObjectPermission(name="port all", actions=["view", "add", "change", "delete"])
        perm.save()
        perm.users.add(self.user)
        perm.object_types.add(ObjectType.objects.get_for_model(self.model))

    def _list_url(self):
        return reverse(
            "plugins-api:netbox_custom_objects-api:customobject-list",
            kwargs={"custom_object_type": self.cot.slug},
        )

    def test_create_normalizes(self):
        response = self.client.post(
            self._list_url(), {"name": "eth0", "mac": "001a.2b3c.4d5e"}, format="json", **self.header
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["mac"], "00:1A:2B:3C:4D:5E")
        response = self.client.get(self._list_url(), {"mac": "00-1a-2b-3c-4d-5e"}, **self.header)
        self.assertEqual([row["mac"] for row in response.data["results"]], ["00:1A:2B:3C:4D:5E"])

    def test_null_or_blank_allowed(self):
        for value in (None, ""):
            with self.subTest(value=value):
                response = self.client.post(
                    self._list_url(), {"name": f"eth{value!r}", "mac": value}, format="json", **self.header
                )
                self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
                self.assertIsNone(response.data["mac"])

    def test_blank_rejected_when_required(self):
        field = self.cot.fields.get(name="mac")
        field.required = True
        field.save()
        response = self.client.post(self._list_url(), {"name": "eth9", "mac": ""}, format="json", **self.header)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("mac", response.data)

    def test_invalid_rejected(self):
        for value in ("00:1a:2b:3c:4d", "not a mac", "00:1a:2b:3c:4d:5e:6f:70"):
            with self.subTest(value=value):
                response = self.client.post(
                    self._list_url(), {"name": "bad", "mac": value}, format="json", **self.header
                )
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn("mac", response.data)

    def test_field_default_normalized(self):
        self.user.is_superuser = True
        self.user.save()
        url = reverse("plugins-api:netbox_custom_objects-api:customobjecttypefield-list")
        data = {"custom_object_type": self.cot.pk, "name": "uplink", "type": "mac_address", "default": "001a.2b3c.4d5e"}
        response = self.client.post(url, data, format="json", **self.header)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["default"], "00:1A:2B:3C:4D:5E")
        self.assertEqual(self.cot.fields.get(name="uplink").default, "00:1A:2B:3C:4D:5E")

        field = self.cot.fields.get(name="mac")
        url = reverse("plugins-api:netbox_custom_objects-api:customobjecttypefield-detail", kwargs={"pk": field.pk})
        response = self.client.patch(url, {"default": "aa-bb-cc-dd-ee-ff"}, format="json", **self.header)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        field.refresh_from_db()
        self.assertEqual(field.default, "AA:BB:CC:DD:EE:FF")


class URLFieldLinkTitleAPITestCase(CustomObjectsTestCase, NetBoxTestCase):
    """REST API behaviour for the url field type's link-title support."""

    @classmethod
    def setUpTestData(cls):
        cls.cot = CustomObjectType.objects.create(
            name="LinkObject",
            verbose_name_plural="Link Objects",
            slug="link-objects",
        )
        cls.create_custom_object_type_field(
            cls.cot, name="name", type="text", primary=True, required=True
        )
        cls.create_custom_object_type_field(cls.cot, name="website", type="url")
        cls.model = cls.cot.get_model()

    def setUp(self):
        super().setUp()
        self.user = create_test_user("linkuser")
        self.client = APIClient()
        token_key = create_token(self.user)
        self.header = {"HTTP_AUTHORIZATION": f"Token {token_key}"}
        perm = ObjectPermission(
            name="link all", actions=["view", "add", "change", "delete"]
        )
        perm.save()
        perm.users.add(self.user)
        perm.object_types.add(ObjectType.objects.get_for_model(self.model))

    def _list_url(self):
        return reverse(
            "plugins-api:netbox_custom_objects-api:customobject-list",
            kwargs={"custom_object_type": self.cot.slug},
        )

    def _detail_url(self, instance):
        return reverse(
            "plugins-api:netbox_custom_objects-api:customobject-detail",
            kwargs={"pk": instance.pk, "custom_object_type": self.cot.slug},
        )

    def test_serializer_exposes_flat_url_title(self):
        """The URL and title columns are serialized as two flat fields."""
        obj = self.model.objects.create(
            name="Box", website="https://example.com/", website_title="Example Site",
        )
        response = self.client.get(self._detail_url(obj), **self.header)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["website"], "https://example.com/")
        self.assertEqual(response.data["website_title"], "Example Site")

    def test_create_with_url_and_title(self):
        """Creating an object via the API persists both the URL and title columns."""
        data = {
            "name": "Created box",
            "website": "https://example.com/",
            "website_title": "Example Site",
        }
        response = self.client.post(self._list_url(), data, format="json", **self.header)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        obj = self.model.objects.get(pk=response.data["id"])
        self.assertEqual(obj.website, "https://example.com/")
        self.assertEqual(obj.website_title, "Example Site")

    def test_create_with_url_and_no_title_allowed(self):
        """Creating with a URL and no title is accepted (no both-required rule)."""
        data = {"name": "Created box", "website": "https://example.com/"}
        response = self.client.post(self._list_url(), data, format="json", **self.header)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        obj = self.model.objects.get(pk=response.data["id"])
        self.assertEqual(obj.website, "https://example.com/")
        self.assertEqual(obj.website_title, "")


class RequiredScalarFieldAPITestCase(CustomObjectsTestCase, NetBoxTestCase):
    """A scalar field marked "Required" must be enforced by the REST API, not just the UI form."""

    @classmethod
    def setUpTestData(cls):
        cls.cot = CustomObjectType.objects.create(
            name="RequiredScalarTest", slug="required-scalar-test",
        )
        cls.create_custom_object_type_field(
            cls.cot, name="name", type="text", primary=True, required=True,
        )
        cls.create_custom_object_type_field(cls.cot, name="req_text", type="text", required=True)
        cls.create_custom_object_type_field(cls.cot, name="req_longtext", type="longtext", required=True)
        cls.create_custom_object_type_field(cls.cot, name="req_int", type="integer", required=True)
        cls.create_custom_object_type_field(cls.cot, name="opt_int", type="integer", required=False)
        cls.create_custom_object_type_field(cls.cot, name="req_bool", type="boolean", required=True)
        cls.create_custom_object_type_field(cls.cot, name="req_url", type="url", required=True)
        cls.create_custom_object_type_field(cls.cot, name="req_coords", type="coordinates", required=True)
        cls.choice_set = cls.create_choice_set()
        cls.create_custom_object_type_field(
            cls.cot, name="req_select", type="select", required=True, choice_set=cls.choice_set,
        )
        cls.model = cls.cot.get_model()

    def setUp(self):
        super().setUp()
        self.user = create_test_user("requiredscalaruser")
        self.client = APIClient()
        token_key = create_token(self.user)
        self.header = {"HTTP_AUTHORIZATION": f"Token {token_key}"}
        perm = ObjectPermission(name="required scalar perm", actions=["view", "add"])
        perm.save()
        perm.users.add(self.user)
        perm.object_types.add(ObjectType.objects.get_for_model(self.model))

    def _list_url(self):
        return reverse(
            "plugins-api:netbox_custom_objects-api:customobject-list",
            kwargs={"custom_object_type": self.cot.slug},
        )

    def _full_payload(self):
        return {
            "name": "Full object",
            "req_text": "hello",
            "req_longtext": "hello at length",
            "req_int": 5,
            "opt_int": 7,
            "req_bool": True,
            "req_url": "https://example.com",
            "req_coords_latitude": "10.000000",
            "req_coords_longitude": "20.000000",
            "req_select": self.choice_set.extra_choices[0][0],
        }

    def test_post_omitting_required_fields_returns_400(self):
        response = self.client.post(
            self._list_url(), {"name": "Missing required fields"}, format="json", **self.header,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        for field_name in (
            "req_text", "req_longtext", "req_int", "req_bool", "req_url",
            "req_coords_latitude", "req_coords_longitude", "req_select",
        ):
            self.assertIn(field_name, response.data)

    def test_post_with_required_fields_present_returns_201(self):
        response = self.client.post(self._list_url(), self._full_payload(), format="json", **self.header)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

    def test_post_omitting_only_optional_field_returns_201(self):
        payload = self._full_payload()
        del payload["opt_int"]
        response = self.client.post(self._list_url(), payload, format="json", **self.header)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

    def test_options_reports_required_true_for_required_fields(self):
        response = self.client.options(self._list_url(), **self.header)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        post_actions = response.data["actions"]["POST"]
        for field_name in (
            "req_text", "req_longtext", "req_int", "req_bool", "req_url",
            "req_coords_latitude", "req_coords_longitude", "req_select",
        ):
            self.assertTrue(post_actions[field_name]["required"], field_name)

    def test_options_reports_required_false_for_optional_and_title_fields(self):
        response = self.client.options(self._list_url(), **self.header)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        post_actions = response.data["actions"]["POST"]
        for field_name in ("opt_int", "req_url_title"):
            self.assertFalse(post_actions[field_name]["required"], field_name)


class IntegerDecimalMinMaxAPITestCase(CustomObjectsTestCase, NetBoxTestCase):
    """validation_minimum/maximum must be enforced by the REST API, not just the UI form."""

    @classmethod
    def setUpTestData(cls):
        cls.cot = CustomObjectType.objects.create(
            name="MinMaxTest", slug="min-max-test",
        )
        cls.create_custom_object_type_field(
            cls.cot, name="name", type="text", primary=True, required=True,
        )
        cls.create_custom_object_type_field(
            cls.cot, name="bounded_int", type="integer",
            validation_minimum=0, validation_maximum=100,
        )
        cls.create_custom_object_type_field(
            cls.cot, name="bounded_dec", type="decimal",
            validation_minimum=0, validation_maximum=100,
        )
        cls.model = cls.cot.get_model()

    def setUp(self):
        super().setUp()
        self.user = create_test_user("minmaxuser")
        self.client = APIClient()
        token_key = create_token(self.user)
        self.header = {"HTTP_AUTHORIZATION": f"Token {token_key}"}
        perm = ObjectPermission(name="min max perm", actions=["view", "add"])
        perm.save()
        perm.users.add(self.user)
        perm.object_types.add(ObjectType.objects.get_for_model(self.model))

    def _list_url(self):
        return reverse(
            "plugins-api:netbox_custom_objects-api:customobject-list",
            kwargs={"custom_object_type": self.cot.slug},
        )

    def test_post_above_maximum_returns_400(self):
        data = {"name": "too big", "bounded_int": 101, "bounded_dec": "101.00"}
        response = self.client.post(self._list_url(), data, format="json", **self.header)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("bounded_int", response.data)
        self.assertIn("bounded_dec", response.data)

    def test_post_below_minimum_returns_400(self):
        data = {"name": "too small", "bounded_int": -1, "bounded_dec": "-1.00"}
        response = self.client.post(self._list_url(), data, format="json", **self.header)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("bounded_int", response.data)
        self.assertIn("bounded_dec", response.data)

    def test_post_within_range_returns_201(self):
        data = {"name": "just right", "bounded_int": 50, "bounded_dec": "50.00"}
        response = self.client.post(self._list_url(), data, format="json", **self.header)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

    def test_post_at_exact_boundaries_returns_201(self):
        for label, value_int, value_dec in (("minimum", 0, "0.00"), ("maximum", 100, "100.00")):
            with self.subTest(boundary=label):
                data = {
                    "name": f"exact {label}", "bounded_int": value_int, "bounded_dec": value_dec,
                }
                response = self.client.post(self._list_url(), data, format="json", **self.header)
                self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)


class SelectMultiSelectValueLabelAPITestCase(CustomObjectsTestCase, NetBoxTestCase):
    """select/multiselect fields must read as {value, label}, matching core ChoiceField."""

    @classmethod
    def setUpTestData(cls):
        cls.cot = CustomObjectType.objects.create(name="ValueLabelTest", slug="value-label-test")
        cls.create_custom_object_type_field(
            cls.cot, name="name", type="text", primary=True, required=True,
        )
        cls.choice_set = cls.create_choice_set()
        cls.create_custom_object_type_field(
            cls.cot, name="status", type="select", choice_set=cls.choice_set,
        )
        cls.create_custom_object_type_field(
            cls.cot, name="tags_field", type="multiselect", choice_set=cls.choice_set,
        )
        cls.model = cls.cot.get_model()

    def setUp(self):
        super().setUp()
        self.user = create_test_user("valuelabeluser")
        self.client = APIClient()
        token_key = create_token(self.user)
        self.header = {"HTTP_AUTHORIZATION": f"Token {token_key}"}
        perm = ObjectPermission(name="value label perm", actions=["view", "add"])
        perm.save()
        perm.users.add(self.user)
        perm.object_types.add(ObjectType.objects.get_for_model(self.model))

    def _list_url(self):
        return reverse(
            "plugins-api:netbox_custom_objects-api:customobject-list",
            kwargs={"custom_object_type": self.cot.slug},
        )

    def _detail_url(self, instance):
        return reverse(
            "plugins-api:netbox_custom_objects-api:customobject-detail",
            kwargs={"pk": instance.pk, "custom_object_type": self.cot.slug},
        )

    def test_select_field_reads_as_value_label(self):
        instance = self.model.objects.create(name="obj", status="choice2")
        response = self.client.get(self._detail_url(instance), **self.header)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], {"value": "choice2", "label": "Choice 2"})

    def test_multiselect_field_reads_as_list_of_value_label(self):
        instance = self.model.objects.create(name="obj", tags_field=["choice1", "choice3"])
        response = self.client.get(self._detail_url(instance), **self.header)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data["tags_field"],
            [
                {"value": "choice1", "label": "Choice 1"},
                {"value": "choice3", "label": "Choice 3"},
            ],
        )

    def test_select_field_reads_as_none_when_unset(self):
        instance = self.model.objects.create(name="obj")
        response = self.client.get(self._detail_url(instance), **self.header)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data["status"])

    def test_multiselect_field_reads_as_none_when_unset(self):
        instance = self.model.objects.create(name="obj")
        response = self.client.get(self._detail_url(instance), **self.header)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data["tags_field"])

    def test_select_and_multiselect_still_accept_bare_values_on_write(self):
        data = {"name": "written", "status": "choice1", "tags_field": ["choice2"]}
        response = self.client.post(self._list_url(), data, format="json", **self.header)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        instance = self.model.objects.get(pk=response.data["id"])
        self.assertEqual(instance.status, "choice1")
        self.assertEqual(instance.tags_field, ["choice2"])


class SelectMultiSelectNumericChoiceValueAPITestCase(CustomObjectsTestCase, NetBoxTestCase):
    """
    Choice values are always raw admin-typed strings, so a value like "1" or "true"
    must round-trip through the API even though core's ChoiceField would otherwise
    coerce it to int/bool before checking choice membership.
    """

    @classmethod
    def setUpTestData(cls):
        cls.cot = CustomObjectType.objects.create(name="NumericChoiceTest", slug="numeric-choice-test")
        cls.create_custom_object_type_field(
            cls.cot, name="name", type="text", primary=True, required=True,
        )
        cls.choice_set = cls.create_choice_set(
            name="Numeric Choice Set",
            extra_choices=[
                ["1", "One"],
                ["2", "Two"],
                ["true", "Enabled"],
            ],
        )
        cls.create_custom_object_type_field(
            cls.cot, name="priority", type="select", choice_set=cls.choice_set,
        )
        cls.create_custom_object_type_field(
            cls.cot, name="flags", type="multiselect", choice_set=cls.choice_set,
        )
        cls.model = cls.cot.get_model()

    def setUp(self):
        super().setUp()
        self.user = create_test_user("numericchoiceuser")
        self.client = APIClient()
        token_key = create_token(self.user)
        self.header = {"HTTP_AUTHORIZATION": f"Token {token_key}"}
        perm = ObjectPermission(name="numeric choice perm", actions=["view", "add", "change"])
        perm.save()
        perm.users.add(self.user)
        perm.object_types.add(ObjectType.objects.get_for_model(self.model))

    def _list_url(self):
        return reverse(
            "plugins-api:netbox_custom_objects-api:customobject-list",
            kwargs={"custom_object_type": self.cot.slug},
        )

    def _detail_url(self, instance):
        return reverse(
            "plugins-api:netbox_custom_objects-api:customobject-detail",
            kwargs={"pk": instance.pk, "custom_object_type": self.cot.slug},
        )

    def test_numeric_string_choice_value_round_trips_on_select(self):
        response = self.client.post(
            self._list_url(), {"name": "a", "priority": "1"}, format="json", **self.header,
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["priority"], {"value": "1", "label": "One"})

        # Write back the exact value the API just returned in its GET representation.
        round_tripped_value = response.data["priority"]["value"]
        patch_response = self.client.patch(
            self._detail_url(self.model.objects.get(pk=response.data["id"])),
            {"priority": round_tripped_value},
            format="json",
            **self.header,
        )
        self.assertEqual(patch_response.status_code, status.HTTP_200_OK, patch_response.data)
        self.assertEqual(patch_response.data["priority"], {"value": "1", "label": "One"})

    def test_boolean_like_string_choice_value_round_trips_on_select(self):
        response = self.client.post(
            self._list_url(), {"name": "b", "priority": "true"}, format="json", **self.header,
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["priority"], {"value": "true", "label": "Enabled"})

    def test_numeric_string_choice_values_round_trip_on_multiselect(self):
        response = self.client.post(
            self._list_url(), {"name": "c", "flags": ["1", "true"]}, format="json", **self.header,
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(
            response.data["flags"],
            [{"value": "1", "label": "One"}, {"value": "true", "label": "Enabled"}],
        )

    def test_select_still_rejects_value_outside_choice_set(self):
        response = self.client.post(
            self._list_url(), {"name": "d", "priority": "99"}, format="json", **self.header,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("priority", response.data)

    def test_multiselect_still_rejects_value_outside_choice_set(self):
        response = self.client.post(
            self._list_url(), {"name": "e", "flags": ["1", "99"]}, format="json", **self.header,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("flags", response.data)
