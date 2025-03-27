import jsonschema
import re

from django.db import models
from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.core.validators import RegexValidator, ValidationError
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from netbox.models import NetBoxModel, ChangeLoggedModel
from netbox.models.features import CloningMixin, ExportTemplatesMixin
from extras.choices import (
    CustomFieldTypeChoices, CustomFieldFilterLogicChoices, CustomFieldUIVisibleChoices, CustomFieldUIEditableChoices
)
from utilities.validators import validate_regex
# from .choices import MappingFieldTypeChoices


class CustomObjectType(NetBoxModel):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    schema = models.JSONField(blank=True, default=dict)

    class Meta:
        verbose_name = 'Custom Object Type'

    def __str__(self):
        return self.name

    @property
    def formatted_schema(self):
        result = '<ul>'
        for field_name, field in self.schema.items():
            field_type = field.get('type')
            if field_type in ['object', 'multiobject']:
                content_type = ContentType.objects.get(app_label=field['app_label'], model=field['model'])
                field = content_type
            result += f"<li>{field_name}: {field}</li>"
        result += '</ul>'
        return result

    def get_absolute_url(self):
        return reverse('plugins:netbox_custom_objects:customobjecttype', args=[self.pk])


class CustomObject(NetBoxModel):
    custom_object_type = models.ForeignKey(CustomObjectType, on_delete=models.CASCADE, related_name="custom_objects")
    name = models.CharField(max_length=100, unique=True)
    data = models.JSONField(blank=True, default=dict)

    class Meta:
        verbose_name = 'Custom Object'

    def __str__(self):
        return self.name

    @property
    def formatted_data(self):
        result = '<ul>'
        for field_name, field in self.custom_object_type.schema.items():
            value = self.data.get(field_name)
            field_type = field.get('type')
            if field_type in ['object', 'multiobject']:
                content_type = ContentType.objects.get(app_label=field['app_label'], model=field['model'])
                model_class = content_type.model_class()
                if field_type == 'object':
                    instance = model_class.objects.get(pk=value['object_id'])
                    url = instance.get_absolute_url()
                    result += f'<li>{field_name}: <a href="{url}">{instance}</a></li>'
                    continue
                if field_type == 'multiobject':
                    result += f'<li>{field_name}: <ul>'
                    for item in value:
                        instance = model_class.objects.get(pk=item['object_id'])
                        url = instance.get_absolute_url()
                        result += f'<li><a href="{url}">{instance}</a></li>'
                    result += '</ul></li>'
                    continue
            result += f"<li>{field_name}: {value}</li>"
        result += '</ul>'
        return result

    @property
    def fields(self):
        result = {}
        for field in self.custom_object_type.fields.all():
            result[field.name] = self.get_field_value(field.name)
        return result

    def get_field_value(self, field_name):
        custom_object_type_field = self.custom_object_type.fields.get(name=field_name)
        if custom_object_type_field.type == 'object':
            object_ids = CustomObjectRelation.objects.filter(
                custom_object=self, field=custom_object_type_field
            ).values_list('object_id', flat=True)
            field_objects = custom_object_type_field.model_class.objects.filter(pk__in=object_ids)
            return field_objects if custom_object_type_field.many else field_objects.first()
        return self.data.get(field_name)

    def get_absolute_url(self):
        return reverse('plugins:netbox_custom_objects:customobject', args=[self.pk])


