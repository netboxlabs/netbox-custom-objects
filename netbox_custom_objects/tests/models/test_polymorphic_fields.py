"""
Polymorphic object and multi-object fields at the model layer: deleting referenced
objects, cycle detection and reverse descriptors.
"""

from django.core.exceptions import ValidationError
from django.test import TestCase

from core.models import ObjectType
from dcim.models import Site

from netbox_custom_objects.models import CustomObjectType, CustomObjectTypeField
from netbox_custom_objects.tests.base import CustomObjectsTestCase


# ---------------------------------------------------------------------------
# Referenced-object deletion
# ---------------------------------------------------------------------------

class ReferencedObjectDeletionTest(CustomObjectsTestCase, TestCase):
    """
    Tests for the on_delete behaviour when a referenced object (e.g. a Site) or
    the CustomObjectType itself is deleted.

    A GFK field has no DB-level FK from object_id to the concrete target table;
    generic relations cannot express that.  The field's on_delete_behavior is
    applied in Python instead, so deleting a Site referenced by a field with the
    default SET_NULL clears both columns.

    Polymorphic M2M through-tables store (source_id, content_type_id, object_id).
    The content_type FK has on_delete=CASCADE (so deleting the ContentType drops
    the through-table rows), but again there is no FK from object_id to the
    concrete target table.  Deleting a Site leaves a stale through-table row;
    PolymorphicManyToManyManager._get_objects() already skips such rows because
    the batch-fetch query returns no matching object for the deleted PK.
    """

    def setUp(self):
        super().setUp()

        self.site = Site.objects.create(name="Del Site", slug="del-site")
        self.site_ot = ObjectType.objects.get(app_label="dcim", model="site")
        self.prefix_ot = ObjectType.objects.get(app_label="ipam", model="prefix")

        self.cot = CustomObjectType.objects.create(
            name="DelTest", slug="del-test",
            verbose_name_plural="Del Tests",
        )
        CustomObjectTypeField.objects.create(
            custom_object_type=self.cot,
            name="name", type="text", primary=True, required=True,
        )
        self.gfk_field = CustomObjectTypeField.objects.create(
            custom_object_type=self.cot,
            name="poly_obj", label="Poly Obj", type="object",
            is_polymorphic=True,
        )
        self.gfk_field.related_object_types.set([self.site_ot, self.prefix_ot])

        self.m2m_field = CustomObjectTypeField.objects.create(
            custom_object_type=self.cot,
            name="poly_multi", label="Poly Multi", type="multiobject",
            is_polymorphic=True,
        )
        self.m2m_field.related_object_types.set([self.site_ot, self.prefix_ot])

        self.model = self.cot.get_model()

    def test_deleting_referenced_site_nulls_gfk_accessor(self):
        """
        Deleting a Site that is referenced by a polymorphic GFK field with the
        default SET_NULL behaviour clears the reference, so the accessor returns None.
        """
        obj = self.model.objects.create(name="cleared-gfk")
        obj.poly_obj = self.site
        obj.save()

        self.site.delete()

        obj.refresh_from_db()
        self.assertIsNone(obj.poly_obj_content_type_id)
        self.assertIsNone(obj.poly_obj_object_id)
        self.assertIsNone(obj.poly_obj)

    def test_full_clean_flags_newly_assigned_gfk_pointing_at_deleted_object(self):
        """A reference being set to an object that no longer exists is rejected."""
        site_pk = self.site.pk
        self.site.delete()
        obj = self.model(name="stale-gfk", poly_obj_content_type=self.site_ot, poly_obj_object_id=site_pk)
        with self.assertRaises(ValidationError) as cm:
            obj.full_clean()
        self.assertIn("poly_obj_object_id", cm.exception.error_dict)

        # Clearing the reference makes the object valid again.
        obj.poly_obj = None
        obj.full_clean()

    def test_full_clean_leaves_stored_stale_gfk_alone(self):
        """A saved reference whose target was deleted later is only re-validated once it changes."""
        obj = self.model.objects.create(name="stored-stale")
        site_pk = self.site.pk
        self.site.delete()
        # Deleting the target now clears the reference, so write the stale one back as older data would have it.
        self.model.objects.filter(pk=obj.pk).update(poly_obj_content_type=self.site_ot, poly_obj_object_id=site_pk)

        obj = self.model.objects.get(pk=obj.pk)
        obj.name = "stored-stale-renamed"
        obj.full_clean()

        obj.poly_obj_object_id = site_pk + 1
        with self.assertRaises(ValidationError) as cm:
            obj.full_clean()
        self.assertIn("poly_obj_object_id", cm.exception.error_dict)

    def test_full_clean_leaves_stored_half_set_gfk_alone(self):
        """As above when the target type was deleted: content type NULL, object ID still set."""
        obj = self.model.objects.create(name="stored-half")
        self.model.objects.filter(pk=obj.pk).update(poly_obj_content_type=None, poly_obj_object_id=42)
        obj = self.model.objects.get(pk=obj.pk)
        obj.name = "stored-half-renamed"
        obj.full_clean()

    def test_full_clean_rejects_half_set_gfk(self):
        """A content type without an object ID (or vice versa) fails validation."""
        obj = self.model(name="half-gfk")
        obj.poly_obj_content_type = self.site_ot
        with self.assertRaises(ValidationError) as cm:
            obj.full_clean()
        self.assertIn("poly_obj_object_id", cm.exception.error_dict)

        obj.poly_obj_content_type = None
        obj.poly_obj_object_id = self.site.pk
        with self.assertRaises(ValidationError) as cm:
            obj.full_clean()
        self.assertIn("poly_obj_content_type", cm.exception.error_dict)

    def test_full_clean_skips_gfk_columns_it_was_told_to_exclude(self):
        """A bad reference is ignored when its backing columns are excluded, as a ModelForm does."""
        site_pk = self.site.pk
        self.site.delete()
        obj = self.model(name="stale-gfk-excluded", poly_obj_content_type=self.site_ot, poly_obj_object_id=site_pk)
        with self.assertRaises(ValidationError):
            obj.full_clean()
        obj.full_clean(exclude=["poly_obj_content_type", "poly_obj_object_id"])

    def test_full_clean_flags_gfk_whose_model_is_not_installed(self):
        """A content type with no installed model is reported on its column, not raised as AttributeError."""
        from django.contrib.contenttypes.models import ContentType
        gone = ContentType.objects.create(app_label="gone_plugin", model="widget")
        # get_for_id() caches the row process-wide; don't let it outlive the test.
        self.addCleanup(ContentType.objects.clear_cache)
        obj = self.model(name="gone-model", poly_obj_content_type=gone, poly_obj_object_id=1)
        with self.assertRaises(ValidationError) as cm:
            obj.full_clean()
        self.assertIn("poly_obj_content_type", cm.exception.error_dict)
        obj.full_clean(exclude=["poly_obj_content_type", "poly_obj_object_id"])

    def test_full_clean_accepts_valid_gfk(self):
        obj = self.model(name="valid-gfk")
        obj.poly_obj = self.site
        obj.full_clean()
        obj.save()
        obj.refresh_from_db()
        self.assertEqual(obj.poly_obj, self.site)

    def test_deleting_referenced_site_leaves_stale_m2m_row_excluded_from_all(self):
        """
        Deleting a Site that is in a polymorphic M2M through table leaves a stale
        row in the through table (no DB FK on object_id), but all() gracefully
        excludes it because the batch-fetch query returns no matching object.
        """
        obj = self.model.objects.create(name="stale-m2m")
        obj.poly_multi.add(self.site)

        site_pk = self.site.pk
        self.site.delete()

        # Verify the stale row persists: the through table has no DB-level FK from
        # object_id to dcim_site, so deleting a Site cannot cascade into the through
        # table.  Fetch the through model directly from the manager to avoid a
        # global app-registry lookup that could resolve to a stale model from a
        # prior test run when using --keepdb.
        manager = obj.poly_multi  # PolymorphicManyToManyManager
        through = manager._get_through_model()
        self.assertTrue(
            through.objects.filter(source_id=obj.pk, object_id=site_pk).exists(),
            "Stale through-table row should remain after target deletion (no DB FK on object_id).",
        )

        # all() skips the stale row — the result list must be empty.
        self.assertEqual(list(obj.poly_multi.all()), [])

    def test_deleting_custom_object_type_drops_db_table_and_deregisters_model(self):
        """
        Deleting a CustomObjectType drops its DB table, drops polymorphic through
        tables, and removes the model from Django's app registry.
        """
        from django.apps import apps as django_apps
        from django.db import connection
        from netbox_custom_objects.constants import APP_LABEL

        main_table = self.cot.get_database_table_name()
        through_table = self.m2m_field.through_table_name
        through_model_name = self.m2m_field.through_model_name
        model_name = self.model.__name__.lower()

        # Create a row so the delete path exercises cascade logic too.
        obj = self.model.objects.create(name="to-be-cascaded")
        obj.poly_multi.add(self.site)

        self.cot.delete()

        with connection.cursor() as cursor:
            existing_tables = connection.introspection.table_names(cursor)

        self.assertNotIn(main_table, existing_tables, "Main DB table should be dropped.")
        self.assertNotIn(through_table, existing_tables, "Through table should be dropped.")

        # Model and through model must be de-registered from the app registry.
        self.assertNotIn(model_name, django_apps.all_models.get(APP_LABEL, {}))
        self.assertNotIn(
            through_model_name.lower(), django_apps.all_models.get(APP_LABEL, {})
        )

    def test_remove_poly_obj_columns_succeeds_with_pending_deferred_triggers(self):
        """
        remove_polymorphic_object_columns() must not raise
        "cannot ALTER TABLE because it has pending trigger events" when the
        source row was deleted inside the same transaction.

        The through-table created by the MULTIOBJECT field has a source_id FK:
            custom_objects_<id>_poly_multi.source_id → custom_objects_<id>.id
            DEFERRABLE INITIALLY DEFERRED (Django's PostgreSQL backend default)

        When a row in custom_objects_<id> is deleted inside a transaction,
        PostgreSQL queues a deferred trigger event associated with the REFERENCED
        table (custom_objects_<id>), not the referencing through-table.  A
        subsequent ALTER TABLE on that same table then fails with:
            "cannot ALTER TABLE … because it has pending trigger events"
        unless SET CONSTRAINTS ALL IMMEDIATE is issued first to flush the queue.

        The fix in remove_polymorphic_object_columns() issues SET CONSTRAINTS ALL
        IMMEDIATE before the first ALTER TABLE, firing the deferred check against
        the through-table.  If the through-table rows were already deleted (as the
        branching revert path does before removing the COT instance), the check
        finds no FK violation and clears the pending event, allowing the ALTER
        TABLE to proceed.
        """
        from django.db import connection, transaction as db_transaction
        from netbox_custom_objects.field_types import FIELD_TYPE_CLASS

        # Create an instance with the MULTIOBJECT populated so the through-table
        # has a row referencing the source.
        obj = self.model.objects.create(name="revert-repro")
        obj.poly_multi.add(self.site)

        main_table = self.model._meta.db_table
        through_table = self.m2m_field.through_table_name

        with db_transaction.atomic():
            with connection.cursor() as cursor:
                # Step 1: delete through-table rows first (mirrors the branching
                # revert path, which cascades child rows before removing the COT
                # instance).
                cursor.execute(
                    f'DELETE FROM "{through_table}" WHERE source_id = %s', [obj.pk]
                )
                # Step 2: delete the COT instance via raw SQL.  Django's FK
                # constraints are DEFERRABLE INITIALLY DEFERRED, so PostgreSQL
                # queues a deferred trigger on custom_objects_X (the referenced
                # table) instead of checking the constraint immediately.
                cursor.execute(f'DELETE FROM "{main_table}" WHERE id = %s', [obj.pk])
            # Step 3: remove the polymorphic OBJECT field columns.  Without the
            # fix, the ALTER TABLE inside remove_polymorphic_object_columns()
            # fails with "cannot ALTER TABLE … because it has pending trigger
            # events".  The fix calls SET CONSTRAINTS ALL IMMEDIATE first, which
            # fires the deferred check (no FK violation since step 1 already
            # deleted the through-table row) and clears the pending event.
            with connection.schema_editor() as editor:
                field_type = FIELD_TYPE_CLASS[self.gfk_field.type]()
                field_type.remove_polymorphic_object_columns(self.gfk_field, self.model, editor)
        # Reaching here without a database exception confirms the fix is effective.


