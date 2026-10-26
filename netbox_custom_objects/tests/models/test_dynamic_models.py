"""
Tests for the generated custom object models: instances, get_model(), the model cache,
startup ordering, stale references and pickling.
"""
import pickle
import uuid
from decimal import Decimal
from unittest.mock import patch

from django.apps import apps as django_apps
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import FieldDoesNotExist, ValidationError
from django.db.utils import OperationalError, ProgrammingError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

import netbox_custom_objects.models as nco_models

from core.choices import ObjectChangeActionChoices
from core.models import ObjectChange, ObjectType
from dcim.models import Site
from extras.models import Tag, TaggedItem
from netbox_custom_objects.api.serializers import get_serializer_class
from netbox_custom_objects.constants import APP_LABEL
from netbox_custom_objects.field_types import LazyForeignKey, TextFieldType
from netbox_custom_objects.models import CustomObjectType, CustomObjectTypeField
from ..base import CustomObjectsTestCase


class CustomObjectTestCase(CustomObjectsTestCase, TestCase):
    """Test cases for dynamic CustomObject instances."""

    @classmethod
    def setUpTestData(cls):
        """Set up test data that should be created once for the entire test class."""
        super().setUpTestData()
        cls.custom_object_type = cls.create_custom_object_type(name="TestObject", slug="test-objects")
        cls.cot_1_model_name = (
            cls.custom_object_type.get_table_model_name(cls.custom_object_type.id).lower()
        )
        first_object_ct = ObjectType.objects.get(app_label='netbox_custom_objects', model=cls.cot_1_model_name)
        cls.second_custom_object_type = cls.create_custom_object_type(name="TestObject2", slug="test-objects2")
        cls.cot_2_model_name = (
            cls.second_custom_object_type.get_table_model_name(cls.second_custom_object_type.id).lower()
        )
        second_object_ct = ObjectType.objects.get(app_label='netbox_custom_objects', model=cls.cot_2_model_name)

        # Add a primary field
        cls.create_custom_object_type_field(
            cls.custom_object_type,
            name="name",
            label="Name",
            type="text",
            primary=True,
            required=True
        )

        # Add additional fields
        cls.create_custom_object_type_field(
            cls.custom_object_type,
            name="description",
            label="Description",
            type="text",
            required=False
        )

        cls.create_custom_object_type_field(
            cls.custom_object_type,
            name="count",
            label="Count",
            type="integer",
            validation_minimum=0,
            validation_maximum=100
        )

        cls.create_custom_object_type_field(
            cls.custom_object_type,
            name="price",
            label="Price",
            type="decimal",
            validation_minimum=0,
            validation_maximum=100
        )

        cls.create_custom_object_type_field(
            cls.custom_object_type,
            name="is_active",
            label="Is active",
            type="boolean",
        )

        cls.create_custom_object_type_field(
            cls.custom_object_type,
            name="created_on",
            label="Created on (date)",
            type="date",
        )

        cls.create_custom_object_type_field(
            cls.custom_object_type,
            name="created_at",
            label="Created at (datetime)",
            type="datetime",
        )

        cls.create_custom_object_type_field(
            cls.custom_object_type,
            name="url",
            label="URL",
            type="url",
        )

        cls.create_custom_object_type_field(
            cls.custom_object_type,
            name="data",
            label="JSON data",
            type="json",
        )

        choice_set = cls.create_choice_set()
        cls.create_custom_object_type_field(
            cls.custom_object_type,
            name="country",
            label="Single country",
            type="select",
            choice_set=choice_set,
        )

        cls.create_custom_object_type_field(
            cls.custom_object_type,
            name="countries",
            label="Countries",
            type="multiselect",
            choice_set=choice_set,
        )

        site_ct = cls.get_site_object_type()
        cls.create_custom_object_type_field(
            cls.custom_object_type,
            name="site",
            label="Single site",
            type="object",
            related_object_type=site_ct,
        )

        cls.create_custom_object_type_field(
            cls.custom_object_type,
            name="sites",
            label="Sites",
            type="multiobject",
            related_object_type=site_ct,
        )

        # Custom Object single- and multi-object fields: two of each to the same type, so the
        # tests check each field keeps its own references and through-table rows.

        cls.create_custom_object_type_field(
            cls.custom_object_type,
            name="second_object_single",
            label="Second Object Single",
            type="object",
            related_object_type=second_object_ct,
        )

        cls.create_custom_object_type_field(
            cls.custom_object_type,
            name="second_object_single_2",
            label="Second Object Single 2",
            type="object",
            related_object_type=second_object_ct,
        )

        cls.create_custom_object_type_field(
            cls.custom_object_type,
            name="second_object_multi",
            label="Second Object Multi",
            type="multiobject",
            related_object_type=second_object_ct,
        )

        cls.create_custom_object_type_field(
            cls.custom_object_type,
            name="second_object_multi_2",
            label="Second Object Multi 2",
            type="multiobject",
            related_object_type=second_object_ct,
        )

        # Self-referential object and multi-object fields

        cls.create_custom_object_type_field(
            cls.custom_object_type,
            name="self_ref_single",
            label="Self Ref Single",
            type="object",
            related_object_type=first_object_ct,
        )

        cls.create_custom_object_type_field(
            cls.custom_object_type,
            name="self_ref_multi",
            label="Self Ref Multi",
            type="multiobject",
            related_object_type=first_object_ct,
        )

        # Refresh second_custom_object_type so its in-memory cache_timestamp matches the
        # DB value bumped by the signal when TYPE_OBJECT fields were saved above.
        cls.second_custom_object_type.refresh_from_db()

        # Get the dynamic model
        cls.model = cls.custom_object_type.get_model()

    def setUp(self):
        """Set up test data that should be reset between tests."""
        super().setUp()
        # Any test-specific setup can go here

    def test_custom_object_creation(self):
        """Test creating a custom object instance."""
        now = timezone.now()
        site_ct = self.get_site_object_type()
        site = site_ct.model_class().objects.create()
        first_object_1 = self.model.objects.create()
        first_object_2 = self.model.objects.create()
        second_object_model = self.second_custom_object_type.get_model()
        second_object_1 = second_object_model.objects.create()
        second_object_2 = second_object_model.objects.create()

        instance = self.model.objects.create(
            name="Test Instance",
            description="A test instance",
            count=50,
            price=Decimal("10.50"),
            is_active=True,
            created_on=now,
            created_at=now,
            url="http://example.com",
            data={"foo": "bar"},
            country="US",
            countries=["US", "AU"],
            site=site,
            second_object_single=second_object_1,
            second_object_single_2=second_object_2,
            self_ref_single=first_object_1,
        )
        instance.sites.add(site)
        instance.second_object_multi.add(second_object_1)
        instance.second_object_multi.add(second_object_2)
        instance.second_object_multi_2.add(second_object_1)
        instance.self_ref_multi.add(first_object_1)
        instance.self_ref_multi.add(first_object_2)

        self.assertEqual(instance.name, "Test Instance")
        self.assertEqual(instance.description, "A test instance")
        self.assertEqual(instance.count, 50)
        self.assertEqual(instance.price, Decimal("10.50"))
        self.assertEqual(instance.is_active, True)
        self.assertEqual(instance.created_on.date(), now.date())
        self.assertEqual(instance.created_at, now)
        self.assertEqual(instance.url, "http://example.com")
        self.assertEqual(instance.data, {"foo": "bar"})
        self.assertEqual(instance.country, "US")
        self.assertEqual(instance.countries, ["US", "AU"])
        self.assertEqual(instance.site, site)
        self.assertEqual(instance.sites.all().count(), 1)
        self.assertIn(site, instance.sites.all())
        self.assertEqual(str(instance), "Test Instance")
        # Object Fields pointing to Custom Objects
        self.assertEqual(instance.second_object_single, second_object_1)
        self.assertEqual(instance.second_object_single_2, second_object_2)
        self.assertEqual(instance.second_object_multi.count(), 2)
        self.assertEqual(instance.second_object_multi_2.count(), 1)
        # Self-referential Object Fields
        self.assertEqual(instance.self_ref_single, first_object_1)
        self.assertEqual(instance.self_ref_multi.count(), 2)

    def test_custom_object_get_absolute_url(self):
        """Test get_absolute_url method for custom objects."""
        instance = self.model.objects.create(name="Test Instance")
        expected_url = reverse(
            "plugins:netbox_custom_objects:customobject",
            kwargs={
                "custom_object_type": self.custom_object_type.slug,
                "pk": instance.pk
            }
        )
        self.assertEqual(instance.get_absolute_url(), expected_url)

    def test_custom_object_queryset_operations(self):
        """Test queryset operations on custom objects."""
        # Create multiple instances
        self.model.objects.create(name="Instance 1", count=10)
        self.model.objects.create(name="Instance 2", count=20)
        self.model.objects.create(name="Instance 3", count=30)

        # Test filtering
        filtered = self.model.objects.filter(count__gte=20)
        self.assertEqual(filtered.count(), 2)

        # Test ordering
        ordered = self.model.objects.order_by('count')
        self.assertEqual(ordered.first().name, "Instance 1")
        self.assertEqual(ordered.last().name, "Instance 3")

    def test_custom_object_update(self):
        """Test updating custom object instances."""
        instance = self.model.objects.create(name="Test Instance", count=10)

        # Update the instance
        instance.name = "Updated Instance"
        instance.count = 25
        instance.save()

        # Refresh from database
        instance.refresh_from_db()

        self.assertEqual(instance.name, "Updated Instance")
        self.assertEqual(instance.count, 25)

    def test_custom_object_delete(self):
        """Test deleting custom object instances."""
        instance = self.model.objects.create(name="Test Instance")

        # Delete the instance
        instance.delete()

        # Verify it's gone
        self.assertEqual(self.model.objects.count(), 0)

    @override_settings(CUSTOM_VALIDATORS={
        "netbox_custom_objects.test-objects": [{"name": {"min_length": 5}}],
    })
    def test_custom_validators_slug_key_enforced(self):
        """CUSTOM_VALIDATORS keyed by COT slug is applied during full_clean()."""
        instance = self.model(name="ab")
        with self.assertRaises(ValidationError):
            instance.full_clean()

    @override_settings(CUSTOM_VALIDATORS={
        "netbox_custom_objects.test-objects": [{"name": {"min_length": 5}}],
    })
    def test_custom_validators_slug_key_passes_for_valid_value(self):
        """CUSTOM_VALIDATORS slug-key validator passes when the value satisfies the rule."""
        instance = self.model(name="abcde")
        # Should not raise
        instance.full_clean()

    @override_settings(CUSTOM_VALIDATORS={
        "netbox_custom_objects.test-objects": [{"count": {"min": 10}}],
    })
    def test_custom_validators_non_text_field_enforced(self):
        """CUSTOM_VALIDATORS is applied to non-text fields (integer count < min raises)."""
        instance = self.model(name="valid", count=5)
        with self.assertRaises(ValidationError):
            instance.full_clean()

    @override_settings(CUSTOM_VALIDATORS={})
    def test_custom_validators_no_key_configured_passes(self):
        """When no CUSTOM_VALIDATORS key is configured for this COT, clean() passes."""
        instance = self.model(name="x")
        # Should not raise regardless of field value
        instance.full_clean()

    @override_settings(CUSTOM_VALIDATORS={
        "NETBOX_CUSTOM_OBJECTS.TEST-OBJECTS": [{"name": {"min_length": 5}}],
    })
    def test_custom_validators_slug_key_case_insensitive(self):
        """CUSTOM_VALIDATORS key lookup is case-insensitive."""
        instance = self.model(name="ab")
        with self.assertRaises(ValidationError):
            instance.full_clean()

    def test_str_falls_back_when_primary_field_raises_attribute_error(self):
        """CustomObject.__str__ must return "<display_name> <id>" rather than
        propagating an AttributeError when get_display_value fails.  This can
        happen when the generated model class is missing an attribute for the
        primary field (e.g. the field was silently skipped during regeneration
        due to a stale ContentType).  Regression test for issue #353.
        """
        instance = self.model.objects.create(name="Test Instance")
        # Confirm normal __str__ works first.
        self.assertEqual(str(instance), "Test Instance")
        # Simulate the stale-model scenario: get_display_value raises AttributeError.
        with patch.object(TextFieldType, 'get_display_value', side_effect=AttributeError("missing attr")):
            result = str(instance)
        expected = f"{self.custom_object_type.display_name} {instance.id}"
        self.assertEqual(result, expected)


