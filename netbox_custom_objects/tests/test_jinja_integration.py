"""
Tests for the Jinja config-template integration (jinja_env.py + PluginConfig hooks).

The custom_objects filter and CustomObjectsNamespace are pure plugin-side code and
are always tested directly, regardless of NetBox version. End-to-end tests that
depend on NetBox actually invoking these hooks (added in NetBox 4.7 — see
netbox-community/netbox#22363, later renamed by #22436) are skipped on older
NetBox, detected via the same registry check performed by
CustomObjectsPluginConfig.ready().
"""
from django.apps import apps as django_apps
from django.test import TestCase

from netbox_custom_objects import CustomObjectsPluginConfig
from netbox_custom_objects.jinja_env import CustomObjectsNamespace, EmptyCustomObjectsQuerySet, custom_objects_filter

from .base import CustomObjectsTestCase


def _jinja_hooks_available():
    """True if this NetBox install actually registered the custom_objects filter.

    Mirrors the check in CustomObjectsPluginConfig.ready(): on NetBox < 4.7, the
    jinja_filters plugin resource doesn't exist, so ready() never registers
    anything under that key.
    """
    from netbox.registry import registry
    return 'custom_objects' in registry.get('plugins', {}).get('jinja_filters', {})


class CustomObjectsFilterTestCase(CustomObjectsTestCase, TestCase):
    """Tests for custom_objects_filter() directly (no NetBox hook dependency)."""

    @classmethod
    def setUpTestData(cls):
        cls.cot = cls.create_custom_object_type(name='j2widget', slug='j2-widget')
        cls.create_custom_object_type_field(
            cls.cot, name='label', label='Label', type='text', primary=True, required=True,
        )

    def test_returns_queryset_for_known_type(self):
        model = self.cot.get_model()
        model.objects.create(label='alpha')
        model.objects.create(label='beta')
        result = custom_objects_filter('j2widget')
        self.assertEqual(result.count(), 2)

    def test_returns_empty_for_unknown_type(self):
        result = custom_objects_filter('nonexistent_type')
        self.assertIsInstance(result, EmptyCustomObjectsQuerySet)
        self.assertEqual(list(result), [])

    def test_unknown_type_result_tolerates_further_chaining(self):
        """A template that chains .filter()/.all() onto an unresolved name must not crash."""
        result = custom_objects_filter('nonexistent_type')
        self.assertEqual(list(result.filter(label='x').all().exclude(label='y')), [])


class CustomObjectsNamespaceTestCase(CustomObjectsTestCase, TestCase):
    """Tests for CustomObjectsNamespace directly (no NetBox hook dependency)."""

    @classmethod
    def setUpTestData(cls):
        cls.cot = cls.create_custom_object_type(name='j2widget', slug='j2-widget')
        cls.create_custom_object_type_field(
            cls.cot, name='label', label='Label', type='text', primary=True, required=True,
        )

    def test_resolves_by_name_to_model_manager(self):
        ns = CustomObjectsNamespace()
        manager = ns.j2widget
        self.assertIs(manager.model, self.cot.get_model())

    def test_manager_supports_filter(self):
        model = self.cot.get_model()
        model.objects.create(label='alpha')
        model.objects.create(label='beta')
        ns = CustomObjectsNamespace()
        self.assertEqual(ns.j2widget.filter(label='alpha').count(), 1)

    def test_unknown_name_returns_empty_queryset_stand_in(self):
        """An unresolved name must not raise -- matches custom_objects_filter()'s behavior."""
        ns = CustomObjectsNamespace()
        result = ns.no_such_type
        self.assertIsInstance(result, EmptyCustomObjectsQuerySet)
        self.assertEqual(list(result), [])

    def test_unknown_name_result_tolerates_further_chaining(self):
        """A template that chains .filter(device=device) onto an unresolved name must not crash."""
        ns = CustomObjectsNamespace()
        self.assertEqual(list(ns.no_such_type.filter(device='anything')), [])

    def test_does_not_intercept_dunder_attributes(self):
        """Internal/dunder lookups (e.g. by copy.deepcopy) must not trigger a DB query."""
        ns = CustomObjectsNamespace()
        with self.assertRaises(AttributeError):
            _ = ns.__deepcopy__


class PluginConfigJinjaHooksTestCase(CustomObjectsTestCase, TestCase):
    """Tests for CustomObjectsPluginConfig.get_jinja_context() directly."""

    @classmethod
    def setUpTestData(cls):
        cls.cot = cls.create_custom_object_type(name='j2widget', slug='j2-widget')
        cls.create_custom_object_type_field(
            cls.cot, name='label', label='Label', type='text', primary=True, required=True,
        )

    def test_get_jinja_context_returns_custom_objects_namespace(self):
        plugin_config = django_apps.get_app_config('netbox_custom_objects')
        self.assertIsInstance(plugin_config, CustomObjectsPluginConfig)
        ctx = plugin_config.get_jinja_context()
        self.assertIn('custom_objects', ctx)
        self.assertIsInstance(ctx['custom_objects'], CustomObjectsNamespace)

    def test_get_jinja_context_namespace_resolves_live_data(self):
        model = self.cot.get_model()
        model.objects.create(label='gamma')
        plugin_config = django_apps.get_app_config('netbox_custom_objects')
        ctx = plugin_config.get_jinja_context()
        self.assertEqual(ctx['custom_objects'].j2widget.count(), 1)


class JinjaHookIntegrationTestCase(CustomObjectsTestCase, TestCase):
    """
    End-to-end tests exercising the actual NetBox render pipeline. Skipped on
    NetBox versions that don't expose the jinja_filters / get_jinja_context hooks.
    """

    @classmethod
    def setUpTestData(cls):
        cls.cot = cls.create_custom_object_type(name='j2widget', slug='j2-widget')
        cls.create_custom_object_type_field(
            cls.cot, name='label', label='Label', type='text', primary=True, required=True,
        )

    def setUp(self):
        super().setUp()
        if not _jinja_hooks_available():
            self.skipTest(
                'NetBox Jinja config template hooks (jinja_filters / get_jinja_context) '
                'are not available in this NetBox version; requires NetBox 4.7+.'
            )

    def test_filter_syntax_available_in_render_jinja2(self):
        from utilities.jinja2 import render_jinja2
        model = self.cot.get_model()
        model.objects.create(label='alpha')
        result = render_jinja2("{{ 'j2widget' | custom_objects | list | length }}", {})
        self.assertEqual(result, '1')

    def test_context_namespace_available_in_config_template_render(self):
        from extras.models import ConfigTemplate
        model = self.cot.get_model()
        model.objects.create(label='alpha')
        tmpl = ConfigTemplate(
            name='test-j2',
            template_code='{{ custom_objects.j2widget.all() | list | length }}',
        )
        self.assertEqual(tmpl.render(), '1')

    def test_unknown_type_name_in_filter_syntax_renders_empty(self):
        from utilities.jinja2 import render_jinja2
        result = render_jinja2("{{ 'no_such_type' | custom_objects | list | length }}", {})
        self.assertEqual(result, '0')

    def test_unknown_type_name_in_attribute_syntax_renders_empty(self):
        """
        A template chaining .filter() onto an unresolved attribute-style name (as in
        every documented example) must render no rows, not raise UndefinedError.
        """
        from extras.models import ConfigTemplate
        tmpl = ConfigTemplate(
            name='test-j2-unknown',
            template_code='{{ custom_objects.no_such_type.filter(label="x") | list | length }}',
        )
        self.assertEqual(tmpl.render(), '0')
