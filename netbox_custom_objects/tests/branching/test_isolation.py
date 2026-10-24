"""
Changes in a branch, and deleting it, leave main alone.
"""
import unittest

from core.models import ObjectType
from dcim.models import Site
from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.db import connection as main_conn

from netbox_custom_objects.models import (
    CustomObjectType,
    CustomObjectTypeField,
)

from .base import (
    HAS_BRANCHING,
    _make_request,
    _TestBase,
    _provision_branch,
    BranchingTestBase,
)

if HAS_BRANCHING:
    from netbox.context_managers import event_tracking
    from netbox_branching.models import Branch
    from netbox_branching.utilities import activate_branch


# ── Branch deletion (abandon without merge) ───────────────────────────────────

@unittest.skipUnless(HAS_BRANCHING, 'netbox-branching is not installed')
class BranchDeletionTestCase(BranchingTestBase, _TestBase):
    """
    Deleting a branch without merging must drop the branch's PostgreSQL
    schema and must NOT leak any of the branch's COT / field / table state
    into main.

    The branch deletion path (``Branch.delete()`` → ``deprovision()`` →
    ``DROP SCHEMA ... CASCADE``) bypasses the merge/revert ObjectChange
    replay engine.  We exercise it here so that the abandon flow stays
    correct even though it doesn't go through the same code as merge.
    """

    def test_branch_delete_without_merge_does_not_leak_to_main(self):
        site_ot = ObjectType.objects.get(app_label='dcim', model='site')

        with event_tracking(self.request):
            site = Site.objects.create(name='Abandon Site', slug='abandon-site')

        branch = _provision_branch('Abandon Branch', 'iterative', self.user)
        schema_name = branch.schema_name
        branch_request = _make_request(self.user)

        # ── branch: create COT + fields + CO that exist ONLY in the branch ─
        branch_cot_pk = None
        branch_field_pk = None
        branch_co_pk = None
        with activate_branch(branch), event_tracking(branch_request):
            cot = CustomObjectType.objects.create(name='abandon_cot', slug='abandon-cot')
            field = CustomObjectTypeField.objects.create(
                custom_object_type=cot,
                name='label',
                label='Label',
                type='text',
            )
            multi_field = CustomObjectTypeField.objects.create(
                custom_object_type=cot, name='multi', label='Multi',
                type='multiobject', related_object_type=site_ot,
            )
            co = cot.get_model().objects.create(label='only in branch')
            co.multi.set([site])
            branch_cot_pk = cot.pk
            branch_field_pk = field.pk
            branch_co_pk = co.pk
            branch_multi_through = multi_field.through_table_name
            branch_co_table = cot.get_database_table_name()

        # The branch's schema must exist before we delete it.
        with main_conn.cursor() as cursor:
            cursor.execute(
                'SELECT 1 FROM information_schema.schemata WHERE schema_name = %s',
                [schema_name],
            )
            self.assertTrue(cursor.fetchone(), f'Branch schema {schema_name!r} must exist before delete')

        # Main must NOT have any of the branch-only state.
        self.assertFalse(
            CustomObjectType.objects.filter(pk=branch_cot_pk).exists(),
            'COT created in branch must not be visible in main',
        )
        self.assertFalse(
            CustomObjectTypeField.objects.filter(pk=branch_field_pk).exists(),
            'Field created in branch must not be visible in main',
        )
        main_tables_pre = main_conn.introspection.table_names()
        self.assertNotIn(branch_co_table, main_tables_pre)
        self.assertNotIn(branch_multi_through, main_tables_pre)

        # ── delete (abandon) the branch ───────────────────────────────────
        branch.delete()

        # Schema must be gone.
        with main_conn.cursor() as cursor:
            cursor.execute(
                'SELECT 1 FROM information_schema.schemata WHERE schema_name = %s',
                [schema_name],
            )
            self.assertIsNone(
                cursor.fetchone(),
                f'Branch schema {schema_name!r} must be dropped after Branch.delete()',
            )

        # Main is still clean — no branch-only state was promoted.
        self.assertFalse(
            CustomObjectType.objects.filter(pk=branch_cot_pk).exists(),
            'Abandoned-branch COT must not appear in main',
        )
        self.assertFalse(
            CustomObjectTypeField.objects.filter(pk=branch_field_pk).exists(),
            'Abandoned-branch field must not appear in main',
        )
        main_tables_post = main_conn.introspection.table_names()
        self.assertNotIn(
            branch_co_table, main_tables_post,
            'Branch-only CO table must not appear in main after delete',
        )
        self.assertNotIn(
            branch_multi_through, main_tables_post,
            'Branch-only through-table must not appear in main after delete',
        )

        # The Branch row itself must be gone.
        self.assertFalse(
            Branch.objects.filter(pk=branch.pk).exists(),
            'Branch row must be deleted',
        )

        # branch_co_pk is asserted unused but referenced for clarity.
        self.assertIsNotNone(branch_co_pk)