class M2MSerializationRegressionTestCase(CustomObjectsTestCase, TestCase):
    """Guards the ``through._meta.auto_created = model`` opt-in in
    ``MultiObjectFieldType.after_model_generation`` — without it, Django's
    JSON serializer skips the M2M and merge replay zeroes through-table rows.
    """

    def test_serialize_object_includes_m2m_values(self):
        cot = self.create_custom_object_type(name="M2MSerialize", slug="m2m-serialize")
        site_ct = self.get_site_object_type()
        self.create_custom_object_type_field(
            cot, name="name", label="Name", type="text", primary=True,
        )
        self.create_custom_object_type_field(
            cot, name="sites", label="Sites", type="multiobject",
            related_object_type=site_ct,
        )
        model = cot.get_model()
        site_model = site_ct.model_class()
        site_a = site_model.objects.create(name="A", slug="a")
        site_b = site_model.objects.create(name="B", slug="b")

        obj = model.objects.create(name="obj")
        obj.sites.set([site_a, site_b])

        data = obj.serialize_object()
        self.assertIn("sites", data)
        self.assertEqual(set(data["sites"]), {site_a.pk, site_b.pk})


class CrossCOTGetModelOutsideReadyTestCase(CustomObjectsTestCase, TestCase):
    """Regression tests for CustomObjectType.get_model() bypassing ready()'s two-pass
    cross-COT FK resolution (#637)."""

    def _make_cross_cot_pair(self, suffix):
        """Build a fresh target/source COT pair, uniquely named so registry
        mutations in one test method can't bleed into another."""
        target_type = CustomObjectType.objects.create(
            name=f"ForwarderProfile637{suffix}", slug=f"forwarder-profile-637-{suffix}",
        )
        CustomObjectTypeField.objects.create(
            custom_object_type=target_type, name="name", label="Name",
            type="text", primary=True,
        )
        source_type = CustomObjectType.objects.create(
            name=f"DnsZone637{suffix}", slug=f"dns-zone-637-{suffix}",
        )
        CustomObjectTypeField.objects.create(
            custom_object_type=source_type, name="name", label="Name",
            type="text", primary=True,
        )
        target_ct = ObjectType.objects.get_for_model(target_type.get_model())
        CustomObjectTypeField.objects.create(
            custom_object_type=source_type, name="forwarder_profile",
            label="Forwarder Profile", type="object", related_object_type=target_ct,
        )
        return target_type, source_type

    def _unregister(self, *cots):
        """Simulate a fresh process where none of the given COT models are registered yet."""
        for cot in cots:
            model_name = cot.get_table_model_name(cot.id).lower()
            django_apps.all_models.get(APP_LABEL, {}).pop(model_name, None)
            CustomObjectType.clear_model_cache(cot.id)

    def test_get_model_omits_cross_cot_field_when_unsafe(self):
        """Omits an unregistered cross-COT field rather than leaving a dangling
        LazyForeignKey, and doesn't cache the degraded model."""
        target_type, source_type = self._make_cross_cot_pair("a")
        self._unregister(target_type, source_type)
        config = django_apps.get_app_config(APP_LABEL)

        with patch.object(config.__class__, '_dynamic_model_creation_unsafe', return_value=True):
            model = source_type.get_model()

        with self.assertRaises(FieldDoesNotExist):
            model._meta.get_field('forwarder_profile')
        self.assertFalse(CustomObjectType.is_model_cached(source_type.id))

    def test_get_model_system_checks_pass_after_migrate_time_call(self):
        """Reproduces the reported symptom: system checks must not raise E300/E307."""
        from django.core.checks.registry import registry as checks_registry

        target_type, source_type = self._make_cross_cot_pair("b")
        self._unregister(target_type, source_type)
        config = django_apps.get_app_config(APP_LABEL)

        with patch.object(config.__class__, '_dynamic_model_creation_unsafe', return_value=True):
            source_type.get_model()

        errors = checks_registry.run_checks()
        relevant = [e for e in errors if 'forwarder_profile' in str(e)]
        self.assertEqual(relevant, [])

    def test_get_model_regenerates_in_full_on_next_normal_call(self):
        """Once the degraded model's target is registered again, a later call
        fully re-resolves the cross-COT field."""
        target_type, source_type = self._make_cross_cot_pair("c")
        self._unregister(target_type, source_type)
        config = django_apps.get_app_config(APP_LABEL)

        with patch.object(config.__class__, '_dynamic_model_creation_unsafe', return_value=True):
            source_type.get_model()

        target_type.get_model()  # simulates ready()'s Pass 1 registering every COT
        full_model = source_type.get_model()
        field = full_model._meta.get_field('forwarder_profile')
        self.assertFalse(isinstance(field.remote_field.model, str))

    def test_get_model_unaffected_during_ordinary_test_run(self):
        """Ordinary test-suite get_model() calls stay fully hydrated."""
        target_type, source_type = self._make_cross_cot_pair("d")
        model = source_type.get_model(no_cache=True)
        field = model._meta.get_field('forwarder_profile')
        self.assertFalse(isinstance(field.remote_field.model, str))

    def test_get_model_keeps_core_model_field_when_unsafe(self):
        """A field targeting a core NetBox model (not another COT) can't dangle
        the way a cross-COT LazyForeignKey can, so it must not be dropped during
        the unsafe window (review of #664)."""
        source_type = CustomObjectType.objects.create(
            name="DnsZone637e", slug="dns-zone-637-e",
        )
        CustomObjectTypeField.objects.create(
            custom_object_type=source_type, name="name", label="Name",
            type="text", primary=True,
        )
        CustomObjectTypeField.objects.create(
            custom_object_type=source_type, name="device", label="Device",
            type="object", related_object_type=self.get_device_object_type(),
        )
        self._unregister(source_type)
        config = django_apps.get_app_config(APP_LABEL)

        with patch.object(config.__class__, '_dynamic_model_creation_unsafe', return_value=True):
            model = source_type.get_model()

        field = model._meta.get_field('device')
        self.assertFalse(isinstance(field.remote_field.model, str))

    def test_get_model_keeps_polymorphic_field_when_unsafe(self):
        """A polymorphic Object field uses a GFK, which needs no app-registry
        resolution, so it must not be dropped during the unsafe window either."""
        source_type = CustomObjectType.objects.create(
            name="DnsZone637f", slug="dns-zone-637-f",
        )
        CustomObjectTypeField.objects.create(
            custom_object_type=source_type, name="name", label="Name",
            type="text", primary=True,
        )
        CustomObjectTypeField.objects.create(
            custom_object_type=source_type, name="owner", label="Owner",
            type="object", is_polymorphic=True,
        )
        self._unregister(source_type)
        config = django_apps.get_app_config(APP_LABEL)

        with patch.object(config.__class__, '_dynamic_model_creation_unsafe', return_value=True):
            model = source_type.get_model()

        # A GFK field is a virtual field, not a concrete one — get_field() still
        # finds it, but the important thing is that it's present at all.
        self.assertIsNotNone(model._meta.get_field('owner'))


