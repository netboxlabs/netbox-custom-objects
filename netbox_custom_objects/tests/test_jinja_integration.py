"""
Tests for the Jinja config-template integration (jinja_env.py + PluginConfig hooks).

The custom_objects filter and CustomObjectsNamespace are pure plugin-side code and
are always tested directly, regardless of NetBox version. End-to-end tests that
depend on NetBox actually invoking these hooks (added in NetBox 4.7 — see
netbox-community/netbox#22363, later renamed by #22436) are skipped on older
NetBox, detected via the same registry check performed by
CustomObjectsPluginConfig.ready().
"""
from unittest.mock import patch

import jinja2
from django.apps import apps as django_apps
from django.test import SimpleTestCase, TestCase

from netbox_custom_objects import CustomObjectsPluginConfig, jinja_env
from netbox_custom_objects.jinja_env import CustomObjectsNamespace, EmptyCustomObjectsQuerySet, custom_objects_filter
from netbox_custom_objects.models import CustomObjectType

from .base import CustomObjectsTestCase


def _jinja_hooks_available():
    """True if this NetBox install actually registered the custom_objects filter.

    Mirrors the check in CustomObjectsPluginConfig.ready(): on NetBox < 4.7, the
    jinja_filters plugin resource doesn't exist, so ready() never registers
    anything under that key.
    """
    from netbox.registry import registry
    return 'custom_objects' in registry.get('plugins', {}).get('jinja_filters', {})


