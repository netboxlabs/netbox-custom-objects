"""
Tests for the CustomObjectType model.
"""
from unittest.mock import patch

from django.apps import apps as django_apps
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import DatabaseError, connection, transaction
from django.db.utils import ProgrammingError
from django.test import TestCase
from django.urls import reverse


from core.models import ObjectType
from dcim.models import Site
from netbox.search import registry
from netbox_custom_objects.constants import APP_LABEL
from netbox_custom_objects.field_types import ObjectFieldType, TextFieldType
from netbox_custom_objects.models import CustomObjectType
from ..base import CustomObjectsTestCase


class CustomObjectTypeTestCase(CustomObjectsTestCase, TestCase):
    """Test cases for CustomObjectType model."""

    def test_custom_object_type_creation(self):
        """Test creating a CustomObjectType."""
        custom_object_type = self.create_custom_object_type(
            name="TestObject",
            description="A test custom object type",
            verbose_name_plural="Test Objects",
            slug="test-objects",
        )

        self.assertEqual(custom_object_type.name, "TestObject")
        self.assertEqual(custom_object_type.description, "A test custom object type")
        self.assertEqual(custom_object_type.verbose_name_plural, "Test Objects")
        self.assertEqual(custom_object_type.slug, "test-objects")
        self.assertEqual(str(custom_object_type), "TestObject")

    def test_custom_object_type_name_validation(self):
        """COT name must match the schema identifier pattern (no leading/trailing/double underscores)."""
        invalid_names = [
            "test-type",    # hyphen not allowed
            "test__type",   # double underscore not allowed
            "_test_type",   # leading underscore not allowed
            "test_type_",   # trailing underscore not allowed
        ]
        for invalid_name in invalid_names:
            with self.assertRaises(ValidationError, msg=f"Expected ValidationError for name={invalid_name!r}"):
                cot = CustomObjectType(name=invalid_name, slug=f"slug-{invalid_name}")
                cot.full_clean()

    def test_custom_object_type_unique_name_constraint(self):
        """Test that custom object type names must be unique (case-insensitive)."""
        self.create_custom_object_type(name="TestObject")

        # Should not allow duplicate name (case-insensitive)
        with self.assertRaises(Exception):
            self.create_custom_object_type(name="testobject")

    def test_custom_object_type_get_absolute_url(self):
        """Test get_absolute_url method."""
        custom_object_type = self.create_custom_object_type(name="TestObject")
        expected_url = reverse("plugins:netbox_custom_objects:customobjecttype", args=[custom_object_type.pk])
        self.assertEqual(custom_object_type.get_absolute_url(), expected_url)

    def test_custom_object_type_get_list_url(self):
        """Test get_list_url method."""
        custom_object_type = self.create_custom_object_type(name="TestObject")
        expected_url = reverse(
            "plugins:netbox_custom_objects:customobject_list",
            kwargs={"custom_object_type": custom_object_type.slug}
        )
        self.assertEqual(custom_object_type.get_list_url(), expected_url)

    def test_custom_object_type_get_model_without_fields(self):
        """Test get_model method when no fields are defined."""
        custom_object_type = self.create_custom_object_type(name="TestObject")

        model = custom_object_type.get_model()
        # Base fields: id, created, last_updated, owner
        self.assertEqual(len(model._meta.fields), 4)

    def test_custom_object_type_get_model_with_primary_field(self):
        """Test get_model method with a primary field."""
        custom_object_type = self.create_custom_object_type(name="TestObject")

        # Add a primary field
        self.create_custom_object_type_field(
            custom_object_type,
            name="name",
            label="Name",
            type="text",
            primary=True,
            required=True
        )

        # Get the dynamic model
        model = custom_object_type.get_model()

        # Verify the model has the expected fields
        self.assertTrue(hasattr(model, 'name'))
        self.assertTrue(hasattr(model, 'get_absolute_url'))
        self.assertTrue(hasattr(model, '__str__'))

    def test_custom_object_type_get_model_with_multiple_fields(self):
        """Test get_model method with multiple fields of different types."""
        custom_object_type = self.create_custom_object_type(name="TestObject")

        # Add various field types
        self.create_custom_object_type_field(
            custom_object_type,
            name="name",
            label="Name",
            type="text",
            primary=True,
            required=True
        )

        self.create_custom_object_type_field(
            custom_object_type,
            name="count",
            label="Count",
            type="integer",
            validation_minimum=0,
            validation_maximum=100
        )

        self.create_custom_object_type_field(
            custom_object_type,
            name="active",
            label="Active",
            type="boolean",
            default=True
        )

        # Get the dynamic model
        model = custom_object_type.get_model()

        # Verify all fields exist
        self.assertTrue(hasattr(model, 'name'))
        self.assertTrue(hasattr(model, 'count'))
        self.assertTrue(hasattr(model, 'active'))

    def test_custom_object_type_save_creates_table(self):
        """Test that saving a custom object type creates the database table."""
        custom_object_type = self.create_custom_object_type(name="TestObject")

        # Add a primary field
        self.create_custom_object_type_field(
            custom_object_type,
            name="name",
            label="Name",
            type="text",
            primary=True,
            required=True
        )

        # Save to trigger table creation
        custom_object_type.save()

        # Check if the table exists
        with connection.cursor() as cursor:
            tables = connection.introspection.table_names(cursor)
            expected_table = f"custom_objects_{custom_object_type.id}"
            self.assertIn(expected_table, tables)

    def test_register_search_index_skips_object_field_absent_from_stub_model(self):
        """register_custom_object_search_index() must use local_fields/local_many_to_many
        rather than _meta.get_field() to check field presence.  _meta.get_field() for a
        name not in _forward_fields_map triggers Django's lazy _relation_tree computation,
        which calls apps.get_models() → our override → get_model() for every COT →
        infinite recursion when called during model registration.

        Regression for PR #474: the stub model generated with skip_object_fields=True
        does not have the OBJECT field, but self.fields.filter(search_weight__gt=0)
        still returns it from the database.
        """
        cot = self.create_custom_object_type(name="StubSearchTest", slug="stub-search-test")
        self.create_custom_object_type_field(
            cot, name="name", label="Name", type="text", primary=True, search_weight=1000,
        )
        self.create_custom_object_type_field(
            cot, name="ref_site", label="Site", type="object",
            related_object_type=self.get_site_object_type(),
            search_weight=500,
        )
        # CustomObjectTypeField.save() now caches a full model after each field
        # save (to defend against a rename/post_save race), so the cache holds a
        # full model here.  Clear it to force generation of a fresh stub.
        cot.clear_model_cache(cot.id)
        stub_model = cot.get_model(skip_object_fields=True)
        model_field_names = (
            {f.name for f in stub_model._meta.local_fields}
            | {f.name for f in stub_model._meta.local_many_to_many}
        )
        self.assertNotIn("ref_site", model_field_names,
                         "OBJECT field must be absent from stub model")
        # Must not raise FieldDoesNotExist, RecursionError, or any other exception.
        cot.register_custom_object_search_index(stub_model)

    def test_register_search_index_includes_context_fields_in_display_attrs(self):
        """Fields marked context=True surface as display_attrs (issue #655), so
        NetBox's global search shows them as supplementary "attributes" alongside
        each result, the same as any other model's SearchIndex.display_attrs."""
        cot = self.create_custom_object_type(name="ContextSearchTest", slug="context-search-test")
        self.create_custom_object_type_field(
            cot, name="name", label="Name", type="text", primary=True, search_weight=1000,
        )
        self.create_custom_object_type_field(
            cot, name="status", label="Status", type="text", context=True,
        )
        cot.clear_model_cache(cot.id)
        model = cot.get_model()
        cot.register_custom_object_search_index(model)

        label = f"{APP_LABEL}.{cot.get_table_model_name(cot.id).lower()}"
        search_index = registry["search"][label]
        self.assertIn("status", search_index.display_attrs)
        self.assertNotIn("name", search_index.display_attrs)

    def test_register_search_index_includes_object_context_fields(self):
        """A context field of type object (a single real FK, unlike multiobject's
        M2M) IS included in display_attrs -- getattr() on it returns the related
        instance directly, not a manager, so NetBox's generic renderer handles it
        correctly. Confirms the TYPE_MULTIOBJECT exclusion is scoped to just that
        one type, not object fields generally."""
        cot = self.create_custom_object_type(name="ContextObjectTest", slug="context-object-test")
        self.create_custom_object_type_field(
            cot, name="name", label="Name", type="text", primary=True, search_weight=1000,
        )
        self.create_custom_object_type_field(
            cot, name="related_site", label="Related Site", type="object",
            related_object_type=self.get_site_object_type(), context=True,
        )
        cot.clear_model_cache(cot.id)
        model = cot.get_model()
        cot.register_custom_object_search_index(model)

        label = f"{APP_LABEL}.{cot.get_table_model_name(cot.id).lower()}"
        search_index = registry["search"][label]
        self.assertIn("related_site", search_index.display_attrs)

    def test_register_search_index_excludes_multiobject_context_fields(self):
        """A context field of type multiobject must not reach display_attrs.

        NetBox core's CachedValue.display_attrs (the only consumer of this
        attribute) renders each entry via plain getattr() on the instance --
        for a real ManyToManyField that returns the RelatedManager itself, not
        its contents, so the search UI would show a broken object repr instead
        of the related objects. Excluding multiobject context fields here avoids
        surfacing that.
        """
        cot = self.create_custom_object_type(name="ContextM2MTest", slug="context-m2m-test")
        self.create_custom_object_type_field(
            cot, name="name", label="Name", type="text", primary=True, search_weight=1000,
        )
        self.create_custom_object_type_field(
            cot, name="related_sites", label="Related Sites", type="multiobject",
            related_object_type=self.get_site_object_type(), context=True,
        )
        cot.clear_model_cache(cot.id)
        model = cot.get_model()
        cot.register_custom_object_search_index(model)

        label = f"{APP_LABEL}.{cot.get_table_model_name(cot.id).lower()}"
        search_index = registry["search"][label]
        self.assertNotIn("related_sites", search_index.display_attrs)

    def test_skipped_object_field_with_stale_content_type_logs_warning(self):
        """When get_model_field raises NotImplementedError for an object field whose
        related_object_type_id is non-null (stale/deleted ContentType), a WARNING must
        be emitted — not just DEBUG — so operators can identify the broken field.
        Regression test for the fix in _fetch_and_generate_field_attrs (issue #353).
        """
        cot = self.create_custom_object_type(name="StaleCtTest", slug="stale-ct-test")
        self.create_custom_object_type_field(
            cot,
            name="device",
            label="Device",
            type="object",
            related_object_type=self.get_device_object_type(),
        )
        CustomObjectType.clear_model_cache(cot.id)
        # Simulate a stale ContentType: get_model_field raises NotImplementedError
        # while related_object_type_id is still set (non-null).
        with patch.object(ObjectFieldType, 'get_model_field', side_effect=NotImplementedError):
            with self.assertLogs('netbox_custom_objects.models', level='WARNING') as cm:
                cot.get_model()
        self.assertTrue(
            any('device' in msg for msg in cm.output),
            f"Expected WARNING mentioning field name 'device'; got: {cm.output}",
        )

    def test_delete_unregisters_model_from_app_registry(self):
        """
        Regression test for: deleting a Custom Object then its Custom Object Type
        leaves the dynamically-generated model in Django's app registry.  When a
        related model (e.g. dcim.Device) is subsequently deleted, Django's ORM
        Collector discovers the stale model class, tries to query the dropped table
        and raises "relation '<table>' does not exist".
        """
        custom_object_type = self.create_custom_object_type(
            name="RegTestObject", slug="reg-test-object"
        )
        self.create_custom_object_type_field(
            custom_object_type,
            name="name",
            label="Name",
            type="text",
            primary=True,
            required=True,
        )
        model = custom_object_type.get_model()
        model_name = model.__name__.lower()

        # Confirm the model is registered before deletion
        self.assertIn(model_name, django_apps.all_models.get(APP_LABEL, {}))

        custom_object_type.delete()

        # After deletion the model must be removed from the app registry so that
        # Django's cascade-delete collector no longer tries to query the dropped table.
        self.assertNotIn(model_name, django_apps.all_models.get(APP_LABEL, {}))

    def test_stale_registry_entry_causes_relation_error_on_related_object_delete(self):
        """
        Demonstrates the failure mode for issue #429.

        Re-registers the stale model class after deletion (simulating the pre-fix
        state) and confirms that deleting a related NetBox object then raises
        ProgrammingError('relation "custom_objects_<id>" does not exist').

        The test uses a savepoint so the aborted PostgreSQL transaction does not
        poison the surrounding test transaction.
        """
        site = Site.objects.create(name='Stale Registry Test Site', slug='stale-registry-test-site')

        cot = self.create_custom_object_type(name='SiteLinked', slug='site-linked')
        self.create_custom_object_type_field(
            cot,
            name='linked_site',
            label='Linked Site',
            type='object',
            related_object_type=self.get_site_object_type(),
        )

        stale_model = cot.get_model()
        stale_model_name = stale_model.__name__.lower()

        # Delete the COT; the fix removes the model from apps.all_models.
        cot.delete()
        self.assertNotIn(stale_model_name, django_apps.all_models.get(APP_LABEL, {}))

        # Simulate pre-fix state: put the stale model back in the registry and
        # force Django to rebuild its relation trees from the now-stale registry.
        django_apps.all_models[APP_LABEL][stale_model_name] = stale_model
        django_apps.clear_cache()

        try:
            # Deleting the site now triggers Django's cascade-delete Collector,
            # which finds the stale FK, queries the dropped table, and fails.
            # Use transaction.atomic() rather than a manual savepoint: when the
            # ProgrammingError propagates through Atomic.__exit__, it clears
            # connection.needs_rollback before issuing ROLLBACK TO SAVEPOINT, so
            # the subsequent savepoint_rollback SQL can actually execute.  A raw
            # transaction.savepoint_rollback() call goes through cursor.execute()
            # which calls validate_no_broken_transaction() while needs_rollback is
            # still True, raising TransactionManagementError instead.
            with transaction.atomic():
                site.delete()
                self.fail('Expected ProgrammingError was not raised — the bug is not being reproduced')
        except ProgrammingError as exc:
            self.assertIn('does not exist', str(exc))
        finally:
            # Restore clean registry state so subsequent tests are unaffected.
            django_apps.all_models[APP_LABEL].pop(stale_model_name, None)
            django_apps.clear_cache()


