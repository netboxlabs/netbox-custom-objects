"""
Tests for GraphQL support for custom objects.

The plugin contributes its GraphQL schema at startup, which is intentionally
skipped during the test run (``should_skip_dynamic_model_creation()`` returns
True when ``test`` is on the command line — see ``__init__.py``).  We therefore
exercise the schema-generation functions directly: build a Strawberry schema
from custom object types created in each test and execute queries against it
in-process, mirroring what NetBox does at boot.
"""

from typing import List
from unittest import mock

import strawberry
import strawberry_django
from django.test import TestCase
from strawberry.schema.config import StrawberryConfig

from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site

from netbox_custom_objects.graphql import live as live_module
from netbox_custom_objects.graphql import schema as schema_module
from netbox_custom_objects.graphql.schema import build_query_classes, _query_field_name
from netbox_custom_objects.graphql.types import build_object_type

from .base import CustomObjectsTestCase


class _Context:
    """Minimal stand-in for Strawberry-Django's request context."""

    def __init__(self, request):
        self.request = request


def build_test_schema(custom_object_types):
    """Assemble a Strawberry schema from the given COTs, bypassing the startup guard."""
    annotations = {}
    attrs = {}
    used_names = set()
    for cot in custom_object_types:
        gql_type = build_object_type(cot)
        field_name = _query_field_name(cot, used_names)
        list_name = f"{field_name}_list"
        annotations[field_name] = gql_type
        attrs[field_name] = strawberry_django.field()
        annotations[list_name] = List[gql_type]
        attrs[list_name] = strawberry_django.field()
    attrs["__annotations__"] = annotations
    # The GraphQL type name must be "Query" for strawberry-django to attach the
    # single-object `id` lookup argument (it only does so on the root Query
    # type).  In production our class is mixed into NetBox's real Query, which is
    # named "Query"; here we name it directly.
    query_cls = strawberry.type(type("Query", (), attrs))
    return strawberry.Schema(query=query_cls, config=StrawberryConfig(auto_camel_case=False))


class GraphQLSchemaGenerationTestCase(CustomObjectsTestCase, TestCase):
    """Tests for the schema/query-class generation helpers."""

    def test_query_field_name_sanitizes_slug(self):
        used = set()
        cot = self.create_custom_object_type(name="Widget", slug="my-widget")
        name = _query_field_name(cot, used)
        self.assertEqual(name, "my_widget")

    def test_query_field_name_dedupes_collisions(self):
        used = set()
        a = self.create_custom_object_type(name="A", slug="a-b")
        b = self.create_custom_object_type(name="B", slug="a_b", verbose_name_plural="Bs")
        first = _query_field_name(a, used)
        second = _query_field_name(b, used)
        self.assertEqual(first, "a_b")
        self.assertEqual(second, f"a_b_{b.id}")

    def test_build_query_classes_skipped_during_tests(self):
        # ``test`` is on sys.argv during the suite, so the startup builder
        # short-circuits to an empty list rather than touching the DB.
        self.create_simple_custom_object_type()
        self.assertEqual(build_query_classes(), [])

    def test_module_exposes_schema_list(self):
        # The module-level ``schema`` attribute must always be a list so that
        # NetBox's ``register_graphql_schema`` (which calls ``.extend``) works.
        self.assertIsInstance(schema_module.schema, list)

    def test_real_builder_produces_assemblable_query(self):
        # Exercise the actual production builder (normally skipped during tests)
        # to confirm it yields a Query class that assembles into a valid schema
        # exposing the expected per-type fields.
        self.create_simple_custom_object_type(name="Gadget", slug="gadget")
        with mock.patch(
            "netbox_custom_objects.CustomObjectsPluginConfig."
            "should_skip_dynamic_model_creation",
            return_value=False,
        ):
            classes = build_query_classes()

        self.assertEqual(len(classes), 1)
        # Assemble exactly as NetBox does: the contributed class as a base of the
        # root Query type.
        query_cls = strawberry.type(type("Query", (classes[0],), {}))
        built = strawberry.Schema(
            query=query_cls, config=StrawberryConfig(auto_camel_case=False)
        )
        sdl = str(built)
        self.assertIn("gadget", sdl)
        self.assertIn("gadget_list", sdl)


