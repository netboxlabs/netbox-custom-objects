"""
Unit tests for the netbox-branching hooks: those in ``netbox_custom_objects.branching``
and the two signal receivers in ``netbox_custom_objects/__init__.py``.

The hooks are pure functions over model metadata, ObjectChange payloads and context
state, so they're exercised with stand-in models and collapsed changes, and no database
is required, hence ``SimpleTestCase``.  Only the tests that the receivers are connected
to netbox-branching's signals need it installed, and a database: netbox-branching's own
receivers on those signals look up event rules.
"""
from types import SimpleNamespace
from unittest import mock, skipUnless
from unittest.mock import Mock

from django.test import SimpleTestCase, TestCase

from netbox_custom_objects import _heal_branch_on_migrate, _reset_deferred_co_field_data
from netbox_custom_objects.branching import (
    _collect_co_refs,
    _constraint_field_refs,
    add_custom_object_dependencies,
    objectchange_field_migrator,
    supports_branching_resolver,
)
from netbox_custom_objects.constants import APP_LABEL
from netbox_custom_objects.models import (
    POLY_M2M_SIDECAR_KEY,
    CustomObjectTypeConstraint,
    CustomObjectTypeField,
    _deferred_co_field_data,
)
from netbox_custom_objects.utilities import is_branching_enabled

# Taken from the models, so renaming one fails these tests until branching.py's labels follow.
FIELD_LABEL = CustomObjectTypeField._meta.label_lower
CONSTRAINT_LABEL = CustomObjectTypeConstraint._meta.label_lower


def _model(app_label=APP_LABEL, model_name='table1model', m2m=(), field_objects=None, **attrs):
    """Return a stand-in model class with just the attributes the hooks read."""
    meta = SimpleNamespace(app_label=app_label, model_name=model_name, local_many_to_many=list(m2m))
    return SimpleNamespace(_meta=meta, _field_objects=field_objects, **attrs)


def _m2m(name, related_label):
    app_label, model_name = related_label.split('.')
    related = SimpleNamespace(_meta=SimpleNamespace(app_label=app_label, model_name=model_name))
    return SimpleNamespace(name=name, related_model=related)


def _change(key, action, model_class=None, pre=None, post=None):
    """Return a stand-in netbox-branching CollapsedChange."""
    return SimpleNamespace(
        key=key,
        model_class=model_class,
        final_action=SimpleNamespace(value=action) if action else None,
        prechange_data=pre,
        postchange_data=post,
        depends_on=set(),
        depended_by=set(),
    )


def _build(*changes):
    collapsed = {cc.key: cc for cc in changes}
    add_custom_object_dependencies(sender=None, collapsed_changes=collapsed, operation='merge')
    return collapsed


class SupportsBranchingResolverTestCase(SimpleTestCase):

    def test_through_model_is_branchable(self):
        model = _model(model_name='through_custom_objects_table1model_devices')
        self.assertIs(supports_branching_resolver(model), True)

    def test_other_plugin_models_are_left_to_the_default(self):
        for model in (
            _model(model_name='table1model'),
            _model(model_name='customobjecttype'),
            _model(app_label='dcim', model_name='through_custom_objects_x'),
            SimpleNamespace(),
        ):
            with self.subTest(model=model):
                self.assertIsNone(supports_branching_resolver(model))


class ObjectChangeFieldMigratorTestCase(SimpleTestCase):

    def test_delegates_to_resolve_field_aliases(self):
        resolve = Mock(return_value={'new_name': 1})
        model = _model(resolve_field_aliases=resolve)
        self.assertEqual(objectchange_field_migrator(model, {'old_name': 1}), {'new_name': 1})
        resolve.assert_called_once_with({'old_name': 1})

    def test_returns_none_for_models_it_cannot_migrate(self):
        resolve = Mock()
        for model in (
            _model(app_label='dcim', resolve_field_aliases=resolve),
            _model(model_name='customobjecttype'),
            SimpleNamespace(),
        ):
            with self.subTest(model=model):
                self.assertIsNone(objectchange_field_migrator(model, {'name': 1}))
        resolve.assert_not_called()