class CustomObjectTypeConfigContextTestCase(CustomObjectsTestCase, TestCase):
    """Config context support (issue #98) on custom object types."""

    @staticmethod
    def _table_columns(table_name):
        with connection.cursor() as cursor:
            return {
                col.name
                for col in connection.introspection.get_table_description(cursor, table_name)
            }

    def test_enabled_model_is_config_context_subclass_with_column(self):
        """A config-context-enabled type generates a ConfigContextModel subclass
        whose backing table has a local_context_data column."""
        from extras.models import ConfigContextModel

        cot = self.create_custom_object_type(
            name="cc_enabled", slug="cc-enabled", config_context_enabled=True,
        )
        self.create_custom_object_type_field(
            cot, name="name", label="Name", type="text", primary=True, required=True,
        )
        model = cot.get_model(no_cache=True)
        self.assertTrue(issubclass(model, ConfigContextModel))
        self.assertIn("local_context_data", self._table_columns(cot.get_database_table_name()))

    def test_disabled_model_has_no_config_context(self):
        """The default (flag False) type is not a ConfigContextModel and has no
        local_context_data column."""
        from extras.models import ConfigContextModel

        cot = self.create_custom_object_type(name="cc_disabled", slug="cc-disabled")
        self.assertFalse(cot.config_context_enabled)
        self.create_custom_object_type_field(
            cot, name="name", label="Name", type="text", primary=True, required=True,
        )
        model = cot.get_model(no_cache=True)
        self.assertFalse(issubclass(model, ConfigContextModel))
        self.assertNotIn("local_context_data", self._table_columns(cot.get_database_table_name()))

    def test_local_context_data_round_trips_and_get_config_context(self):
        """An instance stores local_context_data and get_config_context() returns
        it without touching the org-dimension aggregation that custom objects lack."""
        cot = self.create_custom_object_type(
            name="cc_roundtrip", slug="cc-roundtrip", config_context_enabled=True,
        )
        self.create_custom_object_type_field(
            cot, name="name", label="Name", type="text", primary=True, required=True,
        )
        model = cot.get_model(no_cache=True)

        obj = model(name="obj-1", local_context_data={"ntp_servers": ["10.0.0.1"]})
        obj.save()
        obj.refresh_from_db()
        self.assertEqual(obj.local_context_data, {"ntp_servers": ["10.0.0.1"]})
        # Safe override: returns the local data, never raises on missing site/tenant/role.
        self.assertEqual(obj.get_config_context(), {"ntp_servers": ["10.0.0.1"]})

        empty = model(name="obj-2")
        empty.save()
        self.assertEqual(empty.get_config_context(), {})

    def test_clean_rejects_non_dict_local_context_data(self):
        """The inherited ConfigContextModel.clean() validator rejects non-object JSON."""
        cot = self.create_custom_object_type(
            name="cc_clean", slug="cc-clean", config_context_enabled=True,
        )
        self.create_custom_object_type_field(
            cot, name="name", label="Name", type="text", primary=True, required=True,
        )
        model = cot.get_model(no_cache=True)
        obj = model(name="bad", local_context_data=["not", "a", "dict"])
        with self.assertRaises(ValidationError):
            obj.clean()

    def test_config_context_enabled_immutable_in_clean(self):
        """full_clean() rejects flipping config_context_enabled on an existing type."""
        cot = self.create_custom_object_type(name="cc_clean_immut", slug="cc-clean-immut")
        self.assertFalse(cot.config_context_enabled)
        cot.config_context_enabled = True
        with self.assertRaises(ValidationError):
            cot.full_clean()

    def test_config_context_enabled_clean_allows_unchanged(self):
        """Re-validating without changing the flag must not raise."""
        cot = self.create_custom_object_type(
            name="cc_clean_ok", slug="cc-clean-ok", config_context_enabled=True,
        )
        cot.description = "edited"
        cot.full_clean()  # should not raise

    # --- Source-context aggregation via field-naming convention (#98 phase 2) ---

    def _site_cot(self, name, slug):
        """Config-context-enabled COT with a primary name field and a `site` object field."""
        cot = self.create_custom_object_type(name=name, slug=slug, config_context_enabled=True)
        self.create_custom_object_type_field(
            cot, name="name", label="Name", type="text", primary=True, required=True,
        )
        self.create_custom_object_type_field(
            cot, name="site", label="Site", type="object",
            related_object_type=self.get_site_object_type(),
        )
        return cot

    def test_aggregates_referenced_site_config_context(self):
        """A `site` field pulls in ConfigContexts assigned to the referenced Site,
        with local_context_data merged on top."""
        from extras.models import ConfigContext

        site = Site.objects.create(name="CC Site", slug="cc-site")
        cc = ConfigContext.objects.create(name="cc-site-ctx", weight=1000, is_active=True,
                                          data={"ntp": "10.0.0.1", "dns": "8.8.8.8"})
        cc.sites.add(site)

        cot = self._site_cot("cc_agg_site", "cc-agg-site")
        model = cot.get_model(no_cache=True)
        obj = model.objects.create(name="o1", site=site, local_context_data={"dns": "1.1.1.1"})

        rendered = obj.get_config_context()
        self.assertEqual(rendered["ntp"], "10.0.0.1")        # from source context
        self.assertEqual(rendered["dns"], "1.1.1.1")         # local overrides source

    def test_renderer_shares_source_data_without_leaking_local_overrides(self):
        """Objects sharing source contexts don't see each other's nested local overrides."""
        from extras.models import ConfigContext

        from netbox_custom_objects.models import ConfigContextRenderer

        site = Site.objects.create(name="CC Shared", slug="cc-shared")
        cc = ConfigContext.objects.create(name="cc-shared-ctx", weight=1000, is_active=True,
                                          data={"ntp": {"primary": "10.0.0.1", "secondary": "10.0.0.2"}})
        cc.sites.add(site)
        cot = self._site_cot("cc_shared", "cc-shared")
        model = cot.get_model(no_cache=True)
        overridden = model.objects.create(name="o1", site=site, local_context_data={"ntp": {"primary": "local"}})
        plain = model.objects.create(name="o2", site=site)

        renderer = ConfigContextRenderer()
        self.assertEqual(renderer.render(overridden)["ntp"], {"primary": "local", "secondary": "10.0.0.2"})
        self.assertEqual(renderer.render(plain)["ntp"], {"primary": "10.0.0.1", "secondary": "10.0.0.2"})

    def test_no_convention_field_returns_local_only(self):
        """Without a convention-named dimension field, no source aggregation happens."""
        from extras.models import ConfigContext

        # A global (unassigned) active context that WOULD match a dimensionful object.
        ConfigContext.objects.create(name="global-ctx", weight=1000, is_active=True, data={"g": 1})

        cot = self.create_custom_object_type(name="cc_nodim", slug="cc-nodim", config_context_enabled=True)
        self.create_custom_object_type_field(
            cot, name="name", label="Name", type="text", primary=True, required=True,
        )
        model = cot.get_model(no_cache=True)
        obj = model.objects.create(name="o1", local_context_data={"local": 2})

        self.assertEqual(obj.get_config_context(), {"local": 2})

    def test_misnamed_field_is_ignored(self):
        """A `site`-named field pointing at the wrong model must not feed the site dimension."""
        from extras.models import ConfigContext

        site = Site.objects.create(name="CC Site 2", slug="cc-site-2")
        cc = ConfigContext.objects.create(name="cc-site-ctx-2", weight=1000, is_active=True, data={"x": 1})
        cc.sites.add(site)

        # Field named `site` but pointing at Prefix (wrong model) → ignored.
        cot = self.create_custom_object_type(name="cc_wrong", slug="cc-wrong", config_context_enabled=True)
        self.create_custom_object_type_field(
            cot, name="name", label="Name", type="text", primary=True, required=True,
        )
        self.create_custom_object_type_field(
            cot, name="site", label="Site", type="object",
            related_object_type=self.get_prefix_object_type(),
        )
        model = cot.get_model(no_cache=True)
        obj = model.objects.create(name="o1", local_context_data={"only": "local"})

        self.assertEqual(obj.get_config_context(), {"only": "local"})

    def test_aggregates_referenced_tenant_config_context(self):
        """The `tenant` dimension (a direct obj.tenant read in get_for_object) works."""
        from extras.models import ConfigContext
        from tenancy.models import Tenant

        tenant = Tenant.objects.create(name="CC Tenant", slug="cc-tenant")
        cc = ConfigContext.objects.create(name="cc-tenant-ctx", weight=1000, is_active=True, data={"t": 1})
        cc.tenants.add(tenant)

        cot = self.create_custom_object_type(name="cc_tenant", slug="cc-tenant", config_context_enabled=True)
        self.create_custom_object_type_field(
            cot, name="name", label="Name", type="text", primary=True, required=True,
        )
        self.create_custom_object_type_field(
            cot, name="tenant", label="Tenant", type="object",
            related_object_type=ObjectType.objects.get(app_label="tenancy", model="tenant"),
        )
        model = cot.get_model(no_cache=True)
        obj = model.objects.create(name="o1", tenant=tenant)

        self.assertEqual(obj.get_config_context(), {"t": 1})

    def test_aggregates_referenced_role_config_context(self):
        """The `role` dimension (a direct obj.role read + get_ancestors) works."""
        from dcim.models import DeviceRole
        from extras.models import ConfigContext

        role = DeviceRole.objects.create(name="CC Role", slug="cc-role", color="ff0000")
        cc = ConfigContext.objects.create(name="cc-role-ctx", weight=1000, is_active=True, data={"r": 1})
        cc.roles.add(role)

        cot = self.create_custom_object_type(name="cc_role", slug="cc-role", config_context_enabled=True)
        self.create_custom_object_type_field(
            cot, name="name", label="Name", type="text", primary=True, required=True,
        )
        self.create_custom_object_type_field(
            cot, name="role", label="Role", type="object",
            related_object_type=ObjectType.objects.get(app_label="dcim", model="devicerole"),
        )
        model = cot.get_model(no_cache=True)
        obj = model.objects.create(name="o1", role=role)

        self.assertEqual(obj.get_config_context(), {"r": 1})