class NestedCOTStartupOrderingTestCase(CustomObjectsTestCase, TestCase):
    """Regression tests for issue #408: nested COT FK fields missing after restart.

    Three COTs are created with names chosen so alphabetical startup ordering
    (alpha_type < beta_type < gamma_type) processes the FK-source models before
    their FK-target models — the worst-case ordering that exposed the bug.
    The fix uses LazyForeignKey for all cross-COT FKs and re-resolves them in a
    second pass after all models are in the app registry.
    """

    @classmethod
    def setUpTestData(cls):
        # Names chosen so alphabetical ordering is: alpha_type < beta_type < gamma_type
        # (worst-case: each model is processed before its FK target exists in the registry)

        cls.gamma_type = CustomObjectType.objects.create(
            name="gamma_type", slug="gamma-type", verbose_name_plural="gamma_types",
        )
        CustomObjectTypeField.objects.create(
            custom_object_type=cls.gamma_type, name="name", label="Name",
            type="text", primary=True, required=True,
        )

        cls.beta_type = CustomObjectType.objects.create(
            name="beta_type", slug="beta-type", verbose_name_plural="beta_types",
        )
        CustomObjectTypeField.objects.create(
            custom_object_type=cls.beta_type, name="name", label="Name",
            type="text", primary=True, required=True,
        )
        gamma_model = cls.gamma_type.get_model()
        gamma_ot = ObjectType.objects.get(
            app_label=gamma_model._meta.app_label, model=gamma_model._meta.model_name,
        )
        CustomObjectTypeField.objects.create(
            custom_object_type=cls.beta_type, name="gamma", label="Gamma",
            type="object", related_object_type=gamma_ot,
        )

        cls.alpha_type = CustomObjectType.objects.create(
            name="alpha_type", slug="alpha-type", verbose_name_plural="alpha_types",
        )
        CustomObjectTypeField.objects.create(
            custom_object_type=cls.alpha_type, name="name", label="Name",
            type="text", primary=True, required=True,
        )
        beta_model = cls.beta_type.get_model()
        beta_ot = ObjectType.objects.get(
            app_label=beta_model._meta.app_label, model=beta_model._meta.model_name,
        )
        CustomObjectTypeField.objects.create(
            custom_object_type=cls.alpha_type, name="beta", label="Beta",
            type="object", related_object_type=beta_ot,
        )

    def _simulate_startup_ordering(self):
        """Clear all model caches and regenerate in alphabetical order, as ready() does.

        Keep in sync with the two-pass loop in ready() (__init__.py).
        """
        CustomObjectType.clear_model_cache()

        qs = list(CustomObjectType.objects.all())  # alphabetical: alpha_type, beta_type, gamma_type

        # Pass 1
        for obj in qs:
            obj.get_model()

        # Pass 2: re-resolve lazy FKs
        for obj in qs:
            model = CustomObjectType.get_cached_model(obj.id)
            if model is None:
                continue
            for field in model._meta.local_fields:
                if isinstance(field, LazyForeignKey) and isinstance(field.remote_field.model, str):
                    resolve_method = getattr(model, f'_resolve_{field.name}_model', None)
                    if resolve_method:
                        resolve_method(model)

        return {obj.slug: CustomObjectType.get_cached_model(obj.id) for obj in qs}

    def test_beta_type_has_gamma_fk_after_startup(self):
        """beta_type model must have its FK to gamma_type after the startup passes."""
        models = self._simulate_startup_ordering()
        beta_model = models['beta-type']
        field_names = [f.name for f in beta_model._meta.local_fields]
        self.assertIn('gamma', field_names,
                      "beta_type FK field to gamma_type must be present after startup")

    def test_alpha_type_has_beta_fk_after_startup(self):
        """alpha_type model must have its FK to beta_type after the startup passes."""
        models = self._simulate_startup_ordering()
        alpha_model = models['alpha-type']
        field_names = [f.name for f in alpha_model._meta.local_fields]
        self.assertIn('beta', field_names,
                      "alpha_type FK field to beta_type must be present after startup")

    def test_three_level_chain_fk_targets_are_full_models(self):
        """FK remote_field.model on each level must be the full model (with its own FKs)."""
        models = self._simulate_startup_ordering()

        alpha_model = models['alpha-type']
        beta_fk = alpha_model._meta.get_field('beta')
        related_beta = beta_fk.related_model
        # The related model must have its own FK to gamma_type
        self.assertIn('gamma', [f.name for f in related_beta._meta.local_fields],
                      "beta_type (as FK target from alpha_type) must itself have FK to gamma_type")