@unittest.skipUnless(HAS_BRANCHING, 'netbox-branching is not installed')
class BranchContextDeletionTestCase(BranchingTestBase, _TestBase):
    """
    Deleting custom objects keeps working in main and in a branch after the same
    process has used both.

    Each context generates its own model classes, and Django relates them all by
    model label, so the deletion collector sees relations whose target is the
    other context's class.  Covers object, multi-object and polymorphic
    multi-object fields, deleted from both the source and the target side.
    """

    def test_delete_in_main_and_branch_after_switching_context(self):
        site_ot = ObjectType.objects.get(app_label='dcim', model='site')
        target_cot = CustomObjectType.objects.create(name='ctx_target', slug='ctx-target')
        CustomObjectTypeField.objects.create(
            custom_object_type=target_cot, name='label', label='Label', type='text',
        )
        source_cot = CustomObjectType.objects.create(name='ctx_source', slug='ctx-source')
        CustomObjectTypeField.objects.create(
            custom_object_type=source_cot, name='label', label='Label', type='text',
        )
        for name, field_type in (('ref', 'object'), ('refs', 'multiobject')):
            CustomObjectTypeField.objects.create(
                custom_object_type=source_cot, name=name, label=name, type=field_type,
                related_object_type=target_cot.object_type,
            )
        poly = CustomObjectTypeField.objects.create(
            custom_object_type=source_cot, name='poly', label='poly', type='multiobject',
            is_polymorphic=True,
        )
        poly.related_object_types.set([target_cot.object_type, site_ot])

        def models():
            source_cot.refresh_from_db()
            target_cot.refresh_from_db()
            return source_cot.get_model(), target_cot.get_model()

        source_model, target_model = models()
        targets = [target_model.objects.create(label=f'target {i}') for i in range(4)]
        sources = []
        for i in range(4):
            source = source_model.objects.create(label=f'source {i}', ref=targets[i])
            source.refs.set(targets)
            source.poly.set(targets)
            sources.append(source)

        branch = _provision_branch('Context Branch', 'iterative', self.user)
        branch_request = _make_request(self.user)

        def delete(source_pk, target_pk):
            source_model, target_model = models()
            source_model.objects.get(pk=source_pk).delete()
            target_model.objects.get(pk=target_pk).delete()
            self.assertFalse(source_model.objects.filter(pk=source_pk).exists())
            self.assertFalse(target_model.objects.filter(pk=target_pk).exists())

        # Main first, then the branch, then main again: each switch leaves the
        # other context's classes in the shared relation graph.
        with event_tracking(self.request):
            delete(sources[0].pk, targets[0].pk)
        with activate_branch(branch), event_tracking(branch_request):
            delete(sources[1].pk, targets[1].pk)
        with event_tracking(self.request):
            delete(sources[2].pk, targets[2].pk)

        source_model, target_model = models()
        self.assertEqual(list(source_model.objects.values_list('pk', flat=True)), [sources[1].pk, sources[3].pk])
        self.assertEqual(list(target_model.objects.values_list('pk', flat=True)), [targets[1].pk, targets[3].pk])
        with activate_branch(branch):
            source_model, target_model = models()
            self.assertEqual(
                list(source_model.objects.values_list('pk', flat=True)), [sources[0].pk, sources[2].pk, sources[3].pk]
            )
            self.assertEqual(
                list(target_model.objects.values_list('pk', flat=True)), [targets[0].pk, targets[2].pk, targets[3].pk]
            )


