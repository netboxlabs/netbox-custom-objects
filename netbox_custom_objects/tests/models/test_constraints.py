"""
Tests for the CustomObjectTypeConstraint model.
"""
from datetime import date
from unittest.mock import PropertyMock, patch

from django.apps import apps as django_apps
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import TestCase

import netbox_custom_objects as nco

from dcim.models import Site
from netbox_custom_objects.models import CustomObjectTypeConstraint, CustomObjectTypeField
from ..base import CustomObjectsTestCase


class CustomObjectTypeConstraintTestCase(CustomObjectsTestCase, TestCase):
    """Compound unique constraints on a custom object type, enforced by the database."""

    def setUp(self):
        super().setUp()
        self.cot = self.create_custom_object_type(name="Asset", slug="assets")
        self.vendor = self.create_custom_object_type_field(self.cot, name="vendor", type="text", primary=True)
        self.serial = self.create_custom_object_type_field(self.cot, name="serial", type="text")

    def _constraint(self, fields=None, **kwargs):
        """Validate and save a constraint over *fields* (default: vendor and serial)."""
        fields = fields or [self.vendor, self.serial]
        constraint = CustomObjectTypeConstraint(
            custom_object_type=self.cot,
            name=kwargs.pop("name", "vendor_serial"),
            field_schema_ids=[f.schema_id for f in fields],
            **kwargs,
        )
        constraint.full_clean()
        constraint.save()
        return constraint

    def _model(self):
        self.cot.refresh_from_db()
        return self.cot.get_model()

    def _db_constraint_names(self):
        with connection.cursor() as cursor:
            return set(connection.introspection.get_constraints(cursor, self.cot.get_database_table_name()))

    def _assert_duplicate_rejected(self, model, **values):
        """A duplicate fails full_clean() and, if saved anyway, the database."""
        with self.assertRaises(ValidationError):
            model(**values).full_clean()
        with self.assertRaises(IntegrityError), transaction.atomic():
            model.objects.create(**values)

    def test_passes_model_system_checks(self):
        from django.core.checks.model_checks import check_all_models

        constraint = self._constraint()
        self._model()
        # Include generated models, as outside the test runner.
        with patch.object(nco, '_app_ready', True), patch.object(
            nco.CustomObjectsPluginConfig, 'should_skip_dynamic_model_creation', return_value=False
        ):
            django_apps.get_models.cache_clear()
            errors = [e for e in check_all_models(None) if constraint.db_name in e.msg]
        django_apps.get_models.cache_clear()
        self.assertEqual(errors, [])

    def test_rejects_duplicate_combination(self):
        constraint = self._constraint()
        self.assertIn(constraint.db_name, self._db_constraint_names())
        model = self._model()
        model.objects.create(vendor="acme", serial="1")

        self._assert_duplicate_rejected(model, vendor="acme", serial="1")
        # Each value alone may repeat.
        model(vendor="acme", serial="2").full_clean()
        model(vendor="other", serial="1").full_clean()

    def test_case_insensitive(self):
        self._constraint(case_insensitive=True)
        model = self._model()
        model.objects.create(vendor="Acme", serial="ABC")
        self._assert_duplicate_rejected(model, vendor="acme", serial="abc")

    def test_single_field_case_insensitive(self):
        self._constraint(fields=[self.vendor], case_insensitive=True)
        model = self._model()
        model.objects.create(vendor="Acme")
        self._assert_duplicate_rejected(model, vendor="ACME")

    def test_nulls_distinct(self):
        self._constraint()
        model = self._model()
        model.objects.create(vendor="acme")
        model.objects.create(vendor="acme")  # empty serials never conflict

    def test_nulls_not_distinct(self):
        # PostgreSQL < 15 would silently skip the database constraint.
        # A property on Django 5.2, a class attribute on 6.x; patch the class to cover both.
        with patch.object(
            type(connection.features), "supports_nulls_distinct_unique_constraints",
            new_callable=PropertyMock, return_value=False,
        ):
            with self.assertRaisesMessage(ValidationError, "PostgreSQL 15"):
                self._constraint(nulls_distinct=False)
        self._constraint(nulls_distinct=False)
        model = self._model()
        model.objects.create(vendor="acme")
        self._assert_duplicate_rejected(model, vendor="acme")

    @staticmethod
    def _predicate(field, lookup, value):
        return {"field": field.schema_id, "lookup": lookup, "value": value}

    def test_condition_on_empty_object_field(self):
        # A serial unique only among objects with no holder.
        holder = self.create_custom_object_type_field(
            self.cot, name="holder", label="Holder", type="object", related_object_type=self.get_site_object_type()
        )
        model = self._model()
        site = Site.objects.create(name="Site 1", slug="site-1")
        # Duplicates the condition excludes don't block the constraint.
        model.objects.create(vendor="a", serial="1", holder=site)
        model.objects.create(vendor="b", serial="1", holder=site)

        constraint = self._constraint(fields=[self.serial], condition=[self._predicate(holder, "isnull", True)])
        self.assertEqual(constraint.get_condition_display(), "Holder is empty")
        model = self._model()
        model.objects.create(vendor="c", serial="1")
        self._assert_duplicate_rejected(model, vendor="d", serial="1")
        model(vendor="d", serial="1", holder=site).full_clean()

    def test_condition_lookups(self):
        status = self.create_custom_object_type_field(self.cot, name="status", label="Status", type="text")
        # condition, statuses it constrains, statuses it leaves free, display
        cases = {
            "exact": ("exact", "active", {"active"}, {"retired", None}, "Status equals active"),
            "n": ("n", "retired", {"active", None, ""}, {"retired"}, "Status does not equal retired (or is empty)"),
            "in": ("in", ["active", "planned"], {"active", "planned"}, {"retired", None},
                   "Status is one of active, planned"),
            # A blank text value can be stored as NULL or ''; both are empty.
            "isnull": ("isnull", True, {None, ""}, {"active"}, "Status is empty"),
            "not isnull": ("isnull", False, {"active"}, {None, ""}, "Status is not empty"),
        }
        for label, (lookup, value, constrained, free, display) in cases.items():
            with self.subTest(label):
                constraint = self._constraint(
                    fields=[self.serial], name="by_status", condition=[self._predicate(status, lookup, value)]
                )
                self.assertEqual(constraint.get_condition_display(), display)
                model = self._model()
                for st in constrained:
                    model.objects.create(serial=f"c-{st}", status=st)
                    self._assert_duplicate_rejected(model, serial=f"c-{st}", status=st)
                for st in free:
                    model.objects.create(serial=f"f-{st}", status=st)
                    model.objects.create(serial=f"f-{st}", status=st)
                constraint.delete()
                model.objects.all().delete()

    def test_polymorphic_object_field(self):
        poly = self.create_polymorphic_field(self.cot, [self.get_site_object_type()], name="location")
        self._constraint(fields=[self.vendor, poly])
        model = self._model()
        site = Site.objects.create(name="Site 1", slug="site-1")
        model.objects.create(vendor="acme", location=site)
        with self.assertRaises(IntegrityError), transaction.atomic():
            model.objects.create(vendor="acme", location=site)
        model.objects.create(vendor="other", location=site)

    def test_existing_duplicates_block_constraint(self):
        model = self._model()
        model.objects.create(vendor="acme", serial="1")
        model.objects.create(vendor="acme", serial="1")
        with self.assertRaisesMessage(ValidationError, "already have duplicate values"):
            self._constraint()
        self.assertFalse(CustomObjectTypeConstraint.objects.exists())

    def test_validation(self):
        choice_set = self.create_choice_set()
        tags = self.create_custom_object_type_field(
            self.cot, name="tags", type="multiselect", choice_set=choice_set
        )
        count = self.create_custom_object_type_field(self.cot, name="count", type="integer")
        color = self.create_custom_object_type_field(
            self.cot, name="color", type="select", choice_set=choice_set
        )
        site = self.create_custom_object_type_field(
            self.cot, name="site", type="object", related_object_type=self.get_site_object_type()
        )
        shade = self.create_custom_object_type_field(self.cot, name="shade", type="color")
        mac = self.create_custom_object_type_field(self.cot, name="mac", type="mac_address")
        self._constraint()
        constraint = self._constraint(
            name="vendor_mac", fields=[self.vendor, mac], condition=[self._predicate(mac, "exact", "00-1a-2b-3c-4d-5e")]
        )
        # MAC addresses are stored the way the field shows them.
        self.assertEqual(constraint.condition[0]["value"], "00:1A:2B:3C:4D:5E")
        constraint.condition = [self._predicate(mac, "in", ["001a.2b3c.4d5e", "aa:bb:cc:dd:ee:ff"])]
        constraint.full_clean()
        self.assertEqual(constraint.condition[0]["value"], ["00:1A:2B:3C:4D:5E", "AA:BB:CC:DD:EE:FF"])
        # Color fields can be constrained and compared by value.
        self._constraint(
            name="vendor_shade", fields=[self.vendor, shade], condition=[self._predicate(shade, "exact", "ff0000")]
        )
        cases = {
            "one field": dict(fields=[self.vendor]),
            "repeated field": dict(fields=[self.vendor, self.vendor]),
            "ineligible type": dict(fields=[self.vendor, tags]),
            "case-insensitive without text": dict(fields=[count], case_insensitive=True),
            "same fields as another constraint": dict(fields=[self.serial, self.vendor]),
            "same MAC condition, spelled differently": dict(
                fields=[self.vendor, mac], condition=[self._predicate(mac, "exact", "001a.2b3c.4d5e")]
            ),
            "condition not a list": dict(condition={"field": self.vendor.schema_id, "lookup": "isnull"}),
            "condition without lookup": dict(condition=[{"field": self.vendor.schema_id}]),
            "condition extra key": dict(condition=[{**self._predicate(self.vendor, "isnull", True), "not": True}]),
            "condition unknown field": dict(condition=[{"field": 999, "lookup": "isnull", "value": True}]),
            "condition ineligible field": dict(condition=[self._predicate(tags, "exact", "choice1")]),
            "condition unknown lookup": dict(condition=[self._predicate(self.vendor, "contains", "a")]),
            "isnull without a boolean": dict(condition=[self._predicate(self.vendor, "isnull", "yes")]),
            "in without a list": dict(condition=[self._predicate(self.vendor, "in", "a")]),
            "in with an empty list": dict(condition=[self._predicate(self.vendor, "in", [])]),
            "compare to null": dict(condition=[self._predicate(self.vendor, "exact", None)]),
            "value of the wrong type": dict(condition=[self._predicate(count, "exact", "many")]),
            "value not a choice": dict(condition=[self._predicate(color, "exact", "nope")]),
            "value not a color": dict(condition=[self._predicate(shade, "exact", "#FF0000")]),
            "value not a MAC address": dict(condition=[self._predicate(mac, "exact", "00:1a")]),
            "object field compared by value": dict(condition=[self._predicate(site, "exact", 1)]),
        }
        for label, kwargs in cases.items():
            with self.subTest(label), self.assertRaises(ValidationError):
                self._constraint(name="invalid", **kwargs)
        with self.subTest("unknown field"), self.assertRaises(ValidationError):
            CustomObjectTypeConstraint(
                custom_object_type=self.cot, name="invalid",
                field_schema_ids=[self.vendor.schema_id, 999],
            ).full_clean()

    def test_rename_field_keeps_constraint(self):
        constraint = self._constraint()
        serial = CustomObjectTypeField.objects.get(pk=self.serial.pk)
        serial.name = "serial_number"
        serial.save()

        self.assertEqual([f.name for f in constraint.get_fields()], ["vendor", "serial_number"])
        model = self._model()
        model.objects.create(vendor="acme", serial_number="1")
        self._assert_duplicate_rejected(model, vendor="acme", serial_number="1")

    def test_type_change_rejected(self):
        status = self.create_custom_object_type_field(self.cot, name="status", type="text")
        self._constraint(condition=[self._predicate(status, "exact", "active")])
        for field in (self.serial, status):
            with self.subTest(field.name):
                field = CustomObjectTypeField.objects.get(pk=field.pk)
                field.type = "longtext"
                with self.assertRaisesMessage(ValidationError, "vendor_serial"):
                    field.full_clean()

    def _check_constraint(self, rule, name="check", **kwargs):
        """Validate and save a check constraint with *rule*."""
        constraint = CustomObjectTypeConstraint(
            custom_object_type=self.cot, name=name, type="check", rule=rule, **kwargs
        )
        constraint.full_clean()
        constraint.save()
        return constraint

    def _assert_rule_broken(self, model, message=None, **values):
        """An object breaking a check constraint fails full_clean() and, if saved anyway, the database."""
        with self.assertRaises(ValidationError) as cm:
            model(**values).full_clean()
        if message:
            self.assertIn(message, cm.exception.messages)
        with self.assertRaises(IntegrityError), transaction.atomic():
            model.objects.create(**values)

    def _date_fields(self):
        start = self.create_custom_object_type_field(self.cot, name="start_date", label="Start date", type="date")
        end = self.create_custom_object_type_field(self.cot, name="end_date", label="End date", type="date")
        return start, end

    def test_check_compares_fields(self):
        start, end = self._date_fields()
        constraint = self._check_constraint([self._predicate(end, "gt", {"field": start.schema_id})])
        self.assertIn(constraint.db_name, self._db_constraint_names())
        self.assertTrue(constraint.db_name.endswith("_check"))
        self.assertEqual(constraint.get_rule_display(), "End date is greater than Start date")
        model = self._model()

        model.objects.create(vendor="ok", start_date=date(2026, 1, 1), end_date=date(2026, 2, 1))
        # A comparison passes when either value is empty.
        model.objects.create(vendor="no end", start_date=date(2026, 1, 1))
        model.objects.create(vendor="no start", end_date=date(2026, 1, 1))
        self._assert_rule_broken(
            model, "This object must satisfy: End date is greater than Start date.",
            vendor="bad", start_date=date(2026, 2, 1), end_date=date(2026, 1, 1),
        )
        self._assert_rule_broken(model, vendor="same", start_date=date(2026, 1, 1), end_date=date(2026, 1, 1))

    def test_check_with_condition(self):
        kind = self.create_custom_object_type_field(self.cot, name="kind", label="Kind", type="text")
        bandwidth = self.create_custom_object_type_field(
            self.cot, name="bandwidth_mbps", label="Bandwidth", type="integer"
        )
        self._check_constraint(
            [self._predicate(bandwidth, "gt", 0), self._predicate(bandwidth, "lte", 100000)],
            condition=[self._predicate(kind, "exact", "wan")],
            error_message="WAN links need a bandwidth.",
        )
        model = self._model()
        model.objects.create(vendor="wan", kind="wan", bandwidth_mbps=100)
        model.objects.create(vendor="lan", kind="lan", bandwidth_mbps=0)
        model.objects.create(vendor="unset", kind="wan")
        self._assert_rule_broken(model, "WAN links need a bandwidth.", vendor="zero", kind="wan", bandwidth_mbps=0)
        self._assert_rule_broken(model, vendor="huge", kind="wan", bandwidth_mbps=100001)

        default = CustomObjectTypeConstraint(
            custom_object_type=self.cot, type="check", rule=[self._predicate(bandwidth, "gt", 0)],
            condition=[self._predicate(kind, "exact", "wan")],
        )
        self.assertEqual(
            default.get_default_error_message(),
            "When Kind equals wan, this object must satisfy: Bandwidth is greater than 0.",
        )

    def test_check_rule_requires_value(self):
        # "Is not empty" is how a rule requires a value.
        kind = self.create_custom_object_type_field(self.cot, name="kind", type="text")
        self._check_constraint([self._predicate(self.serial, "isnull", False)], condition=[
            self._predicate(kind, "exact", "wan"),
        ])
        model = self._model()
        model.objects.create(vendor="lan", kind="lan")
        model.objects.create(vendor="wan", kind="wan", serial="1")
        self._assert_rule_broken(model, vendor="blank", kind="wan", serial="")
        self._assert_rule_broken(model, vendor="null", kind="wan")

    def test_check_compares_mac_fields(self):
        mac = self.create_custom_object_type_field(self.cot, name="mac", type="mac_address")
        peer_mac = self.create_custom_object_type_field(self.cot, name="peer_mac", type="mac_address")
        self._check_constraint([self._predicate(mac, "n", {"field": peer_mac.schema_id})])
        model = self._model()
        model.objects.create(vendor="ok", mac="00:1a:2b:3c:4d:5e", peer_mac="aa:bb:cc:dd:ee:ff")
        self._assert_rule_broken(model, vendor="same", mac="00:1a:2b:3c:4d:5e", peer_mac="00-1A-2B-3C-4D-5E")

    def test_check_blocked_by_existing_objects(self):
        start, end = self._date_fields()
        model = self._model()
        model.objects.create(vendor="bad", start_date=date(2026, 2, 1), end_date=date(2026, 1, 1))
        with self.assertRaisesMessage(ValidationError, "already break this rule"):
            self._check_constraint([self._predicate(end, "gte", {"field": start.schema_id})])
        self.assertFalse(CustomObjectTypeConstraint.objects.exists())

    def test_check_validation(self):
        start, end = self._date_fields()
        count = self.create_custom_object_type_field(self.cot, name="count", type="integer")
        shade = self.create_custom_object_type_field(self.cot, name="shade", type="color")
        mac = self.create_custom_object_type_field(self.cot, name="mac", type="mac_address")
        rule = [self._predicate(end, "gt", {"field": start.schema_id})]
        cases = {
            "fields": ({"field_schema_ids": [self.vendor.schema_id], "rule": rule}, "field_schema_ids"),
            "case-insensitive": ({"case_insensitive": True, "rule": rule}, "case_insensitive"),
            "nulls not distinct": ({"nulls_distinct": False, "rule": rule}, "nulls_distinct"),
            "no rule": ({"rule": []}, "rule"),
            "greater than on text": ({"rule": [self._predicate(self.serial, "gt", "a")]}, "rule"),
            "kinds differ": ({"rule": [self._predicate(end, "gt", {"field": count.schema_id})]}, "rule"),
            "compared with itself": ({"rule": [self._predicate(end, "gt", {"field": end.schema_id})]}, "rule"),
            "MAC compared with a color": (
                {"rule": [self._predicate(mac, "n", {"field": shade.schema_id})]}, "rule"
            ),
            "in with a field": ({"rule": [self._predicate(end, "in", {"field": start.schema_id})]}, "rule"),
            "unknown field": ({"rule": [self._predicate(end, "gt", {"field": 999})]}, "rule"),
            # Only an ID refers to a field; true would otherwise match schema ID 1.
            "a list as the compared field": (
                {"rule": [self._predicate(end, "gt", {"field": [start.schema_id]})]}, "rule"
            ),
            "true as the compared field": ({"rule": [self._predicate(end, "gt", {"field": True})]}, "rule"),
            "a name as the field": ({"rule": [{"field": "end", "lookup": "isnull", "value": True}]}, "rule"),
            "bad value": ({"rule": [self._predicate(count, "gt", "many")]}, "rule"),
            # "Is empty" tests for blank text.
            "blank value": ({"rule": [self._predicate(self.serial, "exact", "")]}, "rule"),
            "blank in a list": ({"rule": [self._predicate(self.serial, "in", ["a", ""])]}, "rule"),
        }
        for label, (kwargs, key) in cases.items():
            with self.subTest(label):
                constraint = CustomObjectTypeConstraint(
                    custom_object_type=self.cot, name="c", type="check", **kwargs
                )
                with self.assertRaises(ValidationError) as cm:
                    constraint.full_clean()
                self.assertIn(key, cm.exception.message_dict)

        unique_cases = {
            "unique without fields": ({"field_schema_ids": []}, "field_schema_ids"),
            "unique with a rule": ({"rule": rule}, "rule"),
            "unique with an error message": ({"error_message": "No"}, "error_message"),
        }
        for label, (kwargs, key) in unique_cases.items():
            with self.subTest(label):
                kwargs = {"field_schema_ids": [self.vendor.schema_id, self.serial.schema_id], **kwargs}
                constraint = CustomObjectTypeConstraint(custom_object_type=self.cot, name="c", **kwargs)
                with self.assertRaises(ValidationError) as cm:
                    constraint.full_clean()
                self.assertIn(key, cm.exception.message_dict)

        # A unique constraint's condition can compare too.
        self._constraint(condition=[self._predicate(count, "gte", 1)])

        # A field deleted after the fields were validated.
        constraint = CustomObjectTypeConstraint(custom_object_type=self.cot, name="gone", type="check", rule=rule)
        with patch.object(CustomObjectTypeConstraint, "to_model_constraint", return_value=None):
            with self.assertRaisesMessage(ValidationError, "A field this constraint uses no longer exists."):
                constraint.full_clean()

    def test_condition_compares_fields(self):
        part = self.create_custom_object_type_field(self.cot, name="part", type="text")
        # Vendor unique among objects whose serial matches their part.
        self._constraint(
            fields=[self.vendor], name="by_match",
            condition=[self._predicate(self.serial, "exact", {"field": part.schema_id})],
        )
        model = self._model()
        model.objects.create(vendor="acme", serial="x", part="x")
        self._assert_duplicate_rejected(model, vendor="acme", serial="y", part="y")
        model.objects.create(vendor="acme", serial="x", part="z")
        # Blank text is empty, and a comparison with an empty value doesn't match.
        for _ in range(2):
            model(vendor="blank", serial="", part="").full_clean()
            model.objects.create(vendor="blank", serial="", part="")

    def test_field_delete_deletes_constraint(self):
        status = self.create_custom_object_type_field(self.cot, name="status", type="text")
        start, end = self._date_fields()
        constraint = self._constraint()
        conditional = self._constraint(
            fields=[self.vendor], name="by_status", condition=[self._predicate(status, "exact", "active")]
        )
        # The start date appears only as the value the rule compares with.
        check = self._check_constraint([self._predicate(end, "gt", {"field": start.schema_id})])
        CustomObjectTypeField.objects.get(pk=self.serial.pk).delete()
        CustomObjectTypeField.objects.get(pk=status.pk).delete()
        CustomObjectTypeField.objects.get(pk=start.pk).delete()

        for c in (constraint, conditional, check):
            self.assertFalse(CustomObjectTypeConstraint.objects.filter(pk=c.pk).exists())
            self.assertNotIn(c.db_name, self._db_constraint_names())
        self.assertEqual(self._model()._meta.constraints, [])

    def test_delete_constraint(self):
        constraint = self._constraint()
        model = self._model()
        model.objects.create(vendor="acme", serial="1")

        constraint.delete()
        self.assertNotIn(constraint.db_name, self._db_constraint_names())
        model = self._model()
        model(vendor="acme", serial="1").full_clean()
        model.objects.create(vendor="acme", serial="1")

    def test_edit_constraint(self):
        constraint = self._constraint()
        old_db_name = constraint.db_name
        constraint.name = "renamed"
        constraint.case_insensitive = True
        constraint.full_clean()
        constraint.save()

        names = self._db_constraint_names()
        self.assertNotIn(old_db_name, names)
        self.assertIn(constraint.db_name, names)
        model = self._model()
        model.objects.create(vendor="acme", serial="X")
        self._assert_duplicate_rejected(model, vendor="ACME", serial="x")

    def test_type_change_is_a_definition_change(self):
        # A changed definition makes clean() re-check existing objects.
        constraint = self._constraint()
        self.assertFalse(constraint._definition_changed())
        constraint.type = "other"
        self.assertTrue(constraint._definition_changed())
        constraint.type = "unique"
        constraint.condition = [self._predicate(self.vendor, "isnull", False)]
        self.assertTrue(constraint._definition_changed())

    def test_save_is_idempotent(self):
        # Branch merges replay a constraint's save() where it may already exist.
        constraint = self._constraint()
        constraint.save()
        self.assertIn(constraint.db_name, self._db_constraint_names())