# ---------------------------------------------------------------------------
# Cycle-detection: multi-hop polymorphic cycles
# ---------------------------------------------------------------------------

class PolymorphicCycleDetectionTest(CustomObjectsTestCase, TestCase):
    """
    Verifies that _has_circular_reference detects cycles that pass entirely
    through polymorphic legs.

    Polymorphic fields store their allowed target types on the related_object_types
    M2M rather than the related_object_type FK, so the DFS must explicitly walk
    that M2M to catch multi-hop polymorphic cycles.
    """

    def setUp(self):
        super().setUp()

        self.cot_a = CustomObjectType.objects.create(
            name="CycleA", slug="cycle-a", verbose_name_plural="Cycle As",
        )
        CustomObjectTypeField.objects.create(
            custom_object_type=self.cot_a, name="name", type="text", primary=True, required=True,
        )

        self.cot_b = CustomObjectType.objects.create(
            name="CycleB", slug="cycle-b", verbose_name_plural="Cycle Bs",
        )
        CustomObjectTypeField.objects.create(
            custom_object_type=self.cot_b, name="name", type="text", primary=True, required=True,
        )

        self.cot_c = CustomObjectType.objects.create(
            name="CycleC", slug="cycle-c", verbose_name_plural="Cycle Cs",
        )
        CustomObjectTypeField.objects.create(
            custom_object_type=self.cot_c, name="name", type="text", primary=True, required=True,
        )

        # Generate models so ContentTypes are registered.
        self.cot_a.get_model()
        self.cot_b.get_model()
        self.cot_c.get_model()

        self.ot_a = ObjectType.objects.get_for_model(self.cot_a.get_model())
        self.ot_b = ObjectType.objects.get_for_model(self.cot_b.get_model())
        self.ot_c = ObjectType.objects.get_for_model(self.cot_c.get_model())

    def test_first_poly_edge_is_accepted(self):
        """A →(poly) B: no cycle yet, must succeed."""
        field_a = CustomObjectTypeField.objects.create(
            custom_object_type=self.cot_a,
            name="link_to_b", type="object", is_polymorphic=True,
        )
        try:
            field_a.related_object_types.set([self.ot_b])
        except Exception as exc:
            self.fail(
                f"Adding COT-B as allowed type for COT-A's poly field raised "
                f"{type(exc).__name__}: {exc}."
            )
        self.assertIn(self.ot_b, field_a.related_object_types.all())

    def test_two_hop_polymorphic_cycle_is_detected(self):
        """A →(poly) B →(poly) A must be rejected."""
        field_a = CustomObjectTypeField.objects.create(
            custom_object_type=self.cot_a,
            name="link_to_b", type="object", is_polymorphic=True,
        )
        field_a.related_object_types.set([self.ot_b])  # no cycle yet

        field_b = CustomObjectTypeField.objects.create(
            custom_object_type=self.cot_b,
            name="link_to_a", type="object", is_polymorphic=True,
        )
        with self.assertRaises(ValidationError):
            field_b.related_object_types.set([self.ot_a])  # closes A→B→A cycle

    def test_three_hop_polymorphic_cycle_is_detected(self):
        """A →(poly) B →(poly) C →(poly) A must be rejected."""
        field_a = CustomObjectTypeField.objects.create(
            custom_object_type=self.cot_a,
            name="link_to_b", type="object", is_polymorphic=True,
        )
        field_a.related_object_types.set([self.ot_b])  # no cycle

        field_b = CustomObjectTypeField.objects.create(
            custom_object_type=self.cot_b,
            name="link_to_c", type="object", is_polymorphic=True,
        )
        field_b.related_object_types.set([self.ot_c])  # no cycle

        field_c = CustomObjectTypeField.objects.create(
            custom_object_type=self.cot_c,
            name="link_to_a", type="object", is_polymorphic=True,
        )
        with self.assertRaises(ValidationError):
            field_c.related_object_types.set([self.ot_a])  # closes A→B→C→A cycle

    def test_mixed_leg_cycle_is_detected(self):
        """A →(non-poly) B →(poly) A must be rejected."""
        # Non-polymorphic object field on A pointing to B.
        CustomObjectTypeField.objects.create(
            custom_object_type=self.cot_a,
            name="link_to_b", type="object",
            related_object_type=self.ot_b,
        )

        # Polymorphic object field on B attempting to point back to A.
        field_b = CustomObjectTypeField.objects.create(
            custom_object_type=self.cot_b,
            name="link_to_a", type="object", is_polymorphic=True,
        )
        with self.assertRaises(ValidationError):
            field_b.related_object_types.set([self.ot_a])  # closes A→B→A cycle


