from netbox.plugins import PluginTemplateExtension

__all__ = (
    'MappingSchema',
    'MappingElements',
    'template_extensions',
)


class MappingSchema(PluginTemplateExtension):
    models = ['netbox_service_mappings.servicemappingtype']

    def full_width_page(self):
        if not (instance := self.context['object']):
            return ''

        return instance.formatted_schema


class MappingElements(PluginTemplateExtension):
    models = ['netbox_service_mappings.servicemapping']

    def full_width_page(self):
        if not (instance := self.context['object']):
            return ''

        return instance.formatted_data


template_extensions = (
    MappingSchema,
    MappingElements,
)
