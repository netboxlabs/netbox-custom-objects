"""
Merge and revert of custom object types, fields and objects made in a branch, under
both merge strategies, and of the field types the strategy matrix leaves out.
"""
import datetime
import decimal
import unittest

from core.models import ObjectType
from dcim.models import Site
from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError, connection as main_conn, transaction
from extras.models import CustomFieldChoiceSet

from netbox_custom_objects.choices import ObjectFieldOnDeleteChoices
from netbox_custom_objects.models import (
    USER_TABLE_DATABASE_NAME_PREFIX,
    CustomObjectType,
    CustomObjectTypeConstraint,
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
    from netbox_branching.choices import BranchStatusChoices
    from netbox_branching.utilities import activate_branch


# ── Shared merge/revert tests (strategy-agnostic) ────────────────────────────

@unittest.skipUnless(HAS_BRANCHING, 'netbox-branching is not installed')
class BaseBranchingTests(BranchingTestBase):
    """
    Merge and revert tests that run against every merge strategy.

    Subclasses must:
    - set ``MERGE_STRATEGY`` to an iterative or squash strategy string
    - also inherit from ``TransactionTestCase``

    Example::

        class IterativeBranchingTestCase(BaseBranchingTests, TransactionTestCase):
            MERGE_STRATEGY = 'iterative'
    """

    MERGE_STRATEGY = None

    # ── simple: one COT, one text field, one CO ───────────────────────────

    def test_simple_merge_and_revert(self):
        """
        Create a COT with a single text field and one custom object instance
        inside a branch.  Merge to main, then revert.

        Assertions
        ----------
        Before merge
            - COT is absent from main
            - field is absent from main

        After merge
            - COT is present in main
            - field is present in main
            - get() on the CO in main returns the correct field value

        After revert
            - COT is absent from main
            - field is absent from main
        """
        branch = _provision_branch('Simple Branch', self.MERGE_STRATEGY, self.user)
        request = _make_request(self.user)

        # ── create inside branch ──────────────────────────────────────────
        with activate_branch(branch), event_tracking(request):
            cot = CustomObjectType.objects.create(name='simple_cot', slug='simple-cot')
            field = CustomObjectTypeField.objects.create(
                custom_object_type=cot,
                name='notes',
                label='Notes',
                type='text',
            )
            Model = cot.get_model()
            co = Model.objects.create(notes='hello from branch')

        cot_pk, field_pk, co_pk = cot.pk, field.pk, co.pk

        # ── before merge: nothing in main ─────────────────────────────────
        self.assertFalse(
            CustomObjectType.objects.filter(pk=cot_pk).exists(),
            'COT must not be visible in main before merge',
        )
        self.assertFalse(
            CustomObjectTypeField.objects.filter(pk=field_pk).exists(),
            'Field must not be visible in main before merge',
        )

        # ── merge ─────────────────────────────────────────────────────────
        branch.merge(user=self.user, commit=True)
        branch.refresh_from_db()
        self.assertEqual(branch.status, BranchStatusChoices.MERGED)

        # ── after merge: present in main ──────────────────────────────────
        self.assertTrue(
            CustomObjectType.objects.filter(pk=cot_pk).exists(),
            'COT must be in main after merge',
        )
        self.assertTrue(
            CustomObjectTypeField.objects.filter(pk=field_pk).exists(),
            'Field must be in main after merge',
        )

        # get() the CO and verify its value
        cot_main = CustomObjectType.objects.get(pk=cot_pk)
        co_main = cot_main.get_model().objects.get(pk=co_pk)
        self.assertEqual(co_main.notes, 'hello from branch')

        # ── revert ────────────────────────────────────────────────────────
        branch.revert(user=self.user, commit=True)
        branch.refresh_from_db()

        # ── after revert: gone from main ──────────────────────────────────
        self.assertFalse(
            CustomObjectType.objects.filter(pk=cot_pk).exists(),
            'COT must not be in main after revert',
        )
        self.assertFalse(
            CustomObjectTypeField.objects.filter(pk=field_pk).exists(),
            'Field must not be in main after revert',
        )

    # ── comprehensive: one of every field type ────────────────────────────

    def test_comprehensive_merge_and_revert(self):
        """
        Create a COT with one field of each supported type, plus a CO instance,
        inside a branch.  Merge to main, then revert.

        Field types
        -----------
        text        — plain VARCHAR column
        integer     — INTEGER column
        decimal     — DECIMAL column (exercises numeric precision handling)
        boolean     — BOOLEAN column
        datetime    — TIMESTAMPTZ column (exercises timezone-aware handling)
        select      — VARCHAR column with a ChoiceSet
        object      — ForeignKey column (to dcim.Site)
        multiobject — M2M through-table (to dcim.Site)
        url         — two backing columns (<name>, <name>_title); the CO is
                      created in the same atomic block as the field, so under
                      squash the CO's CREATE can replay before the field's own
                      CREATE — exercising _apply_deferred_co_field's replay of
                      both the URL and title values (title only reverts to its
                      default empty string, not the URL, if the replay ever
                      drops it)

        This exercises every distinct schema-editor operation
        (add_field, add_FK, create_through_table) across a merge cycle and
        verifies that all field values survive the round-trip.

        Assertions mirror test_simple_merge_and_revert but for every field type.
        """
        # The Site is created in main before provisioning so it exists in both
        # main and the branch schema and is valid as an FK target during merge.
        with event_tracking(self.request):
            site = Site.objects.create(name='Reference Site', slug='reference-site')

        branch = _provision_branch('Comprehensive Branch', self.MERGE_STRATEGY, self.user)
        request = _make_request(self.user)

        site_ot = ObjectType.objects.get(app_label='dcim', model='site')
        cot_pk = None
        field_pks = {}
        co_pk = None

        test_dt = datetime.datetime(2024, 6, 15, 12, 0, 0, tzinfo=datetime.timezone.utc)
        test_decimal = decimal.Decimal('3.14')

        # ── create inside branch ──────────────────────────────────────────
        with activate_branch(branch), event_tracking(request):
            choice_set = CustomFieldChoiceSet.objects.create(
                name='Statuses',
                extra_choices=[['active', 'Active'], ['inactive', 'Inactive']],
            )
            cot = CustomObjectType.objects.create(name='full_cot', slug='full-cot')
            cot_pk = cot.pk

            field_specs = [
                ('text_field', {'type': 'text'}),
                ('int_field', {'type': 'integer'}),
                ('dec_field', {'type': 'decimal'}),
                ('bool_field', {'type': 'boolean'}),
                ('dt_field', {'type': 'datetime'}),
                ('select_field', {'type': 'select', 'choice_set': choice_set}),
                ('obj_field', {'type': 'object', 'related_object_type': site_ot}),
                ('multi_field', {'type': 'multiobject', 'related_object_type': site_ot}),
                ('url_field', {'type': 'url'}),
            ]
            for name, kwargs in field_specs:
                f = CustomObjectTypeField.objects.create(
                    custom_object_type=cot,
                    name=name,
                    label=name.replace('_', ' ').title(),
                    **kwargs,
                )
                field_pks[name] = f.pk

            Model = cot.get_model()
            co = Model.objects.create(
                text_field='hello',
                int_field=42,
                dec_field=test_decimal,
                bool_field=True,
                dt_field=test_dt,
                select_field='active',
                obj_field_id=site.pk,
                # multi_field left empty — through-table creation is still exercised
                url_field='https://example.com/path',
                url_field_title='Example Path',
            )
            co_pk = co.pk

        # ── before merge: nothing in main ─────────────────────────────────
        self.assertFalse(
            CustomObjectType.objects.filter(pk=cot_pk).exists(),
            'COT must not be in main before merge',
        )
        for name, pk in field_pks.items():
            self.assertFalse(
                CustomObjectTypeField.objects.filter(pk=pk).exists(),
                f'Field {name!r} must not be in main before merge',
            )

        # ── merge ─────────────────────────────────────────────────────────
        branch.merge(user=self.user, commit=True)
        branch.refresh_from_db()
        self.assertEqual(branch.status, BranchStatusChoices.MERGED)

        # ── after merge: present in main, values intact ───────────────────
        self.assertTrue(
            CustomObjectType.objects.filter(pk=cot_pk).exists(),
            'COT must be in main after merge',
        )
        for name, pk in field_pks.items():
            self.assertTrue(
                CustomObjectTypeField.objects.filter(pk=pk).exists(),
                f'Field {name!r} must be in main after merge',
            )

        cot_main = CustomObjectType.objects.get(pk=cot_pk)
        co_main = cot_main.get_model().objects.get(pk=co_pk)
        self.assertEqual(co_main.text_field, 'hello')
        self.assertEqual(co_main.int_field, 42)
        self.assertEqual(co_main.dec_field, test_decimal)
        self.assertTrue(co_main.bool_field)
        self.assertEqual(co_main.dt_field, test_dt)
        self.assertEqual(co_main.select_field, 'active')
        self.assertEqual(co_main.obj_field_id, site.pk)
        self.assertEqual(co_main.url_field, 'https://example.com/path')
        self.assertEqual(
            co_main.url_field_title, 'Example Path',
            'Title value must survive replay alongside the URL value',
        )

        # Capture the multi_field's physical through-table name *while* it
        # still exists so we can confirm it's gone after revert.
        multi_field_main = CustomObjectTypeField.objects.get(pk=field_pks['multi_field'])
        through_table = multi_field_main.through_table_name

        # ── revert ────────────────────────────────────────────────────────
        branch.revert(user=self.user, commit=True)
        branch.refresh_from_db()

        # ── after revert: gone from main ──────────────────────────────────
        self.assertFalse(
            CustomObjectType.objects.filter(pk=cot_pk).exists(),
            'COT must not be in main after revert',
        )
        for name, pk in field_pks.items():
            self.assertFalse(
                CustomObjectTypeField.objects.filter(pk=pk).exists(),
                f'Field {name!r} must not be in main after revert',
            )
        # The multi_field's through-table must also be physically dropped from
        # main's schema — ORM absence isn't enough; without this assertion an
        # orphaned through table could survive the revert and break a later
        # COT that picks up the same id.
        self.assertNotIn(
            through_table,
            main_conn.introspection.table_names(),
            f'Through-table {through_table!r} must be physically dropped after revert',
        )

    # ── object modified inside branch ─────────────────────────────────────

    def test_object_modified_merge_and_revert(self):
        """
        CO that exists in main is modified inside a branch.  Merge brings the
        new value to main; revert restores the original.

        The COT and field are created in main before provisioning so the branch
        has a full schema copy.  Only the CO data changes inside the branch.
        """
        request = _make_request(self.user)

        with event_tracking(request):
            cot = CustomObjectType.objects.create(name='modify_cot', slug='modify-cot')
            CustomObjectTypeField.objects.create(
                custom_object_type=cot,
                name='notes',
                label='Notes',
                type='text',
            )
            Model = cot.get_model()
            co = Model.objects.create(notes='original value')

        co_pk = co.pk
        branch = _provision_branch('Modify Branch', self.MERGE_STRATEGY, self.user)
        branch_request = _make_request(self.user)

        # Modify CO inside the branch.
        with activate_branch(branch), event_tracking(branch_request):
            branch_co = cot.get_model().objects.get(pk=co_pk)
            branch_co.snapshot()  # captures pre-change state for ObjectChange.diff()['pre'] during revert
            branch_co.notes = 'modified in branch'
            branch_co.save()

        # Main must not see the modification yet.
        co.refresh_from_db()
        self.assertEqual(co.notes, 'original value', 'Main must not see branch modification before merge')

        # ── merge ─────────────────────────────────────────────────────────
        branch.merge(user=self.user, commit=True)
        branch.refresh_from_db()
        self.assertEqual(branch.status, BranchStatusChoices.MERGED)

        co.refresh_from_db()
        self.assertEqual(co.notes, 'modified in branch', 'Main must see modified value after merge')

        # ── revert ────────────────────────────────────────────────────────
        branch.revert(user=self.user, commit=True)

        co.refresh_from_db()
        self.assertEqual(co.notes, 'original value', 'Main must have original value after revert')

    # ── object deleted inside branch ──────────────────────────────────────

    def test_object_deleted_merge_and_revert(self):
        """
        CO that exists in main is deleted inside a branch.  Merge removes it
        from main; revert restores it.
        """
        request = _make_request(self.user)

        with event_tracking(request):
            cot = CustomObjectType.objects.create(name='delete_cot', slug='delete-cot')
            CustomObjectTypeField.objects.create(
                custom_object_type=cot,
                name='notes',
                label='Notes',
                type='text',
            )
            Model = cot.get_model()
            co = Model.objects.create(notes='will be deleted')

        co_pk = co.pk
        branch = _provision_branch('Delete Branch', self.MERGE_STRATEGY, self.user)
        branch_request = _make_request(self.user)

        with activate_branch(branch), event_tracking(branch_request):
            cot.get_model().objects.get(pk=co_pk).delete()

        # CO must still exist in main before merge.
        self.assertTrue(
            cot.get_model().objects.filter(pk=co_pk).exists(),
            'CO must still exist in main before merge',
        )

        # ── merge ─────────────────────────────────────────────────────────
        branch.merge(user=self.user, commit=True)
        branch.refresh_from_db()
        self.assertEqual(branch.status, BranchStatusChoices.MERGED)

        self.assertFalse(
            cot.get_model().objects.filter(pk=co_pk).exists(),
            'CO must be deleted from main after merge',
        )

        # ── revert ────────────────────────────────────────────────────────
        branch.revert(user=self.user, commit=True)

        self.assertTrue(
            cot.get_model().objects.filter(pk=co_pk).exists(),
            'CO must be restored in main after revert',
        )

    # ── field renamed inside branch → merge ───────────────────────────────

    def test_field_rename_merge_and_revert(self):
        """
        Field created and then renamed inside a branch.  Merge brings the COT
        with the renamed column to main; revert removes it.

        Exercises _schema_alter_field via the merge deserialization path using
        PK-based rename detection (same PK, different name values).
        """
        branch = _provision_branch('Rename Branch', self.MERGE_STRATEGY, self.user)
        request = _make_request(self.user)

        with activate_branch(branch), event_tracking(request):
            cot = CustomObjectType.objects.create(name='rename_cot', slug='rename-cot')
            field = CustomObjectTypeField.objects.create(
                custom_object_type=cot,
                name='old_name',
                label='Old Name',
                type='text',
            )
            # Load from DB so _original is set, then rename.
            field = CustomObjectTypeField.objects.get(pk=field.pk)
            field.snapshot()  # captures pre-change state for ObjectChange.diff()['pre'] during revert
            field.name = 'new_name'
            field.label = 'New Name'
            field.save()
            Model = cot.get_model()
            co = Model.objects.create(new_name='value after rename')

        field_pk, cot_pk, co_pk = field.pk, cot.pk, co.pk

        self.assertFalse(
            CustomObjectType.objects.filter(pk=cot_pk).exists(),
            'COT must not exist in main before merge',
        )

        # ── merge ─────────────────────────────────────────────────────────
        branch.merge(user=self.user, commit=True)
        branch.refresh_from_db()
        self.assertEqual(branch.status, BranchStatusChoices.MERGED)

        self.assertTrue(CustomObjectTypeField.objects.filter(pk=field_pk).exists())
        field_main = CustomObjectTypeField.objects.get(pk=field_pk)
        self.assertEqual(field_main.name, 'new_name', 'Field must have new name in main after merge')

        cot_main = CustomObjectType.objects.get(pk=cot_pk)
        co_main = cot_main.get_model().objects.get(pk=co_pk)
        self.assertEqual(co_main.new_name, 'value after rename')

        # ── revert ────────────────────────────────────────────────────────
        branch.revert(user=self.user, commit=True)

        self.assertFalse(CustomObjectType.objects.filter(pk=cot_pk).exists())
        self.assertFalse(CustomObjectTypeField.objects.filter(pk=field_pk).exists())

    # ── unique constraint toggled inside branch → merge ───────────────────

    def test_field_unique_toggle_merge_and_revert(self):
        """
        Field created without a unique constraint inside a branch, then
        toggled to unique=True.  Merge brings the COT with the UNIQUE
        constraint to main; revert removes it.

        Exercises alter_field for constraint-only changes via the merge path.
        """
        branch = _provision_branch('Unique Branch', self.MERGE_STRATEGY, self.user)
        request = _make_request(self.user)

        with activate_branch(branch), event_tracking(request):
            cot = CustomObjectType.objects.create(name='unique_cot', slug='unique-cot')
            field = CustomObjectTypeField.objects.create(
                custom_object_type=cot,
                name='code',
                label='Code',
                type='text',
                unique=False,
            )
            # Load from DB so _original is set, then enable unique.
            field = CustomObjectTypeField.objects.get(pk=field.pk)
            field.snapshot()  # captures pre-change state for ObjectChange.diff()['pre'] during revert
            field.unique = True
            field.save()
            Model = cot.get_model()
            co_pk = Model.objects.create(code='ABC').pk

        field_pk, cot_pk = field.pk, cot.pk

        # ── merge ─────────────────────────────────────────────────────────
        branch.merge(user=self.user, commit=True)
        branch.refresh_from_db()
        self.assertEqual(branch.status, BranchStatusChoices.MERGED)

        field_main = CustomObjectTypeField.objects.get(pk=field_pk)
        self.assertTrue(field_main.unique, 'Field must have unique=True in main after merge')

        # Verify the UNIQUE constraint exists in main's physical schema.
        cot_main = CustomObjectType.objects.get(pk=cot_pk)
        table_name = cot_main.get_database_table_name()
        with main_conn.cursor() as cursor:
            constraints = main_conn.introspection.get_constraints(cursor, table_name)
        self.assertTrue(
            any(c['unique'] and c.get('columns') == ['code'] for c in constraints.values()),
            'UNIQUE constraint on "code" must exist in main schema after merge',
        )

        # CO with code='ABC' must have survived the merge.
        cot_main2 = CustomObjectType.objects.get(pk=cot_pk)
        self.assertTrue(cot_main2.get_model().objects.filter(pk=co_pk).exists())

        # ── revert ────────────────────────────────────────────────────────
        branch.revert(user=self.user, commit=True)

        self.assertFalse(CustomObjectType.objects.filter(pk=cot_pk).exists())

    # ── non-schema field attributes inside branch → merge ─────────────────

    def test_field_non_schema_attrs_merge_and_revert(self):
        """
        Field attributes that do not affect the physical schema (label,
        primary, required, description) survive a branch merge and revert
        without causing schema errors or spurious ALTER TABLE calls.

        These attributes are excluded from _field_schema_key and exist only
        at the application layer.  This test confirms that altering them in
        a branch and merging correctly updates the ORM-level field record in
        main without touching the DB column definition.
        """
        branch = _provision_branch('Attrs Branch', self.MERGE_STRATEGY, self.user)
        request = _make_request(self.user)

        with activate_branch(branch), event_tracking(request):
            cot = CustomObjectType.objects.create(name='attrs_cot', slug='attrs-cot')
            field = CustomObjectTypeField.objects.create(
                custom_object_type=cot,
                name='title',
                label='Title',
                type='text',
                primary=False,
                required=False,
                description='original description',
            )
            # Load from DB so _original is set, then mutate non-schema attrs.
            field = CustomObjectTypeField.objects.get(pk=field.pk)
            field.snapshot()  # captures pre-change state for ObjectChange.diff()['pre'] during revert
            field.label = 'Updated Title'
            field.primary = True
            field.required = True
            field.description = 'updated description'
            field.save()

        field_pk, cot_pk = field.pk, cot.pk

        # ── merge ─────────────────────────────────────────────────────────
        branch.merge(user=self.user, commit=True)
        branch.refresh_from_db()
        self.assertEqual(branch.status, BranchStatusChoices.MERGED)

        field_main = CustomObjectTypeField.objects.get(pk=field_pk)
        self.assertEqual(field_main.label, 'Updated Title')
        self.assertTrue(field_main.primary)
        self.assertTrue(field_main.required)
        self.assertEqual(field_main.description, 'updated description')

        # ── revert ────────────────────────────────────────────────────────
        branch.revert(user=self.user, commit=True)

        self.assertFalse(CustomObjectType.objects.filter(pk=cot_pk).exists())
        self.assertFalse(CustomObjectTypeField.objects.filter(pk=field_pk).exists())

    # ── existing main COT extended with new fields inside a branch ────────

    def test_extend_main_cot_with_new_fields_merge_and_revert(self):
        """
        COT exists in main with text + object + multiobject fields and a CO with all
        three filled.  A branch then *adds* new fields of each type to the same COT
        and inserts a new CO that uses both the original and the new fields.  Merge
        brings the new fields and CO into main; revert removes them and leaves the
        original CO intact.

        Exercises ``_schema_add_field`` (including FK and M2M through-table creation)
        running against a target schema that already has live data and existing
        through-tables — distinct from ``test_comprehensive_merge_and_revert`` which
        creates everything greenfield inside the branch.
        """
        # Two Sites in main so they exist in both schemas as valid FK targets.
        with event_tracking(self.request):
            site_a = Site.objects.create(name='Site A', slug='site-a')
            site_b = Site.objects.create(name='Site B', slug='site-b')

        site_ot = ObjectType.objects.get(app_label='dcim', model='site')

        # ── main: COT with text + object + multiobject; one CO ────────────
        with event_tracking(self.request):
            cot = CustomObjectType.objects.create(name='extend_cot', slug='extend-cot')
            CustomObjectTypeField.objects.create(
                custom_object_type=cot, name='main_text', label='Main Text', type='text',
            )
            CustomObjectTypeField.objects.create(
                custom_object_type=cot, name='main_obj', label='Main Obj',
                type='object', related_object_type=site_ot,
            )
            CustomObjectTypeField.objects.create(
                custom_object_type=cot, name='main_multi', label='Main Multi',
                type='multiobject', related_object_type=site_ot,
            )
            MainModel = cot.get_model()
            co_main = MainModel.objects.create(
                main_text='main co text',
                main_obj_id=site_a.pk,
            )
            co_main.main_multi.set([site_a, site_b])

        cot_pk = cot.pk
        co_main_pk = co_main.pk

        branch = _provision_branch('Extend Branch', self.MERGE_STRATEGY, self.user)
        branch_request = _make_request(self.user)

        # ── branch: add new fields of each type, then create a CO with all ─
        with activate_branch(branch), event_tracking(branch_request):
            CustomObjectTypeField.objects.create(
                custom_object_type=cot, name='branch_text', label='Branch Text', type='text',
            )
            CustomObjectTypeField.objects.create(
                custom_object_type=cot, name='branch_obj', label='Branch Obj',
                type='object', related_object_type=site_ot,
            )
            CustomObjectTypeField.objects.create(
                custom_object_type=cot, name='branch_multi', label='Branch Multi',
                type='multiobject', related_object_type=site_ot,
            )
            BranchModel = cot.get_model()
            co_branch = BranchModel.objects.create(
                main_text='branch co original text',
                main_obj_id=site_b.pk,
                branch_text='branch co new text',
                branch_obj_id=site_a.pk,
            )
            co_branch.main_multi.set([site_b])
            co_branch.branch_multi.set([site_a, site_b])

        co_branch_pk = co_branch.pk

        # ── before merge: branch fields and branch CO not visible in main ─
        for fname in ('branch_text', 'branch_obj', 'branch_multi'):
            self.assertFalse(
                CustomObjectTypeField.objects.filter(custom_object_type=cot, name=fname).exists(),
                f'{fname!r} must not be in main before merge',
            )
        MainModelPre = cot.get_model()
        self.assertFalse(
            MainModelPre.objects.filter(pk=co_branch_pk).exists(),
            'Branch CO must not be in main before merge',
        )

        # ── merge ─────────────────────────────────────────────────────────
        branch.merge(user=self.user, commit=True)
        branch.refresh_from_db()
        self.assertEqual(branch.status, BranchStatusChoices.MERGED)

        # ── after merge: all six fields present, both COs intact ──────────
        cot_main = CustomObjectType.objects.get(pk=cot_pk)
        names = set(cot_main.fields(manager='objects').values_list('name', flat=True))
        self.assertEqual(
            names,
            {'main_text', 'main_obj', 'main_multi', 'branch_text', 'branch_obj', 'branch_multi'},
        )

        MergedModel = cot_main.get_model()

        # Original main CO untouched.
        co_main_after = MergedModel.objects.get(pk=co_main_pk)
        self.assertEqual(co_main_after.main_text, 'main co text')
        self.assertEqual(co_main_after.main_obj_id, site_a.pk)
        self.assertEqual(
            set(co_main_after.main_multi.values_list('pk', flat=True)),
            {site_a.pk, site_b.pk},
        )

        # CO created in branch is in main with all field values.
        co_branch_after = MergedModel.objects.get(pk=co_branch_pk)
        self.assertEqual(co_branch_after.main_text, 'branch co original text')
        self.assertEqual(co_branch_after.main_obj_id, site_b.pk)
        self.assertEqual(co_branch_after.branch_text, 'branch co new text')
        self.assertEqual(co_branch_after.branch_obj_id, site_a.pk)
        self.assertEqual(
            set(co_branch_after.main_multi.values_list('pk', flat=True)),
            {site_b.pk},
        )
        self.assertEqual(
            set(co_branch_after.branch_multi.values_list('pk', flat=True)),
            {site_a.pk, site_b.pk},
        )

        # ── revert ────────────────────────────────────────────────────────
        branch.revert(user=self.user, commit=True)

        # Branch fields gone; main fields still present.
        names_after_revert = set(
            cot_main.fields(manager='objects').values_list('name', flat=True)
        )
        self.assertEqual(names_after_revert, {'main_text', 'main_obj', 'main_multi'})

        RevertedModel = cot_main.get_model()
        # Original main CO survived.
        co_main_reverted = RevertedModel.objects.get(pk=co_main_pk)
        self.assertEqual(co_main_reverted.main_text, 'main co text')
        self.assertEqual(co_main_reverted.main_obj_id, site_a.pk)
        self.assertEqual(
            set(co_main_reverted.main_multi.values_list('pk', flat=True)),
            {site_a.pk, site_b.pk},
        )
        # Branch CO removed.
        self.assertFalse(
            RevertedModel.objects.filter(pk=co_branch_pk).exists(),
            'CO created in branch must be gone after revert',
        )

    def test_constraint_merge_and_revert(self):
        """
        A branch adds fields and a constraint covering one and testing another to a COT from
        main. The constraint must be created after the fields on merge and dropped before them
        on revert.
        """
        with event_tracking(self.request):
            cot = CustomObjectType.objects.create(name='constraint_cot', slug='constraint-cot')
            vendor = CustomObjectTypeField.objects.create(custom_object_type=cot, name='vendor', type='text')

        branch = _provision_branch('Constraint Branch', self.MERGE_STRATEGY, self.user)
        with activate_branch(branch), event_tracking(_make_request(self.user)):
            serial = CustomObjectTypeField.objects.create(custom_object_type=cot, name='serial', type='text')
            status = CustomObjectTypeField.objects.create(custom_object_type=cot, name='status', type='text')
            constraint = CustomObjectTypeConstraint.objects.create(
                custom_object_type=CustomObjectType.objects.get(pk=cot.pk),
                name='vendor_serial',
                field_schema_ids=[vendor.schema_id, serial.schema_id],
                condition=[{'field': status.schema_id, 'lookup': 'n', 'value': 'retired'}],
            )
            # A check constraint whose rule compares two fields added in the branch.
            low = CustomObjectTypeField.objects.create(custom_object_type=cot, name='low', type='integer')
            high = CustomObjectTypeField.objects.create(custom_object_type=cot, name='high', type='integer')
            check = CustomObjectTypeConstraint.objects.create(
                custom_object_type=CustomObjectType.objects.get(pk=cot.pk),
                name='low_high',
                type='check',
                rule=[{'field': high.schema_id, 'lookup': 'gte', 'value': {'field': low.schema_id}}],
            )

        def db_constraint_names():
            with main_conn.cursor() as cursor:
                return set(main_conn.introspection.get_constraints(cursor, cot.get_database_table_name()))

        self.assertNotIn(constraint.db_name, db_constraint_names())
        self.assertNotIn(check.db_name, db_constraint_names())

        branch.merge(user=self.user, commit=True)
        branch.refresh_from_db()
        self.assertEqual(branch.status, BranchStatusChoices.MERGED)
        self.assertTrue(CustomObjectTypeConstraint.objects.filter(pk=constraint.pk).exists())
        self.assertIn(constraint.db_name, db_constraint_names())
        model = CustomObjectType.objects.get(pk=cot.pk).get_model()
        model.objects.create(vendor='acme', serial='1')
        with self.assertRaises(IntegrityError), transaction.atomic():
            model.objects.create(vendor='acme', serial='1')
        model.objects.create(vendor='acme', serial='1', status='retired')
        model.objects.create(vendor='acme', serial='1', status='retired')
        self.assertIn(check.db_name, db_constraint_names())
        model.objects.create(vendor='range', low=1, high=2)
        with self.assertRaises(IntegrityError), transaction.atomic():
            model.objects.create(vendor='range', low=2, high=1)
        model.objects.all().delete()

        branch.revert(user=self.user, commit=True)
        self.assertFalse(CustomObjectTypeConstraint.objects.filter(pk__in=[constraint.pk, check.pk]).exists())
        self.assertFalse(
            CustomObjectTypeField.objects.filter(pk__in=[serial.pk, status.pk, low.pk, high.pk]).exists()
        )
        self.assertNotIn(constraint.db_name, db_constraint_names())
        self.assertNotIn(check.db_name, db_constraint_names())

    # ── COT deleted inside branch → merge / revert ────────────────────────

    def test_cot_deleted_in_branch_merge_and_revert(self):
        """
        Delete a COT (with fields and CO instances in main) inside a branch,
        merge the deletion to main, then revert and verify the schema is
        restored.

        Scenario
        --------
        1. Main: create COT with a text field, an object field, and a
           multiobject field; insert one CO using all three.
        2. Provision branch.
        3. Branch: delete the COT.
        4. Merge: main loses the COT, its fields, the CO instances, the
           main table, and the multi-object through-table.
        5. Revert: COT, fields, the dynamic table, and the multi-object
           through-table must all come back at the *original* PKs — the
           ContentType pk in particular has to survive the round-trip so
           any existing FK references remain valid.

        Both schema directions are exercised:
        - Forward (merge): the squash strategy collapses field-level
          deletes alongside the COT delete, so ``_schema_remove_field``
          must stay idempotent when the COT's own ``delete()`` already
          dropped the through-table.
        - Backward (revert): ``CustomObjectType.delete()`` destroys the
          related ContentType row to satisfy ChangeDiff's PROTECT FK.
          Restoring the COT then requires the original ContentType pk
          to come back too — handled by ``restore_object`` (the DELETE-
          undo counterpart to ``deserialize_object``).

        CO data preservation is **not** asserted here.  The current
        delete path drops the dynamic table via raw DDL
        (``schema_editor.delete_model``) without firing per-row
        ``pre_delete`` signals, so no ObjectChange records exist for the
        CO instances and there is nothing for revert to replay.
        Recovering CO data across a COT-delete cycle would require
        iterating instances and calling ``.delete()`` on each before the
        DROP TABLE — a separate, larger change.
        """
        # FK target lives in main so both schemas share it.
        with event_tracking(self.request):
            site = Site.objects.create(name='COT-delete Site', slug='cot-delete-site')

        site_ot = ObjectType.objects.get(app_label='dcim', model='site')

        # ── main: COT + fields + CO ───────────────────────────────────────
        with event_tracking(self.request):
            cot = CustomObjectType.objects.create(name='doomed_cot', slug='doomed-cot')
            CustomObjectTypeField.objects.create(
                custom_object_type=cot, name='note', label='Note', type='text',
            )
            CustomObjectTypeField.objects.create(
                custom_object_type=cot, name='site_ref', label='Site',
                type='object', related_object_type=site_ot,
            )
            multi_field = CustomObjectTypeField.objects.create(
                custom_object_type=cot, name='sites', label='Sites',
                type='multiobject', related_object_type=site_ot,
            )
            Model = cot.get_model()
            co = Model.objects.create(note='hello', site_ref_id=site.pk)
            co.sites.set([site])

        cot_pk = cot.pk
        co_pk = co.pk
        co_table = cot.get_database_table_name()
        through_table = multi_field.through_table_name

        # Sanity: physical tables exist in main before we touch anything.
        self.assertIn(co_table, main_conn.introspection.table_names())
        self.assertIn(through_table, main_conn.introspection.table_names())

        branch = _provision_branch('COT Delete Branch', self.MERGE_STRATEGY, self.user)
        branch_request = _make_request(self.user)

        # ── branch: delete the COT ────────────────────────────────────────
        with activate_branch(branch), event_tracking(branch_request):
            branch_cot = CustomObjectType.objects.get(pk=cot_pk)
            branch_cot.snapshot()
            branch_cot.delete()

        # Main still has the COT before the merge applies.
        self.assertTrue(CustomObjectType.objects.filter(pk=cot_pk).exists())

        # ── merge ─────────────────────────────────────────────────────────
        branch.merge(user=self.user, commit=True)
        branch.refresh_from_db()
        self.assertEqual(branch.status, BranchStatusChoices.MERGED)

        # COT, fields, and physical tables must all be gone from main.
        self.assertFalse(
            CustomObjectType.objects.filter(pk=cot_pk).exists(),
            'COT must be gone from main after merge of branch deletion',
        )
        main_tables = main_conn.introspection.table_names()
        self.assertNotIn(
            co_table, main_tables,
            f'Main CO table {co_table!r} must be dropped after merge',
        )
        self.assertNotIn(
            through_table, main_tables,
            f'Through-table {through_table!r} must be dropped after merge',
        )

        # ── revert ────────────────────────────────────────────────────────
        branch.revert(user=self.user, commit=True)
        branch.refresh_from_db()

        # COT and fields must come back at their original pks.
        self.assertTrue(
            CustomObjectType.objects.filter(pk=cot_pk).exists(),
            'COT must be restored after revert of branch deletion',
        )
        restored = CustomObjectType.objects.get(pk=cot_pk)
        field_names = set(restored.fields(manager='objects').values_list('name', flat=True))
        self.assertEqual(field_names, {'note', 'site_ref', 'sites'})

        # The COT comes back with a fresh ContentType/ObjectType pair
        # (clean_fields nulls the stale FK; the post_save handler then
        # calls get_or_create which creates a new row).  The pk is
        # intentionally not preserved — cross-branch audit data that
        # referenced the original pk was already invalidated when the
        # COT was deleted, so a fresh pk is the honest representation.
        restored_model = restored.get_model()
        self.assertIsNotNone(restored.object_type_id)
        self.assertTrue(
            ObjectType.objects.filter(pk=restored.object_type_id).exists(),
            'Restored COT must reference a live ObjectType row',
        )

        restored_tables = main_conn.introspection.table_names()
        self.assertIn(co_table, restored_tables, 'CO table must be re-created on revert')
        self.assertIn(through_table, restored_tables, 'Through-table must be re-created on revert')

        # CO data is NOT restored — see docstring.  ``co_pk`` is referenced
        # here only to make the unused-variable warning irrelevant; the row
        # at that pk legitimately does not exist after revert.
        self.assertFalse(
            restored_model.objects.filter(pk=co_pk).exists(),
            'CO instances are not recovered by revert (see test docstring)',
        )

    # ── multi-object field rename across merge ────────────────────────────

    def test_multiobject_field_rename_merge_and_revert(self):
        """
        Rename a multi-object field inside a branch and merge.

        Through-table renames are the most fragile schema operation: the
        physical table name changes (``alter_db_table``) but the integer
        FKs to the parent CO model and to the related object type must
        keep pointing at the same rows.  This test verifies that:

        * The old through-table is gone from main after merge.
        * The new through-table is present and holds the same rows.
        * Reading the M2M via the new accessor returns the original
          values intact.
        * Revert restores the old through-table name and field name.
        """
        site_ot = ObjectType.objects.get(app_label='dcim', model='site')

        with event_tracking(self.request):
            site_a = Site.objects.create(name='M2M Site A', slug='m2m-site-a')
            site_b = Site.objects.create(name='M2M Site B', slug='m2m-site-b')

        # ── main: COT with a multi-object field + a CO with M2M values ────
        with event_tracking(self.request):
            cot = CustomObjectType.objects.create(name='m2m_rename_cot', slug='m2m-rename-cot')
            field = CustomObjectTypeField.objects.create(
                custom_object_type=cot, name='tags_old', label='Tags',
                type='multiobject', related_object_type=site_ot,
            )
            MainModel = cot.get_model()
            co = MainModel.objects.create()
            co.tags_old.set([site_a, site_b])

        cot_pk = cot.pk
        field_pk = field.pk
        co_pk = co.pk
        old_through = field.through_table_name

        branch = _provision_branch('M2M Rename Branch', self.MERGE_STRATEGY, self.user)
        branch_request = _make_request(self.user)

        # ── branch: rename the multi-object field ─────────────────────────
        with activate_branch(branch), event_tracking(branch_request):
            f = CustomObjectTypeField.objects.get(pk=field_pk)
            f.snapshot()
            f.name = 'tags_new'
            f.label = 'Tags (renamed)'
            f.save()

        # Compute the post-rename through-table name from a freshly-loaded
        # field record so we don't depend on the in-memory branch state.
        renamed_field = CustomObjectTypeField.objects.get(pk=field_pk)
        # Field name in main hasn't applied yet (still 'tags_old' there) — we
        # need the *branch's* current name, which is what the rename target is.
        new_through = (
            f"custom_objects_{renamed_field.custom_object_type_id}_tags_new"
        )

        # Before merge: main still sees the old field/through-table.
        self.assertEqual(
            CustomObjectTypeField.objects.get(pk=field_pk).name, 'tags_old',
        )
        main_tables_before = main_conn.introspection.table_names()
        self.assertIn(old_through, main_tables_before)
        self.assertNotIn(new_through, main_tables_before)

        # ── merge ─────────────────────────────────────────────────────────
        branch.merge(user=self.user, commit=True)
        branch.refresh_from_db()
        self.assertEqual(branch.status, BranchStatusChoices.MERGED)

        # After merge: field name is updated; old through-table is gone, new
        # through-table is present, and its rows survived the rename.
        self.assertEqual(
            CustomObjectTypeField.objects.get(pk=field_pk).name, 'tags_new',
        )
        main_tables_after = main_conn.introspection.table_names()
        self.assertNotIn(
            old_through, main_tables_after,
            f'Old through-table {old_through!r} must be gone after rename merge',
        )
        self.assertIn(
            new_through, main_tables_after,
            f'New through-table {new_through!r} must exist after rename merge',
        )

        MergedModel = CustomObjectType.objects.get(pk=cot_pk).get_model()
        co_merged = MergedModel.objects.get(pk=co_pk)
        self.assertEqual(
            set(co_merged.tags_new.values_list('pk', flat=True)),
            {site_a.pk, site_b.pk},
            'M2M values must survive the through-table rename',
        )
        # Old accessor must no longer be accessible on the model.
        self.assertFalse(
            hasattr(co_merged, 'tags_old'),
            'Old field accessor must be gone after rename merge',
        )

        # ── revert ────────────────────────────────────────────────────────
        branch.revert(user=self.user, commit=True)
        branch.refresh_from_db()

        # Field name restored, through-table restored, rows intact.
        self.assertEqual(
            CustomObjectTypeField.objects.get(pk=field_pk).name, 'tags_old',
        )
        main_tables_reverted = main_conn.introspection.table_names()
        self.assertIn(
            old_through, main_tables_reverted,
            f'Old through-table {old_through!r} must be restored after revert',
        )
        self.assertNotIn(
            new_through, main_tables_reverted,
            f'New through-table {new_through!r} must be gone after revert',
        )

        RevertedModel = CustomObjectType.objects.get(pk=cot_pk).get_model()
        co_reverted = RevertedModel.objects.get(pk=co_pk)
        self.assertEqual(
            set(co_reverted.tags_old.values_list('pk', flat=True)),
            {site_a.pk, site_b.pk},
            'M2M values must survive the round-trip rename → revert',
        )

    # ── single-field DELETE inside branch ─────────────────────────────────

    def test_single_field_delete_merge_and_revert(self):
        """
        Delete one field from a COT in a branch (parent COT intact).

        Exercises ``_schema_remove_field`` via the merge replay engine
        without the through-table-already-gone shortcut that
        ``test_cot_deleted_in_branch_merge_and_revert`` triggers.  Covers
        the most direct CRUD gap: per-field delete is the common
        production case but was previously only exercised transitively.
        """
        site_ot = ObjectType.objects.get(app_label='dcim', model='site')

        with event_tracking(self.request):
            site = Site.objects.create(name='Field-Delete Site', slug='field-delete-site')
            cot = CustomObjectType.objects.create(name='field_delete_cot', slug='field-delete-cot')
            CustomObjectTypeField.objects.create(
                custom_object_type=cot, name='keep_me', label='Keep', type='text',
            )
            scalar_field = CustomObjectTypeField.objects.create(
                custom_object_type=cot, name='drop_me', label='Drop', type='integer',
            )
            multi_field = CustomObjectTypeField.objects.create(
                custom_object_type=cot, name='drop_m2m', label='Drop M2M',
                type='multiobject', related_object_type=site_ot,
            )
            Model = cot.get_model()
            co = Model.objects.create(keep_me='hello', drop_me=7)
            co.drop_m2m.set([site])

        cot_pk = cot.pk
        co_pk = co.pk
        scalar_field_pk = scalar_field.pk
        multi_field_pk = multi_field.pk
        through_table = multi_field.through_table_name
        co_table = cot.get_database_table_name()

        # Sanity: column + through table exist in main before the merge.
        with main_conn.cursor() as cursor:
            cols_before = {
                c.name for c in main_conn.introspection.get_table_description(cursor, co_table)
            }
        self.assertIn('drop_me', cols_before)
        self.assertIn(through_table, main_conn.introspection.table_names())

        branch = _provision_branch('Field Delete Branch', self.MERGE_STRATEGY, self.user)
        branch_request = _make_request(self.user)

        with activate_branch(branch), event_tracking(branch_request):
            CustomObjectTypeField.objects.get(pk=scalar_field_pk).delete()
            CustomObjectTypeField.objects.get(pk=multi_field_pk).delete()

        # Before merge: main still has both fields and their schema.
        self.assertTrue(CustomObjectTypeField.objects.filter(pk=scalar_field_pk).exists())
        self.assertTrue(CustomObjectTypeField.objects.filter(pk=multi_field_pk).exists())

        # ── merge ─────────────────────────────────────────────────────────
        branch.merge(user=self.user, commit=True)
        branch.refresh_from_db()
        self.assertEqual(branch.status, BranchStatusChoices.MERGED)

        # Fields gone from ORM; physical column + through table gone too.
        self.assertFalse(CustomObjectTypeField.objects.filter(pk=scalar_field_pk).exists())
        self.assertFalse(CustomObjectTypeField.objects.filter(pk=multi_field_pk).exists())
        with main_conn.cursor() as cursor:
            cols_after = {
                c.name for c in main_conn.introspection.get_table_description(cursor, co_table)
            }
        self.assertNotIn('drop_me', cols_after, 'drop_me column must be removed from main')
        self.assertIn('keep_me', cols_after, 'keep_me column must remain in main')
        self.assertNotIn(
            through_table, main_conn.introspection.table_names(),
            f'Through-table {through_table!r} must be dropped after merge',
        )

        # The retained field's data on the CO must still be readable.
        cot_main = CustomObjectType.objects.get(pk=cot_pk)
        co_main = cot_main.get_model().objects.get(pk=co_pk)
        self.assertEqual(co_main.keep_me, 'hello')

        # ── revert ────────────────────────────────────────────────────────
        branch.revert(user=self.user, commit=True)

        # Both fields are restored, column and through table re-created.
        self.assertTrue(CustomObjectTypeField.objects.filter(pk=scalar_field_pk).exists())
        self.assertTrue(CustomObjectTypeField.objects.filter(pk=multi_field_pk).exists())
        with main_conn.cursor() as cursor:
            cols_reverted = {
                c.name for c in main_conn.introspection.get_table_description(cursor, co_table)
            }
        self.assertIn('drop_me', cols_reverted, 'drop_me column must be restored after revert')
        self.assertIn(
            through_table, main_conn.introspection.table_names(),
            f'Through-table {through_table!r} must be restored after revert',
        )

    # ── polymorphic OBJECT field merge/revert ─────────────────────────────

    def test_polymorphic_object_field_merge_and_revert(self):
        """
        Polymorphic OBJECT field (GenericForeignKey backed by content_type +
        object_id columns).  None of the existing branching tests cover this
        path, and the per-(cot, branch) through-model refactor changed
        through-model handling — polymorphic GFK fields have no through but
        do create dedicated columns that need branch-aware DDL routing.
        """
        site_ot = ObjectType.objects.get(app_label='dcim', model='site')

        with event_tracking(self.request):
            site = Site.objects.create(name='Poly Site', slug='poly-site')

        branch = _provision_branch('Poly OBJ Branch', self.MERGE_STRATEGY, self.user)
        branch_request = _make_request(self.user)

        with activate_branch(branch), event_tracking(branch_request):
            cot = CustomObjectType.objects.create(name='poly_obj_cot', slug='poly-obj-cot')
            field = CustomObjectTypeField.objects.create(
                custom_object_type=cot,
                name='target',
                label='Target',
                type='object',
                is_polymorphic=True,
            )
            field.related_object_types.set([site_ot])
            Model = cot.get_model()
            co = Model.objects.create(
                target_content_type=ContentType.objects.get_for_model(Site),
                target_object_id=site.pk,
            )

        cot_pk, field_pk, co_pk = cot.pk, field.pk, co.pk

        # ── merge ─────────────────────────────────────────────────────────
        branch.merge(user=self.user, commit=True)
        branch.refresh_from_db()
        self.assertEqual(branch.status, BranchStatusChoices.MERGED)

        cot_main = CustomObjectType.objects.get(pk=cot_pk)
        co_main = cot_main.get_model().objects.get(pk=co_pk)
        self.assertEqual(co_main.target_object_id, site.pk)
        self.assertEqual(
            co_main.target_content_type_id,
            ContentType.objects.get_for_model(Site).pk,
        )

        # ── revert ────────────────────────────────────────────────────────
        branch.revert(user=self.user, commit=True)
        self.assertFalse(CustomObjectType.objects.filter(pk=cot_pk).exists())
        self.assertFalse(CustomObjectTypeField.objects.filter(pk=field_pk).exists())

    def test_polymorphic_object_field_cascade_merge_and_revert(self):
        """
        A Site deleted inside a branch cascades to the CO whose polymorphic OBJECT
        field references it with on_delete_behavior=cascade.  Merge removes both
        from main; revert restores both, with the CO still pointing at the Site.
        """
        site_ot = ObjectType.objects.get(app_label='dcim', model='site')
        site_ct = ContentType.objects.get_for_model(Site)

        with event_tracking(self.request):
            site = Site.objects.create(name='Cascade Site', slug='cascade-site')
            cot = CustomObjectType.objects.create(name='poly_cascade_cot', slug='poly-cascade-cot')
            field = CustomObjectTypeField.objects.create(
                custom_object_type=cot,
                name='target',
                label='Target',
                type='object',
                is_polymorphic=True,
                on_delete_behavior=ObjectFieldOnDeleteChoices.CASCADE,
            )
            field.related_object_types.set([site_ot])
            co = cot.get_model().objects.create(target=site)

        site_pk, co_pk = site.pk, co.pk
        branch = _provision_branch('Poly Cascade Branch', self.MERGE_STRATEGY, self.user)
        branch_request = _make_request(self.user)

        with activate_branch(branch), event_tracking(branch_request):
            Site.objects.get(pk=site_pk).delete()
            self.assertFalse(
                cot.get_model().objects.filter(pk=co_pk).exists(),
                'Deleting the Site in the branch must cascade to the CO',
            )

        self.assertTrue(Site.objects.filter(pk=site_pk).exists(), 'Site must still exist in main before merge')
        self.assertTrue(
            cot.get_model().objects.filter(pk=co_pk).exists(),
            'CO must still exist in main before merge',
        )

        # ── merge ─────────────────────────────────────────────────────────
        branch.merge(user=self.user, commit=True)
        branch.refresh_from_db()
        self.assertEqual(branch.status, BranchStatusChoices.MERGED)

        self.assertFalse(Site.objects.filter(pk=site_pk).exists(), 'Site must be deleted from main after merge')
        self.assertFalse(
            cot.get_model().objects.filter(pk=co_pk).exists(),
            'CO must be deleted from main after merge',
        )

        # ── revert ────────────────────────────────────────────────────────
        branch.revert(user=self.user, commit=True)

        self.assertTrue(Site.objects.filter(pk=site_pk).exists(), 'Site must be restored in main after revert')
        co_main = cot.get_model().objects.get(pk=co_pk)
        self.assertEqual(co_main.target_content_type_id, site_ct.pk)
        self.assertEqual(co_main.target_object_id, site_pk)

    # ── polymorphic MULTIOBJECT field merge/revert ────────────────────────

    def test_polymorphic_multiobject_field_merge_and_revert(self):
        self._assert_polymorphic_multiobject_field_merge_and_revert('targets')

    def test_polymorphic_multiobject_long_field_name_merge_and_revert(self):
        """
        Regression for #762: a field name long enough that the raw through-table
        name exceeds PostgreSQL's 63-char limit, so ``safe_table_name()`` hashes
        it.  Merge replay must target the hashed name, not the raw one.
        """
        field_name = 'polymorphic_targets_with_a_very_long_field_name'
        self.assertGreater(len(f'{USER_TABLE_DATABASE_NAME_PREFIX}1_{field_name}'), 63)
        self._assert_polymorphic_multiobject_field_merge_and_revert(field_name)

    def _assert_polymorphic_multiobject_field_merge_and_revert(self, field_name):
        """
        Polymorphic MULTIOBJECT field — through table has
        (source_id, content_type_id, object_id) columns.  Asserts the
        through-table schema lifecycle (create in branch / merge / revert)
        AND the M2M data round-trip: rows added via
        ``PolymorphicManyToManyManager`` in the branch must appear in main
        after merge.

        Data preservation requires three pieces:
        - ``PolymorphicManyToManyManager`` fires ``m2m_changed``, triggering
          an UPDATE ObjectChange on the parent CO.
        - ``CustomObject.serialize_object`` includes polymorphic M2M values
          (Django's serializer skips them — the descriptor isn't a real
          M2M field on ``_meta``).
        - ``CustomObject.deserialize_object`` replays the polymorphic
          values via the descriptor after the CO row saves.
        """
        site_ot = ObjectType.objects.get(app_label='dcim', model='site')
        site_ct = ContentType.objects.get_for_model(Site)

        with event_tracking(self.request):
            site_a = Site.objects.create(name='Poly M2M A', slug='poly-m2m-a')
            site_b = Site.objects.create(name='Poly M2M B', slug='poly-m2m-b')

        branch = _provision_branch('Poly M2M Branch', self.MERGE_STRATEGY, self.user)
        branch_request = _make_request(self.user)

        with activate_branch(branch), event_tracking(branch_request):
            cot = CustomObjectType.objects.create(name='poly_m2m_cot', slug='poly-m2m-cot')
            field = CustomObjectTypeField.objects.create(
                custom_object_type=cot,
                name=field_name,
                label='Targets',
                type='multiobject',
                is_polymorphic=True,
            )
            field.related_object_types.set([site_ot])
            Model = cot.get_model()
            co = Model.objects.create()
            co.snapshot()
            getattr(co, field_name).add(site_a)
            getattr(co, field_name).add(site_b)

        cot_pk, field_pk, co_pk = cot.pk, field.pk, co.pk
        through_table = field.through_table_name

        # ── merge ─────────────────────────────────────────────────────────
        branch.merge(user=self.user, commit=True)
        branch.refresh_from_db()
        self.assertEqual(branch.status, BranchStatusChoices.MERGED)

        self.assertIn(
            through_table, main_conn.introspection.table_names(),
            'Polymorphic through-table must exist in main after merge',
        )

        # Read the through table directly: the polymorphic descriptor's
        # query helpers are exercised elsewhere; here we just want to
        # confirm the rows landed in main.
        with main_conn.cursor() as cursor:
            cursor.execute(
                f'SELECT object_id FROM "{through_table}" '
                'WHERE source_id = %s AND content_type_id = %s ORDER BY object_id',
                [co_pk, site_ct.pk],
            )
            rows = [r[0] for r in cursor.fetchall()]
        self.assertEqual(
            rows, sorted([site_a.pk, site_b.pk]),
            'Polymorphic M2M rows added in branch must land in main after merge',
        )

        # ── revert ────────────────────────────────────────────────────────
        branch.revert(user=self.user, commit=True)
        self.assertFalse(CustomObjectType.objects.filter(pk=cot_pk).exists())
        self.assertFalse(CustomObjectTypeField.objects.filter(pk=field_pk).exists())
        self.assertNotIn(
            through_table, main_conn.introspection.table_names(),
            'Polymorphic through-table must be dropped after revert',
        )

    # ── self-referential OBJECT field merge/revert ────────────────────────

    def test_self_referential_object_field_merge_and_revert(self):
        """
        Self-referential OBJECT field (FK to the same COT).  The generated
        model resolves the FK target back to itself in
        ``after_model_generation``; we verify that path works across merge.
        """
        branch = _provision_branch('Self Ref OBJ Branch', self.MERGE_STRATEGY, self.user)
        branch_request = _make_request(self.user)

        with activate_branch(branch), event_tracking(branch_request):
            cot = CustomObjectType.objects.create(name='self_ref_obj_cot', slug='self-ref-obj-cot')
            self_ot = ObjectType.objects.get(
                app_label='netbox_custom_objects',
                model=cot.get_table_model_name(cot.id).lower(),
            )
            CustomObjectTypeField.objects.create(
                custom_object_type=cot, name='label', label='Label', type='text',
            )
            CustomObjectTypeField.objects.create(
                custom_object_type=cot, name='parent', label='Parent',
                type='object', related_object_type=self_ot,
            )
            Model = cot.get_model()
            root = Model.objects.create(label='root')
            child = Model.objects.create(label='child', parent_id=root.pk)

        cot_pk = cot.pk
        root_pk, child_pk = root.pk, child.pk

        branch.merge(user=self.user, commit=True)
        branch.refresh_from_db()
        self.assertEqual(branch.status, BranchStatusChoices.MERGED)

        Model = CustomObjectType.objects.get(pk=cot_pk).get_model()
        child_main = Model.objects.get(pk=child_pk)
        self.assertEqual(child_main.parent_id, root_pk)
        self.assertEqual(child_main.label, 'child')

        branch.revert(user=self.user, commit=True)
        self.assertFalse(CustomObjectType.objects.filter(pk=cot_pk).exists())

    # ── self-referential MULTIOBJECT field merge/revert ───────────────────

    def test_self_referential_multiobject_field_merge_and_revert(self):
        """
        Self-referential MULTIOBJECT — through table with both source and
        target FKs pointing at the same dynamic CO model.  Exercises the
        ``_is_self_referential`` path in
        ``MultiObjectFieldType.after_model_generation``.
        """
        branch = _provision_branch('Self Ref M2M Branch', self.MERGE_STRATEGY, self.user)
        branch_request = _make_request(self.user)

        with activate_branch(branch), event_tracking(branch_request):
            cot = CustomObjectType.objects.create(name='self_ref_m2m_cot', slug='self-ref-m2m-cot')
            self_ot = ObjectType.objects.get(
                app_label='netbox_custom_objects',
                model=cot.get_table_model_name(cot.id).lower(),
            )
            CustomObjectTypeField.objects.create(
                custom_object_type=cot, name='label', label='Label', type='text',
            )
            field = CustomObjectTypeField.objects.create(
                custom_object_type=cot, name='peers', label='Peers',
                type='multiobject', related_object_type=self_ot,
            )
            Model = cot.get_model()
            a = Model.objects.create(label='a')
            b = Model.objects.create(label='b')
            c = Model.objects.create(label='c')
            a.peers.set([b, c])

        cot_pk = cot.pk
        a_pk, b_pk, c_pk = a.pk, b.pk, c.pk
        through_table = field.through_table_name

        branch.merge(user=self.user, commit=True)
        branch.refresh_from_db()
        self.assertEqual(branch.status, BranchStatusChoices.MERGED)

        Model = CustomObjectType.objects.get(pk=cot_pk).get_model()
        a_main = Model.objects.get(pk=a_pk)
        self.assertEqual(
            set(a_main.peers.values_list('pk', flat=True)),
            {b_pk, c_pk},
            'Self-referential M2M values must survive merge',
        )
        self.assertIn(through_table, main_conn.introspection.table_names())

        branch.revert(user=self.user, commit=True)
        self.assertFalse(CustomObjectType.objects.filter(pk=cot_pk).exists())
        self.assertNotIn(through_table, main_conn.introspection.table_names())

    # ── cross-COT FK created entirely inside branch ───────────────────────

    def test_array_field_merge_and_revert(self):
        """
        Array values, which the changelog stores serialized (a tuple array as a
        list of JSON strings), merge and revert: an object modified and one
        created in the branch, and a field added in the branch with a value.
        """
        request = _make_request(self.user)
        with event_tracking(request):
            cot = CustomObjectType.objects.create(name='array_cot', slug='array-cot')
            CustomObjectTypeField.objects.create(
                custom_object_type=cot, name='points', label='Points', type='array',
                array_element_type='integer', array_tuple_size=2,
            )
            co = cot.get_model().objects.create(points=[[0, 0]])

        branch = _provision_branch('Array Branch', self.MERGE_STRATEGY, self.user)
        branch_request = _make_request(self.user)
        with activate_branch(branch), event_tracking(branch_request):
            branch_co = cot.get_model().objects.get(pk=co.pk)
            branch_co.snapshot()
            branch_co.points = [[1, 2], [3, 4]]
            branch_co.save()
            CustomObjectTypeField.objects.create(
                custom_object_type=cot, name='days', label='Days', type='array', array_element_type='date',
            )
            new_co = cot.get_model().objects.create(points=[[5, 6]], days=[datetime.date(2026, 1, 1)])
        new_pk = new_co.pk

        branch.merge(user=self.user, commit=True)
        branch.refresh_from_db()
        self.assertEqual(branch.status, BranchStatusChoices.MERGED)
        Model = CustomObjectType.objects.get(pk=cot.pk).get_model()
        self.assertEqual(Model.objects.get(pk=co.pk).points, [[1, 2], [3, 4]])
        created = Model.objects.get(pk=new_pk)
        self.assertEqual(created.points, [[5, 6]])
        self.assertEqual(created.days, [datetime.date(2026, 1, 1)])

        branch.revert(user=self.user, commit=True)
        Model = CustomObjectType.objects.get(pk=cot.pk).get_model()
        self.assertEqual(Model.objects.get(pk=co.pk).points, [[0, 0]])
        self.assertFalse(Model.objects.filter(pk=new_pk).exists())

    def test_cross_cot_fk_branch_creates_both_merge_and_revert(self):
        """
        Branch creates COT B, then COT A with a FK pointing at B, then a CO
        of type A referencing a CO of type B.  Merge must apply the COT
        creates and field create in an order that respects the FK
        dependency, and the data must survive.
        """
        branch = _provision_branch('Cross COT Branch', self.MERGE_STRATEGY, self.user)
        branch_request = _make_request(self.user)

        with activate_branch(branch), event_tracking(branch_request):
            cot_b = CustomObjectType.objects.create(name='cross_b_cot', slug='cross-b-cot')
            CustomObjectTypeField.objects.create(
                custom_object_type=cot_b, name='b_label', label='B Label', type='text',
            )
            b_ot = ObjectType.objects.get(
                app_label='netbox_custom_objects',
                model=cot_b.get_table_model_name(cot_b.id).lower(),
            )

            cot_a = CustomObjectType.objects.create(name='cross_a_cot', slug='cross-a-cot')
            CustomObjectTypeField.objects.create(
                custom_object_type=cot_a, name='a_label', label='A Label', type='text',
            )
            CustomObjectTypeField.objects.create(
                custom_object_type=cot_a, name='b_ref', label='B Ref',
                type='object', related_object_type=b_ot,
            )

            BModel = cot_b.get_model()
            b_inst = BModel.objects.create(b_label='hello B')
            AModel = cot_a.get_model()
            a_inst = AModel.objects.create(a_label='hello A', b_ref_id=b_inst.pk)

        cot_a_pk, cot_b_pk = cot_a.pk, cot_b.pk
        a_pk, b_pk = a_inst.pk, b_inst.pk

        branch.merge(user=self.user, commit=True)
        branch.refresh_from_db()
        self.assertEqual(branch.status, BranchStatusChoices.MERGED)

        AModel = CustomObjectType.objects.get(pk=cot_a_pk).get_model()
        BModel = CustomObjectType.objects.get(pk=cot_b_pk).get_model()
        self.assertEqual(BModel.objects.get(pk=b_pk).b_label, 'hello B')
        a_main = AModel.objects.get(pk=a_pk)
        self.assertEqual(a_main.b_ref_id, b_pk)
        self.assertEqual(a_main.a_label, 'hello A')

        branch.revert(user=self.user, commit=True)
        self.assertFalse(CustomObjectType.objects.filter(pk=cot_a_pk).exists())
        self.assertFalse(CustomObjectType.objects.filter(pk=cot_b_pk).exists())


# ── Concrete test classes (one per merge strategy) ────────────────────────────

@unittest.skipUnless(HAS_BRANCHING, 'netbox-branching is not installed')
class IterativeBranchingTestCase(BaseBranchingTests, _TestBase):
    """Run BaseBranchingTests with the iterative merge strategy."""
    MERGE_STRATEGY = 'iterative'


@unittest.skipUnless(HAS_BRANCHING, 'netbox-branching is not installed')
class SquashBranchingTestCase(BaseBranchingTests, _TestBase):
    """Run BaseBranchingTests with the squash merge strategy."""
    MERGE_STRATEGY = 'squash'


# ── Missing field-type coverage (iterative only) ──────────────────────────────

@unittest.skipUnless(HAS_BRANCHING, 'netbox-branching is not installed')
class MissingFieldTypesTestCase(BranchingTestBase, _TestBase):
    """
    Field types that ``test_comprehensive_merge_and_revert`` doesn't cover:
    longtext, date (separate from datetime), URL, JSON, multiselect.

    Iterative only — strategy-specific bugs in these field types would still
    surface in the comprehensive squash test once they're added there.  This
    standalone class keeps the round-trip times manageable while filling out
    the field-type matrix.
    """

    def test_merge_and_revert_for_extra_field_types(self):
        with event_tracking(self.request):
            choice_set = CustomFieldChoiceSet.objects.create(
                name='Multi Statuses',
                extra_choices=[['a', 'A'], ['b', 'B'], ['c', 'C']],
            )

        branch = _provision_branch('Extra Types Branch', 'iterative', self.user)
        branch_request = _make_request(self.user)

        with activate_branch(branch), event_tracking(branch_request):
            cot = CustomObjectType.objects.create(name='extra_types_cot', slug='extra-types-cot')
            CustomObjectTypeField.objects.create(
                custom_object_type=cot, name='longtext_field', label='Long Text', type='longtext',
            )
            CustomObjectTypeField.objects.create(
                custom_object_type=cot, name='date_field', label='Date', type='date',
            )
            CustomObjectTypeField.objects.create(
                custom_object_type=cot, name='url_field', label='URL', type='url',
            )
            CustomObjectTypeField.objects.create(
                custom_object_type=cot, name='json_field', label='JSON', type='json',
            )
            CustomObjectTypeField.objects.create(
                custom_object_type=cot, name='multiselect_field', label='Multi',
                type='multiselect', choice_set=choice_set,
            )
            Model = cot.get_model()
            co = Model.objects.create(
                longtext_field='line1\nline2',
                date_field=datetime.date(2026, 5, 21),
                url_field='https://example.com/path',
                url_field_title='Example Path',
                json_field={'k': 'v', 'n': 1, 'list': [1, 2, 3]},
                multiselect_field=['a', 'c'],
            )

        cot_pk, co_pk = cot.pk, co.pk

        branch.merge(user=self.user, commit=True)
        branch.refresh_from_db()
        self.assertEqual(branch.status, BranchStatusChoices.MERGED)

        Model = CustomObjectType.objects.get(pk=cot_pk).get_model()
        co_main = Model.objects.get(pk=co_pk)
        self.assertEqual(co_main.longtext_field, 'line1\nline2')
        self.assertEqual(co_main.date_field, datetime.date(2026, 5, 21))
        self.assertEqual(co_main.url_field, 'https://example.com/path')
        self.assertEqual(co_main.url_field_title, 'Example Path')
        self.assertEqual(co_main.json_field, {'k': 'v', 'n': 1, 'list': [1, 2, 3]})
        self.assertEqual(sorted(co_main.multiselect_field), ['a', 'c'])

        branch.revert(user=self.user, commit=True)
        self.assertFalse(CustomObjectType.objects.filter(pk=cot_pk).exists())
