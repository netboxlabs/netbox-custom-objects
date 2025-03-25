import django_tables2 as tables
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _

from core.models import ObjectChange
from netbox.tables import NetBoxTable, columns
from netbox_custom_objects.models import CustomObject, CustomObjectType
from utilities.templatetags.builtins.filters import placeholder
# from .columns import ConflictsColumn, DiffColumn

__all__ = (
    'CustomObjectTable',
)


OBJECTCHANGE_FULL_NAME = """
{% load helpers %}
{{ value.get_full_name|placeholder }}
"""

OBJECTCHANGE_OBJECT = """
{% if value and value.get_absolute_url %}
    <a href="{{ value.get_absolute_url }}">{{ record.object_repr }}</a>
{% else %}
    {{ record.object_repr }}
{% endif %}
"""

BEFORE_DIFF = """
{% if record.action == 'create' %}
    {{ ''|placeholder }}
{% else %}
    <pre class="p-0">{% for k, v in record.diff.pre.items %}{{ k }}: {{ v }}
{% endfor %}</pre>
{% endif %}
"""

AFTER_DIFF = """
{% if record.action == 'delete' %}
    {{ ''|placeholder }}
{% else %}
    <pre class="p-0">{% for k, v in record.diff.post.items %}{{ k }}: {{ v }}
{% endfor %}</pre>
{% endif %}
"""

OBJECTCHANGE_REQUEST_ID = """
<a href="?request_id={{ value }}">{{ value }}</a>
"""


class CustomObjectTypeTable(NetBoxTable):
    # tags = columns.TagColumn(
    #     url_name='plugins:netbox_service_mappings:customobjecttype_list'
    # )

    class Meta(NetBoxTable.Meta):
        model = CustomObjectType
        fields = (
            'pk', 'id', 'name', 'created', 'last_updated',
        )
        default_columns = (
            'pk', 'id', 'name', 'created', 'last_updated',
        )


class CustomObjectTable(NetBoxTable):
    # name = tables.Column(
    #     verbose_name=_('Name'),
    #     linkify=True
    # )
    # is_active = columns.BooleanColumn(
    #     verbose_name=_('Active')
    # )
    # status = columns.ChoiceFieldColumn(
    #     verbose_name=_('Status')
    # )
    # is_stale = columns.BooleanColumn(
    #     true_mark=mark_safe('<span class="text-danger"><i class="mdi mdi-alert-circle"></i></span>'),
    #     false_mark=None,
    #     verbose_name=_('Stale')
    # )
    # conflicts = ConflictsColumn(
    #     verbose_name=_('Conflicts')
    # )
    # schema_id = tables.TemplateColumn(
    #     template_code='<span class="font-monospace">{{ value }}</code>'
    # )
    # tags = columns.TagColumn(
    #     url_name='plugins:netbox_service_mappings:servicemapping_list'
    # )

    class Meta(NetBoxTable.Meta):
        model = CustomObject
        fields = (
            'pk', 'id', 'name', 'custom_object_type', 'created', 'last_updated',
        )
        default_columns = (
            'pk', 'id', 'name', 'custom_object_type', 'created', 'last_updated',
        )

    # def render_is_active(self, value):
    #     if value:
    #         return mark_safe('<span class="text-success"><i class="mdi mdi-check-bold"></i></span>')
    #     return placeholder('')
