"""
Tests for how custom objects react to changes in their choice sets.
"""
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

import netbox_custom_objects as nco
import netbox_custom_objects.models as nco_models

from extras.models import CustomFieldChoiceSet
from ..base import CustomObjectsTestCase, create_token


class ChoiceSetCacheInvalidationTestCase(CustomObjectsTestCase, TestCase):
    """Editing a CustomFieldChoiceSet's values must invalidate any COT model
    that references it via a select/multiselect field, since those fields'
    choices are baked into the generated model at cache time."""

    def setUp(self):
        super().setUp()
        self.choice_set = self.create_choice_set(
            name="CacheInvalidationChoices",
            extra_choices=[["one", "One"], ["two", "Two"], ["three", "Three"]],
        )

    def test_multiselect_accepts_value_added_after_model_was_cached(self):
        cot = self.create_custom_object_type(name="MultiSelectCache", slug="multiselect-cache")
        self.create_custom_object_type_field(cot, name="name", type="text", primary=True, required=True)
        self.create_custom_object_type_field(cot, name="opts", type="multiselect", choice_set=self.choice_set)

        model = cot.get_model()
        obj = model.objects.create(name="obj-1", opts=["one", "two"])

        self.choice_set.extra_choices = [
            ["one", "One"], ["two", "Two"], ["three", "Three"], ["four", "Four"],
        ]
        self.choice_set.save()

        cot.refresh_from_db()
        fresh_model = cot.get_model()
        self.assertIsNot(
            fresh_model, model,
            "get_model() must regenerate after the choice set it depends on changes",
        )

        fresh_obj = fresh_model.objects.get(pk=obj.pk)
        fresh_obj.opts = ["one", "four"]
        fresh_obj.full_clean()
        fresh_obj.save()
        fresh_obj.refresh_from_db()
        self.assertEqual(fresh_obj.opts, ["one", "four"])

    def test_select_accepts_value_added_after_model_was_cached(self):
        cot = self.create_custom_object_type(name="SelectCache", slug="select-cache")
        self.create_custom_object_type_field(cot, name="name", type="text", primary=True, required=True)
        self.create_custom_object_type_field(cot, name="opt", type="select", choice_set=self.choice_set)

        model = cot.get_model()
        obj = model.objects.create(name="obj-1", opt="one")

        self.choice_set.extra_choices = [
            ["one", "One"], ["two", "Two"], ["three", "Three"], ["four", "Four"],
        ]
        self.choice_set.save()

        cot.refresh_from_db()
        fresh_model = cot.get_model()
        self.assertIsNot(fresh_model, model)

        fresh_obj = fresh_model.objects.get(pk=obj.pk)
        fresh_obj.opt = "four"
        fresh_obj.full_clean()
        fresh_obj.save()
        fresh_obj.refresh_from_db()
        self.assertEqual(fresh_obj.opt, "four")

    def test_unrelated_cot_is_not_invalidated(self):
        """Only COTs actually referencing the edited choice set should have
        their cache_timestamp bumped."""
        other_cot = self.create_custom_object_type(name="Unrelated", slug="unrelated-cot")
        self.create_custom_object_type_field(other_cot, name="name", type="text", primary=True, required=True)
        other_cot.get_model()
        timestamp_before = other_cot.cache_timestamp

        self.choice_set.extra_choices = [["one", "One"], ["two", "Two"], ["three", "Three"], ["four", "Four"]]
        self.choice_set.save()

        other_cot.refresh_from_db()
        self.assertEqual(other_cot.cache_timestamp, timestamp_before)


class ChoiceSetRemovedChoiceTestCase(CustomObjectsTestCase, TestCase):
    """A choice set can't drop a choice that custom objects still use."""

    def setUp(self):
        super().setUp()
        self.choice_set = self.create_choice_set(
            name="RemovalChoices",
            extra_choices=[["one", "One"], ["two", "Two"], ["three", "Three"]],
        )
        self.cot = self.create_custom_object_type(name="RemovalCheck", slug="removal-check")
        self.create_custom_object_type_field(self.cot, name="name", type="text", primary=True, required=True)
        self.create_custom_object_type_field(self.cot, name="opt", type="select", choice_set=self.choice_set)
        self.create_custom_object_type_field(self.cot, name="opts", type="multiselect", choice_set=self.choice_set)
        self.model = self.cot.get_model()

    def _remove(self, *values):
        """Return the choice set, freshly loaded, with *values* removed from its extra choices."""
        choice_set = CustomFieldChoiceSet.objects.get(pk=self.choice_set.pk)
        choice_set.extra_choices = [c for c in choice_set.extra_choices if c[0] not in values]
        return choice_set

    def test_removing_choice_used_by_select_field_is_rejected(self):
        self.model.objects.create(name="obj", opt="three")
        with self.assertRaisesMessage(ValidationError, "Cannot remove choice three"):
            self._remove("three").full_clean()

    def test_removing_choice_used_by_multiselect_field_is_rejected(self):
        self.model.objects.create(name="obj", opts=["one", "three"])
        with self.assertRaisesMessage(ValidationError, "Cannot remove choice three"):
            self._remove("three").full_clean()

    def test_removing_unused_choice_is_allowed(self):
        self.model.objects.create(name="obj", opt="one", opts=["one", "two"])
        choice_set = self._remove("three")
        choice_set.full_clean()
        choice_set.save()
        self.assertEqual([c[0] for c in CustomFieldChoiceSet.objects.get(pk=self.choice_set.pk).extra_choices],
                         ["one", "two"])

    def test_removing_used_extra_choice_still_in_base_choices_is_allowed(self):
        self.choice_set.base_choices = "ISO_3166"
        self.choice_set.extra_choices = [*self.choice_set.extra_choices, ["US", "United States"]]
        self.choice_set.save()
        self.cot.get_model().objects.create(name="obj", opt="US", opts=["US"])
        choice_set = self._remove("US")
        choice_set.full_clean()
        choice_set.save()
        self.assertNotIn("US", [c[0] for c in CustomFieldChoiceSet.objects.get(pk=self.choice_set.pk).extra_choices])

    def test_patch_installed_twice_checks_once(self):
        nco._patch_choice_set_clean()
        nco._patch_choice_set_clean()
        with patch.object(nco_models, "check_removed_choices") as check:
            self._remove("three").full_clean()
        check.assert_called_once()

    def test_removing_used_choice_via_api_is_rejected(self):
        self.model.objects.create(name="obj", opt="three")
        self.user.is_superuser = True
        self.user.save()
        url = reverse("extras-api:customfieldchoiceset-detail", kwargs={"pk": self.choice_set.pk})
        response = APIClient().patch(
            url, {"extra_choices": [["one", "One"], ["two", "Two"]]}, format="json",
            HTTP_AUTHORIZATION=f"Token {create_token(self.user)}",
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("Cannot remove choice three", response.content.decode())
        self.choice_set.refresh_from_db()
        self.assertEqual(len(self.choice_set.extra_choices), 3)
