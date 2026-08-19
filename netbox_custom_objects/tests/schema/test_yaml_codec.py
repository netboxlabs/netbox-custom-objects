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

    def test_empty_body_raises_parse_error(self):
        # Matches JSONParser, which also raises ParseError (400) for an empty body.
        with self.assertRaises(ParseError):
            YAMLParser().parse(io.BytesIO(b""))

    def test_null_document_raises_parse_error(self):
        with self.assertRaises(ParseError):
            YAMLParser().parse(io.BytesIO(b"null\n"))

    def test_malformed_yaml_raises_parse_error(self):
        stream = io.BytesIO(b"types: [\n")
        with self.assertRaises(ParseError):
            YAMLParser().parse(stream)

    def test_unquoted_no_yes_on_off_preserved_as_strings(self):
        # PyYAML's default YAML 1.1 resolver treats these as booleans (the
        # "Norway problem"); the schema parser must not, since field
        # names/values could legitimately be these words.
        stream = io.BytesIO(b"a: no\nb: yes\nc: On\nd: OFF\n")
        data = YAMLParser().parse(stream)
        self.assertEqual(data, {"a": "no", "b": "yes", "c": "On", "d": "OFF"})

    def test_unquoted_true_false_still_resolve_to_booleans(self):
        stream = io.BytesIO(b"a: true\nb: False\n")
        data = YAMLParser().parse(stream)
        self.assertEqual(data, {"a": True, "b": False})


class YAMLRendererTestCase(SimpleTestCase):

    def test_renders_dict_to_yaml_bytes(self):
        # DRF renderer convention: render() returns bytes, not str (matches
        # JSONRenderer; DRF's Response.render() would otherwise have to
        # re-encode a str result itself).
        output = YAMLRenderer().render({"diffs": [{"slug": "circuit"}]})
        self.assertIsInstance(output, bytes)
        self.assertEqual(
            output,
            b"diffs:\n- slug: circuit\n",
        )

    def test_renders_none_to_empty_bytes(self):
        output = YAMLRenderer().render(None)
        self.assertIsInstance(output, bytes)
        self.assertEqual(output, b'')

    def test_round_trips_through_parser(self):
        import yaml
        original = {"schema_version": "1", "types": [{"name": "circuit", "slug": "circuit"}]}
        rendered = YAMLRenderer().render(original)
        parsed = YAMLParser().parse(io.BytesIO(rendered))
        self.assertEqual(parsed, original)
        # Sanity-check against a plain yaml.safe_load too.
        self.assertEqual(yaml.safe_load(rendered), original)
