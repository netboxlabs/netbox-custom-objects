"""
Tests for the related_tabs subpackage (combined "Custom Objects" tab).

Focused on the surfaces most likely to regress:

* ``reference_q()`` builds the correct filter per field kind, and returns an
  EMPTY Q (which callers must treat as "skip", never as match-all) for an
  unsupported field type or an unresolvable polymorphic through model. A
  regression here would leak every custom object of a type onto every host page.
* ``_public_host_model_classes()`` enumerates the public models a COT field can
  target (the tab is registered on all of them) and excludes this plugin's
  own app (custom-object hosts are served by the generic injected URL).
* ``register_combined_tabs()`` adds a ``custom_objects`` view to NetBox's view
  registry for each model, idempotently.
* ``_count_linked_custom_objects()`` returns None for a model nothing references
  (the cheap ``.exists()`` fast path that keeps the per-detail-page badge cheap)
  and a positive count for a referenced one.
"""

from types import SimpleNamespace
from unittest.mock import patch

from core.models import ObjectType
from django.db.models import Q
from django.test import TestCase, TransactionTestCase
from extras.choices import CustomFieldTypeChoices
from netbox.registry import registry

from dcim.models import Site

from netbox_custom_objects.constants import APP_LABEL
from netbox_custom_objects.related_tabs.registry import _public_host_model_classes
from netbox_custom_objects.related_tabs.views.combined import (
    COMBINED_LABEL,
    COMBINED_WEIGHT,
    _count_linked_custom_objects,
    _filter_linked_objects,
    _get_field_value,
    _get_linked_custom_objects,
    _max_multiobject_display,
    _sort_header,
    reference_q,
    register_combined_tabs,
)
from netbox_custom_objects.tests.base import CustomObjectsTestCase, TransactionCleanupMixin


class ReferenceQTests(TestCase):
    """
    ``reference_q()`` builds the correct filter per field kind, and — critically —
    returns an EMPTY Q (which callers must treat as "skip", never as match-all) for
    an unsupported field type or an unresolvable polymorphic through model. A
    regression here would leak every custom object of a type onto every host page.
    """

    def test_object_non_polymorphic(self):
        self.assertEqual(
            reference_q(1, 42, 'site', CustomFieldTypeChoices.TYPE_OBJECT, False, None),
            Q(site_id=42),
        )

    def test_object_polymorphic(self):
        self.assertEqual(
            reference_q(7, 42, 'thing', CustomFieldTypeChoices.TYPE_OBJECT, True, None),
            Q(thing_content_type_id=7, thing_object_id=42),
        )

    def test_multiobject_non_polymorphic(self):
        self.assertEqual(
            reference_q(1, 42, 'sites', CustomFieldTypeChoices.TYPE_MULTIOBJECT, False, None),
            Q(sites=42),
        )

    def test_unsupported_field_type_returns_empty_q(self):
        q = reference_q(1, 42, 'x', CustomFieldTypeChoices.TYPE_TEXT, False, None)
        self.assertFalse(q.children)  # empty Q == "skip", NOT match-all

    def test_unresolvable_through_returns_empty_q(self):
        # Polymorphic MULTIOBJECT whose through model isn't in the app registry.
        with self.assertLogs('netbox_custom_objects.related_tabs', level='ERROR'):
            q = reference_q(1, 42, 'x', CustomFieldTypeChoices.TYPE_MULTIOBJECT, True, 'Through_does_not_exist')
        self.assertFalse(q.children)


class PublicHostModelsTests(TestCase):
    """
    ``_public_host_model_classes()`` is the Variant-B registration set: every
    public model a COT Object/Multi-object field can target, minus this plugin's
    own app (custom-object hosts are served by the generic injected URL).
    """

    def test_includes_public_builtin_model(self):
        labels = {(m._meta.app_label, m._meta.model_name) for m in _public_host_model_classes()}
        self.assertIn(('dcim', 'site'), labels)

    def test_excludes_custom_objects_app(self):
        offenders = [m for m in _public_host_model_classes() if m._meta.app_label == APP_LABEL]
        self.assertEqual(offenders, [], 'CO host models are served by the generic URL and must not be registered')

    def test_deduplicated(self):
        labels = [(m._meta.app_label, m._meta.model_name) for m in _public_host_model_classes()]
        self.assertEqual(len(labels), len(set(labels)))


