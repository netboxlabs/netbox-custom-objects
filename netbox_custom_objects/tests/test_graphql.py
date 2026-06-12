"""
Tests for GraphQL support for custom objects.

Two layers are exercised:

* Unit tests for the schema-generation helpers (query-field naming, per-structure
  type caching, the live-schema signature/rebuild machinery).  The plugin's
  GraphQL schema contribution is intentionally skipped during the test run
  (``should_skip_dynamic_model_creation()`` returns True when ``test`` is on the
  command line), so these call the generation functions directly.

* End-to-end tests that drive the real ``/graphql/`` HTTP endpoint with token
  authentication, modelled on NetBox's own GraphQL test pattern.  These patch the
  startup guard off so the live schema (installed via the view patch in
  ``__init__.py``) is built and bound per request, exactly as in production.
"""

import decimal
import json
from unittest import mock

import strawberry
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from core.models import ObjectType
from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Region, Site
from users.models import ObjectPermission

from netbox_custom_objects.graphql import live as live_module
from netbox_custom_objects.graphql import schema as schema_module
from netbox_custom_objects.graphql.schema import build_query_classes, _query_field_name
from netbox_custom_objects.graphql.types import build_object_type

from .base import CustomObjectsTestCase, create_token


class GraphQLSchemaGenerationTestCase(CustomObjectsTestCase, TestCase):
    """Tests for the schema/query-class generation helpers."""

    def test_query_field_name_sanitizes_and_namespaces_slug(self):
        used = set()
        cot = self.create_custom_object_type(name="Widget", slug="my-widget")
        name = _query_field_name(cot, used)
        # Hyphens sanitised to underscores, and namespaced with the prefix so the
        # field can never collide with a core/plugin root query field.
        self.assertEqual(name, "custom_objects_my_widget")

    def test_query_field_name_dedupes_collisions(self):
        used = set()
        a = self.create_custom_object_type(name="A", slug="a-b")
        b = self.create_custom_object_type(name="B", slug="a_b", verbose_name_plural="Bs")
        first = _query_field_name(a, used)
        second = _query_field_name(b, used)
        self.assertEqual(first, "custom_objects_a_b")
        self.assertEqual(second, f"custom_objects_a_b_{b.id}")

    def test_query_field_name_avoids_list_suffix_collision(self):
        # A type whose slug sanitizes to '<other>_list' must not claim the same
        # GraphQL field as another type's auto-generated '<name>_list' list field.
        used = set()
        a = self.create_custom_object_type(name="Foo", slug="foo")
        b = self.create_custom_object_type(
            name="Foo List", slug="foo-list", verbose_name_plural="Foo Lists"
        )
        name_a = _query_field_name(a, used)
        name_b = _query_field_name(b, used)
        self.assertEqual(name_a, "custom_objects_foo")
        # 'custom_objects_foo_list' is already reserved as A's list field, so B is
        # disambiguated.
        self.assertNotEqual(name_b, "custom_objects_foo_list")
        # All four generated names (singular + list for each type) stay distinct.
        all_fields = {name_a, f"{name_a}_list", name_b, f"{name_b}_list"}
        self.assertEqual(len(all_fields), 4)

    def test_build_object_type_is_cached_per_structure(self):
        # An unchanged COT reuses its built type; a structural change (which bumps
        # cache_timestamp) forces a rebuild.
        from netbox_custom_objects.graphql import types as types_module

        types_module.clear_type_cache()
        self.addCleanup(types_module.clear_type_cache)

        cot = self.create_simple_custom_object_type(name="Cached", slug="cached")
        first = build_object_type(cot)
        self.assertIs(first, build_object_type(cot))

        self.create_custom_object_type_field(cot, name="extra", label="Extra", type="text")
        cot.refresh_from_db()
        self.assertIsNot(first, build_object_type(cot))

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
        from strawberry.schema.config import StrawberryConfig

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
        self.assertIn("custom_objects_gadget", sdl)
        self.assertIn("custom_objects_gadget_list", sdl)

    def test_self_referential_object_field_does_not_recurse(self):
        # A self-referential OBJECT field (FK to the same COT) must not send
        # build_object_type into infinite recursion: the cycle guard breaks the
        # back-edge with the flat stub.  Regression test for the str/int mismatch
        # that left the guard inert (extract_cot_id returns a str, the in-progress
        # stack holds ints), which caused a RecursionError on this configuration.
        from netbox_custom_objects.graphql import types as types_module

        types_module.clear_type_cache()
        self.addCleanup(types_module.clear_type_cache)

        cot = self.create_custom_object_type(name="Node", slug="node")
        self.create_custom_object_type_field(
            cot, name="label", label="Label", type="text", primary=True, required=True
        )
        self_ot = ObjectType.objects.get(
            app_label="netbox_custom_objects",
            model=cot.get_table_model_name(cot.id).lower(),
        )
        self.create_custom_object_type_field(
            cot, name="parent", label="Parent", type="object", related_object_type=self_ot
        )
        cot.refresh_from_db()

        # Completes (no RecursionError) and yields a usable type.
        gql_type = build_object_type(cot)
        self.assertIsNotNone(gql_type)
        # The cyclic build used the flat stub for the self-edge, so the type is
        # intentionally not cached — a later top-level query rebuilds it.
        self.assertIsNot(gql_type, build_object_type(cot))

    def test_rebuild_prefetch_covers_field_access(self):
        # Regression for the schema-rebuild N+1: build_query_classes preloads types
        # with their fields and each field's related type(s).  Loading a type via
        # that same prefetch must make the rebuild's per-field accesses
        # (types.py: the fields iteration, the FK target, and the polymorphic M2M
        # targets) issue zero further queries.
        from django.db import connection
        from django.db.models import Prefetch
        from django.test.utils import CaptureQueriesContext

        from netbox_custom_objects.models import CustomObjectType, CustomObjectTypeField

        cot = self.create_custom_object_type(name="Pf", slug="pf")
        self.create_custom_object_type_field(
            cot, name="name", label="Name", type="text", primary=True, required=True
        )
        # Non-polymorphic object field → exercises the related_object_type FK.
        self.create_custom_object_type_field(
            cot, name="single", label="Single", type="object",
            related_object_type=self.get_site_object_type(),
        )
        # Polymorphic object field → exercises the related_object_types M2M.
        self.create_polymorphic_field(
            cot, [self.get_site_object_type(), self.get_device_object_type()],
            name="poly", type="object",
        )

        # The exact prefetch build_query_classes uses.
        fields_qs = (
            CustomObjectTypeField.objects
            .select_related("related_object_type")
            .prefetch_related("related_object_types")
        )
        loaded = CustomObjectType.objects.prefetch_related(
            Prefetch("fields", queryset=fields_qs)
        ).get(pk=cot.pk)

        with CaptureQueriesContext(connection) as ctx:
            for field in loaded.fields.all():           # fields iteration (no query)
                if field.related_object_type_id:
                    _ = field.related_object_type        # FK — select_related
                if field.is_polymorphic:
                    list(field.related_object_types.all())  # M2M — prefetch_related
        self.assertEqual(
            len(ctx.captured_queries), 0,
            f"prefetch missed an access: {[q['sql'] for q in ctx.captured_queries]}",
        )

    def test_related_repr_degrades_on_broken_str(self):
        # A referenced object whose __str__ (or get_absolute_url) raises must not
        # propagate — that would error the whole relationship field instead of just
        # this one object.  It degrades to a stable placeholder display.
        from netbox_custom_objects.graphql.types import _related_repr

        class Broken:
            pk = 7

            class _meta:
                label_lower = "app.broken"

            def __str__(self):
                raise ValueError("boom")

            def get_absolute_url(self):
                raise ValueError("no url")

        rep = _related_repr(Broken())
        self.assertEqual(rep.id, 7)
        self.assertEqual(rep.object_type, "app.broken")
        self.assertEqual(rep.display, "app.broken:7")  # fallback, not an exception
        self.assertIsNone(rep.url)


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
        self.assertNotIn("custom_objects_runtime_thing", str(first))

        # ...and must appear after creation, with no restart and without manually
        # clearing the cache (the signature change drives the rebuild).
        self.create_simple_custom_object_type(name="Runtime Thing", slug="runtime_thing")
        second = live_module.get_live_schema()
        self.assertIsNot(first, second)
        sdl = str(second)
        self.assertIn("custom_objects_runtime_thing", sdl)
        self.assertIn("custom_objects_runtime_thing_list", sdl)

    def test_get_live_schema_cached_when_unchanged(self):
        self.create_simple_custom_object_type(name="Stable", slug="stable")
        a = live_module.get_live_schema()
        b = live_module.get_live_schema()
        # No DB change between calls → same cached object, no rebuild.
        self.assertIs(a, b)

    def test_live_schema_drops_deleted_type(self):
        from netbox_custom_objects.models import CustomObjectType

        cot = self.create_simple_custom_object_type(name="Temp", slug="temp_type")
        self.assertIn("custom_objects_temp_type", str(live_module.get_live_schema()))
        # Delete via the queryset rather than cot.delete(): the schema-drop
        # behaviour only depends on the row being gone (which changes the
        # signature and triggers a rebuild), and this avoids the unrelated COT
        # teardown machinery.
        CustomObjectType.objects.filter(pk=cot.pk).delete()
        self.assertNotIn("custom_objects_temp_type", str(live_module.get_live_schema()))


