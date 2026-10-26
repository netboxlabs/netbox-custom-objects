"""
GraphQL schema generation for custom objects.

The plugin's GraphQL schema contribution is skipped during the test run
(``should_skip_dynamic_model_creation()`` returns True when ``test`` is on the
command line), so these tests call the schema-generation helpers directly: query-field
naming, per-structure type caching, and the live-schema signature/rebuild machinery.
"""

from unittest import mock

import strawberry
from django.test import TestCase

from core.models import ObjectType
from dcim.models import Device, Site

from netbox_custom_objects.graphql import live as live_module
from netbox_custom_objects.graphql import schema as schema_module
from netbox_custom_objects.graphql.schema import build_query_classes, _query_field_name
from netbox_custom_objects.graphql.types import build_object_type

from ..base import CustomObjectsTestCase


class GraphQLSchemaGenerationTestCase(CustomObjectsTestCase, TestCase):
    """Tests for the schema/query-class generation helpers."""

    def test_gfk_prefetch_fallback_builds_generic_prefetch(self):
        """_build_gfk_prefetch_fallback (used when NetBox < 4.6.8 lacks
        netbox.graphql.optimization.build_gfk_prefetch) must still produce a
        working GenericPrefetch, one queryset per target model."""
        from django.contrib.contenttypes.prefetch import GenericPrefetch

        from netbox_custom_objects.graphql.types import _build_gfk_prefetch_fallback

        prefetch_fn = _build_gfk_prefetch_fallback("target", [Site, Device])
        result = prefetch_fn(info=None)
        self.assertIsInstance(result, GenericPrefetch)
        self.assertEqual(result.prefetch_through, "target")
        self.assertEqual(len(result.querysets), 2)
        self.assertEqual({qs.model for qs in result.querysets}, {Site, Device})

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
        self.user.is_superuser = True
        self.user.save()

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
        first = live_module.get_live_schema(self.user)
        self.assertIsNotNone(first)
        self.assertNotIn("custom_objects_runtime_thing", str(first))

        # ...and must appear after creation, with no restart and without manually
        # clearing the cache (the signature change drives the rebuild).
        self.create_simple_custom_object_type(name="Runtime Thing", slug="runtime_thing")
        second = live_module.get_live_schema(self.user)
        self.assertIsNot(first, second)
        sdl = str(second)
        self.assertIn("custom_objects_runtime_thing", sdl)
        self.assertIn("custom_objects_runtime_thing_list", sdl)

    def test_get_live_schema_cached_when_unchanged(self):
        self.create_simple_custom_object_type(name="Stable", slug="stable")
        a = live_module.get_live_schema(self.user)
        b = live_module.get_live_schema(self.user)
        # No DB change between calls → same cached object, no rebuild.
        self.assertIs(a, b)

    def test_live_schema_drops_deleted_type(self):
        from netbox_custom_objects.models import CustomObjectType

        cot = self.create_simple_custom_object_type(name="Temp", slug="temp_type")
        self.assertIn("custom_objects_temp_type", str(live_module.get_live_schema(self.user)))
        # Delete via the queryset rather than cot.delete(): the schema-drop
        # behaviour only depends on the row being gone (which changes the
        # signature and triggers a rebuild), and this avoids the unrelated COT
        # teardown machinery.
        CustomObjectType.objects.filter(pk=cot.pk).delete()
        self.assertNotIn("custom_objects_temp_type", str(live_module.get_live_schema(self.user)))

    def test_get_live_schema_without_get_schema_extensions(self):
        # NetBox < 4.6.1 has no get_schema_extensions(); its extensions are inline.
        import netbox.graphql.schema as ngs
        from strawberry.extensions import MaxAliasesLimiter
        from strawberry_django.optimizer import DjangoOptimizerExtension

        with mock.patch.dict(ngs.__dict__):
            ngs.__dict__.pop("get_schema_extensions", None)
            self.create_simple_custom_object_type(name="Legacy NetBox", slug="legacy_netbox")
            schema = live_module.get_live_schema(self.user)

        self.assertIsNotNone(schema)
        self.assertIn("custom_objects_legacy_netbox", str(schema))
        self.assertEqual(
            {type(ext) for ext in schema.extensions},
            {DjangoOptimizerExtension, MaxAliasesLimiter},
        )

    def test_schema_extensions_fallback_returns_fresh_instances(self):
        import netbox.graphql.schema as ngs

        with mock.patch.dict(ngs.__dict__):
            ngs.__dict__.pop("get_schema_extensions", None)
            first = live_module._schema_extensions()
            second = live_module._schema_extensions()

        self.assertEqual(len(first), 2)
        for a, b in zip(first, second):
            self.assertIsNot(a, b)


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
            "django.apps.apps.is_installed",
            side_effect=lambda app: app != "netbox_branching",
        ):
            # Must not raise — previously raised RuntimeError here.
            connect_signature_invalidation()

        self._assert_evict_handler_not_registered()
