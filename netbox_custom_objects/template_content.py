from dataclasses import dataclass
from typing import Any
from django.apps import apps as django_apps
from django.contrib.contenttypes.models import ContentType
from django.template import Template, Context
from netbox.plugins import PluginTemplateExtension
from extras.choices import CustomFieldTypeChoices
from utilities.paginator import EnhancedPaginator
from netbox_custom_objects.constants import APP_LABEL
from netbox_custom_objects.models import CustomObjectTypeField
from netbox_custom_objects.tables import LinkedCustomObjectTable

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
        # Get custom objects linking to this object
        content_type = ContentType.objects.get_for_model(
            self.context["object"]._meta.model
        )
        non_poly_fields = CustomObjectTypeField.objects.filter(
            related_object_type=content_type
        )
        poly_fields = CustomObjectTypeField.objects.filter(
            related_object_types=content_type
        )
        linked_custom_objects = []
        target_obj = self.context["object"]

        for field in list(non_poly_fields) + list(poly_fields):
            model = field.custom_object_type.get_model(no_cache=True)

            if field.type == CustomFieldTypeChoices.TYPE_MULTIOBJECT:
                if field.is_polymorphic:
                    through = django_apps.get_model(APP_LABEL, field.through_model_name)
                    linked_ids = through.objects.filter(
                        content_type_id=content_type.id,
                        object_id=target_obj.pk,
                    ).values_list('source_id', flat=True)
                else:
                    m2m_field = model._meta.get_field(field.name)
                    through_model = m2m_field.remote_field.through
                    linked_ids = through_model.objects.filter(
                        target_id=target_obj.pk
                    ).values_list('source_id', flat=True)
                linked_objects = model.objects.filter(pk__in=linked_ids)
            else:
                if field.is_polymorphic:
                    linked_objects = model.objects.filter(**{
                        f"{field.name}_content_type_id": content_type.id,
                        f"{field.name}_object_id": target_obj.pk,
                    })
                else:
                    linked_objects = model.objects.filter(**{field.name: target_obj})

            for model_object in linked_objects:
                linked_custom_objects.append(
                    LinkedCustomObject(custom_object=model_object, field=field)
                )

        request = self.context["request"]
        linked_objects_table = LinkedCustomObjectTable(linked_custom_objects, orderable=False)
        linked_objects_table.configure(request)
        linked_objects_table.paginate(page=request.GET.get("page", 1), per_page=50, paginator_class=EnhancedPaginator)

        template_str = """
            {% load render_table from django_tables2 %}
            {% load i18n %}
            <div class="card">
              <h2 class="card-header">{% trans "Custom Objects linking to this object" %}</h2>
              {% if table.rows %}
                <div class="table-responsive">
                  {% render_table table 'inc/table.html' %}
                  {% include 'inc/paginator.html' with paginator=table.paginator page=table.page %}
                </div>
              {% else %}
                <div class="card-body text-muted">{% trans "None" %}</div>
              {% endif %}
            </div>
        """
        template = Template(template_str)
        context = Context({'table': linked_objects_table, "request": request})
        rendered_content = template.render(context)
        return rendered_content


template_extensions = (
    CustomObjectSchema,
    MappingElements,
    CustomObjectLink,
)