class CollectCORefsTestCase(SimpleTestCase):

    def test_empty_data(self):
        self.assertEqual(_collect_co_refs(_model(), None), set())
        self.assertEqual(_collect_co_refs(_model(), {}), set())

    def test_m2m_targets(self):
        model = _model(m2m=[_m2m('devices', 'dcim.device'), _m2m('peers', f'{APP_LABEL}.table1model')])
        data = {'devices': [3, 4], 'peers': [7], 'name': 'x'}
        self.assertEqual(
            _collect_co_refs(model, data),
            {('dcim.device', 3), ('dcim.device', 4), (f'{APP_LABEL}.table1model', 7)},
        )

    def test_empty_m2m_and_non_integer_values_are_skipped(self):
        model = _model(m2m=[_m2m('devices', 'dcim.device'), _m2m('sites', 'dcim.site')])
        self.assertEqual(_collect_co_refs(model, {'devices': [], 'sites': ['a', 5]}), {('dcim.site', 5)})

    def test_field_objects_add_field_refs(self):
        model = _model(field_objects={
            1: {'field': SimpleNamespace(pk=11)},
            2: {'field': SimpleNamespace(pk=None)},
            3: {'field': None},
            4: 'not-a-dict',
        })
        self.assertEqual(_collect_co_refs(model, {'name': 'x'}), {(FIELD_LABEL, 11)})

    def test_poly_m2m_sidecar_adds_field_refs(self):
        data = {POLY_M2M_SIDECAR_KEY: [{'pk': 21}, {'pk': None}, 'junk'], 'name': 'x'}
        self.assertEqual(_collect_co_refs(_model(), data), {(FIELD_LABEL, 21)})

    def test_unregistered_model_falls_back_to_integer_lists(self):
        label = f'{APP_LABEL}.table1model'
        data = {
            'peers': [5, 6],
            'mixed': [1, 'a'],
            'empty': [],
            'tags': [9],
            POLY_M2M_SIDECAR_KEY: [{'pk': 21}],
            'count': 3,
        }
        self.assertEqual(
            _collect_co_refs(None, data, model_label=label),
            {(label, 5), (label, 6), (FIELD_LABEL, 21)},
        )

    def test_fallback_skips_declared_m2m_fields(self):
        label = f'{APP_LABEL}.table1model'
        model = _model(m2m=[_m2m('devices', 'dcim.device')])
        self.assertEqual(_collect_co_refs(model, {'devices': [3]}, model_label=label), {('dcim.device', 3)})

    def test_fallback_needs_a_plugin_label(self):
        self.assertEqual(_collect_co_refs(None, {'peers': [5]}, model_label='dcim.device'), set())
        self.assertEqual(_collect_co_refs(None, {'peers': [5]}), set())


class ConstraintFieldRefsTestCase(SimpleTestCase):

    def setUp(self):
        self.fields = {
            (1, 10): (FIELD_LABEL, 100),
            (1, 11): (FIELD_LABEL, 101),
            (1, 12): (FIELD_LABEL, 102),
            (1, 13): (FIELD_LABEL, 103),
            (2, 10): (FIELD_LABEL, 200),
        }

    def test_empty_data(self):
        self.assertEqual(_constraint_field_refs(None, self.fields), set())
        self.assertEqual(_constraint_field_refs({}, self.fields), set())

    def test_members_condition_and_rule_fields(self):
        data = {
            'custom_object_type': 1,
            'field_schema_ids': [10],
            'condition': [{'field': 11, 'lookup': 'exact', 'value': 'x'}],
            'rule': [{'field': 12, 'lookup': 'gte', 'value': {'field': 13}}],
        }
        self.assertEqual(
            _constraint_field_refs(data, self.fields),
            {(FIELD_LABEL, 100), (FIELD_LABEL, 101), (FIELD_LABEL, 102), (FIELD_LABEL, 103)},
        )

    def test_matches_on_the_constraints_own_type(self):
        data = {'custom_object_type': 2, 'field_schema_ids': [10, 11]}
        self.assertEqual(_constraint_field_refs(data, self.fields), {(FIELD_LABEL, 200)})

    def test_fields_not_in_the_squash_are_ignored(self):
        data = {'custom_object_type': 1, 'field_schema_ids': [99], 'condition': None, 'rule': ['junk']}
        self.assertEqual(_constraint_field_refs(data, self.fields), set())


