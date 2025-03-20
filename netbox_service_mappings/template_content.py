from django.contrib.contenttypes.models import ContentType
from netbox.plugins import PluginTemplateExtension
from .models import MappingRelation
from utilities.jinja2 import render_jinja2

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

        return ''
        # Debug
        # return instance.formatted_schema


class MappingElements(PluginTemplateExtension):
    models = ['netbox_service_mappings.servicemapping']

    def full_width_page(self):
        if not (instance := self.context['object']):
            return ''

        return ''
        # Debug
        # return instance.formatted_data


class MappingLink(PluginTemplateExtension):

    def left_page(self):
        if not (instance := self.context['object']):
            return ''

        content_type = ContentType.objects.get_for_model(instance)
        relations = MappingRelation.objects.filter(field__content_type=content_type, object_id=instance.pk)
        if not relations.exists():
            return ''

        return render_jinja2("""
          <div class="card">
            <h2 class="card-header">Custom Objects linking to this object</h2>
            <table class="table table-hover attr-table">
              {% for relation in relations %}
                <tr>
                    <th scope="row"><a href="{{ relation.mapping.get_absolute_url() }}">{{ relation.mapping }}</a></th>
                    <td>{{ relation.mapping.id }}</td>
                </tr>
              {% endfor %}
            </table>
          </div>
          """, {'relations': relations})


template_extensions = (
    MappingSchema,
    MappingElements,
    MappingLink,
)
