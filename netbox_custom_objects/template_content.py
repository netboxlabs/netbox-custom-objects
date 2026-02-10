from dataclasses import dataclass
from typing import Any
from django.contrib.contenttypes.models import ContentType
from netbox.plugins import PluginTemplateExtension
from extras.choices import CustomFieldTypeChoices
from utilities.jinja2 import render_jinja2
from netbox_custom_objects.models import CustomObjectTypeField

__all__ = (
    "CustomObjectSchema",
    "MappingElements",
    "template_extensions",
)


class CustomObjectSchema(PluginTemplateExtension):
    models = ["netbox_custom_objects.customobjecttype"]

    def full_width_page(self):
        # TODO: Implement this
        return ""


class MappingElements(PluginTemplateExtension):
    models = ["netbox_custom_objects.customobject"]

    def full_width_page(self):
        # TODO: Implement this
        return ""


@dataclass
class LinkedCustomObject:
    custom_object: Any
    field: CustomObjectTypeField


class CustomObjectLink(PluginTemplateExtension):

    def left_page(self):
        content_type = ContentType.objects.get_for_model(
            self.context["object"]._meta.model
        )
        custom_object_type_fields = CustomObjectTypeField.objects.filter(
            related_object_type=content_type
        )
        linked_custom_objects = []

        for field in custom_object_type_fields:
            model = field.custom_object_type.get_model(no_cache=True)

            if field.type == CustomFieldTypeChoices.TYPE_MULTIOBJECT:
                # Get the M2M field from the model
                m2m_field = model._meta.get_field(field.name)
                through_model = m2m_field.remote_field.through

                linked_ids = through_model.objects.filter(
                    target_id=self.context["object"].pk
                ).values_list('source_id', flat=True)

                linked_objects = model.objects.filter(pk__in=linked_ids)

                for model_object in linked_objects:
                    linked_custom_objects.append(
                        LinkedCustomObject(
                            custom_object=model_object, field=field
                        )
                    )
            else:
                # Build a filter dynamically using the field name
                filter_kwargs = {field.name: self.context["object"]}
                linked_objects = model.objects.filter(**filter_kwargs)

                for model_object in linked_objects:
                    linked_custom_objects.append(
                        LinkedCustomObject(
                            custom_object=model_object, field=field
                        )
                    )
        return render_jinja2(
            """
          <div class="card">
            <h2 class="card-header">Custom Objects linking to this object</h2>
            <table class="table table-hover attr-table">
              <thead>
                <tr>
                  <th>Type</th>
                  <th>Custom Object</th>
                  <th>Field</th>
                </tr>
              </thead>
              {% if linked_custom_objects|count <= 20 %}
                {% for obj in linked_custom_objects %}
                  <tr>
                    <td>{{ obj.field.custom_object_type }}</td>
                    <th scope="row">
                      <a href="{{ obj.custom_object.get_absolute_url() }}">{{ obj.custom_object }}</a>
                    </th>
                    <td>{{ obj.field }}</td>
                  </tr>
                {% endfor %}
              {% endif %}
              <tr>
                <td colspan="3">{{ linked_custom_objects|count }} objects</td>
              </tr>
            </table>
          </div>
          """,
            {"linked_custom_objects": linked_custom_objects},
        )


template_extensions = (
    CustomObjectSchema,
    MappingElements,
    CustomObjectLink,
)
