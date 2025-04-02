import decimal
import jsonschema
import json
import re
from datetime import datetime, date

import django_filters
from django import forms
from django.conf import settings
from django.db import models
from django.db.models import F, Func, Value
from django.db.models.expressions import RawSQL
from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.core.validators import RegexValidator, ValidationError
from django.urls import reverse
from django.utils.html import escape
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _
from netbox.models import NetBoxModel, ChangeLoggedModel
from netbox.models.features import (
    BookmarksMixin,
    ChangeLoggingMixin,
    CloningMixin,
    CustomFieldsMixin,
    CustomLinksMixin,
    CustomValidationMixin,
    ExportTemplatesMixin,
    JournalingMixin,
    NotificationsMixin,
    TagsMixin,
    EventRulesMixin,
)
from extras.choices import (
    CustomFieldTypeChoices, CustomFieldFilterLogicChoices, CustomFieldUIVisibleChoices, CustomFieldUIEditableChoices
)
from extras.constants import CUSTOMFIELD_EMPTY_VALUES
from utilities import filters
from utilities.datetime import datetime_from_timestamp
from utilities.forms.fields import (
    CSVChoiceField, CSVModelChoiceField, CSVModelMultipleChoiceField, CSVMultipleChoiceField, DynamicChoiceField,
    DynamicModelChoiceField, DynamicModelMultipleChoiceField, DynamicMultipleChoiceField, JSONField, LaxURLField,
)
from utilities.forms.utils import add_blank_choice
from utilities.forms.widgets import APISelect, APISelectMultiple, DatePicker, DateTimePicker
from utilities.querysets import RestrictedQuerySet
from utilities.templatetags.builtins.filters import render_markdown
from utilities.validators import validate_regex
# from .choices import MappingFieldTypeChoices
from extras.models.customfields import SEARCH_TYPES


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