class CycleDetectionFalsePositiveRegressionTest(CustomObjectsTestCase, TestCase):
    """Regression test for the false-positive in _has_circular_reference.

    When an intermediate COT has a self-referencing field (e.g. a multiobject
    that points back to itself), the DFS can re-encounter that COT's ID in the
    ``visited`` set.  Before the fix, any node already in ``visited`` caused an
    immediate ``return True``, so validating a perfectly legal
    EC → Control FK raised "Circular reference detected" even though there is
    no actual cycle.

    After the fix, ``return custom_object_type.id == self.custom_object_type.id``
    only signals a real cycle when the DFS finds its way back to the ORIGIN node.
    """

    def setUp(self):
        super().setUp()
        # COT Control: has a self-referencing multiobject field
        self.cot_control = CustomObjectType.objects.create(
            name="ControlCycle", slug="control-cycle",
            verbose_name_plural="ControlCycles",
        )
        CustomObjectTypeField.objects.create(
            custom_object_type=self.cot_control,
            name="name", type="text", primary=True, required=True,
        )
        # Self-referencing M:N field
        self.cot_control.get_model()
        CustomObjectTypeField.objects.create(
            custom_object_type=self.cot_control,
            name="refs", type="multiobject",
            related_object_type=self.cot_control.object_type,
        )

        # COT EC: will hold the FK to Control
        self.cot_ec = CustomObjectType.objects.create(
            name="ECCycle", slug="ec-cycle",
            verbose_name_plural="ECCycles",
        )
        CustomObjectTypeField.objects.create(
            custom_object_type=self.cot_ec,
            name="name", type="text", primary=True, required=True,
        )
        self.cot_ec.get_model()

    def test_fk_into_self_referencing_cot_is_not_false_positive(self):
        """A non-circular FK from EC to Control must not raise ValidationError.

        Control has a self-referencing M:N field.  Validating a new EC→Control
        FK field must NOT report "Circular reference detected" — that would be a
        false positive.
        """
        # Build an unsaved field (EC → Control) and invoke the recursion check
        # directly.  _check_recursion() is the method called from clean().
        field = CustomObjectTypeField(
            custom_object_type=self.cot_ec,
            name="control_ref",
            type="object",
            related_object_type=self.cot_control.object_type,
        )
        # Must NOT raise ValidationError ("Circular reference detected")
        try:
            field._check_recursion()
        except ValidationError as exc:
            self.fail(
                f"_check_recursion() raised ValidationError for a valid EC→Control FK "
                f"(Control has a self-referencing M:N field). "
                f"This is a false positive. Error: {exc}"
            )

    def test_genuine_cycle_is_still_detected(self):
        """A genuine A → B → A cycle must still be detected."""
        # Create COT Alpha and Beta
        cot_alpha = CustomObjectType.objects.create(
            name="AlphaCycle", slug="alpha-cycle",
            verbose_name_plural="AlphaCycles",
        )
        CustomObjectTypeField.objects.create(
            custom_object_type=cot_alpha,
            name="name", type="text", primary=True, required=True,
        )
        cot_alpha.get_model()

        cot_beta = CustomObjectType.objects.create(
            name="BetaCycle", slug="beta-cycle",
            verbose_name_plural="BetaCycles",
        )
        CustomObjectTypeField.objects.create(
            custom_object_type=cot_beta,
            name="name", type="text", primary=True, required=True,
        )
        cot_beta.get_model()

        # Beta → Alpha (legitimate so far)
        CustomObjectTypeField.objects.create(
            custom_object_type=cot_beta,
            name="alpha_ref",
            type="object",
            related_object_type=cot_alpha.object_type,
        )

        # Alpha → Beta: this creates a genuine cycle A → B → A
        field = CustomObjectTypeField(
            custom_object_type=cot_alpha,
            name="beta_ref",
            type="object",
            related_object_type=cot_beta.object_type,
        )
        with self.assertRaises(ValidationError):
            field._check_recursion()