@unittest.skipUnless(HAS_BRANCHING, 'netbox-branching is not installed')
class InboundRelationBranchIsolationTestCase(BranchingTestBase, _TestBase):
    """Regenerating a type's model in a branch must not repoint main's cached
    relations from other types to the branch's class."""

    def test_branch_regeneration_leaves_main_relations_alone(self):
        with event_tracking(self.request):
            bar = CustomObjectType.objects.create(name='bar', slug='bars')
            CustomObjectTypeField.objects.create(custom_object_type=bar, name='name', label='Name', type='text')
            foo = CustomObjectType.objects.create(name='foo', slug='foos')
            for name, field_type in (('bar', 'object'), ('bars', 'multiobject')):
                CustomObjectTypeField.objects.create(
                    custom_object_type=foo, name=name, label=name, type=field_type,
                    related_object_type=bar.object_type,
                )

        bar_model = CustomObjectType.objects.get(pk=bar.pk).get_model()
        foo_model = CustomObjectType.objects.get(pk=foo.pk).get_model()
        bar_obj = bar_model.objects.create(name='x')
        foo_obj = foo_model.objects.create(bar=bar_obj)
        foo_obj.bars.set([bar_obj])

        branch = _provision_branch('Inbound Relation Branch', user=self.user)
        with activate_branch(branch):
            branch_foo_model = CustomObjectType.objects.get(pk=foo.pk).get_model()
            branch_bar_model = CustomObjectType.objects.get(pk=bar.pk).get_model(no_cache=True)
            self.assertIs(branch_foo_model._meta.get_field('bars').related_model, branch_bar_model)
            self.assertIs(branch_foo_model._meta.get_field('bar').related_model, branch_bar_model)

        self.assertIsNot(branch_bar_model, bar_model)
        bars_field = foo_model._meta.get_field('bars')
        self.assertIs(bars_field.related_model, bar_model)
        self.assertIs(bars_field.remote_field.through._meta.get_field('target').related_model, bar_model)
        self.assertIs(foo_model._meta.get_field('bar').related_model, bar_model)
        self.assertEqual(foo_model.objects.filter(bars=bar_obj).count(), 1)
        self.assertEqual(foo_model.objects.filter(bar=bar_obj).count(), 1)