class CustomObject(
    BookmarksMixin,
    ChangeLoggingMixin,
    CloningMixin,
    # CustomFieldsMixin,
    CustomLinksMixin,
    CustomValidationMixin,
    ExportTemplatesMixin,
    JournalingMixin,
    NotificationsMixin,
    TagsMixin,
    EventRulesMixin,
    models.Model,
):
    custom_object_type = models.ForeignKey(CustomObjectType, on_delete=models.CASCADE, related_name="custom_objects")
    name = models.CharField(max_length=100, unique=True)
    data = models.JSONField(blank=True, default=dict)

    objects = RestrictedQuerySet.as_manager()

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
    def custom_field_data(self):
        return self.data

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

    def clean(self):
        super().clean()

        custom_fields = CustomObjectTypeField.objects.filter(custom_object_type=self.custom_object_type)

        # Validate all field values
        for field_name, value in self.custom_field_data.items():
            try:
                custom_field = custom_fields.get(name=field_name)
            except CustomObjectTypeField.DoesNotExist:
                raise ValidationError(_("Unknown field name '{name}' in custom field data.").format(
                    name=field_name
                ))
            try:
                custom_field.validate(value)
            except ValidationError as e:
                raise ValidationError(_("Invalid value for custom field '{name}': {error}").format(
                    name=field_name, error=e.message
                ))

            # Validate uniqueness if enforced
            # TODO: change this to validate uniqueness per custom_object
            if custom_field.unique and value not in CUSTOMFIELD_EMPTY_VALUES:
                if self._meta.model.objects.exclude(pk=self.pk).filter(**{
                    f'custom_field_data__{field_name}': value
                }).exists():
                    raise ValidationError(_("Custom field '{name}' must have a unique value.").format(
                        name=field_name
                    ))

        # Check for missing required values
        for cf in custom_fields:
            if cf.required and cf.name not in self.custom_field_data:
                raise ValidationError(_("Missing required custom field '{name}'.").format(name=cf.name))


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
        ordering = ['group_name', 'weight', 'name']
        verbose_name = _('custom object type field')
        verbose_name_plural = _('custom object type fields')
        constraints = (
            models.UniqueConstraint(
                fields=('name', 'custom_object_type'),
                name='%(app_label)s_%(class)s_unique_name'
            ),
        )

    def __str__(self):
        return self.label or self.name.replace('_', ' ').capitalize()

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

    @property
    def docs_url(self):
        return f'{settings.STATIC_URL}docs/models/extras/customfield/'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Cache instance's original name so we can check later whether it has changed
        self._name = self.__dict__.get('name')

    @property
    def search_type(self):
        return SEARCH_TYPES.get(self.type)

    @property
    def choices(self):
        if self.choice_set:
            return self.choice_set.choices
        return []

    def get_ui_visible_color(self):
        return CustomFieldUIVisibleChoices.colors.get(self.ui_visible)

    def get_ui_editable_color(self):
        return CustomFieldUIEditableChoices.colors.get(self.ui_editable)

    def get_choice_label(self, value):
        if not hasattr(self, '_choice_map'):
            self._choice_map = dict(self.choices)
        return self._choice_map.get(value, value)

    def populate_initial_data(self, content_types):
        """
        Populate initial custom field data upon either a) the creation of a new CustomField, or
        b) the assignment of an existing CustomField to new object types.
        """
        if self.default is None:
            # We have to convert None to a JSON null for jsonb_set()
            value = RawSQL("'null'::jsonb", [])
        else:
            value = Value(self.default, models.JSONField())
        for ct in content_types:
            ct.model_class().objects.update(
                custom_field_data=Func(
                    F('custom_field_data'),
                    Value([self.name]),
                    value,
                    function='jsonb_set'
                )
            )

    def remove_stale_data(self, content_types):
        """
        Delete custom field data which is no longer relevant (either because the CustomField is
        no longer assigned to a model, or because it has been deleted).
        """
        for ct in content_types:
            if model := ct.model_class():
                model.objects.update(
                    custom_field_data=F('custom_field_data') - self.name
                )

    def rename_object_data(self, old_name, new_name):
        """
        Called when a CustomField has been renamed. Removes the original key and inserts the new
        one, copying the value of the old key.
        """
        for ct in self.object_types.all():
            ct.model_class().objects.update(
                custom_field_data=Func(
                    F('custom_field_data') - old_name,
                    Value([new_name]),
                    Func(
                        F('custom_field_data'),
                        function='jsonb_extract_path_text',
                        template=f"to_jsonb(%(expressions)s -> '{old_name}')"
                    ),
                    function='jsonb_set')
            )

    def clean(self):
        super().clean()

        # Validate the field's default value (if any)
        if self.default is not None:
            try:
                if self.type in (CustomFieldTypeChoices.TYPE_TEXT, CustomFieldTypeChoices.TYPE_LONGTEXT):
                    default_value = str(self.default)
                else:
                    default_value = self.default
                self.validate(default_value)
            except ValidationError as err:
                raise ValidationError({
                    'default': _(
                        'Invalid default value "{value}": {error}'
                    ).format(value=self.default, error=err.message)
                })

        # Minimum/maximum values can be set only for numeric fields
        if self.type not in (CustomFieldTypeChoices.TYPE_INTEGER, CustomFieldTypeChoices.TYPE_DECIMAL):
            if self.validation_minimum:
                raise ValidationError({'validation_minimum': _("A minimum value may be set only for numeric fields")})
            if self.validation_maximum:
                raise ValidationError({'validation_maximum': _("A maximum value may be set only for numeric fields")})

        # Regex validation can be set only for text fields
        regex_types = (
            CustomFieldTypeChoices.TYPE_TEXT,
            CustomFieldTypeChoices.TYPE_LONGTEXT,
            CustomFieldTypeChoices.TYPE_URL,
        )
        if self.validation_regex and self.type not in regex_types:
            raise ValidationError({
                'validation_regex': _("Regular expression validation is supported only for text and URL fields")
            })

        # Uniqueness can not be enforced for boolean fields
        if self.unique and self.type == CustomFieldTypeChoices.TYPE_BOOLEAN:
            raise ValidationError({
                'unique': _("Uniqueness cannot be enforced for boolean fields")
            })

        # Choice set must be set on selection fields, and *only* on selection fields
        if self.type in (
                CustomFieldTypeChoices.TYPE_SELECT,
                CustomFieldTypeChoices.TYPE_MULTISELECT
        ):
            if not self.choice_set:
                raise ValidationError({
                    'choice_set': _("Selection fields must specify a set of choices.")
                })
        elif self.choice_set:
            raise ValidationError({
                'choice_set': _("Choices may be set only on selection fields.")
            })

        # Object fields must define an object_type; other fields must not
        if self.type in (CustomFieldTypeChoices.TYPE_OBJECT, CustomFieldTypeChoices.TYPE_MULTIOBJECT):
            if not self.related_object_type:
                raise ValidationError({
                    'related_object_type': _("Object fields must define an object type.")
                })
        elif self.related_object_type:
            raise ValidationError({
                'type': _("{type} fields may not define an object type.") .format(type=self.get_type_display())
            })

        # Related object filter can be set only for object-type fields, and must contain a dictionary mapping (if set)
        if self.related_object_filter is not None:
            if self.type not in (CustomFieldTypeChoices.TYPE_OBJECT, CustomFieldTypeChoices.TYPE_MULTIOBJECT):
                raise ValidationError({
                    'related_object_filter': _("A related object filter can be defined only for object fields.")
                })
            if type(self.related_object_filter) is not dict:
                raise ValidationError({
                    'related_object_filter': _("Filter must be defined as a dictionary mapping attributes to values.")
                })

    def serialize(self, value):
        """
        Prepare a value for storage as JSON data.
        """
        if value is None:
            return value
        if self.type == CustomFieldTypeChoices.TYPE_DATE and type(value) is date:
            return value.isoformat()
        if self.type == CustomFieldTypeChoices.TYPE_DATETIME and type(value) is datetime:
            return value.isoformat()
        if self.type == CustomFieldTypeChoices.TYPE_OBJECT:
            return value.pk
        if self.type == CustomFieldTypeChoices.TYPE_MULTIOBJECT:
            return [obj.pk for obj in value] or None
        return value

    def deserialize(self, value):
        """
        Convert JSON data to a Python object suitable for the field type.
        """
        if value is None:
            return value
        if self.type == CustomFieldTypeChoices.TYPE_DATE:
            try:
                return date.fromisoformat(value)
            except ValueError:
                return value
        if self.type == CustomFieldTypeChoices.TYPE_DATETIME:
            try:
                return datetime.fromisoformat(value)
            except ValueError:
                return value
        if self.type == CustomFieldTypeChoices.TYPE_OBJECT:
            model = self.related_object_type.model_class()
            return model.objects.filter(pk=value).first()
        if self.type == CustomFieldTypeChoices.TYPE_MULTIOBJECT:
            model = self.related_object_type.model_class()
            return model.objects.filter(pk__in=value)
        return value

    def to_form_field(self, set_initial=True, enforce_required=True, enforce_visibility=True, for_csv_import=False):
        """
        Return a form field suitable for setting a CustomField's value for an object.

        set_initial: Set initial data for the field. This should be False when generating a field for bulk editing.
        enforce_required: Honor the value of CustomField.required. Set to False for filtering/bulk editing.
        enforce_visibility: Honor the value of CustomField.ui_visible. Set to False for filtering.
        for_csv_import: Return a form field suitable for bulk import of objects in CSV format.
        """
        initial = self.default if set_initial else None
        required = self.required if enforce_required else False

        # Integer
        if self.type == CustomFieldTypeChoices.TYPE_INTEGER:
            field = forms.IntegerField(
                required=required,
                initial=initial,
                min_value=self.validation_minimum,
                max_value=self.validation_maximum
            )

        # Decimal
        elif self.type == CustomFieldTypeChoices.TYPE_DECIMAL:
            field = forms.DecimalField(
                required=required,
                initial=initial,
                max_digits=12,
                decimal_places=4,
                min_value=self.validation_minimum,
                max_value=self.validation_maximum
            )

        # Boolean
        elif self.type == CustomFieldTypeChoices.TYPE_BOOLEAN:
            choices = (
                (None, '---------'),
                (True, _('True')),
                (False, _('False')),
            )
            field = forms.NullBooleanField(
                required=required, initial=initial, widget=forms.Select(choices=choices)
            )

        # Date
        elif self.type == CustomFieldTypeChoices.TYPE_DATE:
            field = forms.DateField(required=required, initial=initial, widget=DatePicker())

        # Date & time
        elif self.type == CustomFieldTypeChoices.TYPE_DATETIME:
            field = forms.DateTimeField(required=required, initial=initial, widget=DateTimePicker())

        # Select
        elif self.type in (CustomFieldTypeChoices.TYPE_SELECT, CustomFieldTypeChoices.TYPE_MULTISELECT):
            choices = self.choice_set.choices
            default_choice = self.default if self.default in self.choices else None

            if not required or default_choice is None:
                choices = add_blank_choice(choices)

            # Set the initial value to the first available choice (if any)
            if set_initial and default_choice:
                initial = default_choice

            if for_csv_import:
                if self.type == CustomFieldTypeChoices.TYPE_SELECT:
                    field_class = CSVChoiceField
                else:
                    field_class = CSVMultipleChoiceField
                field = field_class(choices=choices, required=required, initial=initial)
            else:
                if self.type == CustomFieldTypeChoices.TYPE_SELECT:
                    field_class = DynamicChoiceField
                    widget_class = APISelect
                else:
                    field_class = DynamicMultipleChoiceField
                    widget_class = APISelectMultiple
                field = field_class(
                    choices=choices,
                    required=required,
                    initial=initial,
                    widget=widget_class(api_url=f'/api/extras/custom-field-choice-sets/{self.choice_set.pk}/choices/')
                )

        # URL
        elif self.type == CustomFieldTypeChoices.TYPE_URL:
            field = LaxURLField(assume_scheme='https', required=required, initial=initial)

        # JSON
        elif self.type == CustomFieldTypeChoices.TYPE_JSON:
            field = JSONField(required=required, initial=json.dumps(initial) if initial else None)

        # Object
        elif self.type == CustomFieldTypeChoices.TYPE_OBJECT:
            model = self.related_object_type.model_class()
            field_class = CSVModelChoiceField if for_csv_import else DynamicModelChoiceField
            kwargs = {
                'queryset': model.objects.all(),
                'required': required,
                'initial': initial,
            }
            if not for_csv_import:
                kwargs['query_params'] = self.related_object_filter
                kwargs['selector'] = True

            field = field_class(**kwargs)

        # Multiple objects
        elif self.type == CustomFieldTypeChoices.TYPE_MULTIOBJECT:
            model = self.related_object_type.model_class()
            field_class = CSVModelMultipleChoiceField if for_csv_import else DynamicModelMultipleChoiceField
            kwargs = {
                'queryset': model.objects.all(),
                'required': required,
                'initial': initial,
            }
            if not for_csv_import:
                kwargs['query_params'] = self.related_object_filter
                kwargs['selector'] = True

            field = field_class(**kwargs)

        # Text
        else:
            widget = forms.Textarea if self.type == CustomFieldTypeChoices.TYPE_LONGTEXT else None
            field = forms.CharField(required=required, initial=initial, widget=widget)
            if self.validation_regex:
                field.validators = [
                    RegexValidator(
                        regex=self.validation_regex,
                        message=mark_safe(_("Values must match this regex: <code>{regex}</code>").format(
                            regex=escape(self.validation_regex)
                        ))
                    )
                ]

        field.model = self
        field.label = str(self)
        if self.description:
            field.help_text = render_markdown(self.description)

        # Annotate read-only fields
        if enforce_visibility and self.ui_editable != CustomFieldUIEditableChoices.YES:
            field.disabled = True

        return field

    def to_filter(self, lookup_expr=None):
        """
        Return a django_filters Filter instance suitable for this field type.

        :param lookup_expr: Custom lookup expression (optional)
        """
        kwargs = {
            'field_name': f'custom_field_data__{self.name}'
        }
        if lookup_expr is not None:
            kwargs['lookup_expr'] = lookup_expr

        # Text/URL
        if self.type in (
                CustomFieldTypeChoices.TYPE_TEXT,
                CustomFieldTypeChoices.TYPE_LONGTEXT,
                CustomFieldTypeChoices.TYPE_URL,
        ):
            filter_class = filters.MultiValueCharFilter
            if self.filter_logic == CustomFieldFilterLogicChoices.FILTER_LOOSE:
                kwargs['lookup_expr'] = 'icontains'

        # Integer
        elif self.type == CustomFieldTypeChoices.TYPE_INTEGER:
            filter_class = filters.MultiValueNumberFilter

        # Decimal
        elif self.type == CustomFieldTypeChoices.TYPE_DECIMAL:
            filter_class = filters.MultiValueDecimalFilter

        # Boolean
        elif self.type == CustomFieldTypeChoices.TYPE_BOOLEAN:
            filter_class = django_filters.BooleanFilter

        # Date
        elif self.type == CustomFieldTypeChoices.TYPE_DATE:
            filter_class = filters.MultiValueDateFilter

        # Date & time
        elif self.type == CustomFieldTypeChoices.TYPE_DATETIME:
            filter_class = filters.MultiValueDateTimeFilter

        # Select
        elif self.type == CustomFieldTypeChoices.TYPE_SELECT:
            filter_class = filters.MultiValueCharFilter

        # Multiselect
        elif self.type == CustomFieldTypeChoices.TYPE_MULTISELECT:
            filter_class = filters.MultiValueArrayFilter

        # Object
        elif self.type == CustomFieldTypeChoices.TYPE_OBJECT:
            filter_class = filters.MultiValueNumberFilter

        # Multi-object
        elif self.type == CustomFieldTypeChoices.TYPE_MULTIOBJECT:
            filter_class = filters.MultiValueNumberFilter
            kwargs['lookup_expr'] = 'contains'

        # Unsupported custom field type
        else:
            return None

        filter_instance = filter_class(**kwargs)
        filter_instance.custom_field = self

        return filter_instance

    def validate(self, value):
        """
        Validate a value according to the field's type validation rules.
        """
        if value not in [None, '']:

            # Validate text field
            if self.type in (CustomFieldTypeChoices.TYPE_TEXT, CustomFieldTypeChoices.TYPE_LONGTEXT):
                if type(value) is not str:
                    raise ValidationError(_("Value must be a string."))
                if self.validation_regex and not re.match(self.validation_regex, value):
                    raise ValidationError(_("Value must match regex '{regex}'").format(regex=self.validation_regex))

            # Validate integer
            elif self.type == CustomFieldTypeChoices.TYPE_INTEGER:
                if type(value) is not int:
                    raise ValidationError(_("Value must be an integer."))
                if self.validation_minimum is not None and value < self.validation_minimum:
                    raise ValidationError(
                        _("Value must be at least {minimum}").format(minimum=self.validation_minimum)
                    )
                if self.validation_maximum is not None and value > self.validation_maximum:
                    raise ValidationError(
                        _("Value must not exceed {maximum}").format(maximum=self.validation_maximum)
                    )

            # Validate decimal
            elif self.type == CustomFieldTypeChoices.TYPE_DECIMAL:
                try:
                    decimal.Decimal(value)
                except decimal.InvalidOperation:
                    raise ValidationError(_("Value must be a decimal."))
                if self.validation_minimum is not None and value < self.validation_minimum:
                    raise ValidationError(
                        _("Value must be at least {minimum}").format(minimum=self.validation_minimum)
                    )
                if self.validation_maximum is not None and value > self.validation_maximum:
                    raise ValidationError(
                        _("Value must not exceed {maximum}").format(maximum=self.validation_maximum)
                    )

            # Validate boolean
            elif self.type == CustomFieldTypeChoices.TYPE_BOOLEAN and value not in [True, False, 1, 0]:
                raise ValidationError(_("Value must be true or false."))

            # Validate date
            elif self.type == CustomFieldTypeChoices.TYPE_DATE:
                if type(value) is not date:
                    try:
                        date.fromisoformat(value)
                    except ValueError:
                        raise ValidationError(_("Date values must be in ISO 8601 format (YYYY-MM-DD)."))

            # Validate date & time
            elif self.type == CustomFieldTypeChoices.TYPE_DATETIME:
                if type(value) is not datetime:
                    try:
                        datetime_from_timestamp(value)
                    except ValueError:
                        raise ValidationError(
                            _("Date and time values must be in ISO 8601 format (YYYY-MM-DD HH:MM:SS).")
                        )

            # Validate selected choice
            elif self.type == CustomFieldTypeChoices.TYPE_SELECT:
                if value not in self.choice_set.values:
                    raise ValidationError(
                        _("Invalid choice ({value}) for choice set {choiceset}.").format(
                            value=value,
                            choiceset=self.choice_set
                        )
                    )

            # Validate all selected choices
            elif self.type == CustomFieldTypeChoices.TYPE_MULTISELECT:
                if not set(value).issubset(self.choice_set.values):
                    raise ValidationError(
                        _("Invalid choice(s) ({value}) for choice set {choiceset}.").format(
                            value=value,
                            choiceset=self.choice_set
                        )
                    )

            # Validate selected object
            elif self.type == CustomFieldTypeChoices.TYPE_OBJECT:
                if type(value) is not int:
                    raise ValidationError(_("Value must be an object ID, not {type}").format(type=type(value).__name__))

            # Validate selected objects
            elif self.type == CustomFieldTypeChoices.TYPE_MULTIOBJECT:
                if type(value) is not list:
                    raise ValidationError(
                        _("Value must be a list of object IDs, not {type}").format(type=type(value).__name__)
                    )
                for id in value:
                    if type(id) is not int:
                        raise ValidationError(_("Found invalid object ID: {id}").format(id=id))

        elif self.required:
            raise ValidationError(_("Required field cannot be empty."))


class CustomObjectRelation(models.Model):
    custom_object = models.ForeignKey(CustomObject, on_delete=models.CASCADE)
    field = models.ForeignKey(CustomObjectTypeField, on_delete=models.CASCADE, related_name="relations")
    object_id = models.PositiveIntegerField(db_index=True)

    @property
    def instance(self):
        model_class = self.field.related_object_type.model_class()
        return model_class.objects.get(pk=self.object_id)