class RegisterCombinedTabsTests(TestCase):
    """
    ``register_combined_tabs()`` adds one ``custom_objects`` view per model to
    NetBox's process-global view registry, idempotently.
    """

    def tearDown(self):
        # Don't pollute the process-global registry for other tests.
        entries = registry['views'].get('dcim', {}).get('site', [])
        registry['views']['dcim']['site'] = [e for e in entries if e['name'] != 'custom_objects']
        super().tearDown()

    def _site_tab_names(self):
        return [e['name'] for e in registry['views'].get('dcim', {}).get('site', [])]

    def test_registers_tab_view(self):
        # Don't assert the tab is absent first: register_tabs() at ready() may
        # already have registered it in this process. Idempotency (verified in
        # the next test) means register_combined_tabs leaves exactly one entry
        # either way.
        register_combined_tabs([Site], COMBINED_LABEL, COMBINED_WEIGHT)
        self.assertEqual(self._site_tab_names().count('custom_objects'), 1)

    def test_registration_is_idempotent(self):
        register_combined_tabs([Site], COMBINED_LABEL, COMBINED_WEIGHT)
        register_combined_tabs([Site], COMBINED_LABEL, COMBINED_WEIGHT)
        self.assertEqual(self._site_tab_names().count('custom_objects'), 1)


class BadgeGateTests(TransactionCleanupMixin, CustomObjectsTestCase, TransactionTestCase):
    """
    ``_count_linked_custom_objects`` is both the tab badge and the display gate.
    It must return None (so hide_if_empty hides the tab, and the cheap .exists()
    fast path fires) when nothing references the host model, and a live count
    when something does.
    """

    def test_unreferenced_model_returns_none(self):
        # No COT field targets dcim.site -> the .exists() guard short-circuits.
        site = Site.objects.create(name='Lonely Badge Site', slug='lonely-badge-site')
        self.assertIsNone(_count_linked_custom_objects(site))

    def test_referenced_model_returns_live_count(self):
        site = Site.objects.create(name='Badge Site', slug='badge-site')
        cot = self.create_custom_object_type(name='badge_cot', slug='badge-cot')
        self.create_custom_object_type_field(cot, name='name', label='Name', type='text', primary=True)
        self.create_custom_object_type_field(
            cot,
            name='site',
            label='Site',
            type=CustomFieldTypeChoices.TYPE_OBJECT,
            is_polymorphic=False,
            related_object_type=ObjectType.objects.get_for_model(Site),
        )
        model = cot.get_model()
        model.objects.create(name='co-1', site=site)

        self.assertEqual(_count_linked_custom_objects(site), 1)
        # A different, unreferenced site still gates to None.
        other = Site.objects.create(name='Other Site', slug='other-badge-site')
        self.assertIsNone(_count_linked_custom_objects(other))


class _Label:
    """Minimal stand-in with a controllable ``str()`` for the pure-unit tests below."""

    def __init__(self, label):
        self._label = label

    def __str__(self):
        return self._label


class _FieldLabel(_Label):
    def __init__(self, label, cot_label):
        super().__init__(label)
        self.custom_object_type = _Label(cot_label)


class FilterLinkedObjectsTests(TestCase):
    """``_filter_linked_objects`` is a case-insensitive substring match across the
    object name, the custom object type, and the field label."""

    def setUp(self):
        self.linked = [
            (_Label('Web Server'), _FieldLabel('Hostname', 'Tickets')),
            (_Label('DB Primary'), _FieldLabel('Owner', 'Assets')),
        ]

    def test_empty_query_returns_all(self):
        self.assertEqual(_filter_linked_objects(self.linked, ''), self.linked)
        self.assertEqual(_filter_linked_objects(self.linked, '   '), self.linked)

    def test_matches_object_name_case_insensitively(self):
        self.assertEqual(_filter_linked_objects(self.linked, 'WEB'), [self.linked[0]])

    def test_matches_custom_object_type(self):
        self.assertEqual(_filter_linked_objects(self.linked, 'asset'), [self.linked[1]])

    def test_matches_field_label(self):
        self.assertEqual(_filter_linked_objects(self.linked, 'hostname'), [self.linked[0]])

    def test_no_match_returns_empty(self):
        self.assertEqual(_filter_linked_objects(self.linked, 'zzz'), [])


