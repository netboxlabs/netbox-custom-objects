from django.contrib.contenttypes.models import ContentType
from netbox.plugins import PluginTemplateExtension
from .models import MappingRelation

__all__ = (
    'MappingSchema',
    'MappingElements',
    'MappingLink',
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


class MappingLink(PluginTemplateExtension):

    def left_page(self):
        if not (instance := self.context['object']):
            return ''

        content_type = ContentType.objects.get_for_model(instance)
        relations = MappingRelation.objects.filter(field__content_type=content_type, object_id=instance.pk)
        if not relations.exists():
            return ''

        result = '<h2 class="card-header">Service Mappings</h2>'
        result += '<ul>'
        for relation in relations:
            url = relation.mapping.get_absolute_url()
            result += f'<li><a href="{url}">{relation.mapping}</a></li>'
        result += '</ul>'
        return result


template_extensions = (
    MappingSchema,
    MappingElements,
    MappingLink,
)