class GraphQLSignalRegistrationTestCase(TestCase):
    """
    Regression tests for connect_signature_invalidation() guard logic.

    The function must not crash when netbox-branching is installed in the Python
    environment but absent from INSTALLED_APPS (which raises RuntimeError, not
    ImportError, when Django model classes are imported).
    """

    def _assert_evict_handler_not_registered(self):
        """
        Assert that the branch-eviction handler is not registered, using
        Signal.disconnect() rather than inspecting the undocumented receivers
        list.  disconnect() is the public API and returns True only when it
        actually removed something — so if it returns False the handler was
        never connected (which is the desired outcome).  Calling disconnect()
        also serves as cleanup: if a prior test or ready() left a stale
        registration, this removes it without affecting the assertion.
        """
        from django.db.models.signals import post_delete
        was_registered = post_delete.disconnect(dispatch_uid="nco_graphql_evict_branch")
        self.assertFalse(
            was_registered,
            "Branch eviction handler must not be registered when branching is not enabled",
        )

    def test_branching_installed_but_not_enabled_does_not_crash(self):
        # Simulate netbox-branching installed but not in INSTALLED_APPS by
        # patching apps.is_installed to return False for it, then verify that
        # connect_signature_invalidation() returns without registering the
        # branch-eviction handler.
        from django.db.models.signals import post_delete

        from netbox_custom_objects.graphql.live import connect_signature_invalidation

        # Remove any pre-existing registration (e.g. from ready()) so the test
        # is self-contained regardless of the environment.
        post_delete.disconnect(dispatch_uid="nco_graphql_evict_branch")

        with mock.patch(
            "netbox_custom_objects.graphql.live.django_apps.is_installed",
            side_effect=lambda app: app != "netbox_branching",
        ):
            # Must not raise — previously raised RuntimeError here.
            connect_signature_invalidation()

        self._assert_evict_handler_not_registered()

    def test_branching_not_installed_does_not_crash(self):
        # When netbox-branching is entirely absent, is_installed returns False
        # for all apps and the function must return cleanly without registering
        # the branch-eviction handler.
        from django.db.models.signals import post_delete

        from netbox_custom_objects.graphql.live import connect_signature_invalidation

        post_delete.disconnect(dispatch_uid="nco_graphql_evict_branch")

        with mock.patch(
            "netbox_custom_objects.graphql.live.django_apps.is_installed",
            side_effect=lambda app: app != "netbox_branching",
        ):
            connect_signature_invalidation()  # must not raise

        self._assert_evict_handler_not_registered()


