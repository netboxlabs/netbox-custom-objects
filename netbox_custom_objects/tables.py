from urllib.parse import quote

import django_tables2 as tables

from django.contrib.auth.models import AnonymousUser
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _
from django.template import Context, Template
from django.urls import reverse

from core.models import ObjectChange
from netbox.tables import NetBoxTable, columns
from netbox_custom_objects.models import CustomObject, CustomObjectType
from netbox_custom_objects.utilities import get_viewname
from utilities.permissions import get_permission_for_model
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


class CustomObjectActionsColumn(columns.ActionsColumn):

    def render(self, record, table, **kwargs):
        model = table.Meta.model

        # Skip if no actions or extra buttons are defined
        if not (self.actions or self.extra_buttons):
            return ''
        # Skip dummy records (e.g. available VLANs or IP ranges replacing individual IPs)
        if type(record) is not model or not getattr(record, 'pk', None):
            return ''

        if request := getattr(table, 'context', {}).get('request'):
            return_url = request.GET.get('return_url', request.get_full_path())
            url_appendix = f'?return_url={quote(return_url)}'
        else:
            url_appendix = ''

        html = ''

        # Compile actions menu
        button = None
        dropdown_class = 'secondary'
        dropdown_links = []
        user = getattr(request, 'user', AnonymousUser())
        for idx, (action, attrs) in enumerate(self.actions.items()):
            permission = get_permission_for_model(model, attrs.permission)
            if attrs.permission is None or user.has_perm(permission):
                url = reverse(get_viewname(model, action), kwargs={'pk': record.pk})

                # Render a separate button if a) only one action exists, or b) if split_actions is True
                if len(self.actions) == 1 or (self.split_actions and idx == 0):
                    dropdown_class = attrs.css_class
                    button = (
                        f'<a class="btn btn-sm btn-{attrs.css_class}" href="{url}{url_appendix}" type="button" '
                        f'aria-label="{attrs.title}">'
                        f'<i class="mdi mdi-{attrs.icon}"></i></a>'
                    )

                # Add dropdown menu items
                else:
                    dropdown_links.append(
                        f'<li><a class="dropdown-item" href="{url}{url_appendix}">'
                        f'<i class="mdi mdi-{attrs.icon}"></i> {attrs.title}</a></li>'
                    )

        # Create the actions dropdown menu
        toggle_text = _('Toggle Dropdown')
        if button and dropdown_links:
            html += (
                f'<span class="btn-group dropdown">'
                f'  {button}'
                f'  <a class="btn btn-sm btn-{dropdown_class} dropdown-toggle" type="button" data-bs-toggle="dropdown" '
                f'style="padding-left: 2px">'
                f'  <span class="visually-hidden">{toggle_text}</span></a>'
                f'  <ul class="dropdown-menu">{"".join(dropdown_links)}</ul>'
                f'</span>'
            )
        elif button:
            html += button
        elif dropdown_links:
            html += (
                f'<span class="btn-group dropdown">'
                f'  <a class="btn btn-sm btn-secondary dropdown-toggle" type="button" data-bs-toggle="dropdown">'
                f'  <span class="visually-hidden">{toggle_text}</span></a>'
                f'  <ul class="dropdown-menu">{"".join(dropdown_links)}</ul>'
                f'</span>'
            )

        # Render any extra buttons from template code
        if self.extra_buttons:
            template = Template(self.extra_buttons)
            context = getattr(table, "context", Context())
            context.update({'record': record})
            html = template.render(context) + html

        return mark_safe(html)


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
    pk = columns.ToggleColumn(
        visible=False
    )
    id = tables.Column(
        linkify=True,
        verbose_name=_('ID')
    )
    actions = CustomObjectActionsColumn()

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