class GraphQLLiveSchemaTestCase(CustomObjectsTestCase, TestCase):
    """
    The schema must reflect custom object types created/deleted at runtime
    without a NetBox restart (issue #30 follow-up).

    ``should_skip_dynamic_model_creation`` returns True during the test run, so
    we patch it off for these tests and reset the per-process cache around each.
    """

    def setUp(self):
        super().setUp()
        live_module.reset_cache()
        patcher = mock.patch(
            "netbox_custom_objects.CustomObjectsPluginConfig."
            "should_skip_dynamic_model_creation",
            return_value=False,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(live_module.reset_cache)

    def test_signature_changes_when_type_added(self):
        sig_before = live_module.schema_signature()
        self.create_simple_custom_object_type(name="Sig", slug="sig")
        sig_after = live_module.schema_signature()
        self.assertNotEqual(sig_before, sig_after)

    def test_signature_changes_when_field_added(self):
        cot = self.create_simple_custom_object_type(name="Sig2", slug="sig2")
        sig_before = live_module.schema_signature()
        self.create_custom_object_type_field(cot, name="extra", label="Extra", type="text")
        sig_after = live_module.schema_signature()
        self.assertNotEqual(sig_before, sig_after)

    def test_get_live_schema_rebuilds_on_new_type(self):
        # A type that does not exist yet must not be in the schema...
        first = live_module.get_live_schema()
        self.assertIsNotNone(first)
        self.assertNotIn("runtime_thing", str(first))

        # ...and must appear after creation, with no restart and without manually
        # clearing the cache (the signature change drives the rebuild).
        self.create_simple_custom_object_type(name="Runtime Thing", slug="runtime_thing")
        second = live_module.get_live_schema()
        self.assertIsNot(first, second)
        sdl = str(second)
        self.assertIn("runtime_thing", sdl)
        self.assertIn("runtime_thing_list", sdl)

    def test_get_live_schema_cached_when_unchanged(self):
        self.create_simple_custom_object_type(name="Stable", slug="stable")
        a = live_module.get_live_schema()
        b = live_module.get_live_schema()
        # No DB change between calls → same cached object, no rebuild.
        self.assertIs(a, b)

    def test_live_schema_drops_deleted_type(self):
        from netbox_custom_objects.models import CustomObjectType

        cot = self.create_simple_custom_object_type(name="Temp", slug="temp_type")
        self.assertIn("temp_type", str(live_module.get_live_schema()))
        # Delete via the queryset rather than cot.delete(): the schema-drop
        # behaviour only depends on the row being gone (which changes the
        # signature and triggers a rebuild), and this avoids the unrelated COT
        # teardown machinery.
        CustomObjectType.objects.filter(pk=cot.pk).delete()
        self.assertNotIn("temp_type", str(live_module.get_live_schema()))


class GraphQLQueryTestCase(CustomObjectsTestCase, TestCase):
    """End-to-end query execution against a generated schema."""

    def setUp(self):
        super().setUp()
        # restrict() returns everything for a superuser, keeping these tests
        # focused on schema generation rather than permission wiring.
        self.user.is_superuser = True
        self.user.save()
        self.request = self._make_request()

    def _make_request(self):
        from django.test import RequestFactory

        request = RequestFactory().post("/graphql/")
        request.user = self.user
        return request

    def _execute(self, schema, query):
        result = schema.execute_sync(query, context_value=_Context(self.request))
        self.assertIsNone(result.errors, msg=str(result.errors))
        return result.data

    def _make_device(self):
        manufacturer = Manufacturer.objects.create(name="Mfr", slug="mfr")
        device_type = DeviceType.objects.create(
            manufacturer=manufacturer, model="Model", slug="model"
        )
        role = DeviceRole.objects.create(name="Role", slug="role")
        site = Site.objects.create(name="Site", slug="site")
        return Device.objects.create(
            name="dev1", device_type=device_type, role=role, site=site
        )

    def test_scalar_fields_query(self):
        cot = self.create_complex_custom_object_type(name="Asset", slug="asset")
        model = cot.get_model()
        model.objects.create(name="First", count=7, active=True, status="choice1")

        schema = build_test_schema([cot])
        data = self._execute(
            schema,
            "{ asset_list { id name count active status } }",
        )
        rows = data["asset_list"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "First")
        self.assertEqual(rows[0]["count"], 7)
        self.assertTrue(rows[0]["active"])
        self.assertEqual(rows[0]["status"], "choice1")

    def test_single_object_query_by_id(self):
        cot = self.create_simple_custom_object_type(name="Note", slug="note")
        model = cot.get_model()
        instance = model.objects.create(name="Hello", description="world")

        schema = build_test_schema([cot])
        data = self._execute(
            schema,
            f'{{ note(id: {instance.pk}) {{ id name description }} }}',
        )
        self.assertEqual(data["note"]["name"], "Hello")
        self.assertEqual(data["note"]["description"], "world")

    def test_object_relationship_field(self):
        device = self._make_device()
        cot = self.create_complex_custom_object_type(name="Link", slug="link")
        model = cot.get_model()
        model.objects.create(name="L1", device=device)

        schema = build_test_schema([cot])
        data = self._execute(
            schema,
            "{ link_list { name device { id display object_type } } }",
        )
        related = data["link_list"][0]["device"]
        self.assertEqual(related["id"], device.pk)
        self.assertEqual(related["object_type"], "dcim.device")
        self.assertEqual(related["display"], str(device))

    def test_multiobject_relationship_field(self):
        device = self._make_device()
        cot = self.create_multi_object_custom_object_type(name="Group", slug="group")
        model = cot.get_model()
        instance = model.objects.create(name="G1")
        instance.devices.add(device)

        schema = build_test_schema([cot])
        data = self._execute(
            schema,
            "{ group_list { name devices { id object_type } } }",
        )
        devices = data["group_list"][0]["devices"]
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0]["id"], device.pk)
        self.assertEqual(devices[0]["object_type"], "dcim.device")

    def test_tags_and_base_fields(self):
        cot = self.create_simple_custom_object_type(name="Doc", slug="doc")
        model = cot.get_model()
        model.objects.create(name="D1")

        schema = build_test_schema([cot])
        data = self._execute(
            schema,
            "{ doc_list { id display created tags { name } } }",
        )
        row = data["doc_list"][0]
        self.assertEqual(row["display"], "D1")
        self.assertIsNotNone(row["id"])
        self.assertEqual(row["tags"], [])