class AddCustomObjectDependenciesTestCase(SimpleTestCase):

    def setUp(self):
        self.co_label = f'{APP_LABEL}.table1model'
        self.model = _model(m2m=[_m2m('peers', self.co_label), _m2m('devices', 'dcim.device')])

    def assertGraph(self, collapsed, *edges):
        """Assert the graph's edges are exactly *edges*, each a ``(before, after)`` pair of changes."""
        expected = {(before.key, after.key) for before, after in edges}
        changes = collapsed.values()
        self.assertEqual({(dep, cc.key) for cc in changes for dep in cc.depends_on}, expected)
        self.assertEqual({(cc.key, dep) for cc in changes for dep in cc.depended_by}, expected)

    def test_create_depends_on_created_m2m_target(self):
        target = _change((self.co_label, 2), 'create', self.model, post={'peers': []})
        source = _change((self.co_label, 1), 'create', self.model, post={'peers': [2]})
        self.assertGraph(_build(target, source), (target, source))

    def test_create_ignores_self_reference(self):
        cc = _change((self.co_label, 1), 'create', self.model, post={'peers': [1]})
        self.assertGraph(_build(cc))

    def test_create_depends_on_field_create(self):
        field = _change((FIELD_LABEL, 11), 'create', post={'schema_id': 1, 'custom_object_type': 1})
        model = _model(field_objects={1: {'field': SimpleNamespace(pk=11)}})
        cc = _change((self.co_label, 1), 'create', model, post={'name': 'x'})
        self.assertGraph(_build(field, cc), (field, cc))

    def test_update_depends_on_created_target(self):
        target = _change((self.co_label, 2), 'create', self.model, post={'peers': []})
        cc = _change((self.co_label, 1), 'update', self.model, pre={'peers': []}, post={'peers': [2]})
        self.assertGraph(_build(target, cc), (target, cc))

    def test_update_precedes_deleting_old_target(self):
        target = _change((self.co_label, 2), 'delete', self.model, pre={'peers': []})
        cc = _change((self.co_label, 1), 'update', self.model, pre={'peers': [2]}, post={'peers': []})
        self.assertGraph(_build(target, cc), (cc, target))

    def test_delete_precedes_deleting_target(self):
        target = _change((self.co_label, 2), 'delete', self.model, pre={'peers': []})
        cc = _change((self.co_label, 1), 'delete', self.model, pre={'peers': [2]})
        self.assertGraph(_build(target, cc), (cc, target))

    def test_delete_ignores_self_reference(self):
        cc = _change((self.co_label, 1), 'delete', self.model, pre={'peers': [1]})
        self.assertGraph(_build(cc))

    def test_unregistered_model_uses_its_key_label(self):
        target = _change((self.co_label, 2), 'create', None, post={'peers': []})
        source = _change((self.co_label, 1), 'create', None, post={'peers': [2]})
        self.assertGraph(_build(target, source), (target, source))

    def test_constraint_create_depends_on_field_creates(self):
        member = _change((FIELD_LABEL, 100), 'create', post={'schema_id': 10, 'custom_object_type': 1})
        compared = _change((FIELD_LABEL, 101), 'create', post={'schema_id': 11, 'custom_object_type': 1})
        other_type = _change((FIELD_LABEL, 200), 'create', post={'schema_id': 10, 'custom_object_type': 2})
        constraint = _change((CONSTRAINT_LABEL, 1), 'create', post={
            'custom_object_type': 1,
            'field_schema_ids': [10],
            'rule': [{'field': 10, 'lookup': 'gte', 'value': {'field': 11}}],
        })
        self.assertGraph(
            _build(member, compared, other_type, constraint), (member, constraint), (compared, constraint)
        )

    def test_constraint_delete_precedes_field_delete(self):
        field = _change((FIELD_LABEL, 100), 'delete', pre={'schema_id': 10, 'custom_object_type': 1})
        constraint = _change(
            (CONSTRAINT_LABEL, 1), 'delete', pre={'custom_object_type': 1, 'field_schema_ids': [10]}
        )
        self.assertGraph(_build(field, constraint), (constraint, field))

    def test_constraint_schema_ids_are_not_read_as_self_references(self):
        # The generic walker would read field_schema_ids [1] as a reference to constraint 1.
        other = _change((CONSTRAINT_LABEL, 1), 'create', post={'custom_object_type': 1, 'field_schema_ids': []})
        constraint = _change((CONSTRAINT_LABEL, 2), 'create', post={'custom_object_type': 1, 'field_schema_ids': [1]})
        self.assertGraph(_build(other, constraint))

    def test_other_apps_and_missing_actions_are_skipped(self):
        device = _change(('dcim.device', 3), 'create', _model(app_label='dcim', model_name='device'))
        plain = _change(('dcim.site', 1), 'create', None, post={'peers': [3]})
        unresolved = _change((self.co_label, 1), None, self.model, post={'devices': [3]})
        self.assertGraph(_build(device, plain, unresolved))