class GetFieldValueTests(TestCase):
    """``_get_field_value`` reads OBJECT / MULTIOBJECT values for the Value column,
    fetching one extra MULTIOBJECT item so the template can flag truncation without
    a COUNT query."""

    def test_object_returns_related_instance(self):
        related = _Label('site-a')
        field = SimpleNamespace(type=CustomFieldTypeChoices.TYPE_OBJECT, name='site')
        self.assertIs(_get_field_value(SimpleNamespace(site=related), field), related)

    def test_object_unset_returns_none(self):
        field = SimpleNamespace(type=CustomFieldTypeChoices.TYPE_OBJECT, name='site')
        self.assertIsNone(_get_field_value(SimpleNamespace(), field))

    def test_multiobject_truncates_to_max_plus_one(self):
        limit = _max_multiobject_display()
        items = [_Label(str(i)) for i in range(limit + 3)]
        field = SimpleNamespace(type=CustomFieldTypeChoices.TYPE_MULTIOBJECT, name='targets')
        obj = SimpleNamespace(targets=SimpleNamespace(all=lambda: items))
        self.assertEqual(_get_field_value(obj, field), items[: limit + 1])

    def test_multiobject_under_limit_returns_all(self):
        items = [_Label('a'), _Label('b')]
        field = SimpleNamespace(type=CustomFieldTypeChoices.TYPE_MULTIOBJECT, name='targets')
        obj = SimpleNamespace(targets=SimpleNamespace(all=lambda: items))
        self.assertEqual(_get_field_value(obj, field), items)

    def test_multiobject_respects_configured_limit(self):
        """The Value column honours the max_multiobject_display plugin setting."""
        items = [_Label(str(i)) for i in range(10)]
        field = SimpleNamespace(type=CustomFieldTypeChoices.TYPE_MULTIOBJECT, name='targets')
        obj = SimpleNamespace(targets=SimpleNamespace(all=lambda: items))
        with patch(
            'netbox_custom_objects.related_tabs.views.combined.get_plugin_config',
            return_value=1,
        ):
            # configured limit 1, plus the 1 extra item used for truncation detection
            self.assertEqual(_get_field_value(obj, field), items[:2])

    def test_multiobject_unset_returns_empty_list(self):
        field = SimpleNamespace(type=CustomFieldTypeChoices.TYPE_MULTIOBJECT, name='targets')
        self.assertEqual(_get_field_value(SimpleNamespace(), field), [])


class SortHeaderTests(TestCase):
    """``_sort_header`` builds native-style sortable headers off a single ``?sort=``
    param (a ``-`` prefix means descending)."""

    def test_inactive_column(self):
        h = _sort_header('object', {}, 'type', False)
        self.assertFalse(h['is_active'])
        self.assertEqual(h['th_class'], '')
        self.assertEqual(h['url'], '?sort=object')
        self.assertEqual(h['clear_url'], '?')

    def test_active_ascending_toggles_to_descending(self):
        h = _sort_header('type', {}, 'type', False)
        self.assertTrue(h['is_active'])
        self.assertEqual(h['th_class'], 'asc')
        self.assertEqual(h['url'], '?sort=-type')

    def test_active_descending_toggles_to_ascending(self):
        h = _sort_header('type', {}, 'type', True)
        self.assertTrue(h['is_active'])
        self.assertEqual(h['th_class'], 'desc')
        self.assertEqual(h['url'], '?sort=type')

    def test_base_params_preserved_in_links(self):
        h = _sort_header('object', {'q': 'x'}, 'type', False)
        self.assertEqual(h['url'], '?q=x&sort=object')
        self.assertEqual(h['clear_url'], '?q=x')