# ---------------------------------------------------------------------------
# Semver / version string validation (issue #392)
# ---------------------------------------------------------------------------

class SemverValidationTestCase(CustomObjectsTestCase, TestCase):
    """Validate that version-string fields reject non-PEP-440 values."""

    # ------------------------------------------------------------------
    # CustomObjectType.version
    # ------------------------------------------------------------------

    def test_cot_version_blank_is_valid(self):
        cot = self.create_custom_object_type(name='semver_cot', slug='semver-cot')
        cot.version = ''
        cot.full_clean()  # must not raise

    def test_cot_version_valid_semver(self):
        cot = self.create_custom_object_type(name='semver_cot2', slug='semver-cot-2')
        for v in ('1.0.0', '2.3.4', '0.0.1', '1.0.0.post1', '1.0.0a1'):
            cot.version = v
            cot.full_clean()  # must not raise

    def test_cot_version_invalid_raises_validation_error(self):
        cot = self.create_custom_object_type(name='semver_cot3', slug='semver-cot-3')
        for bad in ('not-a-version', '1.x.0', 'latest', '!!invalid!!'):
            cot.version = bad
            with self.assertRaises(ValidationError, msg=f"Expected ValidationError for version={bad!r}"):
                cot.full_clean()

    # ------------------------------------------------------------------
    # CustomObjectTypeField.deprecated_since
    # ------------------------------------------------------------------

    def test_field_deprecated_since_blank_is_valid(self):
        cot = self.create_custom_object_type(name='semver_f1', slug='semver-f1')
        field = self.create_custom_object_type_field(cot, name='alpha', type='text')
        field.deprecated_since = ''
        field.full_clean()

    def test_field_deprecated_since_valid_semver(self):
        cot = self.create_custom_object_type(name='semver_f2', slug='semver-f2')
        field = self.create_custom_object_type_field(cot, name='beta', type='text')
        field.deprecated_since = '2.0.0'
        field.full_clean()

    def test_field_deprecated_since_invalid_raises(self):
        cot = self.create_custom_object_type(name='semver_f3', slug='semver-f3')
        field = self.create_custom_object_type_field(cot, name='gamma', type='text')
        for bad in ('not-a-version', '1.x.0', 'latest', '!!invalid!!'):
            field.deprecated_since = bad
            with self.assertRaises(ValidationError, msg=f"Expected ValidationError for deprecated_since={bad!r}"):
                field.full_clean()

    # ------------------------------------------------------------------
    # CustomObjectTypeField.scheduled_removal
    # ------------------------------------------------------------------

    def test_field_scheduled_removal_blank_is_valid(self):
        cot = self.create_custom_object_type(name='semver_f4', slug='semver-f4')
        field = self.create_custom_object_type_field(cot, name='delta', type='text')
        field.scheduled_removal = ''
        field.full_clean()

    def test_field_scheduled_removal_valid_semver(self):
        cot = self.create_custom_object_type(name='semver_f5', slug='semver-f5')
        field = self.create_custom_object_type_field(cot, name='epsilon', type='text')
        field.scheduled_removal = '3.0.0'
        field.full_clean()

    def test_field_scheduled_removal_invalid_raises(self):
        cot = self.create_custom_object_type(name='semver_f6', slug='semver-f6')
        field = self.create_custom_object_type_field(cot, name='zeta', type='text')
        for bad in ('v-bad', '1.x.0', 'latest', '!!invalid!!'):
            field.scheduled_removal = bad
            with self.assertRaises(ValidationError, msg=f"Expected ValidationError for scheduled_removal={bad!r}"):
                field.full_clean()