class BranchingReceiversTestCase(SimpleTestCase):
    """The receivers ``__init__.py`` connects to netbox-branching's signals."""

    def setUp(self):
        self.addCleanup(_deferred_co_field_data.set, None)
        self.branch = SimpleNamespace(pk=1)

    def test_reset_clears_deferred_field_data(self):
        _deferred_co_field_data.set({"table1model": {1: {"data": {"code": "stale"}}}})
        _reset_deferred_co_field_data(sender=None)
        self.assertIsNone(_deferred_co_field_data.get())

    def test_heal_on_migrate_heals_the_branch(self):
        with mock.patch("netbox_custom_objects.mixin_migration.heal_branch") as heal_branch:
            _heal_branch_on_migrate(sender=None, branch=self.branch, verbosity=0)
        heal_branch.assert_called_once_with(self.branch, verbosity=0)

    def test_heal_on_migrate_logs_failures(self):
        with mock.patch("netbox_custom_objects.mixin_migration.heal_branch", side_effect=RuntimeError("boom")):
            with self.assertLogs("netbox_custom_objects", level="ERROR") as logs:
                _heal_branch_on_migrate(sender=None, branch=self.branch)
        self.assertIn("healing branch 1 after migrate", logs.output[0])


@skipUnless(is_branching_enabled(), "netbox-branching is not installed")
class BranchingSignalConnectionTestCase(TestCase):
    """The receivers run when netbox-branching sends its signals."""

    def setUp(self):
        self.addCleanup(_deferred_co_field_data.set, None)
        self.branch = SimpleNamespace(pk=1)

    def test_merge_sync_and_revert_clear_deferred_field_data(self):
        from netbox_branching import signals

        for name in ("pre_merge", "post_merge", "pre_sync", "post_sync", "pre_revert", "post_revert"):
            with self.subTest(signal=name):
                _deferred_co_field_data.set({"table1model": {1: {"data": {"code": "stale"}}}})
                getattr(signals, name).send(sender=None, branch=self.branch, user=None)
                self.assertIsNone(_deferred_co_field_data.get())

    def test_migrate_heals_the_branch(self):
        from netbox_branching.signals import post_migrate

        with mock.patch("netbox_custom_objects.mixin_migration.heal_branch") as heal_branch:
            post_migrate.send(sender=None, branch=self.branch, user=None)
        heal_branch.assert_called_once_with(self.branch, verbosity=1)

    def test_squash_graph_gets_custom_object_edges(self):
        from netbox_branching.signals import squash_dependency_graph_built

        label = f"{APP_LABEL}.table1model"
        model = _model(m2m=[_m2m("peers", label)])
        target = _change((label, 2), "create", model, post={"peers": []})
        source = _change((label, 1), "create", model, post={"peers": [2]})
        squash_dependency_graph_built.send(
            sender=None, collapsed_changes={cc.key: cc for cc in (target, source)}, operation="merge",
        )
        self.assertEqual(source.depends_on, {target.key})
