import copy
import re

import yaml
from rest_framework.exceptions import ParseError
from rest_framework.parsers import BaseParser

_BOOL_TAG = 'tag:yaml.org,2002:bool'


class _StrictBoolLoader(yaml.SafeLoader):
    """
    SafeLoader variant that only resolves the literal tokens ``true``/``false``
    (any case) as booleans, instead of PyYAML's default YAML 1.1 behaviour of
    also treating ``yes``/``no``/``on``/``off`` as booleans (the "Norway
    problem"). Without this, an unquoted schema value like ``no`` would be
    silently coerced to ``False`` rather than kept as the string "no".
    """


_StrictBoolLoader.yaml_implicit_resolvers = {
    key: [item for item in resolvers if item[0] != _BOOL_TAG]
    for key, resolvers in copy.deepcopy(yaml.SafeLoader.yaml_implicit_resolvers).items()
}
_StrictBoolLoader.add_implicit_resolver(
    _BOOL_TAG,
    re.compile(r'^(?:true|True|TRUE|false|False|FALSE)$'),
    list('tTfF'),
)


class YAMLParser(BaseParser):
    """Parses YAML request bodies for the schema preview/apply endpoints (#665)."""

    media_type = 'application/yaml'

    def parse(self, stream, media_type=None, parser_context=None):
        try:
            data = yaml.load(stream, Loader=_StrictBoolLoader)
        except yaml.YAMLError as exc:
            raise ParseError(f"YAML parse error: {exc}")
        if data is None:
            # Matches JSONParser, which raises ParseError (400) for an empty
            # body rather than silently treating it as an empty document.
            raise ParseError("YAML parse error: request body is empty.")
        return data