class DisplayExpressionTestCase(CustomObjectsTestCase, TestCase):
    """Tests for CustomObjectType.display_expression Jinja2 rendering."""

    def _make_cot(self, expression=''):
        cot = self.create_custom_object_type(
            name='ExprTest', slug='expr-test', display_expression=expression,
        )
        self.create_custom_object_type_field(cot, name='make', label='Make', type='text', primary=True, required=True)
        self.create_custom_object_type_field(cot, name='model', label='Model', type='text', required=False)
        return cot

    def test_expression_renders_composite_name(self):
        cot = self._make_cot('{{ make }} - {{ model }}')
        instance = cot.get_model().objects.create(make='Cisco', model='ASR1001')
        self.assertEqual(str(instance), 'Cisco - ASR1001')

    def test_expression_with_missing_field_renders_empty_string(self):
        # Fields referenced in the expression but not present render as ''
        cot = self._make_cot('{{ make }} / {{ nonexistent }}')
        instance = cot.get_model().objects.create(make='Juniper')
        self.assertEqual(str(instance), 'Juniper /')

    def test_empty_expression_falls_back_to_primary_field(self):
        cot = self._make_cot('')
        instance = cot.get_model().objects.create(make='Arista', model='7050')
        self.assertEqual(str(instance), 'Arista')

    def test_expression_rendering_error_falls_back_to_primary_field(self):
        # An expression saved without validation (clean() rejects it) falls back silently
        cot = self._make_cot('{% invalid jinja %}')
        instance = cot.get_model().objects.create(make='HP')
        self.assertEqual(str(instance), 'HP')

    def test_expression_runtime_error_falls_back_to_primary_field(self):
        cot = self._make_cot('{{ make }} {{ 1 // 0 }}')
        instance = cot.get_model().objects.create(make='Lenovo')
        self.assertEqual(str(instance), 'Lenovo')

    def test_bad_field_value_renders_empty(self):
        cot = self._make_cot('{{ make }}/{{ model }}')
        instance = cot.get_model().objects.create(make='Ubiquiti', model='USW')
        with patch.object(TextFieldType, 'get_display_value', autospec=True, side_effect=[
            'Ubiquiti', ObjectDoesNotExist('gone'),
        ]):
            self.assertEqual(str(instance), 'Ubiquiti/')

    def test_database_error_propagates(self):
        """A database error may have aborted the transaction, so it isn't hidden behind the fallback name."""
        cot = self._make_cot('{{ make }}')
        instance = cot.get_model().objects.create(make='Extreme')
        # Only the expression's value fails, so swallowing the error would fall back to the primary field.
        with patch.object(TextFieldType, 'get_display_value', side_effect=[DatabaseError('aborted'), 'Extreme']):
            with self.assertRaises(DatabaseError):
                str(instance)

    def test_clean_rejects_invalid_syntax(self):
        for expression in ('{% invalid jinja %}', '{% if make %}{{ make }}', '{{ make'):
            with self.subTest(expression=expression):
                cot = CustomObjectType(name='invalid', slug='invalid', display_expression=expression)
                with self.assertRaises(ValidationError) as cm:
                    cot.full_clean()
                [error] = cm.exception.error_dict['display_expression']
                self.assertEqual(error.code, 'invalid')
                self.assertIn('Invalid Jinja2 syntax', error.messages[0])

    def test_clean_accepts_valid_expression(self):
        expression = '{{ make }}{% if model %} / {{ model }}{% endif %}'
        cot = CustomObjectType(name='valid', slug='valid', display_expression=expression)
        cot.full_clean()

    def test_expression_empty_result_falls_back_to_primary_field(self):
        # Expression that renders to empty string falls back
        cot = self._make_cot('{{ nonexistent }}')
        instance = cot.get_model().objects.create(make='Dell')
        self.assertEqual(str(instance), 'Dell')

    def test_trailing_separator_with_blank_optional_field(self):
        # When an optional field is blank, the expression renders a dangling
        # separator unless the template guards it.  Verify both behaviours so
        # the documented {% if %} pattern is regression-tested.
        cot_bare = self.create_custom_object_type(
            name='SepBare', slug='sep-bare',
            display_expression='{{ make }} / {{ model }}',
        )
        self.create_custom_object_type_field(
            cot_bare, name='make', label='Make', type='text', primary=True, required=True,
        )
        self.create_custom_object_type_field(
            cot_bare, name='model', label='Model', type='text', required=False,
        )

        guarded_expr = '{{ make }}{% if model %} / {{ model }}{% endif %}'
        cot_guarded = self.create_custom_object_type(
            name='SepGuarded', slug='sep-guarded', display_expression=guarded_expr,
        )
        self.create_custom_object_type_field(
            cot_guarded, name='make', label='Make', type='text', primary=True, required=True,
        )
        self.create_custom_object_type_field(
            cot_guarded, name='model', label='Model', type='text', required=False,
        )

        model_bare = cot_bare.get_model()
        model_guarded = cot_guarded.get_model()

        bare_instance = model_bare.objects.create(make='Arista')       # model is blank
        guarded_instance = model_guarded.objects.create(make='Arista')  # model is blank

        self.assertEqual(str(bare_instance), 'Arista /')       # trailing separator
        self.assertEqual(str(guarded_instance), 'Arista')      # cleaned up with {% if %}

        # With model populated, both render identically.
        bare_full = model_bare.objects.create(make='Cisco', model='ASR')
        guarded_full = model_guarded.objects.create(make='Cisco', model='ASR')
        self.assertEqual(str(bare_full), 'Cisco / ASR')
        self.assertEqual(str(guarded_full), 'Cisco / ASR')


class DisplayExpressionFormValidationTestCase(CustomObjectsTestCase, TestCase):
    """The type form rejects invalid display expression syntax, which CustomObjectType.clean() checks."""

    def _form(self, expression):
        from netbox_custom_objects.forms import CustomObjectTypeForm
        data = {
            'name': 'validtest',
            'slug': 'validtest',
            'display_expression': expression,
        }
        return CustomObjectTypeForm(data=data)

    def test_valid_expression_passes(self):
        form = self._form('{{ make }} - {{ model }}')
        # display_expression itself should not produce a validation error
        form.is_valid()
        self.assertNotIn('display_expression', form.errors)

    def test_blank_expression_passes(self):
        form = self._form('')
        form.is_valid()
        self.assertNotIn('display_expression', form.errors)

    def test_invalid_jinja2_syntax_raises_validation_error(self):
        form = self._form('{% invalid jinja %}')
        form.is_valid()
        self.assertIn('display_expression', form.errors)

    def test_unclosed_block_raises_validation_error(self):
        form = self._form('{% if make %}{{ make }}')  # missing {% endif %}
        form.is_valid()
        self.assertIn('display_expression', form.errors)
