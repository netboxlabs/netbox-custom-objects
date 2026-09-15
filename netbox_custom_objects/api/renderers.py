import yaml

from rest_framework.renderers import BaseRenderer


class YAMLRenderer(BaseRenderer):
    """Renders responses as YAML for the schema preview/apply endpoints (#665)."""

    media_type = 'application/yaml'
    format = 'yaml'
    charset = 'utf-8'

    def render(self, data, accepted_media_type=None, renderer_context=None):
        if data is None:
            return b''
        return yaml.safe_dump(data, sort_keys=False, default_flow_style=False).encode(self.charset)