class CombinedTabQueryTests(TransactionCleanupMixin, CustomObjectsTestCase, TransactionTestCase):
    """DB-backed coverage for reference resolution, per-row permission enforcement,
    and badge summation — the parts of the tab a mocked unit test can't reach."""

    def _object_field_cot(self, name, slug):
        cot = self.create_custom_object_type(name=name, slug=slug)
        self.create_custom_object_type_field(cot, name='name', label='Name', type='text', primary=True)
        self.create_custom_object_type_field(
            cot,
            name='site',
            label='Site',
            type=CustomFieldTypeChoices.TYPE_OBJECT,
            is_polymorphic=False,
            related_object_type=ObjectType.objects.get_for_model(Site),
        )
        return cot

    def test_polymorphic_multiobject_through_is_resolved_and_counted(self):
        # Exercises reference_q()'s polymorphic-MULTIOBJECT subquery branch end to
        # end: a wrong through filter key would silently drop the row (count 0).
        from django.apps import apps as django_apps
        from django.contrib.contenttypes.models import ContentType

        site = Site.objects.create(name='Poly MO Site', slug='poly-mo-site')
        cot = self.create_custom_object_type(name='poly_mo', slug='poly-mo')
        self.create_custom_object_type_field(cot, name='name', label='Name', type='text', primary=True)
        field = self.create_polymorphic_field(
            cot,
            [ObjectType.objects.get_for_model(Site)],
            name='targets',
            type=CustomFieldTypeChoices.TYPE_MULTIOBJECT,
        )
        obj = cot.get_model().objects.create(name='mo-1')

        # Link the site through the field's through table, exactly as reference_q reads it.
        through = django_apps.get_model(APP_LABEL, field.through_model_name)
        through.objects.create(
            source_id=obj.pk,
            content_type_id=ContentType.objects.get_for_model(Site).id,
            object_id=site.pk,
        )

        self.assertEqual(_count_linked_custom_objects(site), 1)
        # An unrelated site is not matched (guards against a match-all subquery).
        other = Site.objects.create(name='Poly MO Other', slug='poly-mo-other')
        self.assertIsNone(_count_linked_custom_objects(other))

    def test_rows_and_badge_are_restricted_per_user(self):
        from django.contrib.auth import get_user_model
        from django.test import RequestFactory
        from netbox.context import current_request

        site = Site.objects.create(name='Perm Site', slug='perm-site')
        self._object_field_cot('perm_cot', 'perm-cot').get_model().objects.create(name='co-1', site=site)
        limited = get_user_model().objects.create_user(username='limited-rows', password='x')

        # Rows: a non-superuser with no object permissions sees none.
        self.assertEqual(_get_linked_custom_objects(site, user=limited), [])

        # Badge: with no request context it falls back to an unrestricted count...
        self.assertEqual(_count_linked_custom_objects(site), 1)

        # ...and with the limited user on the request, the badge restricts to the
        # same (empty) result, returning None so hide_if_empty hides the tab.
        req = RequestFactory().get('/')
        req.user = limited
        token = current_request.set(req)
        try:
            self.assertIsNone(_count_linked_custom_objects(site))
        finally:
            current_request.reset(token)

    def test_badge_sums_across_multiple_cots(self):
        site = Site.objects.create(name='Multi COT Site', slug='multi-cot-site')
        self._object_field_cot('badge_multi_1', 'badge-multi-1').get_model().objects.create(name='a', site=site)
        self._object_field_cot('badge_multi_2', 'badge-multi-2').get_model().objects.create(name='b', site=site)
        self.assertEqual(_count_linked_custom_objects(site), 2)

    def test_batch_multiobject_values_are_correct_and_dont_scale_per_row(self):
        # Locks in the Value-column batching fix: resolving a non-polymorphic
        # MULTIOBJECT column must cost a constant number of queries (one prefetch
        # per (model, field) group), not one query per row.
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        from netbox_custom_objects.related_tabs.views.combined import _batch_multiobject_values

        target_a = Site.objects.create(name='MO Target A', slug='mo-target-a')
        target_b = Site.objects.create(name='MO Target B', slug='mo-target-b')

        cot = self.create_custom_object_type(name='mo_batch', slug='mo-batch')
        self.create_custom_object_type_field(cot, name='name', label='Name', type='text', primary=True)
        field = self.create_custom_object_type_field(
            cot,
            name='sites',
            label='Sites',
            type=CustomFieldTypeChoices.TYPE_MULTIOBJECT,
            is_polymorphic=False,
            related_object_type=ObjectType.objects.get_for_model(Site),
        )
        model = cot.get_model()
        for i in range(6):
            obj = model.objects.create(name=f'mo-row-{i}')
            obj.sites.set([target_a, target_b])

        # The same field object is shared across a model's rows (mirrors
        # _iter_linked_fields), so id(field) keys the whole group. Build both pair
        # lists up front (fresh instances => no prefetch-cache carryover) so only
        # the _batch_multiobject_values work — not the row fetch — is measured.
        def fresh_pairs(n):
            return [(obj, field) for obj in model.objects.order_by('pk')[:n]]

        small_pairs = fresh_pairs(2)
        large_pairs = fresh_pairs(6)

        with CaptureQueriesContext(connection) as few:
            _batch_multiobject_values(small_pairs, user=None)
        with CaptureQueriesContext(connection) as many:
            resolved = _batch_multiobject_values(large_pairs, user=None)

        self.assertEqual(
            len(few.captured_queries),
            len(many.captured_queries),
            'multiobject Value resolution must not issue a query per row',
        )

        # ...and the batched values are still correct.
        for obj, _field in large_pairs:
            self.assertEqual(
                {s.pk for s in resolved[(id(obj), id(field))]},
                {target_a.pk, target_b.pk},
            )