class StaleFKReferenceRegressionTest(CustomObjectsTestCase, TestCase):
    """Regression test for issue #384: stale FK reference after COT model regeneration.

    When COT A's model is regenerated (cache miss, e.g. after a schema change
    on another worker bumps cache_timestamp), any other COT model (B) that has a
    FK field pointing to A must have that FK field's ``remote_field.model``
    updated to the new A class.

    Without the fix, B's FK field keeps referencing the old A class object.
    Assigning a new-class A instance to B's FK field then raises::

        ValueError: Cannot assign "<TableNModel: ...>": "TableMModel.ref_a" must
        be a "TableNModel" instance.

    (Both class names are identical but they are different Python objects.)
    """

    def setUp(self):
        super().setUp()
        # COT A (target of the FK)
        self.cot_a = CustomObjectType.objects.create(
            name="FkTargetA", slug="fk-target-a",
            verbose_name_plural="FkTargetAs",
        )
        CustomObjectTypeField.objects.create(
            custom_object_type=self.cot_a,
            name="name", type="text", primary=True, required=True,
        )

        # COT B (source: has a direct FK to COT A)
        self.cot_b = CustomObjectType.objects.create(
            name="FkSourceB", slug="fk-source-b",
            verbose_name_plural="FkSourceBs",
        )
        CustomObjectTypeField.objects.create(
            custom_object_type=self.cot_b,
            name="name", type="text", primary=True, required=True,
        )

        # Generate A first so its ObjectType exists, then create the FK field on B
        self.model_a_v1 = self.cot_a.get_model()
        CustomObjectTypeField.objects.create(
            custom_object_type=self.cot_b,
            name="ref_a",
            label="Ref A",
            type="object",
            related_object_type=self.cot_a.object_type,
        )
        CustomObjectTypeField.objects.create(
            custom_object_type=self.cot_b,
            name="refs_a",
            label="Refs A",
            type="multiobject",
            related_object_type=self.cot_a.object_type,
        )
        # Generate B's model; this resolves the LazyForeignKey to model_a_v1
        self.cot_b.get_model()
        # Fetch fresh from cache to get the fully-resolved model
        self.model_b = CustomObjectType.get_cached_model(self.cot_b.id)

    def _get_fk_field(self, model, field_name):
        """Return the FK field from model._meta.local_fields by name (avoids _relation_tree)."""
        return next(
            (f for f in model._meta.local_fields if f.name == field_name),
            None,
        )

    def test_b_fk_resolves_to_a_v1_before_regeneration(self):
        """Sanity: B's FK field must already reference A's model after initial generation."""
        fk = self._get_fk_field(self.model_b, 'ref_a')
        self.assertIsNotNone(fk, "B must have an 'ref_a' FK field")
        # remote_field.model must be the class (not a string) after generation
        self.assertNotIsInstance(
            fk.remote_field.model, str,
            "FK must be resolved to a class, not a string reference",
        )
        self.assertIs(
            fk.remote_field.model, self.model_a_v1,
            "B's FK must initially point to A's first model class",
        )

    def test_b_fk_is_patched_after_a_regeneration(self):
        """After A is regenerated, B's FK field must reference the new A class.

        This is the core regression for issue #384: the isinstance check in
        ForeignKey.__set__ compares the assigned value against
        ``self.field.remote_field.model``.  If that attribute still points to the
        old A class after A is regenerated, assigning a new-A instance raises
        ValueError.
        """
        # Force regeneration of A (simulates a cache miss, e.g. timestamp mismatch)
        model_a_v2 = self.cot_a.get_model(no_cache=True)

        # The regenerated class must be a distinct Python object
        self.assertIsNot(model_a_v2, self.model_a_v1,
                         "Regenerated A must be a new Python class object")

        # B's FK field must now reference A v2, not v1
        fk = self._get_fk_field(self.model_b, 'ref_a')
        self.assertIsNotNone(fk, "B must still have an 'ref_a' FK field")
        self.assertIs(
            fk.remote_field.model, model_a_v2,
            "B's FK field must be patched to the newly generated A class; "
            "a stale reference would cause ValueError on FK assignment",
        )

    def test_b_m2m_is_patched_after_a_regeneration(self):
        """After A is regenerated, B's multi-object field must reference the new A class, as its through model does."""
        model_a_v2 = self.cot_a.get_model(no_cache=True)
        obj_a = model_a_v2.objects.create(name="a")

        m2m = next(f for f in self.model_b._meta.local_many_to_many if f.name == 'refs_a')
        self.assertIs(m2m.remote_field.model, model_a_v2)
        self.assertIs(m2m.remote_field.through._meta.get_field('target').remote_field.model, model_a_v2)
        # Filtering joins through the through model; a mismatch breaks the join.
        self.assertEqual(self.model_b.objects.filter(refs_a=obj_a.pk).count(), 0)