@unittest.skipUnless(HAS_BRANCHING, 'netbox-branching is not installed')
class BranchCOTDeletionIsolationTestCase(BranchingTestBase, _TestBase):
    """Deleting a type in a branch must leave main's registered model, throughs
    and cache entry alone; main still has the type until the branch merges."""

    def test_branch_delete_leaves_main_model_registered(self):
        with event_tracking(self.request):
            cot = CustomObjectType.objects.create(name='doomed', slug='doomed')
            CustomObjectTypeField.objects.create(custom_object_type=cot, name='name', label='Name', type='text')
            CustomObjectTypeField.objects.create(
                custom_object_type=cot, name='sites', label='Sites', type='multiobject',
                related_object_type=ObjectType.objects.get(app_label='dcim', model='site'),
            )

        main_model = CustomObjectType.objects.get(pk=cot.pk).get_model()
        site = Site.objects.create(name='Kept Site', slug='kept-site')
        main_model.objects.create(name='kept').sites.add(site)
        main_through = main_model._meta.get_field('sites').remote_field.through
        model_key = main_model.__name__.lower()
        through_key = main_through.__name__.lower()

        branch = _provision_branch('COT Delete Branch', user=self.user)
        with activate_branch(branch), event_tracking(_make_request(self.user)):
            CustomObjectType.objects.get(pk=cot.pk).delete()
            self.assertFalse(CustomObjectType.objects.filter(pk=cot.pk).exists())

        # The branch's own entries are gone; only main's survive.
        self.assertFalse(CustomObjectType.is_model_cached(cot.pk, branch.pk))
        self.assertIsNone(CustomObjectType.get_cached_through_model(cot.pk, main_through.__name__, branch.pk))

        registered = apps.all_models['netbox_custom_objects']
        self.assertIs(registered.get(model_key), main_model)
        self.assertIs(registered.get(through_key), main_through)
        self.assertIs(CustomObjectType.get_cached_model(cot.pk), main_model)
        self.assertIs(CustomObjectType.get_cached_through_model(cot.pk, main_through.__name__), main_through)
        self.assertIs(CustomObjectType.objects.get(pk=cot.pk).get_model(), main_model)
        self.assertEqual(list(main_model.objects.values_list('name', flat=True)), ['kept'])
        # Main's through table and its rows are intact.
        self.assertEqual(list(main_model.objects.get(name='kept').sites.all()), [site])
        self.assertEqual(main_through.objects.count(), 1)

    def test_branch_delete_clears_branch_cache_entry(self):
        # No fields, so no cascaded field pre_delete clears the branch entry;
        # only delete()'s own final clear can.
        with event_tracking(self.request):
            cot = CustomObjectType.objects.create(name='bare', slug='bare')
        main_model = CustomObjectType.objects.get(pk=cot.pk).get_model()

        branch = _provision_branch('COT Bare Delete Branch', user=self.user)
        with activate_branch(branch), event_tracking(_make_request(self.user)):
            CustomObjectType.objects.get(pk=cot.pk).delete()

        self.assertFalse(CustomObjectType.is_model_cached(cot.pk, branch.pk))
        self.assertIs(CustomObjectType.get_cached_model(cot.pk), main_model)

    def test_branch_delete_of_multiobject_target_leaves_main_through(self):
        # Deleting B in a branch cascades to A's multiobject field; main still has
        # that field, so main's through must stay registered.
        with event_tracking(self.request):
            target = CustomObjectType.objects.create(name='target', slug='target')
            CustomObjectTypeField.objects.create(
                custom_object_type=target, name='name', label='Name', type='text',
            )
            source = CustomObjectType.objects.create(name='source', slug='source')
            CustomObjectTypeField.objects.create(
                custom_object_type=source, name='name', label='Name', type='text',
            )
            CustomObjectTypeField.objects.create(
                custom_object_type=source, name='targets', label='Targets', type='multiobject',
                related_object_type=target.object_type,
            )

        target_model = CustomObjectType.objects.get(pk=target.pk).get_model()
        source_model = CustomObjectType.objects.get(pk=source.pk).get_model()
        main_through = source_model._meta.get_field('targets').remote_field.through
        through_key = main_through.__name__.lower()
        linked = target_model.objects.create(name='linked')
        source_model.objects.create(name='src').targets.add(linked)

        branch = _provision_branch('Inbound M2M Delete Branch', user=self.user)
        with activate_branch(branch), event_tracking(_make_request(self.user)):
            CustomObjectType.objects.get(pk=target.pk).delete()
            self.assertFalse(CustomObjectTypeField.objects.filter(custom_object_type=source, name='targets').exists())

        registered = apps.all_models['netbox_custom_objects']
        registered = apps.all_models['netbox_custom_objects']
        self.assertIs(registered.get(through_key), main_through)
        self.assertIs(CustomObjectType.get_cached_through_model(source.pk, main_through.__name__), main_through)
        source_model = CustomObjectType.objects.get(pk=source.pk).get_model()
        self.assertEqual(
            list(source_model.objects.get(name='src').targets.values_list('name', flat=True)), ['linked'],
        )

    def test_branch_only_self_referencing_delete_unregisters_replacement(self):
        # Deleting the self-referencing field mid-delete regenerates the model,
        # registering a replacement class that delete()'s own class isn't.
        with event_tracking(self.request):
            keeper = CustomObjectType.objects.create(name='keeper', slug='keeper')
            CustomObjectTypeField.objects.create(
                custom_object_type=keeper, name='name', label='Name', type='text',
            )
        keeper_model = CustomObjectType.objects.get(pk=keeper.pk).get_model()
        keeper_key = keeper_model.__name__.lower()

        branch = _provision_branch('Self Ref Delete Branch', user=self.user)
        with activate_branch(branch), event_tracking(_make_request(self.user)):
            cot = CustomObjectType.objects.create(name='selfref', slug='selfref')
            CustomObjectTypeField.objects.create(
                custom_object_type=cot, name='name', label='Name', type='text',
            )
            CustomObjectTypeField.objects.create(
                custom_object_type=cot, name='parent', label='Parent', type='object',
                related_object_type=cot.object_type,
            )
            model_key = cot.get_model().__name__.lower()
            CustomObjectType.objects.get(pk=cot.pk).get_model().objects.create(name='child')

            CustomObjectType.objects.get(pk=cot.pk).delete()

        registered = apps.all_models['netbox_custom_objects']
        self.assertNotIn(model_key, registered)
        self.assertFalse(CustomObjectType.is_model_cached(cot.pk, branch.pk))
        # Main's classes for other types are untouched.
        self.assertIs(registered.get(keeper_key), keeper_model)
        self.assertIs(CustomObjectType.get_cached_model(keeper.pk), keeper_model)


