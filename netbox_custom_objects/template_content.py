from django.contrib.contenttypes.models import ContentType
from netbox.plugins import PluginTemplateExtension
from utilities.jinja2 import render_jinja2

__all__ = (
    'CustomObjectSchema',
    'MappingElements',
    'MappingLink',
    'template_extensions',
)


class CustomObjectSchema(PluginTemplateExtension):
    models = ['netbox_custom_objects.customobjecttype']

    def full_width_page(self):
        if not (instance := self.context['object']):
            return ''

        return ''
        # Debug
        # return instance.formatted_schema


class MappingElements(PluginTemplateExtension):
    models = ['netbox_custom_objects.customobject']

    def full_width_page(self):
        if not (instance := self.context['object']):
            return ''

        return ''
        # Debug
        # return instance.formatted_data


class CustomObjectLink(PluginTemplateExtension):

    def left_page(self):
        # TODO: Implement this
        return ''


template_extensions = (
    CustomObjectSchema,
    MappingElements,
    CustomObjectLink,
)