# ---------------------------------------------------------------------------
# Polymorphic reverse descriptor tests
# ---------------------------------------------------------------------------

class PolymorphicReverseDescriptorTest(CustomObjectsTestCase, TestCase):
    """Verify that polymorphic GFK and M2M fields with a related_name expose a working
    reverse accessor on target model instances."""

    def setUp(self):
        super().setUp()
        self.site_ot = ObjectType.objects.get(app_label="dcim", model="site")
        self.prefix_ot = ObjectType.objects.get(app_label="ipam", model="prefix")

        self.cot = CustomObjectType.objects.create(
            name="RevTest", slug="rev-test",
            verbose_name_plural="Rev Tests",
        )
        CustomObjectTypeField.objects.create(
            custom_object_type=self.cot,
            name="name", type="text", primary=True, required=True,
        )

    # --- GFK reverse descriptor ---

    def _create_gfk_field(self, related_name="rev_co_gfk"):
        field = CustomObjectTypeField.objects.create(
            custom_object_type=self.cot,
            name="target_obj", label="Target", type="object",
            is_polymorphic=True,
            related_name=related_name,
        )
        field.related_object_types.set([self.site_ot, self.prefix_ot])
        return field

    def test_gfk_reverse_descriptor_set_on_target_class(self):
        """After get_model(), the related_name descriptor must exist on the target class."""
        self._create_gfk_field(related_name="co_gfk_reverse")
        self.cot.get_model()
        self.assertTrue(
            hasattr(Site, "co_gfk_reverse"),
            "Reverse descriptor must be set on Site after get_model()",
        )

    def test_gfk_reverse_descriptor_returns_matching_instances(self):
        """site.related_name.all() returns CO instances whose GFK points at that site."""
        from ipam.models import Prefix
        from ipam.choices import PrefixStatusChoices

        self._create_gfk_field(related_name="co_gfk_rev")
        model = self.cot.get_model()

        site = Site.objects.create(name="RevSite", slug="rev-site")
        prefix = Prefix.objects.create(
            prefix="192.168.0.0/24", status=PrefixStatusChoices.STATUS_ACTIVE
        )

        co_a = model.objects.create(name="co-a")
        co_a.target_obj = site
        co_a.save()

        co_b = model.objects.create(name="co-b")
        co_b.target_obj = prefix
        co_b.save()

        site_results = list(site.co_gfk_rev.all())
        self.assertEqual(len(site_results), 1)
        self.assertEqual(site_results[0].pk, co_a.pk)

        prefix_results = list(prefix.co_gfk_rev.all())
        self.assertEqual(len(prefix_results), 1)
        self.assertEqual(prefix_results[0].pk, co_b.pk)

    def test_gfk_reverse_descriptor_count_and_exists(self):
        """count() and exists() work correctly on the reverse manager."""
        self._create_gfk_field(related_name="co_gfk_ce")
        model = self.cot.get_model()

        site = Site.objects.create(name="CeSite", slug="ce-site")
        self.assertEqual(site.co_gfk_ce.count(), 0)
        self.assertFalse(site.co_gfk_ce.exists())

        co = model.objects.create(name="co-ce")
        co.target_obj = site
        co.save()

        self.assertEqual(site.co_gfk_ce.count(), 1)
        self.assertTrue(site.co_gfk_ce.exists())

    def test_gfk_no_reverse_descriptor_when_related_name_blank(self):
        """When related_name is blank, neither m2m_changed nor get_model() injects anything."""
        field = CustomObjectTypeField.objects.create(
            custom_object_type=self.cot,
            name="anon_obj", type="object", is_polymorphic=True,
        )
        before = set(Site.__dict__.keys())
        field.related_object_types.set([self.site_ot])  # fires m2m_changed
        self.cot.get_model()                             # fires _after_model_generation
        after = set(Site.__dict__.keys())
        self.assertEqual(before, after, "No new attribute should be injected on Site for blank related_name")

    def test_gfk_reverse_descriptor_wired_when_type_added(self):
        """Adding a type to related_object_types after initial wiring injects the descriptor via m2m_changed."""
        from dcim.models import Device
        device_ot = ObjectType.objects.get(app_label="dcim", model="device")

        field = CustomObjectTypeField.objects.create(
            custom_object_type=self.cot,
            name="target_obj", label="Target", type="object",
            is_polymorphic=True,
            related_name="co_gfk_added",
        )
        field.related_object_types.set([self.site_ot])
        self.cot.get_model()
        self.assertFalse(hasattr(Device, "co_gfk_added"), "Device not yet in allowed types")

        field.related_object_types.add(device_ot)  # fires m2m_changed post_add

        self.assertTrue(
            hasattr(Device, "co_gfk_added"),
            "Descriptor must be wired on Device after adding it to related_object_types",
        )

    def test_gfk_reverse_descriptor_unwired_when_type_removed(self):
        """Removing a type from related_object_types removes the descriptor from that class."""
        field = self._create_gfk_field(related_name="co_gfk_removed")
        self.cot.get_model()
        self.assertTrue(hasattr(Site, "co_gfk_removed"), "Descriptor must be present before removal")

        field.related_object_types.remove(self.site_ot)  # fires m2m_changed pre_remove

        self.assertFalse(
            hasattr(Site, "co_gfk_removed"),
            "Descriptor must be removed from Site after removing it from related_object_types",
        )

    def test_gfk_reverse_descriptor_removed_on_field_delete(self):
        """Deleting the field removes the reverse descriptor from target models."""
        field = self._create_gfk_field(related_name="co_gfk_del")
        self.cot.get_model()
        self.assertTrue(hasattr(Site, "co_gfk_del"), "Descriptor must be present before delete")

        field.delete()

        self.assertFalse(
            hasattr(Site, "co_gfk_del"),
            "Reverse descriptor must be removed after field delete",
        )

    # --- M2M reverse descriptor ---

    def _create_m2m_field(self, related_name="rev_co_m2m"):
        field = CustomObjectTypeField.objects.create(
            custom_object_type=self.cot,
            name="target_multi", label="Targets", type="multiobject",
            is_polymorphic=True,
            related_name=related_name,
        )
        field.related_object_types.set([self.site_ot, self.prefix_ot])
        return field

    def test_m2m_reverse_descriptor_set_on_target_class(self):
        """After get_model(), the related_name descriptor must exist on the target class."""
        self._create_m2m_field(related_name="co_m2m_reverse")
        self.cot.get_model()
        self.assertTrue(
            hasattr(Site, "co_m2m_reverse"),
            "Reverse descriptor must be set on Site after get_model()",
        )

    def test_m2m_reverse_descriptor_returns_matching_instances(self):
        """site.related_name.all() returns CO instances whose M2M includes that site."""
        from ipam.models import Prefix
        from ipam.choices import PrefixStatusChoices

        self._create_m2m_field(related_name="co_m2m_rev")
        model = self.cot.get_model()

        site = Site.objects.create(name="M2MSite", slug="m2m-site")
        prefix = Prefix.objects.create(
            prefix="10.1.0.0/24", status=PrefixStatusChoices.STATUS_ACTIVE
        )

        co_a = model.objects.create(name="m2m-a")
        co_a.target_multi.add(site)

        co_b = model.objects.create(name="m2m-b")
        co_b.target_multi.add(prefix)

        co_both = model.objects.create(name="m2m-both")
        co_both.target_multi.add(site, prefix)

        site_results = {obj.pk for obj in site.co_m2m_rev.all()}
        self.assertEqual(site_results, {co_a.pk, co_both.pk})

        prefix_results = {obj.pk for obj in prefix.co_m2m_rev.all()}
        self.assertEqual(prefix_results, {co_b.pk, co_both.pk})

    def test_m2m_reverse_descriptor_count_and_exists(self):
        """count() and exists() work correctly on the M2M reverse manager."""
        self._create_m2m_field(related_name="co_m2m_ce")
        model = self.cot.get_model()

        site = Site.objects.create(name="M2MCeSite", slug="m2m-ce-site")
        self.assertEqual(site.co_m2m_ce.count(), 0)
        self.assertFalse(site.co_m2m_ce.exists())

        co = model.objects.create(name="m2m-ce")
        co.target_multi.add(site)

        self.assertEqual(site.co_m2m_ce.count(), 1)
        self.assertTrue(site.co_m2m_ce.exists())

    def test_m2m_reverse_descriptor_removed_on_field_delete(self):
        """Deleting the field removes the reverse descriptor from target models."""
        field = self._create_m2m_field(related_name="co_m2m_del")
        self.cot.get_model()
        self.assertTrue(hasattr(Site, "co_m2m_del"), "Descriptor must be present before delete")

        field.delete()

        self.assertFalse(
            hasattr(Site, "co_m2m_del"),
            "Reverse descriptor must be removed after field delete",
        )

    def test_m2m_reverse_descriptor_wired_when_type_added(self):
        """Adding a type via m2m_changed post_add wires the M2M reverse descriptor."""
        from dcim.models import Device
        device_ot = ObjectType.objects.get(app_label="dcim", model="device")

        field = CustomObjectTypeField.objects.create(
            custom_object_type=self.cot,
            name="target_multi2", label="Targets", type="multiobject",
            is_polymorphic=True,
            related_name="co_m2m_added",
        )
        field.related_object_types.set([self.site_ot])
        self.cot.get_model()
        self.assertFalse(hasattr(Device, "co_m2m_added"), "Device not yet in allowed types")

        field.related_object_types.add(device_ot)  # fires m2m_changed post_add

        self.assertTrue(
            hasattr(Device, "co_m2m_added"),
            "Descriptor must be wired on Device after adding it to related_object_types",
        )

    def test_gfk_reverse_descriptor_unwired_on_clear(self):
        """Clearing related_object_types via pre_clear removes descriptors from all target classes."""
        field = self._create_gfk_field(related_name="co_gfk_cleared")
        self.cot.get_model()
        self.assertTrue(hasattr(Site, "co_gfk_cleared"))

        field.related_object_types.clear()  # fires m2m_changed pre_clear

        self.assertFalse(
            hasattr(Site, "co_gfk_cleared"),
            "Descriptor must be removed from Site after clear()",
        )

    def test_m2m_reverse_descriptor_unwired_on_clear(self):
        """Clearing related_object_types via pre_clear removes M2M descriptors from all target classes."""
        field = self._create_m2m_field(related_name="co_m2m_cleared")
        self.cot.get_model()
        self.assertTrue(hasattr(Site, "co_m2m_cleared"))

        field.related_object_types.clear()  # fires m2m_changed pre_clear

        self.assertFalse(
            hasattr(Site, "co_m2m_cleared"),
            "Descriptor must be removed from Site after clear()",
        )

    def test_polymorphic_field_full_clean_accepts_related_name(self):
        """full_clean() must not raise when related_name is set on a polymorphic field."""
        field = CustomObjectTypeField(
            custom_object_type=self.cot,
            name="clean_test_obj",
            type="object",
            is_polymorphic=True,
            related_name="co_full_clean_check",
        )
        # Must not raise ValidationError (old guard would reject any related_name here)
        field.full_clean()

    def test_polymorphic_related_name_collision_raises(self):
        """full_clean() must raise when two polymorphic fields share a related_name on the same target type."""
        self._create_gfk_field(related_name="co_collision_name")

        duplicate = CustomObjectTypeField(
            custom_object_type=self.cot,
            name="dup_obj",
            type="object",
            is_polymorphic=True,
            related_name="co_collision_name",
        )
        duplicate.save()
        duplicate.related_object_types.set([self.site_ot])

        from django.core.exceptions import ValidationError as DjangoValidationError
        with self.assertRaises(DjangoValidationError):
            duplicate.full_clean()

    def test_gfk_reverse_descriptor_cleaned_up_on_related_name_rename(self):
        """Renaming related_name removes the old descriptor and leaves the new one wired."""
        field = self._create_gfk_field(related_name="co_gfk_old_name")
        self.cot.get_model()
        self.assertTrue(hasattr(Site, "co_gfk_old_name"), "old descriptor must be present before rename")

        # Re-fetch so from_db() sets _original (mirrors real save-from-UI flow)
        field = CustomObjectTypeField.objects.get(pk=field.pk)
        field.related_name = "co_gfk_new_name"
        field.save()
        self.cot.get_model()  # regenerate model so new descriptor is injected

        self.assertFalse(
            hasattr(Site, "co_gfk_old_name"),
            "old descriptor must be removed from Site after rename",
        )
        self.assertTrue(
            hasattr(Site, "co_gfk_new_name"),
            "new descriptor must be present on Site after rename",
        )

    def test_m2m_reverse_descriptor_cleaned_up_on_related_name_rename(self):
        """Renaming related_name removes the old M2M descriptor and leaves the new one wired."""
        field = self._create_m2m_field(related_name="co_m2m_old_name")
        self.cot.get_model()
        self.assertTrue(hasattr(Site, "co_m2m_old_name"), "old descriptor must be present before rename")

        # Re-fetch so from_db() sets _original (mirrors real save-from-UI flow)
        field = CustomObjectTypeField.objects.get(pk=field.pk)
        field.related_name = "co_m2m_new_name"
        field.save()
        self.cot.get_model()  # regenerate model so new descriptor is injected

        self.assertFalse(
            hasattr(Site, "co_m2m_old_name"),
            "old descriptor must be removed from Site after rename",
        )
        self.assertTrue(
            hasattr(Site, "co_m2m_new_name"),
            "new descriptor must be present on Site after rename",
        )

    def test_related_name_colliding_with_target_model_attribute_raises(self):
        """full_clean() must reject a related_name that clashes with a pre-existing attribute
        on the target model class, to prevent silently clobbering it."""
        from django.core.exceptions import ValidationError as DjangoValidationError

        # Use a controlled sentinel so the test is self-contained regardless of prior
        # test state (Site.__dict__ can be dirty after earlier tests run _wire).
        attr_name = "_co_collision_test_attr"
        sentinel = object()
        setattr(Site, attr_name, sentinel)
        try:
            field = CustomObjectTypeField(
                custom_object_type=self.cot,
                name="collision_obj",
                type="object",
                is_polymorphic=True,
                related_name=attr_name,
            )
            field.save()
            field.related_object_types.set([self.site_ot])

            with self.assertRaises(DjangoValidationError) as cm:
                field.full_clean()
            self.assertIn("related_name", cm.exception.message_dict)
        finally:
            if Site.__dict__.get(attr_name) is sentinel:
                delattr(Site, attr_name)

    def test_wire_skips_setattr_when_related_name_collides_with_target_attribute(self):
        """_wire_polymorphic_reverse_descriptors must not overwrite a pre-existing non-CO
        attribute on the target model class, even if clean() was not called first."""
        attr_name = "_co_wire_collision_test_attr"
        sentinel = object()
        setattr(Site, attr_name, sentinel)
        try:
            field = CustomObjectTypeField(
                custom_object_type=self.cot,
                name="wire_collision_obj",
                type="object",
                is_polymorphic=True,
                related_name=attr_name,
            )
            field.save()
            # set() triggers the m2m_changed signal which calls _wire internally.
            field.related_object_types.set([self.site_ot])

            # The sentinel must still be intact — _wire must have skipped the setattr.
            self.assertIs(
                Site.__dict__.get(attr_name), sentinel,
                "_wire must not overwrite a pre-existing non-CO attribute on the target model",
            )
        finally:
            if Site.__dict__.get(attr_name) is sentinel:
                delattr(Site, attr_name)

    def test_related_name_colliding_with_inherited_method_raises(self):
        """full_clean() must reject a related_name that shadows an inherited method on the
        target model (e.g. 'save'), not just directly-defined attributes.
        Regression for the __dict__.get vs getattr gap reported by Arthur."""
        from django.core.exceptions import ValidationError as DjangoValidationError

        field = CustomObjectTypeField(
            custom_object_type=self.cot,
            name="save_collision_obj",
            type="object",
            is_polymorphic=True,
            related_name="save",  # inherited from Model, not in Site.__dict__
        )
        field.save()
        field.related_object_types.set([self.site_ot])

        with self.assertRaises(DjangoValidationError) as cm:
            field.full_clean()
        self.assertIn("related_name", cm.exception.message_dict)

    def test_wire_skips_setattr_for_inherited_method(self):
        """_wire must not overwrite an inherited method (e.g. 'save') on the target class
        even though it doesn't appear in target_cls.__dict__."""
        original_save = getattr(Site, "save")

        field = CustomObjectTypeField(
            custom_object_type=self.cot,
            name="save_wire_obj",
            type="object",
            is_polymorphic=True,
            related_name="save",
        )
        field.save()
        field.related_object_types.set([self.site_ot])

        # The inherited save method must still be accessible and unchanged.
        self.assertIs(getattr(Site, "save"), original_save,
                      "_wire must not overwrite an inherited method on the target model")

    def test_wire_skips_setattr_when_different_co_field_already_owns_name(self):
        """_wire must not overwrite a descriptor placed by a different CO field."""
        cot_b = CustomObjectType.objects.create(
            name="RevTestB", slug="rev-test-b", verbose_name_plural="Rev Tests B",
        )
        CustomObjectTypeField.objects.create(
            custom_object_type=cot_b, name="name", type="text", primary=True, required=True,
        )

        # Field on COT B wires co_cross_ref onto Site first.
        field_b = CustomObjectTypeField.objects.create(
            custom_object_type=cot_b,
            name="target_cross", type="object", is_polymorphic=True,
            related_name="co_cross_ref",
        )
        field_b.related_object_types.set([self.site_ot])
        cot_b.get_model()
        descriptor_b = Site.__dict__.get("co_cross_ref")
        self.assertIsNotNone(descriptor_b, "COT B descriptor must be wired before the test")

        # Field on COT A also claims co_cross_ref. _wire must skip — B owns it.
        field_a = CustomObjectTypeField(
            custom_object_type=self.cot,
            name="target_cross_a", type="object", is_polymorphic=True,
            related_name="co_cross_ref",
        )
        field_a.save()
        field_a.related_object_types.set([self.site_ot])

        self.assertIs(
            Site.__dict__.get("co_cross_ref"), descriptor_b,
            "COT A's _wire must not overwrite COT B's descriptor",
        )

    def test_cot_delete_unwires_own_descriptor(self):
        """Deleting the source COT removes its fields' reverse accessors, so a later
        field can claim the related_name."""
        self._create_gfk_field(related_name="co_deleted_cot_ref")
        self.cot.get_model()
        self.assertIn("co_deleted_cot_ref", Site.__dict__)

        self.cot.delete()

        self.assertNotIn("co_deleted_cot_ref", Site.__dict__)

    def test_source_model_deleted_returns_empty_queryset(self):
        """Accessing .all() on a stale reverse descriptor returns an empty queryset
        rather than raising DoesNotExist when the source COT has been deleted."""
        from netbox_custom_objects.field_types import PolymorphicObjectReverseManager

        site = Site.objects.create(name="Stale Test Site", slug="stale-test-site")
        nonexistent_cot_pk = 999999
        field = self._create_gfk_field(related_name="co_stale_ref")
        manager = PolymorphicObjectReverseManager(site, nonexistent_cot_pk, field.name)

        qs = manager.all()
        self.assertEqual(qs.count(), 0, ".all() must return empty queryset for deleted COT")
        self.assertFalse(manager.exists())

    def test_source_model_deleted_multiobject_returns_empty_queryset(self):
        """Same as above for the M2M (MultiObject) reverse manager."""
        from netbox_custom_objects.field_types import PolymorphicMultiObjectReverseManager

        site = Site.objects.create(name="Stale M2M Site", slug="stale-m2m-site")
        nonexistent_cot_pk = 999999
        manager = PolymorphicMultiObjectReverseManager(site, nonexistent_cot_pk, "NonExistentThrough")

        qs = manager.all()
        self.assertEqual(qs.count(), 0, ".all() must return empty queryset for deleted COT")
        self.assertFalse(manager.exists())

    def _restore_stale_cache(self, model_entry, through_entry=None):
        """Put the pre-change cache entries back, as a worker that didn't handle the
        field save still holds them. Only cache_timestamp tells it the model is stale."""
        key = (self.cot.pk, None)
        with CustomObjectType._global_lock:
            CustomObjectType._model_cache[key] = model_entry
            if through_entry is not None:
                CustomObjectType._through_model_cache[key] = through_entry

    def test_gfk_accessor_regenerates_stale_cached_model(self):
        """A field change on another worker bumps cache_timestamp but leaves this
        worker's cached model untouched; the accessor must regenerate it."""
        self._create_gfk_field(related_name="co_gfk_stale")
        stale_model = self.cot.get_model()
        stale_entry = CustomObjectType._model_cache[(self.cot.pk, None)]

        site = Site.objects.create(name="Stale GFK Site", slug="stale-gfk-site")
        co = stale_model.objects.create(name="co-stale")
        co.target_obj = site
        co.save()

        CustomObjectTypeField.objects.create(
            custom_object_type=self.cot, name="note", label="Note", type="text",
        )
        self._restore_stale_cache(stale_entry)
        self.assertIs(CustomObjectType.get_cached_model(self.cot.pk), stale_model)

        result = list(site.co_gfk_stale.all())
        self.assertEqual([c.pk for c in result], [co.pk])
        self.assertIsNot(type(result[0]), stale_model)
        self.assertTrue(hasattr(result[0], "note"))
        self.assertIsNot(CustomObjectType.get_cached_model(self.cot.pk), stale_model)

    def test_m2m_accessor_regenerates_stale_cached_model(self):
        """Same as above for the M2M reverse manager, whose through model must also
        come from the regenerated model rather than the stale cache."""
        self._create_m2m_field(related_name="co_m2m_stale")
        stale_model = self.cot.get_model()
        stale_entry = CustomObjectType._model_cache[(self.cot.pk, None)]
        stale_throughs = dict(CustomObjectType._through_model_cache[(self.cot.pk, None)])

        site = Site.objects.create(name="Stale M2M Site", slug="stale-m2m-site")
        co = stale_model.objects.create(name="m2m-stale")
        co.target_multi.add(site)

        CustomObjectTypeField.objects.create(
            custom_object_type=self.cot, name="note", label="Note", type="text",
        )
        self._restore_stale_cache(stale_entry, stale_throughs)
        self.assertIs(CustomObjectType.get_cached_model(self.cot.pk), stale_model)

        result = list(site.co_m2m_stale.all())
        self.assertEqual([c.pk for c in result], [co.pk])
        self.assertIsNot(type(result[0]), stale_model)
        self.assertTrue(hasattr(result[0], "note"))
        self.assertIsNot(CustomObjectType.get_cached_model(self.cot.pk), stale_model)

    def test_inbound_reverse_descriptor_wired_when_target_cot_generated_after_source(self):
        """Regression for startup ordering: if COT A is generated before COT B,
        COT A's _wire skips COT B (model_class() is None at that point).  When
        COT B is later generated its _after_model_generation must wire the
        inbound descriptor from COT A onto the new class."""
        # COT B is the target; its ObjectType must be registered before we
        # create the source field so related_object_types.set() can find it.
        cot_b = CustomObjectType.objects.create(
            name="InboundTarget", slug="inbound-target",
            verbose_name_plural="Inbound Targets",
        )
        CustomObjectTypeField.objects.create(
            custom_object_type=cot_b, name="name", type="text", primary=True, required=True,
        )
        cot_b_ot = cot_b.object_type

        # COT A has a polymorphic GFK field pointing at COT B with a related_name.
        field_a = CustomObjectTypeField.objects.create(
            custom_object_type=self.cot,
            name="target_cot_b", type="object", is_polymorphic=True,
            related_name="co_inbound_ref",
        )
        field_a.related_object_types.set([cot_b_ot])

        # Simulate COT A being generated first — COT B's model class doesn't exist yet.
        # We achieve this by generating COT A's model without COT B's model being cached.
        cot_b_model = cot_b.get_model()

        # After COT B is generated, its _after_model_generation should have wired the
        # inbound descriptor from COT A.
        self.assertTrue(
            hasattr(cot_b_model, "co_inbound_ref"),
            "_after_model_generation must wire inbound reverse descriptors from other COTs",
        )

    def test_cross_type_related_name_collision_poly_vs_nonpoly_raises(self):
        """full_clean() must reject a polymorphic field whose related_name collides
        with a non-polymorphic field on the same target type."""
        from django.core.exceptions import ValidationError as DjangoValidationError

        # Create a non-polymorphic Object field with related_name on Site.
        CustomObjectTypeField.objects.create(
            custom_object_type=self.cot,
            name="np_site_ref",
            type="object",
            is_polymorphic=False,
            related_object_type=self.site_ot,
            related_name="co_cross_type_name",
        )

        # Create a second COT with a polymorphic field claiming the same name.
        cot2 = CustomObjectType.objects.create(
            name="CrossTypeTest", slug="cross-type-test",
            verbose_name_plural="Cross Type Tests",
        )
        CustomObjectTypeField.objects.create(
            custom_object_type=cot2, name="name", type="text", primary=True, required=True,
        )
        poly_field = CustomObjectTypeField(
            custom_object_type=cot2,
            name="poly_site_ref",
            type="object",
            is_polymorphic=True,
            related_name="co_cross_type_name",
        )
        poly_field.save()
        poly_field.related_object_types.set([self.site_ot])

        with self.assertRaises(DjangoValidationError) as cm:
            poly_field.full_clean()
        self.assertIn("related_name", cm.exception.message_dict)

    def test_cross_type_related_name_collision_nonpoly_vs_poly_raises(self):
        """full_clean() must reject a non-polymorphic field whose related_name collides
        with a polymorphic field on the same target type."""
        from django.core.exceptions import ValidationError as DjangoValidationError

        # Create a polymorphic field on a second COT first.
        cot2 = CustomObjectType.objects.create(
            name="CrossTypeTest2", slug="cross-type-test-2",
            verbose_name_plural="Cross Type Tests 2",
        )
        CustomObjectTypeField.objects.create(
            custom_object_type=cot2, name="name", type="text", primary=True, required=True,
        )
        poly_field = CustomObjectTypeField.objects.create(
            custom_object_type=cot2,
            name="poly_site_ref2",
            type="object",
            is_polymorphic=True,
            related_name="co_cross_type_name2",
        )
        poly_field.related_object_types.set([self.site_ot])

        # Now try to create a non-polymorphic field on self.cot with the same related_name.
        np_field = CustomObjectTypeField(
            custom_object_type=self.cot,
            name="np_site_ref2",
            type="object",
            is_polymorphic=False,
            related_object_type=self.site_ot,
            related_name="co_cross_type_name2",
        )

        with self.assertRaises(DjangoValidationError) as cm:
            np_field.full_clean()
        self.assertIn("related_name", cm.exception.message_dict)

    def test_unwire_does_not_remove_descriptor_owned_by_different_field(self):
        """Deleting a field that was blocked from wiring must not remove the descriptor
        that a different, still-active field owns at the same related_name."""
        from netbox_custom_objects.models import _unwire_polymorphic_reverse_descriptors

        # COT B wires co_shared_ref onto Site first.
        cot_b = CustomObjectType.objects.create(
            name="UnwireTestB", slug="unwire-test-b",
            verbose_name_plural="Unwire Tests B",
        )
        CustomObjectTypeField.objects.create(
            custom_object_type=cot_b, name="name", type="text", primary=True, required=True,
        )
        field_b = CustomObjectTypeField.objects.create(
            custom_object_type=cot_b,
            name="target_unwire", type="object", is_polymorphic=True,
            related_name="co_shared_ref",
        )
        field_b.related_object_types.set([self.site_ot])
        cot_b.get_model()
        descriptor_b = Site.__dict__.get("co_shared_ref")
        self.assertIsNotNone(descriptor_b, "COT B's descriptor must be wired first")

        # COT A also claims co_shared_ref — _wire blocks it, so field_a is in the DB
        # but its descriptor was never placed on Site.
        field_a = CustomObjectTypeField.objects.create(
            custom_object_type=self.cot,
            name="target_unwire_a", type="object", is_polymorphic=True,
            related_name="co_shared_ref",
        )
        field_a.related_object_types.set([self.site_ot])

        # Deleting field_a must not touch the descriptor owned by field_b.
        _unwire_polymorphic_reverse_descriptors(field_a)
        self.assertIs(
            Site.__dict__.get("co_shared_ref"), descriptor_b,
            "_unwire must not remove a descriptor owned by a different CO field",
        )
