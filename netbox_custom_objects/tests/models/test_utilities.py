"""
Tests for the plugin's utility functions.
"""

from django.test import TestCase


from netbox_custom_objects.utilities import extract_cot_id_from_model_name


class ExtractCotIdFromModelNameTestCase(TestCase):
    """Unit tests for extract_cot_id_from_model_name()."""

    def test_valid_names_return_id_string(self):
        self.assertEqual(extract_cot_id_from_model_name("table1model"), "1")
        self.assertEqual(extract_cot_id_from_model_name("table42model"), "42")
        self.assertEqual(extract_cot_id_from_model_name("table999model"), "999")

    def test_returns_none_for_missing_prefix(self):
        # No leading "table"
        self.assertIsNone(extract_cot_id_from_model_name("42model"))

    def test_returns_none_for_missing_suffix(self):
        # No trailing "model"
        self.assertIsNone(extract_cot_id_from_model_name("table42"))

    def test_returns_none_for_non_digit_id(self):
        self.assertIsNone(extract_cot_id_from_model_name("tableabcmodel"))

    def test_returns_none_for_substring_match(self):
        # "table" and "model" present as substrings but wrong structure
        self.assertIsNone(extract_cot_id_from_model_name("sometablemodel"))
        self.assertIsNone(extract_cot_id_from_model_name("table_model"))
        self.assertIsNone(extract_cot_id_from_model_name("table42modelextra"))

    def test_returns_none_for_empty_string(self):
        self.assertIsNone(extract_cot_id_from_model_name(""))

    def test_case_sensitive(self):
        # The regex is anchored and lowercase-only; uppercase should not match
        self.assertIsNone(extract_cot_id_from_model_name("Table42Model"))
        self.assertIsNone(extract_cot_id_from_model_name("TABLE42MODEL"))
