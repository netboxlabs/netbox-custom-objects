import contextlib
import logging
import re

import django_tables2 as tables
from django.utils.translation import gettext_lazy as _
from django_tables2.data import TableQuerysetData

from extras.choices import CustomFieldTypeChoices, CustomFieldUIVisibleChoices
from netbox.tables import NetBoxTable, columns
from netbox_custom_objects import field_types
from netbox_custom_objects.choices import CustomObjectFieldTypeChoices
from netbox_custom_objects.models import (
    CustomObject,
    CustomObjectType,
    CustomObjectTypeConstraint,
    CustomObjectTypeField,
)
from netbox_custom_objects.utilities import polymorphic_m2m_field_names

__all__ = (
    'CustomObjectTable',
    'CustomObjectTypeConstraintTable',
    'CustomObjectTypeFieldTable',
    'get_custom_object_table',
)

logger = logging.getLogger('netbox_custom_objects.tables')

# The name NetBox looks up a custom object list table by: "Table<id>ModelTable".
_TABLE_NAME_RE = re.compile(r'^Table(\d+)ModelTable$')


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
    tags = columns.TagColumn(url_name='plugins:netbox_custom_objects:customobjecttype_list')
    name = tables.Column(verbose_name=_('Name'), linkify=True)

    class Meta(NetBoxTable.Meta):
        model = CustomObjectType
        fields = (
            'pk',
            'id',
            'name',
            'verbose_name',
            'verbose_name_plural',
            'slug',
            'description',
            'comments',
            'tags',
            'created',
            'last_updated',
        )
        default_columns = (
            'pk',
            'id',
            'name',
            'created',
            'last_updated',
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
    unique = columns.BooleanColumn(verbose_name=_('Unique'))
    is_cloneable = columns.BooleanColumn(verbose_name=_('Cloneable'))
    primary = columns.BooleanColumn()
    required = columns.BooleanColumn()
    default = FieldDefaultColumn(verbose_name=_('Default'))

    class Meta(NetBoxTable.Meta):
        model = CustomObjectTypeField
        fields = (
            'pk',
            'id',
            'name',
            'type',
            'primary',
            'related_object_type',
            'label',
            'group_name',
            'description',
            'required',
            'unique',
            'search_weight',
            'filter_logic',
            'default',
            'related_object_filter',
            'weight',
            'validation_minimum',
            'validation_maximum',
            'validation_regex',
            'choice_set',
            'ui_visible',
            'ui_editable',
            'is_cloneable',
            'comments',
            'tags',
            'created',
            'last_updated',
        )
        default_columns = (
            'id',
            'name',
            'type',
            'primary',
            'default',
            'created',
            'last_updated',
        )


class CustomObjectTable(NetBoxTable):
    pk = columns.ToggleColumn(visible=False)
    id = tables.Column(linkify=True, verbose_name=_('ID'))
    actions = columns.ActionsColumn()
    tags = CustomObjectTagColumn()

    class Meta(NetBoxTable.Meta):
        model = CustomObject
        fields = (
            'pk',
            'id',
            'name',
            'custom_object_type',
            'created',
            'last_updated',
            'tags',
        )
        default_columns = (
            'pk',
            'id',
            'name',
            'custom_object_type',
            'created',
            'last_updated',
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # COMPAT(netbox<4.5.4): NetBoxTable has no _apply_prefetching(), and prefetches the
        # default columns in __init__() instead.
        if not hasattr(NetBoxTable, '_apply_prefetching'):
            self._prefetch_polymorphic_m2m()

    def _apply_prefetching(self, columns=None):
        super()._apply_prefetching(columns)
        self._prefetch_polymorphic_m2m(columns)

    def _prefetch_polymorphic_m2m(self, columns=None):
        """Prefetch the polymorphic multi-object columns, which NetBox's prefetching can't find in _meta."""
        if not isinstance(self.data, TableQuerysetData):
            return
        names = [
            name
            for name in polymorphic_m2m_field_names(self._meta.model)
            if name in self.columns and (self.columns[name].visible if columns is None else name in columns)
        ]
        if names:
            self.data.data = self.data.data.prefetch_related(*names)


def get_custom_object_table(custom_object_type, model=None):
    """
    Build the list table class for a Custom Object Type's objects.
    """
    model = model or custom_object_type.get_model()
    model_fields = custom_object_type.fields.all()
    visible_fields = [field for field in model_fields if field.ui_visible != CustomFieldUIVisibleChoices.HIDDEN]
    fields = ['pk', 'id', *(field.name for field in visible_fields), 'tags', 'created', 'last_updated']
    # Fields shown only when set are left out of the defaults but stay selectable.
    default_columns = [
        'pk',
        'id',
        *(field.name for field in visible_fields if field.ui_visible == CustomFieldUIVisibleChoices.ALWAYS),
        'tags',
    ]

    meta = type(
        'Meta',
        (),
        {
            'model': model,
            'fields': fields,
            'default_columns': default_columns,
            'attrs': {
                'class': 'table table-hover object-list',
            },
        },
    )

    attrs = {
        'Meta': meta,
        '__module__': __name__,
    }

    for field in visible_fields:
        field_type = field_types.FIELD_TYPE_CLASS[field.type]()
        try:
            attrs[field.name] = field_type.get_table_column_field(field)
        except NotImplementedError:
            logger.debug('{} field is not implemented; using a default column'.format(field.name))
        # Link text-based primary fields to their custom objects.
        linkable_field_types = [
            CustomFieldTypeChoices.TYPE_TEXT,
            CustomFieldTypeChoices.TYPE_LONGTEXT,
        ]
        if field.primary and field.type in linkable_field_types:
            attrs[f'render_{field.name}'] = field_type.render_table_column_linkified
        else:
            # Use the field type's custom renderer, if available.
            # See https://django-tables2.readthedocs.io/en/latest/pages/custom-data.html#table-render-foo-methods
            with contextlib.suppress(AttributeError):
                attrs[f'render_{field.name}'] = field_type.render_table_column

    return type(f'{model._meta.object_name}Table', (CustomObjectTable,), attrs)


class CustomObjectTypeConstraintTable(NetBoxTable):
    name = tables.Column(
        verbose_name=_('Name'),
    )
    constrained_fields = tables.TemplateColumn(
        verbose_name=_('Fields'),
        template_code='{{ record.get_fields|join:", "|placeholder }}',
        orderable=False,
    )
    case_insensitive = columns.BooleanColumn(verbose_name=_('Case-insensitive'))
    nulls_distinct = columns.BooleanColumn(verbose_name=_('Empty values distinct'))
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

    class Meta(NetBoxTable.Meta):
        model = CustomObjectTypeConstraint
        fields = (
            'pk',
            'id',
            'name',
            'type',
            'constrained_fields',
            'case_insensitive',
            'nulls_distinct',
            'condition',
            'rule',
            'error_message',
            'description',
            'created',
            'last_updated',
        )
        default_columns = (
            'name',
            'type',
            'constrained_fields',
            'case_insensitive',
            'nulls_distinct',
            'condition',
            'rule',
            'description',
        )


def __getattr__(name):
    """
    Resolve dynamic list tables, e.g. "Table3ModelTable", for NetBox's saved table configurations.

    NetBox imports table classes from "<app_label>.tables.<name>".
    """
    if match := _TABLE_NAME_RE.match(name):
        try:
            custom_object_type = CustomObjectType.objects.get(pk=match.group(1))
        except CustomObjectType.DoesNotExist:
            pass
        else:
            return get_custom_object_table(custom_object_type)
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