class LazySerializerRegistrationTestCase(CustomObjectsTestCase, TestCase):
    """Regression tests for issue #370: SerializerNotFound when a COT is
    created after startup and a worker never served a custom-objects API request.

    Two related bugs:
    1. get_serializer_class(model, skip_object_fields=True) used to overwrite the
       full module-level serializer with a partial one, causing subsequent requests
       to fail or return incomplete data.
    2. get_serializer_for_model() raised SerializerNotFound for a Table{N}Model in
       workers whose ready() ran before the COT existed.
       The serializer_resolver() hook (registered via PluginConfig.serializer_resolver)
       fixes this by generating the serializer on demand, ahead of any import-path
       lookup.
    """

    @classmethod
    def setUpTestData(cls):
        cls.parent_cot = CustomObjectType.objects.create(
            name="lazy_parent", slug="lazy-parent", verbose_name_plural="lazy_parents",
        )
        CustomObjectTypeField.objects.create(
            custom_object_type=cls.parent_cot, name="title", label="Title",
            type="text", primary=True, required=True,
        )

        cls.child_cot = CustomObjectType.objects.create(
            name="lazy_child", slug="lazy-child", verbose_name_plural="lazy_children",
        )
        CustomObjectTypeField.objects.create(
            custom_object_type=cls.child_cot, name="title", label="Title",
            type="text", primary=True, required=True,
        )
        CustomObjectTypeField.objects.create(
            custom_object_type=cls.child_cot, name="parent", label="Parent",
            type="object", related_object_type=cls.parent_cot.object_type,
        )

    def _clear_serializer_registrations(self):
        """Remove any previously registered serializers for parent and child COTs
        from the module so that serializer_resolver() and get_serializer_class()
        behave as they would in a fresh worker process."""
        import netbox_custom_objects.api.serializers as ser_module
        parent_model = self.parent_cot.get_model()
        child_model = self.child_cot.get_model()
        for model in (parent_model, child_model):
            attr = f"{model._meta.object_name}Serializer"
            ser_module.__dict__.pop(attr, None)

    def test_resolver_generates_serializer_on_demand(self):
        """serializer_resolver() must generate and return a serializer for any
        Table{N}Model whose serializer was never pre-registered (simulates a worker
        that started before the COT was created — issue #370 Scenario A).

        NetBox calls this resolver from get_serializer_for_model() before falling
        back to an import-path lookup, so a missing startup-time registration can
        never raise SerializerNotFound."""
        import netbox_custom_objects.api.serializers as ser_module
        self._clear_serializer_registrations()

        parent_model = self.parent_cot.get_model()
        serializer_name = f"{parent_model._meta.object_name}Serializer"

        # Attribute must not be in __dict__ after clearing
        self.assertNotIn(serializer_name, ser_module.__dict__,
                         "precondition: serializer must not be pre-registered")

        # The resolver must generate and return a valid serializer class on demand
        serializer_cls = ser_module.serializer_resolver(parent_model)
        self.assertIsNotNone(serializer_cls)
        self.assertIn("title", serializer_cls.Meta.fields)

        # After the resolver fires, the full serializer is cached in __dict__
        # (get_serializer_class registers it via setattr)
        self.assertIn(serializer_name, ser_module.__dict__,
                      "serializer must be cached in module __dict__ after first resolution")

    def test_skip_object_fields_does_not_overwrite_full_serializer(self):
        """get_serializer_class(model, skip_object_fields=True) must not overwrite
        the full module-level serializer for that model.  The full serializer
        (registered via skip_object_fields=False) must remain accessible after
        a partial serializer is built for the same model (issue #370 Bug 2)."""
        import netbox_custom_objects.api.serializers as ser_module
        self._clear_serializer_registrations()

        parent_model = self.parent_cot.get_model()
        serializer_name = f"{parent_model._meta.object_name}Serializer"

        # Register the full serializer first
        full_serializer = get_serializer_class(parent_model, skip_object_fields=False)
        self.assertIn(serializer_name, ser_module.__dict__)

        # Now call with skip_object_fields=True (simulates the nested-serializer path
        # triggered when building the child serializer's FK field)
        partial_serializer = get_serializer_class(parent_model, skip_object_fields=True)

        # Module must still hold the FULL serializer, not the partial one
        registered = getattr(ser_module, serializer_name)
        self.assertIs(registered, full_serializer,
                      "full serializer must not be overwritten by the partial variant")
        self.assertIsNot(registered, partial_serializer,
                         "partial serializer must be a distinct object, not stored on module")

    def test_child_serializer_does_not_clobber_parent_serializer(self):
        """Building child's serializer (which creates a nested partial for parent)
        must leave the parent's full module-level serializer intact (issue #370)."""
        import netbox_custom_objects.api.serializers as ser_module
        self._clear_serializer_registrations()

        parent_model = self.parent_cot.get_model()
        child_model = self.child_cot.get_model()
        parent_serializer_name = f"{parent_model._meta.object_name}Serializer"

        # Register parent's full serializer first
        full_parent_serializer = get_serializer_class(parent_model)
        self.assertIn("title", full_parent_serializer.Meta.fields)

        # Build child serializer — internally calls get_serializer_class(parent, skip_object_fields=True)
        get_serializer_class(child_model)

        # Parent's full serializer must be unchanged
        registered_parent = getattr(ser_module, parent_serializer_name)
        self.assertIs(registered_parent, full_parent_serializer,
                      "building child serializer must not clobber parent's full serializer")
        self.assertIn("title", registered_parent.Meta.fields,
                      "parent's full field set must be intact after child serializer is built")


