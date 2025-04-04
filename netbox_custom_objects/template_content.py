from django.contrib.contenttypes.models import ContentType
from netbox.plugins import PluginTemplateExtension
from .models import CustomObjectRelation
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
        if not (instance := self.context['object']):
            return ''

        content_type = ContentType.objects.get_for_model(instance)
        relations = CustomObjectRelation.objects.filter(field__related_object_type=content_type, object_id=instance.pk)
        if not relations.exists():
            return ''

        return render_jinja2("""
          <div class="card">
            <h2 class="card-header">Custom Objects linking to this object</h2>
            <table class="table table-hover attr-table">
              {% for relation in relations %}
                <tr>
                    <th scope="row"><a href="{{ relation.custom_object.get_absolute_url() }}">{{ relation.custom_object }}</a></th>
                    <td>{{ relation.custom_object.id }}</td>
                </tr>
              {% endfor %}
            </table>
          </div>
          """, {'relations': relations})


template_extensions = (
    CustomObjectSchema,
    MappingElements,
    CustomObjectLink,
)