@override_settings(LOGIN_REQUIRED=True)
class GraphQLEndpointTestCase(CustomObjectsTestCase, TestCase):
    """
    End-to-end tests against the real ``/graphql/`` HTTP endpoint.

    Patches the startup guard off so the live schema is built and bound to the
    (monkey-patched) GraphQL view per request, and authenticates with a token —
    the same path a real client takes.  The user is a superuser so these tests
    focus on schema/resolution correctness rather than permission wiring
    (permissions are covered separately below).
    """

    def setUp(self):
        super().setUp()
        live_module.reset_cache()
        self.addCleanup(live_module.reset_cache)
        patcher = mock.patch(
            "netbox_custom_objects.CustomObjectsPluginConfig."
            "should_skip_dynamic_model_creation",
            return_value=False,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        self.user.is_superuser = True
        self.user.save()
        # A fresh APIClient using token auth (not the session login set up by the
        # base class), mirroring how an API client reaches the endpoint.
        self.client = APIClient()
        token_key = create_token(self.user)
        self.header = {"HTTP_AUTHORIZATION": f"Token {token_key}"}
        self.url = reverse("graphql")

    def _gql(self, query):
        response = self.client.post(
            self.url, data={"query": query}, format="json", **self.header
        )
        self.assertEqual(
            response.status_code, status.HTTP_200_OK, getattr(response, "content", response)
        )
        payload = json.loads(response.content)
        self.assertNotIn("errors", payload, msg=str(payload.get("errors")))
        return payload["data"]

    def _make_device(self, name="dev1"):
        manufacturer, _ = Manufacturer.objects.get_or_create(name="Mfr", slug="mfr")
        device_type, _ = DeviceType.objects.get_or_create(
            manufacturer=manufacturer, model="Model", slug="model"
        )
        role, _ = DeviceRole.objects.get_or_create(name="Role", slug="role")
        site = self._make_site()
        return Device.objects.create(
            name=name, device_type=device_type, role=role, site=site
        )

    def _make_site(self, name="Site", slug="site", region=None):
        return Site.objects.create(name=name, slug=slug, region=region)

    def _site_object_field_type(self, name="Server", slug="server"):
        """A COT with a primary text field and a single-object field → Site."""
        cot = self.create_custom_object_type(name=name, slug=slug)
        self.create_custom_object_type_field(
            cot, name="name", label="Name", type="text", primary=True, required=True
        )
        self.create_custom_object_type_field(
            cot, name="site", label="Site", type="object",
            related_object_type=self.get_site_object_type(),
        )
        return cot

    def test_scalar_fields_query(self):
        cot = self.create_complex_custom_object_type(name="Asset", slug="asset")
        model = cot.get_model()
        model.objects.create(name="First", count=7, active=True, status="choice1")

        data = self._gql("{ custom_objects_asset_list { id display name count active status } }")
        rows = data["custom_objects_asset_list"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["display"], "First")
        self.assertEqual(rows[0]["name"], "First")
        self.assertEqual(rows[0]["count"], 7)
        self.assertTrue(rows[0]["active"])
        self.assertEqual(rows[0]["status"], "choice1")

    def test_decimal_field_round_trips(self):
        # A decimal field is annotated with the stdlib ``decimal.Decimal``, which
        # Strawberry maps to its built-in Decimal scalar.  Confirm the value
        # survives the round trip through the GraphQL endpoint with full
        # precision (Strawberry serializes the scalar to a string).
        cot = self.create_custom_object_type(name="Product", slug="product")
        self.create_custom_object_type_field(
            cot, name="name", label="Name", type="text", primary=True, required=True
        )
        self.create_custom_object_type_field(
            cot, name="price", label="Price", type="decimal"
        )
        model = cot.get_model()
        model.objects.create(name="Widget", price=decimal.Decimal("19.99"))

        data = self._gql("{ custom_objects_product_list { name price } }")
        rows = data["custom_objects_product_list"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "Widget")
        self.assertEqual(decimal.Decimal(str(rows[0]["price"])), decimal.Decimal("19.99"))

    def test_json_field_round_trips(self):
        # A JSON field is annotated with Strawberry's JSON scalar; confirm a
        # structured value survives the round trip through the endpoint intact.
        cot = self.create_custom_object_type(name="Config", slug="config")
        self.create_custom_object_type_field(
            cot, name="name", label="Name", type="text", primary=True, required=True
        )
        self.create_custom_object_type_field(
            cot, name="data", label="Data", type="json"
        )
        model = cot.get_model()
        payload_value = {"enabled": True, "ports": [80, 443]}
        model.objects.create(name="web", data=payload_value)

        data = self._gql("{ custom_objects_config_list { name data } }")
        rows = data["custom_objects_config_list"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "web")
        self.assertEqual(rows[0]["data"], payload_value)

    def test_single_object_query_by_id(self):
        cot = self.create_simple_custom_object_type(name="Note", slug="note")
        model = cot.get_model()
        instance = model.objects.create(name="Hello", description="world")

        data = self._gql(
            f"{{ custom_objects_note(id: {instance.pk}) {{ id display name description }} }}"
        )
        self.assertEqual(data["custom_objects_note"]["name"], "Hello")
        self.assertEqual(data["custom_objects_note"]["description"], "world")

    def test_object_relationship_resolves_to_native_site_type(self):
        # A single-object field pointing at a Site must resolve to NetBox's
        # SiteType and be fully traversable (including nested relations like
        # region) — not a flat stub.
        region = Region.objects.create(name="West", slug="west")
        site = self._make_site(name="HQ", slug="hq", region=region)
        cot = self._site_object_field_type()
        model = cot.get_model()
        model.objects.create(name="S1", site=site)

        data = self._gql(
            "{ custom_objects_server_list { name site { id name slug region { name } } } }"
        )
        related = data["custom_objects_server_list"][0]["site"]
        self.assertEqual(related["id"], str(site.pk))
        self.assertEqual(related["name"], "HQ")
        self.assertEqual(related["slug"], "hq")
        # Deep traversal into the related object's own relations proves it is the
        # native SiteType, not the flat CustomObjectRelatedObjectType.
        self.assertEqual(related["region"]["name"], "West")

    def test_multiobject_relationship_resolves_to_native_device_type(self):
        device = self._make_device(name="dev-a")
        # Slug 'group' would collide with NetBox's built-in `group_list` root query
        # field (user groups) — but the `custom_objects_` prefix keeps it distinct,
        # so this exercises both native multiobject resolution and the namespacing.
        cot = self.create_multi_object_custom_object_type(name="Group", slug="group")
        model = cot.get_model()
        instance = model.objects.create(name="G1")
        instance.devices.add(device)

        data = self._gql(
            "{ custom_objects_group_list { name devices { id name role { name } } } }"
        )
        devices = data["custom_objects_group_list"][0]["devices"]
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0]["id"], str(device.pk))
        # 'name'/'role' are Device fields → confirms native DeviceType.
        self.assertEqual(devices[0]["name"], "dev-a")
        self.assertEqual(devices[0]["role"]["name"], "Role")

    def test_polymorphic_object_field_resolves_to_union(self):
        # A polymorphic single-object field exposes a union of its target types;
        # an instance pointing at a Site resolves through the SiteType arm.
        site = self._make_site(name="PolySite", slug="polysite")
        cot = self.create_custom_object_type(name="Binding", slug="binding")
        self.create_custom_object_type_field(
            cot, name="name", label="Name", type="text", primary=True, required=True
        )
        self.create_polymorphic_field(
            cot,
            [self.get_site_object_type(), self.get_device_object_type()],
            name="target", type="object",
        )
        model = cot.get_model()
        model.objects.create(name="B1", target=site)

        # Alias the per-type 'name' selections: Site.name is String! while
        # Device.name is String, and GraphQL's same-response-shape rule forbids
        # selecting both under one response key.
        data = self._gql(
            "{ custom_objects_binding_list { name target { "
            "... on SiteType { id siteName: name } "
            "... on DeviceType { id deviceName: name } } } }"
        )
        target = data["custom_objects_binding_list"][0]["target"]
        self.assertEqual(target["id"], str(site.pk))
        self.assertEqual(target["siteName"], "PolySite")

    def test_multiple_types_in_one_schema(self):
        # Several custom object types must all be queryable from the same schema.
        a = self.create_simple_custom_object_type(name="Alpha", slug="alpha")
        b = self.create_simple_custom_object_type(name="Beta", slug="beta")
        a.get_model().objects.create(name="a1")
        b.get_model().objects.create(name="b1")

        data = self._gql(
            "{ custom_objects_alpha_list { name } custom_objects_beta_list { name } }"
        )
        self.assertEqual(data["custom_objects_alpha_list"][0]["name"], "a1")
        self.assertEqual(data["custom_objects_beta_list"][0]["name"], "b1")

    def test_tags_and_base_fields(self):
        cot = self.create_simple_custom_object_type(name="Doc", slug="doc")
        model = cot.get_model()
        model.objects.create(name="D1")

        data = self._gql("{ custom_objects_doc_list { id display created tags { name } } }")
        row = data["custom_objects_doc_list"][0]
        self.assertEqual(row["display"], "D1")
        self.assertIsNotNone(row["id"])
        self.assertEqual(row["tags"], [])