class CustomObjectTypeField(CloningMixin, ExportTemplatesMixin, ChangeLoggedModel):
    # name = models.CharField(max_length=100, unique=True)
    # label = models.CharField(max_length=100, unique=True)
    custom_object_type = models.ForeignKey(CustomObjectType, on_delete=models.CASCADE, related_name="fields")
    # type = models.CharField(max_length=100, choices=CustomFieldTypeChoices)
    # object_types = models.ManyToManyField(
    #     to='core.ObjectType',
    #     related_name='custom_object_types',
    #     help_text=_('The object(s) to which this field applies.')
    # )
    type = models.CharField(
        verbose_name=_('type'),
        max_length=50,
        choices=CustomFieldTypeChoices,
        default=CustomFieldTypeChoices.TYPE_TEXT,
        help_text=_('The type of data this custom field holds')
    )
    related_object_type = models.ForeignKey(
        to='core.ObjectType',
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        help_text=_('The type of NetBox object this field maps to (for object fields)')
    )
    name = models.CharField(
        verbose_name=_('name'),
        max_length=50,
        help_text=_('Internal field name'),
        validators=(
            RegexValidator(
                regex=r'^[a-z0-9_]+$',
                message=_("Only alphanumeric characters and underscores are allowed."),
                flags=re.IGNORECASE
            ),
            RegexValidator(
                regex=r'__',
                message=_("Double underscores are not permitted in custom field names."),
                flags=re.IGNORECASE,
                inverse_match=True
            ),
        )
    )
    label = models.CharField(
        verbose_name=_('label'),
        max_length=50,
        blank=True,
        help_text=_(
            "Name of the field as displayed to users (if not provided, 'the field's name will be used)"
        )
    )
    group_name = models.CharField(
        verbose_name=_('group name'),
        max_length=50,
        blank=True,
        help_text=_("Custom fields within the same group will be displayed together")
    )
    description = models.CharField(
        verbose_name=_('description'),
        max_length=200,
        blank=True
    )
    required = models.BooleanField(
        verbose_name=_('required'),
        default=False,
        help_text=_("This field is required when creating new objects or editing an existing object.")
    )
    unique = models.BooleanField(
        verbose_name=_('must be unique'),
        default=False,
        help_text=_("The value of this field must be unique for the assigned object")
    )
    search_weight = models.PositiveSmallIntegerField(
        verbose_name=_('search weight'),
        default=1000,
        help_text=_(
            "Weighting for search. Lower values are considered more important. Fields with a search weight of zero "
            "will be ignored."
        )
    )
    filter_logic = models.CharField(
        verbose_name=_('filter logic'),
        max_length=50,
        choices=CustomFieldFilterLogicChoices,
        default=CustomFieldFilterLogicChoices.FILTER_LOOSE,
        help_text=_("Loose matches any instance of a given string; exact matches the entire field.")
    )
    default = models.JSONField(
        verbose_name=_('default'),
        blank=True,
        null=True,
        help_text=_(
            'Default value for the field (must be a JSON value). Encapsulate strings with double quotes (e.g. "Foo").'
        )
    )
    related_object_filter = models.JSONField(
        blank=True,
        null=True,
        help_text=_(
            'Filter the object selection choices using a query_params dict (must be a JSON value).'
            'Encapsulate strings with double quotes (e.g. "Foo").'
        )
    )
    weight = models.PositiveSmallIntegerField(
        default=100,
        verbose_name=_('display weight'),
        help_text=_('Fields with higher weights appear lower in a form.')
    )
    validation_minimum = models.BigIntegerField(
        blank=True,
        null=True,
        verbose_name=_('minimum value'),
        help_text=_('Minimum allowed value (for numeric fields)')
    )
    validation_maximum = models.BigIntegerField(
        blank=True,
        null=True,
        verbose_name=_('maximum value'),
        help_text=_('Maximum allowed value (for numeric fields)')
    )
    validation_regex = models.CharField(
        blank=True,
        validators=[validate_regex],
        max_length=500,
        verbose_name=_('validation regex'),
        help_text=_(
            'Regular expression to enforce on text field values. Use ^ and $ to force matching of entire string. For '
            'example, <code>^[A-Z]{3}$</code> will limit values to exactly three uppercase letters.'
        )
    )
    choice_set = models.ForeignKey(
        to='extras.CustomFieldChoiceSet',
        on_delete=models.PROTECT,
        related_name='choices_for_object_type',
        verbose_name=_('choice set'),
        blank=True,
        null=True
    )
    ui_visible = models.CharField(
        max_length=50,
        choices=CustomFieldUIVisibleChoices,
        default=CustomFieldUIVisibleChoices.ALWAYS,
        verbose_name=_('UI visible'),
        help_text=_('Specifies whether the custom field is displayed in the UI')
    )
    ui_editable = models.CharField(
        max_length=50,
        choices=CustomFieldUIEditableChoices,
        default=CustomFieldUIEditableChoices.YES,
        verbose_name=_('UI editable'),
        help_text=_('Specifies whether the custom field value can be edited in the UI')
    )
    is_cloneable = models.BooleanField(
        default=False,
        verbose_name=_('is cloneable'),
        help_text=_('Replicate this value when cloning objects')
    )
    comments = models.TextField(
        verbose_name=_('comments'),
        blank=True
    )

    # For non-object fields, other field attribs (such as choices, length, required) should be added here as a
    # superset, or stored in a JSON field
    # options = models.JSONField(blank=True, default=dict)

    # content_type = models.ForeignKey(ContentType, null=True, blank=True, on_delete=models.CASCADE)
    # many = models.BooleanField(default=False)

    class Meta:
        constraints = (
            models.UniqueConstraint(
                fields=('name', 'custom_object_type'),
                name='%(app_label)s_%(class)s_unique_name'
            ),
        )

    def __str__(self):
        return self.name

    @property
    def model_class(self):
        return apps.get_model(self.related_object_type.app_label, self.related_object_type.model)

    @property
    def is_single_value(self):
        return not self.many

    @property
    def many(self):
        return self.type in ['multiobject', 'multiselect']

    def get_child_relations(self, instance):
        return self.relations.filter(custom_object=instance)

    def get_absolute_url(self):
        return reverse('plugins:netbox_custom_objects:customobjecttype', args=[self.custom_object_type.pk])


class CustomObjectRelation(models.Model):
    custom_object = models.ForeignKey(CustomObject, on_delete=models.CASCADE)
    field = models.ForeignKey(CustomObjectTypeField, on_delete=models.CASCADE, related_name="relations")
    object_id = models.PositiveIntegerField(db_index=True)

    @property
    def instance(self):
        model_class = self.field.related_object_type.model_class()
        return model_class.objects.get(pk=self.object_id)