class DeferredCOFieldReplayOrderingTestCase(CustomObjectsTestCase, TestCase):
    """
    Regression test (PR #641 review) for an ordering bug in the deferred CO
    field replay: _apply_deferred_co_field() replays a url field's title value
    via a raw UPDATE against "<name>_title", but in CustomObjectTypeField.save()
    the title column used to be added *after* that call ran. A deferred title
    value would therefore issue an UPDATE against a column that doesn't exist
    yet and raise psycopg.errors.UndefinedColumn.

    The scenario this guards against — a CO's CREATE replaying before its
    field's CREATE — normally only arises from netbox-branching squash-merge
    ordering, but the replay mechanism itself doesn't depend on branching at
    all: it's driven purely by the _deferred_co_field_data contextvar and
    CustomObjectTypeField.save(). This manufactures that scenario directly
    (populating the contextvar before creating the field) so the ordering fix
    is covered without needing netbox-branching installed.
    """

    def test_title_column_exists_before_deferred_title_replay_runs(self):
        from netbox_custom_objects.models import _deferred_co_field_data

        cot = self.create_custom_object_type(name="DeferredCOT", slug="deferred-cot")
        model = cot.get_model()
        co = model.objects.create()
        table_name = cot.get_database_table_name()

        _deferred_co_field_data.set({
            table_name: {
                co.pk: {
                    "data": {
                        "website": "https://example.com/",
                        "website_title": "Example Site",
                    },
                },
            },
        })
        try:
            self.create_custom_object_type_field(
                cot, name="website", label="Website", type="url",
            )
        finally:
            _deferred_co_field_data.set(None)

        fresh_co = cot.get_model().objects.get(pk=co.pk)
        self.assertEqual(fresh_co.website, "https://example.com/")
        self.assertEqual(
            fresh_co.website_title, "Example Site",
            "deferred title value must be replayed once the title column exists",
        )


class CoordinatesBackingColumnsTestCase(CustomObjectsTestCase, TestCase):
    """
    Regression tests for #770: a coordinates field has no column matching its own
    name -- only ``<name>_latitude`` and ``<name>_longitude`` -- so any code path
    that derives columns from the bare field name silently skips it.  Two such
    paths: the deferred squash-merge replay (``_apply_deferred_co_field``) and the
    audit-key rewrite on rename (``_rename_objectchange_field_key``).
    """

    def test_deferred_replay_fills_both_coordinates_columns(self):
        from netbox_custom_objects.models import _deferred_co_field_data

        cot = self.create_custom_object_type(name="DeferredCoordCOT", slug="deferred-coord-cot")
        model = cot.get_model()
        co = model.objects.create()
        table_name = cot.get_database_table_name()

        _deferred_co_field_data.set({
            table_name: {
                co.pk: {
                    "data": {
                        "geo_latitude": "12.345600",
                        "geo_longitude": "-98.765400",
                    },
                },
            },
        })
        try:
            self.create_custom_object_type_field(cot, name="geo", label="Geo", type="coordinates")
            self.assertIsNone(
                _deferred_co_field_data.get(),
                "deferred coordinates data must be consumed once both columns exist",
            )
        finally:
            _deferred_co_field_data.set(None)

        fresh_co = cot.get_model().objects.get(pk=co.pk)
        self.assertEqual(fresh_co.geo_latitude, Decimal("12.345600"))
        self.assertEqual(fresh_co.geo_longitude, Decimal("-98.765400"))

    def test_deferred_replay_matches_coordinates_columns_under_historical_name(self):
        """A CO recorded under the field's pre-rename name still replays after a rename."""
        from netbox_custom_objects.models import _apply_deferred_co_field, _deferred_co_field_data

        cot = self.create_custom_object_type(name="DeferredCoordRenameCOT", slug="deferred-coord-rename-cot")
        field = self.create_custom_object_type_field(cot, name="geo", label="Geo", type="coordinates")
        co = cot.get_model().objects.create()
        table_name = cot.get_database_table_name()

        field = CustomObjectTypeField.objects.get(pk=field.pk)
        field.name = "location"
        field.save()
        # The rename history _apply_deferred_co_field consults lives in ObjectChange.
        ObjectChange.objects.create(
            user_name="tester",
            request_id=uuid.uuid4(),
            action=ObjectChangeActionChoices.ACTION_UPDATE,
            changed_object_type=ObjectType.objects.get_for_model(CustomObjectTypeField),
            changed_object_id=field.pk,
            object_repr=str(field),
            prechange_data={"name": "geo"},
            postchange_data={"name": "location"},
        )

        _deferred_co_field_data.set({
            table_name: {co.pk: {"data": {"geo_latitude": "1.5", "geo_longitude": "2.5"}}},
        })
        try:
            _apply_deferred_co_field(CustomObjectTypeField.objects.get(pk=field.pk))
        finally:
            _deferred_co_field_data.set(None)

        fresh_co = cot.get_model().objects.get(pk=co.pk)
        self.assertEqual(fresh_co.location_latitude, Decimal("1.5"))
        self.assertEqual(fresh_co.location_longitude, Decimal("2.5"))

    def test_rename_rewrites_both_coordinates_keys_in_objectchange(self):
        cot = self.create_custom_object_type(name="RenameCoordCOT", slug="rename-coord-cot")
        field = self.create_custom_object_type_field(cot, name="geo", label="Geo", type="coordinates")
        self.create_custom_object_type_field(cot, name="note", label="Note", type="text")
        model = cot.get_model()
        co = model.objects.create(geo_latitude=Decimal("1.5"), geo_longitude=Decimal("2.5"), note="n")

        change = ObjectChange.objects.create(
            user_name="tester",
            request_id=uuid.uuid4(),
            action=ObjectChangeActionChoices.ACTION_UPDATE,
            changed_object_type=ObjectType.objects.get_for_model(model),
            changed_object_id=co.pk,
            object_repr=str(co),
            prechange_data={"geo_latitude": "0.0", "geo_longitude": "0.0", "note": "n"},
            postchange_data={"geo_latitude": "1.5", "geo_longitude": "2.5", "note": "n"},
        )

        field = CustomObjectTypeField.objects.get(pk=field.pk)
        field.name = "location"
        field.save()

        change.refresh_from_db()
        self.assertEqual(
            change.prechange_data,
            {"location_latitude": "0.0", "location_longitude": "0.0", "note": "n"},
        )
        self.assertEqual(
            change.postchange_data,
            {"location_latitude": "1.5", "location_longitude": "2.5", "note": "n"},
        )


