import django_tables2 as tables
from django.utils.translation import gettext_lazy as _
from netbox.tables import NetBoxTable, columns

from netbox_custom_objects.choices import CustomObjectFieldTypeChoices
from netbox_custom_objects.models import (CustomObject, CustomObjectType, CustomObjectTypeConstraint,
                                          CustomObjectTypeField)

__all__ = ("CustomObjectTable", "CustomObjectTypeConstraintTable", "CustomObjectTypeFieldTable")


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

# The {% url %} tag must stay on a single line: Django's template lexer does not parse a {% %}
# tag whose delimiters span a newline. {% with %} shortens the slug accessor to keep it under 120.
CUSTOM_OBJECT_TAGS = """
{% load helpers %}
{% with slug=record.custom_object_type.slug %}
{% for tag in value.all %}
    <a href="{% url 'plugins:netbox_custom_objects:customobject_list' custom_object_type=slug %}?tag={{ tag.slug }}">
        <span {% if tag.description %}title="{{ tag.description }}"{% endif %}
              class="badge"
              style="color: {{ tag.color|fgcolor }}; background-color: #{{ tag.color }}">
            {{ tag }}
        </span>
    </a>
{% empty %}
    <span class="text-muted">&mdash;</span>
{% endfor %}
{% endwith %}
"""


class CustomObjectTypeTable(NetBoxTable):
    comments = columns.MarkdownColumn(
        verbose_name=_('Comments'),
    )
    tags = columns.TagColumn(
        url_name='plugins:netbox_custom_objects:customobjecttype_list'
    )
    name = tables.Column(
        verbose_name=_('Name'),
        linkify=True
    )

    class Meta(NetBoxTable.Meta):
        model = CustomObjectType
        fields = (
            "pk",
            "id",
            "name",
            "verbose_name",
            "verbose_name_plural",
            "slug",
            'description',
            'comments',
            'tags',
            "created",
            "last_updated",
        )
        default_columns = (
            "pk",
            "id",
            "name",
            "created",
            "last_updated",
        )


class CustomObjectTagColumn(columns.TagColumn):
    """
    Custom TagColumn that generates tag filter URLs with the custom_object_type slug.
    """
    template_code = CUSTOM_OBJECT_TAGS

    def __init__(self):
        # Override parent __init__ to use our custom template
        tables.TemplateColumn.__init__(
            self,
            orderable=False,
            template_code=self.template_code,
            verbose_name=_('Tags'),
        )


class FieldDefaultColumn(tables.JSONColumn):
    """Shows color and MAC address defaults the way their values are shown, instead of as JSON."""

    def render(self, record, value):
        if record.type == CustomObjectFieldTypeChoices.TYPE_COLOR:
            return columns.ColorColumn().render(value)
        if record.type == CustomObjectFieldTypeChoices.TYPE_MAC_ADDRESS:
            return value
        return super().render(record, value)


class CustomObjectTypeFieldTable(NetBoxTable):
    id = tables.Column()
    name = tables.Column(
        verbose_name=_('Name'),
    )
    unique = columns.BooleanColumn(
        verbose_name=_('Unique')
    )
    is_cloneable = columns.BooleanColumn(
        verbose_name=_('Cloneable')
    )
    primary = columns.BooleanColumn()
    required = columns.BooleanColumn()
    default = FieldDefaultColumn(verbose_name=_('Default'))

    exempt_columns = ('actions',)

    class Meta(NetBoxTable.Meta):
        model = CustomObjectTypeField
        fields = (
            "id",
            "name",
            "type",
            "primary",
            "related_object_type",
            "label",
            "group_name",
            "description",
            "required",
            "unique",
            "search_weight",
            "filter_logic",
            "default",
            "related_object_filter",
            "weight",
            "validation_minimum",
            "validation_maximum",
            "validation_regex",
            "choice_set",
            "ui_visible",
            "ui_editable",
            "is_cloneable",
            "comments",
            "tags",
            "created",
            "last_updated",
        )
        default_columns = (
            "id",
            "name",
            "type",
            "primary",
            "default",
            "created",
            "last_updated",
        )


class CustomObjectTable(NetBoxTable):
    pk = columns.ToggleColumn(visible=False)
    id = tables.Column(linkify=True, verbose_name=_("ID"))
    actions = columns.ActionsColumn()
    tags = CustomObjectTagColumn()

    class Meta(NetBoxTable.Meta):
        model = CustomObject
        fields = (
            "pk",
            "id",
            "name",
            "custom_object_type",
            "created",
            "last_updated",
            "tags",
        )
        default_columns = (
            "pk",
            "id",
            "name",
            "custom_object_type",
            "created",
            "last_updated",
        )


class CustomObjectTypeConstraintTable(NetBoxTable):
    name = tables.Column(
        verbose_name=_('Name'),
    )
    constrained_fields = tables.TemplateColumn(
        verbose_name=_('Fields'),
        template_code='{{ record.get_fields|join:", "|placeholder }}',
        orderable=False,
    )
    case_insensitive = columns.BooleanColumn(
        verbose_name=_('Case-insensitive')
    )
    nulls_distinct = columns.BooleanColumn(
        verbose_name=_('Empty values distinct')
    )
    condition = tables.TemplateColumn(
        verbose_name=_('Condition'),
        template_code='{{ record.get_condition_display|placeholder }}',
        orderable=False,
    )
    rule = tables.TemplateColumn(
        verbose_name=_('Rule'),
        template_code='{{ record.get_rule_display|placeholder }}',
        orderable=False,
    )

    exempt_columns = ('actions',)

    class Meta(NetBoxTable.Meta):
        model = CustomObjectTypeConstraint
        fields = (
            "pk",
            "id",
            "name",
            "type",
            "constrained_fields",
            "case_insensitive",
            "nulls_distinct",
            "condition",
            "rule",
            "error_message",
            "description",
            "created",
            "last_updated",
        )
        default_columns = (
            "name",
            "type",
            "constrained_fields",
            "case_insensitive",
            "nulls_distinct",
            "condition",
            "rule",
            "description",
        )