@override_settings(LOGIN_REQUIRED=True)
class GraphQLPermissionTestCase(CustomObjectsTestCase, TestCase):
    """
    Object-level view permissions must be enforced for objects reached through a
    custom object's relationship fields, not just for the top-level objects.
    """

    def setUp(self):
        super().setUp()
        live_module.reset_cache()
        self.addCleanup(live_module.reset_cache)
        patcher = mock.patch(
            "netbox_custom_objects.CustomObjectsPluginConfig."
            "should_skip_dynamic_model_creation",
            return_value=False,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        # A non-superuser; permissions are granted explicitly per test.
        self.client = APIClient()
        token_key = create_token(self.user)
        self.header = {"HTTP_AUTHORIZATION": f"Token {token_key}"}
        self.url = reverse("graphql")

        # COT 'Server' with a single-object field → Site, holding one Site.
        self.site = Site.objects.create(name="Secret", slug="secret")
        self.cot = self.create_custom_object_type(name="Server", slug="server")
        self.create_custom_object_type_field(
            self.cot, name="name", label="Name", type="text", primary=True, required=True
        )
        self.create_custom_object_type_field(
            self.cot, name="site", label="Site", type="object",
            related_object_type=self.get_site_object_type(),
        )
        self.model = self.cot.get_model()
        self.model.objects.create(name="srv", site=self.site)

    def _grant(self, model_class, name):
        perm = ObjectPermission(name=name, actions=["view"])
        perm.save()
        perm.users.add(self.user)
        perm.object_types.add(ObjectType.objects.get_for_model(model_class))
        return perm

    def _post(self, query):
        response = self.client.post(
            self.url, data={"query": query}, format="json", **self.header
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        return json.loads(response.content)

    def test_related_object_hidden_without_permission(self):
        # View permission on the custom object, but NOT on Site → the related
        # site must be withheld (null), while the object itself is returned.
        self._grant(self.model, "view-co")
        payload = self._post("{ custom_objects_server_list { name site { id name } } }")
        self.assertNotIn("errors", payload, msg=str(payload.get("errors")))
        rows = payload["data"]["custom_objects_server_list"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "srv")
        self.assertIsNone(rows[0]["site"])

    def test_related_object_visible_with_permission(self):
        # Granting view on Site as well makes the related object appear, proving
        # the previous test's null was permission enforcement, not a broken field.
        self._grant(self.model, "view-co")
        self._grant(Site, "view-site")
        payload = self._post("{ custom_objects_server_list { name site { id name } } }")
        self.assertNotIn("errors", payload, msg=str(payload.get("errors")))
        related = payload["data"]["custom_objects_server_list"][0]["site"]
        self.assertEqual(related["id"], str(self.site.pk))
        self.assertEqual(related["name"], "Secret")

    def test_multiobject_related_filtered_by_permission(self):
        # The multi-object resolver filters related objects through the batched
        # permission check (_filter_viewable).  Without view permission on Device
        # the list is empty; granting it makes the device appear.
        manufacturer = Manufacturer.objects.create(name="Mfr", slug="mfr")
        device_type = DeviceType.objects.create(
            manufacturer=manufacturer, model="Model", slug="model"
        )
        role = DeviceRole.objects.create(name="Role", slug="role")
        site = Site.objects.create(name="DevSite", slug="devsite")
        device = Device.objects.create(
            name="dev1", device_type=device_type, role=role, site=site
        )
        cot = self.create_multi_object_custom_object_type(name="Group", slug="group")
        model = cot.get_model()
        instance = model.objects.create(name="G1")
        instance.devices.add(device)

        # View on the custom object but NOT on Device → devices filtered out.
        self._grant(model, "view-grp")
        payload = self._post("{ custom_objects_group_list { name devices { id } } }")
        self.assertNotIn("errors", payload, msg=str(payload.get("errors")))
        rows = payload["data"]["custom_objects_group_list"]
        self.assertEqual(rows[0]["name"], "G1")
        self.assertEqual(rows[0]["devices"], [])

        # Granting view on Device makes it appear (batched check lets it through).
        self._grant(Device, "view-dev")
        payload = self._post("{ custom_objects_group_list { name devices { id } } }")
        self.assertNotIn("errors", payload, msg=str(payload.get("errors")))
        devices = payload["data"]["custom_objects_group_list"][0]["devices"]
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0]["id"], str(device.pk))

    def test_multiobject_related_filtered_to_permitted_subset(self):
        # The previous tests grant view on the whole model (all-or-nothing).  This
        # exercises the core security claim at object level: a *constrained*
        # ObjectPermission must filter related objects to just the permitted subset.
        manufacturer = Manufacturer.objects.create(name="Mfr", slug="mfr")
        device_type = DeviceType.objects.create(
            manufacturer=manufacturer, model="Model", slug="model"
        )
        role = DeviceRole.objects.create(name="Role", slug="role")
        site = Site.objects.create(name="DevSite", slug="devsite")
        allowed = Device.objects.create(
            name="allowed", device_type=device_type, role=role, site=site
        )
        denied = Device.objects.create(
            name="denied", device_type=device_type, role=role, site=site
        )
        cot = self.create_multi_object_custom_object_type(name="Fleet", slug="fleet")
        model = cot.get_model()
        instance = model.objects.create(name="F1")
        instance.devices.add(allowed, denied)

        self._grant(model, "view-fleet")
        # View on Device constrained to only the "allowed" device.
        perm = ObjectPermission(
            name="view-one-device", actions=["view"], constraints={"name": "allowed"}
        )
        perm.save()
        perm.users.add(self.user)
        perm.object_types.add(ObjectType.objects.get_for_model(Device))

        payload = self._post(
            "{ custom_objects_fleet_list { name devices { id name } } }"
        )
        self.assertNotIn("errors", payload, msg=str(payload.get("errors")))
        devices = payload["data"]["custom_objects_fleet_list"][0]["devices"]
        self.assertEqual([d["name"] for d in devices], ["allowed"])
        self.assertEqual(devices[0]["id"], str(allowed.pk))

    @override_settings(EXEMPT_VIEW_PERMISSIONS=["*"])
    def test_related_anonymous_access_honors_exempt_view_permissions(self):
        # Anonymous/None traversal must defer to restrict(), exactly like NetBox's
        # BaseObjectType.get_queryset: when the view is exempt, related objects are
        # visible, not silently denied.
        from django.contrib.auth.models import AnonymousUser

        from netbox_custom_objects.graphql.types import _filter_viewable

        self.assertEqual(_filter_viewable(AnonymousUser(), [self.site]), [self.site])
        self.assertEqual(_filter_viewable(None, [self.site]), [self.site])

    @override_settings(EXEMPT_VIEW_PERMISSIONS=[])
    def test_related_anonymous_access_denied_without_exemption(self):
        # And when the view is not exempt, an unauthenticated user sees nothing —
        # again matching restrict()'s anonymous handling.
        from django.contrib.auth.models import AnonymousUser

        from netbox_custom_objects.graphql.types import _filter_viewable

        self.assertEqual(_filter_viewable(AnonymousUser(), [self.site]), [])
        self.assertEqual(_filter_viewable(None, [self.site]), [])