class PhantomTaggedObjectsTestCase(CustomObjectsTestCase, TestCase):
    """Regression tests for issue #629 (Phantom Tagged Objects): deleting a
    tagged custom object left its TaggedItem row behind."""

    def setUp(self):
        super().setUp()
        self.cot = self.create_custom_object_type(name="SslProfile", slug="ssl-profiles")
        self.create_custom_object_type_field(
            self.cot, name="name", label="Name", type="text", primary=True, required=True,
        )
        self.tag = Tag.objects.create(name="unbound", slug="unbound")

    def _tagged_item_count(self):
        ct = ContentType.objects.get_for_model(self.cot.get_model())
        return TaggedItem.objects.filter(tag=self.tag, content_type=ct).count()

    def test_generated_model_has_tagged_items_generic_relation(self):
        model = self.cot.get_model()
        private_field_names = [f.name for f in model._meta.private_fields]
        self.assertIn('tagged_items', private_field_names)

    def test_direct_delete_removes_tagged_item(self):
        model = self.cot.get_model()
        obj = model.objects.create(name="profile-a")
        obj.tags.set([self.tag])
        self.assertEqual(self._tagged_item_count(), 1)

        obj.delete()

        self.assertEqual(self._tagged_item_count(), 0)

    def test_queryset_bulk_delete_removes_tagged_item(self):
        model = self.cot.get_model()
        obj = model.objects.create(name="profile-b")
        obj.tags.set([self.tag])
        self.assertEqual(self._tagged_item_count(), 1)

        model.objects.filter(pk=obj.pk).delete()

        self.assertEqual(self._tagged_item_count(), 0)

    def test_delete_after_model_regeneration_removes_tagged_item(self):
        model = self.cot.get_model()
        obj = model.objects.create(name="profile-c")
        obj.tags.set([self.tag])
        self.assertEqual(self._tagged_item_count(), 1)

        fresh_model = self.cot.get_model(no_cache=True)
        self.assertIsNot(fresh_model, model)
        fresh_model.objects.get(pk=obj.pk).delete()

        self.assertEqual(self._tagged_item_count(), 0)

    def test_filtering_by_tag_matches_actual_object_count(self):
        model = self.cot.get_model()
        kept = model.objects.create(name="profile-kept")
        kept.tags.set([self.tag])
        deleted = model.objects.create(name="profile-deleted")
        deleted.tags.set([self.tag])
        deleted.delete()

        self.assertEqual(model.objects.filter(tags=self.tag).count(), 1)
        self.assertEqual(self._tagged_item_count(), 1)


class CustomObjectPicklingTestCase(CustomObjectsTestCase, TestCase):
    """
    Generated models can be pickled by reference, as RQ webhook and script jobs do with
    querysets and prefetch caches that hold them.
    """

    def setUp(self):
        super().setUp()
        self.cot = self.create_custom_object_type(name='Session', slug='session')
        self.create_custom_object_type_field(
            self.cot, name='name', label='Name', type='text', primary=True, required=True,
        )
        self.create_custom_object_type_field(
            self.cot, name='site', label='Site', type='object', related_object_type=self.get_site_object_type(),
        )
        self.model = self.cot.get_model()
        self.site = Site.objects.create(name='Pickle Site', slug='pickle-site')
        self.model.objects.create(name='s1', site=self.site)

    def test_model_class_and_queryset_round_trip(self):
        self.assertEqual(self.model.__module__, 'netbox_custom_objects.models')
        # Identity holds because get_model() returns the cached class; see the rebuilt-model test below.
        self.assertIs(pickle.loads(pickle.dumps(self.model)), self.model)

        queryset = pickle.loads(pickle.dumps(self.model.objects.all()))
        self.assertEqual([obj.name for obj in queryset], ['s1'])

    def test_prefetch_cache_round_trips(self):
        """The reported path: a related object whose prefetch cache holds custom objects."""
        accessor = next(
            rel.get_accessor_name() for rel in Site._meta.related_objects if rel.related_model is self.model
        )
        site = Site.objects.prefetch_related(accessor).get(pk=self.site.pk)

        restored = pickle.loads(pickle.dumps(site))
        self.assertEqual([obj.name for obj in getattr(restored, accessor).all()], ['s1'])

    def test_unpickling_resolves_a_rebuilt_model(self):
        """Unpickling resolves the model after its cache is cleared."""
        data = pickle.dumps(self.model.objects.all())
        CustomObjectType.clear_model_cache(self.cot.pk, all_branches=True)

        queryset = pickle.loads(data)
        self.assertIs(queryset.model, CustomObjectType.objects.get(pk=self.cot.pk).get_model())
        self.assertEqual([obj.name for obj in queryset], ['s1'])

    def test_module_attribute_lookup_only_resolves_existing_types(self):
        self.assertIs(getattr(nco_models, self.model.__name__), self.model)
        with self.assertNoLogs(nco_models.logger):
            self.assertFalse(hasattr(nco_models, 'Table999999Model'))
            self.assertFalse(hasattr(nco_models, 'NotAModel'))

    def test_module_attribute_lookup_logs_database_errors(self):
        # Unapplied migrations or a database failure: a missing attribute, with the error logged.
        for error in (ProgrammingError, OperationalError):
            with self.subTest(error=error.__name__):
                with patch.object(CustomObjectType.objects, 'get', side_effect=error('boom')):
                    with self.assertLogs(nco_models.logger, level='WARNING') as logs:
                        self.assertFalse(hasattr(nco_models, self.model.__name__))
                self.assertIs(logs.records[0].exc_info[0], error)
