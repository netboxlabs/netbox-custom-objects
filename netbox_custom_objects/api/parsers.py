import yaml

from rest_framework.exceptions import ParseError
from rest_framework.parsers import BaseParser


class YAMLParser(BaseParser):
    """Parses YAML request bodies for the schema preview/apply endpoints (#665)."""

    media_type = 'application/yaml'

    def parse(self, stream, media_type=None, parser_context=None):
        try:
            data = yaml.safe_load(stream)
        except yaml.YAMLError as exc:
            raise ParseError(f"YAML parse error: {exc}")
        return data if data is not None else {}