class EmptyCustomObjectsQuerySetTestCase(SimpleTestCase):
    """Tests for EmptyCustomObjectsQuerySet's chainable no-op interface directly."""

    def test_read_methods_are_chainable_and_stay_empty(self):
        qs = EmptyCustomObjectsQuerySet()
        chained = (
            qs.filter(x=1).exclude(y=2).all().none().order_by('x')
            .values('x').values_list('x').select_related('x')
            .prefetch_related('x').distinct().annotate(x=1)
        )
        self.assertIsInstance(chained, EmptyCustomObjectsQuerySet)
        self.assertEqual(list(chained), [])

    def test_slicing_returns_self(self):
        qs = EmptyCustomObjectsQuerySet()
        self.assertIsInstance(qs[:5], EmptyCustomObjectsQuerySet)

    def test_integer_index_raises_index_error(self):
        qs = EmptyCustomObjectsQuerySet()
        with self.assertRaises(IndexError):
            _ = qs[0]

    def test_get_raises_lookup_error(self):
        qs = EmptyCustomObjectsQuerySet()
        with self.assertRaises(LookupError):
            qs.get(x=1)

    def test_first_and_last_return_none(self):
        qs = EmptyCustomObjectsQuerySet()
        self.assertIsNone(qs.first())
        self.assertIsNone(qs.last())

    def test_count_and_exists(self):
        qs = EmptyCustomObjectsQuerySet()
        self.assertEqual(qs.count(), 0)
        self.assertFalse(qs.exists())

    def test_len_and_bool(self):
        qs = EmptyCustomObjectsQuerySet()
        self.assertEqual(len(qs), 0)
        self.assertFalse(qs)


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
        # custom_objects_filter is @pass_context; the context argument is unused, so any
        # value (None, here) is fine when calling it directly rather than through Jinja.
        result = custom_objects_filter(None, 'j2widget')
        self.assertEqual(result.count(), 2)

    def test_returns_empty_for_unknown_type(self):
        result = custom_objects_filter(None, 'nonexistent_type')
        self.assertIsInstance(result, EmptyCustomObjectsQuerySet)
        self.assertEqual(list(result), [])

    def test_unknown_type_result_tolerates_further_chaining(self):
        """A template that chains .filter()/.all() onto an unresolved name must not crash."""
        result = custom_objects_filter(None, 'nonexistent_type')
        self.assertEqual(list(result.filter(label='x').all().exclude(label='y')), [])

    def test_unknown_name_warning_logged_once_per_process(self):
        """
        A typo'd type name rendered repeatedly (e.g. across a bulk device config
        export) must log its warning once, not once per lookup.
        """
        unique_name = 'warn_once_filter_type'
        jinja_env._warned_unknown_names.discard(unique_name)
        self.addCleanup(jinja_env._warned_unknown_names.discard, unique_name)

        with patch.object(jinja_env.logger, 'warning') as mock_warning:
            custom_objects_filter(None, unique_name)
            custom_objects_filter(None, unique_name)
            custom_objects_filter(None, unique_name)
        self.assertEqual(mock_warning.call_count, 1)


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

    def test_bracket_notation_resolves_leading_digit_type_name(self):
        """
        Bracket notation is Jinja's own getitem-then-getattr fallback, not
        Python's __getitem__, so it must go through an actual Jinja render.
        """
        cot = self.create_custom_object_type(name='123widget', slug='123-widget')
        self.create_custom_object_type_field(
            cot, name='label', label='Label', type='text', primary=True, required=True,
        )
        model = cot.get_model()
        model.objects.create(label='alpha')
        ns = CustomObjectsNamespace()
        template = jinja2.Environment().from_string("{{ custom_objects['123widget'].filter(label='alpha').count() }}")
        self.assertEqual(template.render(custom_objects=ns), '1')

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

    def test_repeated_access_to_same_name_is_cached_within_a_render(self):
        """
        A template referencing custom_objects.j2widget multiple times in one render
        must resolve the Custom Object Type once, not once per reference.
        """
        ns = CustomObjectsNamespace()
        with patch.object(CustomObjectType.objects, 'get', wraps=CustomObjectType.objects.get) as mock_get:
            ns.j2widget
            ns.j2widget
            ns.j2widget
        self.assertEqual(mock_get.call_count, 1)

    def test_cache_is_not_shared_across_namespace_instances(self):
        """Caching is per-render (per CustomObjectsNamespace instance), not global."""
        CustomObjectsNamespace().j2widget
        with patch.object(CustomObjectType.objects, 'get', wraps=CustomObjectType.objects.get) as mock_get:
            CustomObjectsNamespace().j2widget
        self.assertEqual(mock_get.call_count, 1)

    def test_unknown_name_warning_logged_once_per_process(self):
        """Repeated access to the same unresolved name must log its warning once."""
        unique_name = 'warn_once_namespace_type'
        jinja_env._warned_unknown_names.discard(unique_name)
        self.addCleanup(jinja_env._warned_unknown_names.discard, unique_name)

        ns = CustomObjectsNamespace()
        with patch.object(jinja_env.logger, 'warning') as mock_warning:
            getattr(ns, unique_name)
            # A fresh namespace (new render) still shares the process-level warned set.
            getattr(CustomObjectsNamespace(), unique_name)
        self.assertEqual(mock_warning.call_count, 1)


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

    def test_filter_syntax_resolves_only_once_when_compiled_and_rendered(self):
        """
        Without @pass_context, Jinja can constant-fold the filter call at
        compile time, resolving the type an extra time before render.
        """
        from utilities.jinja2 import render_jinja2
        model = self.cot.get_model()
        model.objects.create(label='alpha')
        template_code = "{% for obj in 'j2widget' | custom_objects %}{{ obj.label }}{% endfor %}"
        with patch.object(CustomObjectType.objects, 'get', wraps=CustomObjectType.objects.get) as mock_get:
            result = render_jinja2(template_code, {})
        self.assertEqual(result, 'alpha')
        self.assertEqual(mock_get.call_count, 1)

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

    def test_bracket_notation_in_config_template_render(self):
        """A leading-digit type name isn't valid dot-notation; use bracket notation."""
        from extras.models import ConfigTemplate
        cot = self.create_custom_object_type(name='123widget', slug='123-widget')
        self.create_custom_object_type_field(
            cot, name='label', label='Label', type='text', primary=True, required=True,
        )
        model = cot.get_model()
        model.objects.create(label='alpha')
        tmpl = ConfigTemplate(
            name='test-j2-leading-digit',
            template_code="{{ custom_objects['123widget'].filter(label='alpha') | list | length }}",
        )
        self.assertEqual(tmpl.render(), '1')
