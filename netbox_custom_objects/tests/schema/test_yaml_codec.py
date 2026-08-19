"""
Unit tests for the YAML parser/renderer used by the schema preview/apply
endpoints (#665).
"""

import io

from django.test import SimpleTestCase
from rest_framework.exceptions import ParseError

from netbox_custom_objects.api.parsers import YAMLParser
from netbox_custom_objects.api.renderers import YAMLRenderer


class YAMLParserTestCase(SimpleTestCase):

    def test_parses_valid_yaml_to_dict(self):
        stream = io.BytesIO(b"schema_version: '1'\ntypes: []\n")
        data = YAMLParser().parse(stream)
        self.assertEqual(data, {"schema_version": "1", "types": []})

    def test_empty_body_returns_empty_dict(self):
        data = YAMLParser().parse(io.BytesIO(b""))
        self.assertEqual(data, {})

    def test_malformed_yaml_raises_parse_error(self):
        stream = io.BytesIO(b"types: [\n")
        with self.assertRaises(ParseError):
            YAMLParser().parse(stream)


class YAMLRendererTestCase(SimpleTestCase):

    def test_renders_dict_to_yaml(self):
        output = YAMLRenderer().render({"diffs": [{"slug": "circuit"}]})
        self.assertEqual(
            output,
            "diffs:\n- slug: circuit\n",
        )

    def test_renders_none_to_empty_string(self):
        self.assertEqual(YAMLRenderer().render(None), '')

    def test_round_trips_through_parser(self):
        import yaml
        original = {"schema_version": "1", "types": [{"name": "circuit", "slug": "circuit"}]}
        rendered = YAMLRenderer().render(original)
        parsed = YAMLParser().parse(io.BytesIO(rendered.encode("utf-8")))
        self.assertEqual(parsed, original)
        # Sanity-check against a plain yaml.safe_load too.
        self.assertEqual(yaml.safe_load(rendered), original)