class PolymorphicReverseAccessorBranchTestCase(BranchingTestBase, _TestBase):
    """Reverse accessors live on target classes shared by every branch: only main
    wires them, and they resolve the source model for the active branch (#766)."""

    def setUp(self):
        super().setUp()
        self.site_ot = ObjectType.objects.get(app_label='dcim', model='site')
        self.site_ct = ContentType.objects.get_for_model(Site)
        with event_tracking(self.request):
            self.cot = CustomObjectType.objects.create(name='holder', slug='holder')
            CustomObjectTypeField.objects.create(
                custom_object_type=self.cot, name='name', label='Name', type='text',
            )
            self.site = Site.objects.create(name='Reverse Site', slug='reverse-site')

    def _add_field(self, type_, name, related_name):
        field = CustomObjectTypeField.objects.create(
            custom_object_type=self.cot, name=name, label=name, type=type_,
            is_polymorphic=True, related_name=related_name,
        )
        field.related_object_types.set([self.site_ot])
        return field

    def _model(self):
        return CustomObjectType.objects.get(pk=self.cot.pk).get_model()

    def test_object_accessor_uses_branch_model(self):
        with event_tracking(self.request):
            self._add_field('object', 'target', 'holders_by_target')
            main_co = self._model().objects.create(
                name='main-co', target_content_type=self.site_ct, target_object_id=self.site.pk,
            )

        branch = _provision_branch('Reverse OBJ Branch', user=self.user)
        with activate_branch(branch), event_tracking(_make_request(self.user)):
            CustomObjectTypeField.objects.create(
                custom_object_type=self.cot, name='note', label='Note', type='text',
            )
            branch_model = self._model()
            branch_co = branch_model.objects.create(
                name='branch-co', note='hi', target_content_type=self.site_ct, target_object_id=self.site.pk,
            )
            result = list(self.site.holders_by_target.all())
            self.assertEqual({type(co) for co in result}, {branch_model})
            self.assertEqual({co.pk for co in result}, {main_co.pk, branch_co.pk})

        self.assertEqual(list(self.site.holders_by_target.all()), [main_co])

    def test_multiobject_accessor_uses_branch_model(self):
        # Reads only: branch writes to a polymorphic through still land in main (#839).
        with event_tracking(self.request):
            self._add_field('multiobject', 'targets', 'holders_by_targets')
            main_model = self._model()
            co = main_model.objects.create(name='main-co')
            co.targets.add(self.site)

        branch = _provision_branch('Reverse M2M Branch', user=self.user)
        with activate_branch(branch), event_tracking(_make_request(self.user)):
            CustomObjectTypeField.objects.create(
                custom_object_type=self.cot, name='note', label='Note', type='text',
            )
            branch_model = self._model()
            result = list(self.site.holders_by_targets.all())
            self.assertEqual([type(c) for c in result], [branch_model])
            self.assertEqual([c.pk for c in result], [co.pk])

        result = list(self.site.holders_by_targets.all())
        self.assertEqual([type(c) for c in result], [main_model])
        self.assertEqual([c.pk for c in result], [co.pk])

    def test_field_added_in_branch_is_not_wired(self):
        branch = _provision_branch('Reverse Add Branch', user=self.user)
        with activate_branch(branch), event_tracking(_make_request(self.user)):
            self._add_field('object', 'target', 'holders_by_target')
            self._model()
            self._add_field('multiobject', 'targets', 'holders_by_targets')
            self._model()

        self.assertFalse(hasattr(Site, 'holders_by_target'))
        self.assertFalse(hasattr(Site, 'holders_by_targets'))

    def test_branch_field_delete_keeps_main_accessor(self):
        with event_tracking(self.request):
            field = self._add_field('object', 'target', 'holders_by_target')
            co = self._model().objects.create(
                name='main-co', target_content_type=self.site_ct, target_object_id=self.site.pk,
            )

        branch = _provision_branch('Reverse Delete Branch', user=self.user)
        with activate_branch(branch), event_tracking(_make_request(self.user)):
            CustomObjectTypeField.objects.get(pk=field.pk).delete()
            # The branch has no such field any more.
            self.assertEqual(list(self.site.holders_by_target.all()), [])

        # Main still has the field until the branch merges.
        self.assertEqual(list(self.site.holders_by_target.all()), [co])

    def test_branch_multiobject_field_delete_keeps_main_accessor(self):
        with event_tracking(self.request):
            field = self._add_field('multiobject', 'targets', 'holders_by_targets')
            co = self._model().objects.create(name='main-co')
            co.targets.add(self.site)

        branch = _provision_branch('Reverse M2M Delete Branch', user=self.user)
        with activate_branch(branch), event_tracking(_make_request(self.user)):
            CustomObjectTypeField.objects.get(pk=field.pk).delete()
            # The branch has no such field, so no through to read either.
            self.assertEqual(list(self.site.holders_by_targets.all()), [])
            self.assertEqual(self.site.holders_by_targets.count(), 0)

        # Main keeps its accessor until the branch merges.  Reading it would fail:
        # the branch delete drops main's through table (#839).
        self.assertIn('holders_by_targets', Site.__dict__)
        self.assertEqual(
            CustomObjectTypeField.objects.get(pk=field.pk).related_name, 'holders_by_targets',
        )
