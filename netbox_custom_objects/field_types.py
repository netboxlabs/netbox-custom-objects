import hashlib
import json
import logging

import django_tables2 as tables
from django import forms
from django.apps import apps
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import FieldDoesNotExist
from django.core.validators import RegexValidator
from django.db import connection, models
from django.db.utils import OperationalError, ProgrammingError
from django.db.models.fields.related import ForeignKey, ManyToManyDescriptor
from django.db.models.manager import Manager
from django.utils.html import escape
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _
from extras.choices import CustomFieldTypeChoices, CustomFieldUIEditableChoices
from utilities.api import get_serializer_for_model
from utilities.forms.fields import (
    CSVChoiceField, CSVModelChoiceField,
    CSVModelMultipleChoiceField, CSVMultipleChoiceField,
    DynamicChoiceField, DynamicModelChoiceField,
    DynamicModelMultipleChoiceField,
    DynamicMultipleChoiceField, JSONField,
    LaxURLField,
)
from utilities.forms.utils import add_blank_choice
from utilities.forms.widgets import (
    APISelect, APISelectMultiple, DatePicker,
    DateTimePicker,
)
from utilities.templatetags.builtins.filters import linkify, render_markdown
from netbox.tables.columns import BooleanColumn

from netbox_custom_objects.choices import ObjectFieldOnDeleteChoices
from netbox_custom_objects.constants import APP_LABEL
from netbox_custom_objects.utilities import extract_cot_id_from_model_name, generate_model

logger = logging.getLogger(__name__)

# PostgreSQL's hard limit for identifier names is 63 bytes.
_PG_MAX_IDENTIFIER_LEN = 63


def _safe_pg_identifier(full_name: str) -> str:
    """
    Return a DB-safe identifier that fits within PostgreSQL's 63-char limit.

    If the full name fits, it is returned unchanged.  If it is too long, the name is
    truncated and an 8-character MD5 digest of the *full* name is appended so that
    different long names with a shared prefix do not collide.
    """
    if len(full_name) <= _PG_MAX_IDENTIFIER_LEN:
        return full_name
    digest = hashlib.md5(full_name.encode()).hexdigest()[:8]
    # Reserve 9 chars for "_" + 8-char digest; strip trailing underscores from the prefix.
    prefix = full_name[:_PG_MAX_IDENTIFIER_LEN - 9].rstrip("_")
    return f"{prefix}_{digest}"


def _safe_index_name(full_name: str) -> str:
    """Alias of _safe_pg_identifier for index names."""
    return _safe_pg_identifier(full_name)


def safe_table_name(full_name: str) -> str:
    """
    Return a DB-safe table name that fits within PostgreSQL's 63-char identifier limit.

    If the full name fits, it is returned unchanged.  If it is too long, the name is
    truncated and an 8-character MD5 digest of the *full* name is appended so that
    different long names with a shared prefix do not collide.
    """
    return _safe_pg_identifier(full_name)


class LazyForeignKey(ForeignKey):
    """
    A ForeignKey field that can handle lazy model references.
    The target model is resolved after the model is fully generated.
    """

    def __init__(self, to_model_name, *args, **kwargs):
        self._to_model_name = to_model_name

        # Filter out our custom parameters before passing to Django's ForeignKey
        field_kwargs = {k: v for k, v in kwargs.items() if not k.startswith('_') and k != 'generating_models'}

        super().__init__(to_model_name, *args, **field_kwargs)

    def contribute_to_class(self, cls, name, **kwargs):
        super().contribute_to_class(cls, name, **kwargs)
        # Mark this field for later resolution
        setattr(cls, f"_resolve_{name}_model", self._resolve_model)

    def _resolve_model(self, model):
        """Resolve the lazy reference to the actual model class."""
        from django.apps import apps as django_apps
        from netbox_custom_objects.utilities import extract_cot_id_from_model_name

        # Retrieve the current class from the app registry.
        try:
            actual_model = django_apps.get_model(self._to_model_name)
        except LookupError:
            # Target model not yet registered — deferred to Pass 2 / caller retry.
            return

        # For cross-COT FKs, also seed _model_cache with this class and the
        # DB-current cache_timestamp.  This ensures that a later call to
        # cot.get_model() returns the SAME Python class via a cache hit, which
        # is required for Django's isinstance check in ForeignKey.__set__.
        # Without this, get_model() would miss the cache and generate a new
        # class object — logically identical but a different Python object —
        # causing the isinstance check to fail.
        #
        # We intentionally use the class already registered in apps.all_models
        # rather than generating a new one, so that reverse accessors (e.g.
        # `certificates`) that were wired to this class by Django's lazy
        # callback remain accessible on instances of the same class.
        _app_label, model_name = self._to_model_name.rsplit('.', 1)
        cot_id_str = extract_cot_id_from_model_name(model_name.lower())
        if cot_id_str is not None:
            try:
                from netbox_custom_objects.models import CustomObjectType
                cot = CustomObjectType.objects.get(pk=int(cot_id_str))
                # Only seed the cache if nothing is cached yet; don't overwrite
                # a full model that was generated by a previous get_model() call.
                if not CustomObjectType.is_model_cached(cot.id):
                    with CustomObjectType._global_lock:
                        CustomObjectType._model_cache[cot.id] = (actual_model, cot.cache_timestamp)
            except (CustomObjectType.DoesNotExist, OperationalError, ProgrammingError):
                pass

        self.remote_field.model = actual_model
        self.to = actual_model


def _make_lazy_cot_fk(cot, field, on_delete, **field_kwargs):
    """Build a LazyForeignKey pointing to the given COT model.

    Shared by the self-referential and cross-COT branches of
    ObjectFieldType.get_model_field — both use an identical LazyForeignKey
    construction that defers resolution until Pass 2 of the startup loop.
    """
    model_name = f"{APP_LABEL}.{cot.get_table_model_name(cot.id)}"
    if field.related_name:
        related_name = field.related_name
    else:
        table_model_name = field.custom_object_type.get_table_model_name(
            field.custom_object_type.id
        ).lower()
        related_name = f"{table_model_name}_{field.name}_set"
    return LazyForeignKey(
        model_name,
        null=True,
        blank=True,
        on_delete=on_delete,
        related_name=related_name,
        **field_kwargs
    )


class FieldType:

    def get_display_value(self, instance, field_name):
        """
        This value is used as the object title in the Custom Object detail view.
        """
        return getattr(instance, field_name)

    def get_model_field(self, field, **kwargs):
        raise NotImplementedError

    def get_serializer_field(self, field, **kwargs):
        raise NotImplementedError

    def get_filterform_field(self, field, **kwargs):
        raise NotImplementedError

    def get_form_field(self, field, **kwargs):
        raise NotImplementedError

    def _safe_kwargs(self, **kwargs):
        """
        Create a safe kwargs dict that can be passed to Django field constructors.
        This method automatically filters out any custom parameters.
        """
        return {k: v for k, v in kwargs.items()
                if not k.startswith('_') and k != 'generating_models'}

    def get_annotated_form_field(self, field, enforce_visibility=True, **kwargs):
        form_field = self.get_form_field(field, **kwargs)
        form_field.model = field
        form_field.label = str(field)
        # Set the field name so Django can properly bind it to the instance
        form_field.name = field.name

        if field.description:
            form_field.help_text = render_markdown(field.description)

        # Annotate read-only fields
        if enforce_visibility and field.ui_editable != CustomFieldUIEditableChoices.YES:
            form_field.disabled = True

        return form_field

    def get_table_column_field(self, field, **kwargs):
        raise NotImplementedError

    def render_table_column_linkified(self, record):
        return linkify(record)

    def _get_related_content_type(self, field):
        """
        Return the ContentType for field.related_object_type_id.

        Raises NotImplementedError (rather than ContentType.DoesNotExist) so that
        all callers — which already guard against NotImplementedError — skip the
        field gracefully when the FK is null or its ContentType row is missing.
        """
        from django.contrib.contenttypes.models import ContentType as CT
        if not field.related_object_type_id:
            raise NotImplementedError(
                f"Field {field.name!r} has no related_object_type set"
            )
        try:
            return CT.objects.get(pk=field.related_object_type_id)
        except CT.DoesNotExist:
            raise NotImplementedError(
                f"Field {field.name!r}: related_object_type_id="
                f"{field.related_object_type_id} references a missing ContentType"
            )

    def after_model_generation(self, instance, model, field_name): ...

    def create_m2m_table(self, instance, model, field_name): ...


class TextFieldType(FieldType):

    def get_model_field(self, field, **kwargs):
        field_kwargs = self._safe_kwargs(**kwargs)
        field_kwargs.update({"default": field.default, "unique": field.unique})
        return models.CharField(null=True, blank=True, **field_kwargs)

    def get_form_field(self, field, **kwargs):
        validators = []
        if field.validation_regex:
            validators = [
                RegexValidator(
                    regex=field.validation_regex,
                    message=mark_safe(
                        _("Values must match this regex: <code>{regex}</code>").format(
                            regex=escape(field.validation_regex)
                        )
                    ),
                )
            ]
        return forms.CharField(
            required=field.required, initial=field.default, validators=validators
        )

    def get_filterform_field(self, field, **kwargs):
        return forms.CharField(
            label=field,
            max_length=100,
            required=False,
        )


class LongTextFieldType(FieldType):
    def get_filterform_field(self, field, **kwargs):
        return forms.CharField(
            label=field,
            required=False,
        )

    def get_model_field(self, field, **kwargs):
        field_kwargs = self._safe_kwargs(**kwargs)
        field_kwargs.update({"default": field.default, "unique": field.unique})
        return models.TextField(null=True, blank=True, **field_kwargs)

    def get_form_field(self, field, **kwargs):
        widget = forms.Textarea
        validators = []
        if field.validation_regex:
            validators = [
                RegexValidator(
                    regex=field.validation_regex,
                    message=mark_safe(
                        _("Values must match this regex: <code>{regex}</code>").format(
                            regex=escape(field.validation_regex)
                        )
                    ),
                )
            ]
        return forms.CharField(
            widget=widget,
            required=field.required,
            initial=field.default,
            validators=validators,
        )

    def render_table_column(self, value):
        return render_markdown(value)


class IntegerFieldType(FieldType):

    def get_model_field(self, field, **kwargs):
        # TODO: handle all args for IntegerField
        field_kwargs = self._safe_kwargs(**kwargs)
        field_kwargs.update({"default": field.default, "unique": field.unique})
        return models.IntegerField(null=True, blank=True, **field_kwargs)

    def get_filterform_field(self, field, **kwargs):
        return forms.IntegerField(
            label=field,
            required=False,
        )

    def get_form_field(self, field, **kwargs):
        return forms.IntegerField(
            required=field.required,
            initial=field.default,
            min_value=field.validation_minimum,
            max_value=field.validation_maximum,
        )


class DecimalFieldType(FieldType):
    def get_model_field(self, field, **kwargs):
        field_kwargs = self._safe_kwargs(**kwargs)
        field_kwargs.update({"default": field.default, "unique": field.unique})
        return models.DecimalField(
            null=True,
            blank=True,
            max_digits=8,
            decimal_places=2,
            **field_kwargs
        )

    def get_form_field(self, field, **kwargs):
        return forms.DecimalField(
            required=field.required,
            initial=field.default,
            max_digits=12,
            decimal_places=4,
            min_value=field.validation_minimum,
            max_value=field.validation_maximum,
        )

    def get_filterform_field(self, field, **kwargs):
        return forms.DecimalField(
            label=field,
            required=False,
            min_value=field.validation_minimum,
            max_value=field.validation_maximum,
        )


class BooleanFieldType(FieldType):
    def get_model_field(self, field, **kwargs):
        field_kwargs = self._safe_kwargs(**kwargs)
        field_kwargs.update({"default": field.default, "unique": field.unique})
        return models.BooleanField(null=True, blank=True, **field_kwargs)

    def get_form_field(self, field, **kwargs):
        choices = (
            (None, "---------"),
            (True, _("True")),
            (False, _("False")),
        )
        return forms.NullBooleanField(
            required=field.required,
            initial=field.default,
            widget=forms.Select(choices=choices),
        )

    def get_filterform_field(self, field, **kwargs):
        choices = (
            ('', '---------'),
            ('true', _("Yes")),
            ('false', _("No")),
        )
        return forms.NullBooleanField(
            label=field,
            required=False,
            widget=forms.Select(choices=choices),
        )

    def get_table_column_field(self, field, **kwargs):
        return BooleanColumn()


class DateFieldType(FieldType):
    def get_model_field(self, field, **kwargs):
        field_kwargs = self._safe_kwargs(**kwargs)
        field_kwargs.update({"default": field.default, "unique": field.unique})
        return models.DateField(null=True, blank=True, **field_kwargs)

    def get_form_field(self, field, **kwargs):
        return forms.DateField(
            required=field.required, initial=field.default, widget=DatePicker()
        )

    def get_filterform_field(self, field, **kwargs):
        return forms.DateField(
            label=field,
            required=False,
            widget=DatePicker(),
        )


class DateTimeFieldType(FieldType):
    def get_model_field(self, field, **kwargs):
        field_kwargs = self._safe_kwargs(**kwargs)
        field_kwargs.update({"default": field.default, "unique": field.unique})
        return models.DateTimeField(null=True, blank=True, **field_kwargs)

    def get_form_field(self, field, **kwargs):
        return forms.DateTimeField(
            required=field.required, initial=field.default, widget=DateTimePicker()
        )

    def get_filterform_field(self, field, **kwargs):
        return forms.DateTimeField(
            label=field,
            required=False,
            widget=DateTimePicker(),
        )


class URLFieldType(FieldType):
    def get_model_field(self, field, **kwargs):
        field_kwargs = self._safe_kwargs(**kwargs)
        field_kwargs.update({"default": field.default, "unique": field.unique})
        return models.URLField(null=True, blank=True, **field_kwargs)

    def get_form_field(self, field, **kwargs):
        return LaxURLField(
            assume_scheme="https", required=field.required, initial=field.default
        )

    def get_filterform_field(self, field, **kwargs):
        return forms.CharField(
            label=field,
            required=False,
        )


class JSONFieldType(FieldType):
    def get_model_field(self, field, **kwargs):
        field_kwargs = self._safe_kwargs(**kwargs)
        field_kwargs.update({"default": field.default, "unique": field.unique})
        return models.JSONField(null=True, blank=True, **field_kwargs)

    def get_form_field(self, field, **kwargs):
        return JSONField(
            required=field.required,
            initial=json.dumps(field.default) if field.default else None,
        )

    def get_filterform_field(self, field, **kwargs):
        return forms.CharField(
            label=field,
            required=False,
        )


class SelectFieldType(FieldType):
    def get_display_value(self, instance, field_name):
        value = getattr(instance, field_name)
        if value is None:
            return ''
        return getattr(instance, f'get_{field_name}_display')() or value

    def get_table_column_field(self, field, **kwargs):
        choices_dict = dict(field.choices)
        choice_set = field.choice_set

        class _SelectLabelColumn(tables.Column):
            def render(self, value):
                if value is None:
                    return self.default
                label = choices_dict.get(value, value)
                color = choice_set.get_choice_color(value) if choice_set else None
                if color:
                    return mark_safe(
                        f'<span class="badge text-bg-{escape(color)}">{escape(label)}</span>'
                    )
                return label

        return _SelectLabelColumn()

    def get_filterform_field(self, field, **kwargs):
        return DynamicMultipleChoiceField(
            choices=field.choice_set.choices,
            label=field,
            required=False,
            widget=APISelectMultiple(
                api_url=f'/api/extras/custom-field-choice-sets/{field.choice_set.pk}/choices/'
            ),
        )

    def get_model_field(self, field, **kwargs):
        field_kwargs = self._safe_kwargs(**kwargs)
        field_kwargs.update({"default": field.default, "unique": field.unique})
        return models.CharField(
            max_length=100,
            choices=field.choices,
            null=True,
            blank=True,
            **field_kwargs
        )

    def get_form_field(self, field, for_csv_import=False, **kwargs):
        choices = field.choice_set.choices
        default_choice = field.default if field.default in field.choices else None

        if not field.required or default_choice is None:
            choices = add_blank_choice(choices)

        # Set the initial value to the first available choice (if any)
        initial = field.default
        if default_choice:
            initial = default_choice

        if for_csv_import:
            field_class = CSVChoiceField
            return field_class(
                choices=choices, required=field.required, initial=initial
            )
        else:
            field_class = DynamicChoiceField
            widget_class = APISelect
            return field_class(
                choices=choices,
                required=field.required,
                initial=initial,
                widget=widget_class(
                    api_url=f"/api/extras/custom-field-choice-sets/{field.choice_set.pk}/choices/"
                ),
            )


class MultiSelectFieldType(FieldType):
    def get_filterform_field(self, field, **kwargs):
        choices = field.choice_set.choices
        return DynamicMultipleChoiceField(
            choices=choices,
            label=field,
            required=False,
            widget=APISelectMultiple(
                api_url=f'/api/extras/custom-field-choice-sets/{field.choice_set.pk}/choices/'
            ),
        )

    def get_display_value(self, instance, field_name):
        values = getattr(instance, field_name) or []
        if not values:
            return ''
        choices_dict = dict(instance._meta.get_field(field_name).base_field.choices)
        return ', '.join(choices_dict.get(v, v) for v in values)

    def get_model_field(self, field, **kwargs):
        field_kwargs = self._safe_kwargs(**kwargs)
        field_kwargs.update({"default": field.default, "unique": field.unique})
        return ArrayField(
            base_field=models.CharField(max_length=50, choices=field.choices),
            null=True,
            blank=True,
            **field_kwargs
        )

    def get_form_field(self, field, for_csv_import=False, **kwargs):
        choices = field.choice_set.choices
        default_choice = field.default if field.default in field.choices else None

        if not field.required or default_choice is None:
            choices = add_blank_choice(choices)

        # Set the initial value to the first available choice (if any)
        initial = field.default
        if default_choice:
            initial = default_choice

        if for_csv_import:
            field_class = CSVMultipleChoiceField
            return field_class(
                choices=choices, required=field.required, initial=initial
            )
        else:
            field_class = DynamicMultipleChoiceField
            widget_class = APISelectMultiple
            return field_class(
                choices=choices,
                required=field.required,
                initial=initial,
                widget=widget_class(
                    api_url=f"/api/extras/custom-field-choice-sets/{field.choice_set.pk}/choices/"
                ),
            )

    # TODO: Implement this
    # def get_form_field(self, field, required, label, **kwargs):
    #     return forms.MultipleChoiceField(
    #         choices=field.choices, required=required, label=label, **kwargs
    #     )

    def get_table_column_field(self, field, **kwargs):
        choices_dict = dict(field.choices)
        choice_set = field.choice_set

        class _MultiSelectLabelColumn(tables.Column):
            def render(self, value):
                if not value:
                    return self.default
                pairs = [
                    (choices_dict.get(v, v), choice_set.get_choice_color(v) if choice_set else None)
                    for v in value
                ]
                if any(color for _, color in pairs):
                    badges = ''.join(
                        f'<span class="badge text-bg-{escape(color)}">{escape(label)}</span>'
                        if color else
                        f'<span class="badge">{escape(label)}</span>'
                        for label, color in pairs
                    )
                    return mark_safe(f'<div class="d-flex flex-wrap gap-1">{badges}</div>')
                return ', '.join(label for label, _ in pairs)

        return _MultiSelectLabelColumn()


class ObjectFieldType(FieldType):
    _ON_DELETE_MAP = {
        ObjectFieldOnDeleteChoices.CASCADE: models.CASCADE,
        ObjectFieldOnDeleteChoices.SET_NULL: models.SET_NULL,
        ObjectFieldOnDeleteChoices.PROTECT: models.PROTECT,
    }

    def get_model_field(self, field, **kwargs):
        if field.is_polymorphic:
            # Polymorphic Object: two concrete columns + one virtual GFK descriptor
            ct_field_name = f"{field.name}_content_type"
            oid_field_name = f"{field.name}_object_id"
            return {
                ct_field_name: models.ForeignKey(
                    "contenttypes.ContentType",
                    null=True,
                    blank=True,
                    on_delete=models.SET_NULL,
                    related_name="+",
                    db_column=f"{field.name}_content_type_id",
                ),
                oid_field_name: models.PositiveBigIntegerField(null=True, blank=True),
                field.name: GenericForeignKey(ct_field_name, oid_field_name),
            }

        content_type = self._get_related_content_type(field)
        to_model = content_type.model

        # Extract our custom parameters and keep only Django field parameters
        field_kwargs = {k: v for k, v in kwargs.items() if not k.startswith('_')}
        field_kwargs.update({"default": field.default, "unique": field.unique})

        on_delete = self._ON_DELETE_MAP.get(
            getattr(field, 'on_delete_behavior', None) or ObjectFieldOnDeleteChoices.SET_NULL,
            models.SET_NULL,
        )

        # Handle self-referential fields by using string references
        if content_type.app_label == APP_LABEL:
            from netbox_custom_objects.models import CustomObjectType

            custom_object_type_id = extract_cot_id_from_model_name(content_type.model)
            if custom_object_type_id is None:
                raise ValueError(
                    f"Expected table<id>model name for {APP_LABEL} content type, got {content_type.model!r}"
                )
            custom_object_type = CustomObjectType.objects.get(pk=custom_object_type_id)

            # Both self-referential and cross-COT FKs use LazyForeignKey to defer
            # resolution until Pass 2 of the startup loop (issue #408).  Calling
            # get_model() here to obtain the target class would cache a partial model
            # (skip_object_fields=True) whenever the target hasn't been generated yet,
            # permanently stripping its FK fields on future lookups.
            return _make_lazy_cot_fk(custom_object_type, field, on_delete, **field_kwargs)
        else:
            # to_model = content_type.model_class()._meta.object_name
            to_ct = f"{content_type.app_label}.{to_model}"
            model = apps.get_model(to_ct)

        # Use user-specified related_name if provided, otherwise generate a unique one
        if field.related_name:
            related_name = field.related_name
        else:
            table_model_name = field.custom_object_type.get_table_model_name(field.custom_object_type.id).lower()
            related_name = f"{table_model_name}_{field.name}_set"
        f = models.ForeignKey(
            model, null=True, blank=True, on_delete=on_delete, related_name=related_name, **field_kwargs
        )

        return f

    def get_form_field(self, field, for_csv_import=False, **kwargs):
        """
        Returns a form field for object relationships.
        For custom objects, uses CustomObjectDynamicModelChoiceField.
        For regular NetBox objects, uses DynamicModelChoiceField.
        """
        if field.is_polymorphic:
            # Polymorphic form field not yet supported in the UI; skip gracefully
            raise NotImplementedError(
                "Polymorphic object form fields are rendered by the view layer, not via this method."
            )

        content_type = self._get_related_content_type(field)

        has_context = False
        if content_type.app_label == APP_LABEL:
            # This is a custom object type
            from netbox_custom_objects.models import CustomObjectType

            custom_object_type_id = extract_cot_id_from_model_name(content_type.model)
            if custom_object_type_id is None:
                raise ValueError(
                    f"Expected table<id>model name for {APP_LABEL} content type, got {content_type.model!r}"
                )
            custom_object_type = CustomObjectType.objects.get(pk=custom_object_type_id)

            model = custom_object_type.get_model()
            has_context = bool(getattr(model, '_context_field_ids', []))
        else:
            # This is a regular NetBox model
            model = content_type.model_class()

        if for_csv_import:
            field_class = CSVModelChoiceField
            # For CSV import, determine to_field_name from the field configuration
            to_field_name = getattr(field, 'to_field_name', None) or 'name'
            return field_class(
                queryset=model.objects.all(),
                required=field.required,
                # Remove initial=field.default to allow Django to handle instance data properly
                to_field_name=to_field_name,
            )
        else:
            field_class = DynamicModelChoiceField
            form_field = field_class(
                queryset=model.objects.all(),
                required=field.required,
                # Remove initial=field.default to allow Django to handle instance data properly
                query_params=(
                    field.related_object_filter
                    if hasattr(field, "related_object_filter")
                    else None
                ),
                selector=model._meta.app_label != APP_LABEL,
            )
            if has_context:
                form_field.widget.attrs['ts-parent-field'] = '_context'
            return form_field

    def get_filterform_field(self, field, **kwargs):
        if field.is_polymorphic:
            base_label = field.label or field.name
            result = {}
            for ot in field.related_object_types.all():
                model_class = ot.model_class()
                if model_class is None:
                    continue
                result[f"{field.name}_{ot.app_label}_{ot.model}"] = DynamicModelChoiceField(
                    queryset=model_class.objects.all(),
                    required=False,
                    label=f"{base_label} ({model_class._meta.verbose_name})",
                    selector=ot.app_label != APP_LABEL,
                )
            return result
        content_type = self._get_related_content_type(field)
        if content_type.app_label == APP_LABEL:
            from netbox_custom_objects.models import CustomObjectType
            custom_object_type_id = extract_cot_id_from_model_name(content_type.model)
            if custom_object_type_id is None:
                raise ValueError(
                    f"Expected table<id>model name for {APP_LABEL} content type, got {content_type.model!r}"
                )
            custom_object_type = CustomObjectType.objects.get(pk=custom_object_type_id)
            model = custom_object_type.get_model()
        else:
            model = content_type.model_class()
        return DynamicModelChoiceField(
            queryset=model.objects.all(),
            required=False,
            label=field,
            selector=model._meta.app_label != APP_LABEL,
            query_params=(
                field.related_object_filter
                if hasattr(field, "related_object_filter")
                else None
            ),
        )

    def render_table_column(self, value):
        return linkify(value)

    def get_serializer_field(self, field, **kwargs):
        if field.is_polymorphic:
            from netbox_custom_objects.api.serializers import PolymorphicObjectSerializerField
            allowed_ids = {ot.id for ot in field.related_object_types.all()}
            return PolymorphicObjectSerializerField(
                allowed_content_type_ids=allowed_ids,
                required=field.required,
                allow_null=not field.required,
            )
        self._get_related_content_type(field)  # validates FK; raises NotImplementedError if null/missing
        related_model_class = field.related_object_type.model_class()
        if related_model_class._meta.app_label == APP_LABEL:
            from netbox_custom_objects.api.serializers import get_serializer_class
            serializer = get_serializer_class(related_model_class, skip_object_fields=True)
        else:
            serializer = get_serializer_for_model(related_model_class)
        return serializer(required=field.required, allow_null=not field.required, nested=True)

    def after_model_generation(self, instance, model, field_name):
        """
        Resolve lazy references after the model is fully generated.
        This ensures that self-referential fields point to the correct model class.
        """
        if instance.is_polymorphic:
            return  # GFK needs no post-generation resolution
        # Check if this field has a resolution method
        if resolve_method := getattr(model, f'_resolve_{field_name}_model', None):
            try:
                resolve_method(model)
            except LookupError:
                # The related COT hasn't been generated yet (startup ordering).
                # ready() runs a second pass to resolve all lazy FKs once every
                # COT model is in the app registry (issue #408).
                pass

    def add_polymorphic_object_columns(self, field_instance, model, schema_editor):
        """
        Add the two concrete DB columns (content_type FK + object_id) for a polymorphic
        Object field, plus a composite index on both columns.
        """
        ct_field_name = f"{field_instance.name}_content_type"
        oid_field_name = f"{field_instance.name}_object_id"

        ct_field = models.ForeignKey(
            "contenttypes.ContentType",
            null=True,
            blank=True,
            on_delete=models.SET_NULL,
            related_name="+",
            db_column=f"{field_instance.name}_content_type_id",
        )
        ct_field.contribute_to_class(model, ct_field_name)
        schema_editor.add_field(model, ct_field)

        oid_field = models.PositiveBigIntegerField(null=True, blank=True)
        oid_field.contribute_to_class(model, oid_field_name)
        schema_editor.add_field(model, oid_field)

        # Composite index as recommended in issue #31
        idx_name = _safe_index_name(
            f"co_{field_instance.custom_object_type_id}_{field_instance.name}_gfk"
        )
        idx = models.Index(fields=[ct_field_name, oid_field_name], name=idx_name)
        schema_editor.add_index(model, idx)

    def remove_polymorphic_object_columns(self, field_instance, model, schema_editor):
        """
        Remove the concrete DB columns for a polymorphic Object field.

        ``schema_editor`` must be supplied by the caller so that all DDL in a
        single operation (e.g. field deletion) runs within one schema editor
        context.  Opening a nested ``with connection.schema_editor()`` here
        would cause deferred_sql from the outer context to be flushed at the
        wrong time on some backends.
        """
        ct_field_name = f"{field_instance.name}_content_type"
        oid_field_name = f"{field_instance.name}_object_id"

        try:
            oid_field = model._meta.get_field(oid_field_name)
            schema_editor.remove_field(model, oid_field)
        except FieldDoesNotExist:
            pass  # Column already absent — nothing to remove.
        except Exception:
            logger.warning(
                "Failed to remove polymorphic object_id column %r from %r",
                oid_field_name, model._meta.db_table, exc_info=True,
            )
        try:
            ct_field = model._meta.get_field(ct_field_name)
            schema_editor.remove_field(model, ct_field)
        except FieldDoesNotExist:
            pass  # Column already absent — nothing to remove.
        except Exception:
            logger.warning(
                "Failed to remove polymorphic content_type column %r from %r",
                ct_field_name, model._meta.db_table, exc_info=True,
            )


# WHY CustomManyToManyManager / CustomManyToManyDescriptor / CustomManyToManyField
# exist instead of using Django's built-in ManyToManyField
# ──────────────────────────────────────────────────────────────────────────────
# Django's ManyToManyField assumes both sides of the relation are registered in
# the app registry *before* any model is instantiated.  Custom object types are
# defined at runtime by end-users and their models are generated dynamically via
# `type(...)`.  This creates two problems:
#
#   1. The through model does not exist in the app registry at import time, so
#      Django's ManyRelatedManager cannot resolve `field.remote_field.through`
#      during class construction.  Attempting to register it later causes
#      "model was already registered" RuntimeWarnings (suppressed in
#      generate_model()) and occasional stale-cache issues.
#
#   2. Django's `get_prefetch_queryset` (and the newer `get_prefetch_querysets`
#      introduced in Django 4.2) builds its result queryset from the through
#      model's manager, which requires the through model to be stable in the
#      registry.  Because our through models are regenerated on every server
#      restart (and on every schema change), the registry entry can be stale,
#      causing prefetch_related() to fetch from the wrong table.
#
# CustomManyToManyManager sidesteps both issues by resolving the through model
# directly from the field instance at access time rather than from the registry,
# and by implementing get_prefetch_queryset with explicit source/target subquery
# joins that work regardless of registry state.
#
# MAINTENANCE NOTE: get_prefetch_queryset returns a private Django tuple format.
# The six-element tuple (queryset, fk_getter, rel_obj_getter, single, cache_name,
# is_descriptor) is documented only in Django internals and may change between
# major versions.  If a Django upgrade breaks prefetch_related() for custom M2M
# fields, this is the first place to check.  The Django source to compare against
# is django/db/models/fields/related_managers.py :: ManyRelatedManager.
class CustomManyToManyManager(Manager):
    def __init__(self, instance=None, field_name=None):
        super().__init__()
        self.instance = instance
        self.field_name = field_name
        self.field = instance._meta.get_field(self.field_name)
        self.model = self.field.remote_field.model
        self.through = self.field.remote_field.through
        self.core_filters = {"source_id": instance.pk}
        self.prefetch_cache_name = self.field_name

    def get_prefetch_querysets(self, instances, querysets=None):
        """Django 4.2+ / 6.0+ prefetch API (plural form, replaces get_prefetch_queryset)."""
        if querysets and len(querysets) != 1:
            raise ValueError(
                "querysets argument of get_prefetch_querysets() should have a length of 1."
            )
        instance_pks = [obj.pk for obj in instances]
        if not instance_pks:
            return (
                (querysets[0] if querysets else self.model.objects.all()).none(),
                lambda obj: None,
                lambda obj: None,
                False,
                self.prefetch_cache_name,
                False,
            )

        queryset = querysets[0] if querysets else self.model.objects.all()
        through_table = self.through._meta.db_table
        target_table = self.model._meta.db_table
        target_pk_col = self.model._meta.pk.column

        # QuerySet.extra() is used intentionally here.  M2M prefetch requires
        # one result row per *relationship*, not per target object — a target
        # linked to N sources must appear N times, each annotated with its
        # source_id so Django's prefetch machinery can group them correctly.
        # annotate()+Subquery can only return one row per target, so an ORM-
        # only solution is not possible.  Django's own ManyToManyDescriptor
        # uses the identical extra()-based JOIN pattern for the same reason
        # (see django/db/models/fields/related_descriptors.py, line ~1160).
        queryset = queryset.extra(  # noqa: S610
            select={"_prefetch_source_id": f'"{through_table}"."source_id"'},
            tables=[through_table],
            where=[
                f'"{through_table}"."target_id" = "{target_table}"."{target_pk_col}"',
                f'"{through_table}"."source_id" IN ({",".join(str(pk) for pk in instance_pks)})',
            ],
        )

        return (
            queryset,
            lambda rel_obj: rel_obj._prefetch_source_id,  # group key from related obj
            lambda inst: inst.pk,  # matching key from source instance
            False,
            self.prefetch_cache_name,
            False,
        )

    def get_queryset(self):
        # Create a base queryset for the target model
        base_qs = self.model.objects.all()

        # Join through the through table using a subquery
        qs = base_qs.filter(
            pk__in=self.through.objects.filter(source_id=self.instance.pk).values_list(
                "target_id", flat=True
            )
        )

        # Add default ordering by pk
        return qs.order_by("pk")

    def add(self, *objs):
        for obj in objs:
            self.through.objects.get_or_create(
                source_id=self.instance.pk, target_id=obj.pk
            )

    def remove(self, *objs):
        for obj in objs:
            self.through.objects.filter(
                source_id=self.instance.pk, target_id=obj.pk
            ).delete()

    def clear(self):
        self.through.objects.filter(source_id=self.instance.pk).delete()

    def set(self, objs, clear=False):
        objs = tuple(objs)  # force evaluation before any mutation
        if clear:
            self.clear()
            self.add(*objs)
        else:
            new_pks = {obj.pk for obj in objs}
            old_pks = set(
                self.through.objects.filter(source_id=self.instance.pk)
                .values_list('target_id', flat=True)
            )
            # Remove relationships no longer in the target set
            to_remove = old_pks - new_pks
            if to_remove:
                self.through.objects.filter(
                    source_id=self.instance.pk,
                    target_id__in=to_remove,
                ).delete()
            # Add only genuinely new relationships
            for pk in new_pks - old_pks:
                self.through.objects.get_or_create(
                    source_id=self.instance.pk, target_id=pk
                )


class CustomManyToManyDescriptor(ManyToManyDescriptor):
    def __init__(self, field):
        self.field = field
        self.rel = field.remote_field
        self.reverse = False
        self.cache_name = self.field.name

    def __get__(self, instance, cls=None):
        if instance is None:
            return self

        return CustomManyToManyManager(instance=instance, field_name=self.field.name)

    def get_prefetch_queryset(self, instances, queryset=None):
        manager = CustomManyToManyManager(instances[0], self.field.name)
        return manager.get_prefetch_queryset(instances, queryset)

    def is_cached(self, instance):
        """
        Returns True if the field's value has been cached for the given instance.
        """
        return hasattr(instance, self.cache_name)

    def get_cached_value(self, instance):
        return instance._prefetched_objects_cache[self.cache_name]

    def set_cached_value(self, instance, value):
        if not hasattr(instance, '_prefetched_objects_cache'):
            instance._prefetched_objects_cache = {}
        instance._prefetched_objects_cache[self.cache_name] = value


class CustomManyToManyField(models.ManyToManyField):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.many_to_many = True
        self.concrete = False

    def m2m_field_name(self):
        return "source_id"

    def m2m_reverse_field_name(self):
        return "target_id"

    def get_foreign_related_value(self, instance):
        """Get the related value for the instance."""
        return (instance.pk,)

    def get_attname(self):
        return f"{self.name}_id"

    def get_attname_column(self):
        return self.name, None

    def contribute_to_class(self, cls, name, **kwargs):
        super().contribute_to_class(cls, name, **kwargs)
        setattr(cls, name, CustomManyToManyDescriptor(self))

    def get_joining_columns(self, reverse_join=False):
        if reverse_join:
            return ((self.m2m_reverse_field_name(), "id"),)
        return ((self.m2m_field_name(), "id"),)


class MultiObjectFieldType(FieldType):
    def get_through_model(self, field, model_string):
        """
        Creates a through model with deferred model references
        """
        # TODO: Register through model in AppsProxy to avoid "model was already registered" warnings
        # app_label = str(uuid.uuid4()) + "_database_table"
        # apps = AppsProxy(dynamic_models=None, app_label=app_label)
        meta = type(
            "Meta",
            (),
            {
                "db_table": field.through_table_name,
                "app_label": APP_LABEL,
                "apps": apps,
                "managed": True,
                "unique_together": ("source", "target"),
            },
        )

        # Check if this is a self-referential M2M
        content_type = self._get_related_content_type(field)
        custom_object_type_id = extract_cot_id_from_model_name(content_type.model)
        if content_type.app_label == APP_LABEL:
            if custom_object_type_id is None:
                raise ValueError(
                    f"Expected table<id>model name for {APP_LABEL} content type, got {content_type.model!r}"
                )
        is_self_referential = (
            content_type.app_label == APP_LABEL
            and field.custom_object_type.id == custom_object_type_id
        )

        attrs = {
            "__module__": "netbox_custom_objects.models",
            "Meta": meta,
            "id": models.AutoField(primary_key=True),
            "source": models.ForeignKey(
                model_string,
                on_delete=models.CASCADE,
                related_name="+",
                db_column="source_id",
            ),
            "target": models.ForeignKey(
                "self" if is_self_referential else model_string,
                on_delete=models.CASCADE,
                related_name="+",
                db_column="target_id",
            ),
        }

        return generate_model(field.through_model_name, (models.Model,), attrs)

    def get_model_field(self, field, **kwargs):
        """
        Creates the M2M field with appropriate model references
        """
        if field.is_polymorphic:
            # Polymorphic MultiObject: return a descriptor instead of a real M2M field.
            # The descriptor manages a through table with (source_id, content_type_id, object_id).
            return PolymorphicM2MDescriptor(through_model_name=field.through_model_name)

        # Check if this is a self-referential M2M
        content_type = self._get_related_content_type(field)
        custom_object_type_id = extract_cot_id_from_model_name(content_type.model)
        if content_type.app_label == APP_LABEL:
            if custom_object_type_id is None:
                raise ValueError(
                    f"Expected table<id>model name for {APP_LABEL} content type, got {content_type.model!r}"
                )

        # Extract our custom parameters and keep only Django field parameters
        field_kwargs = {k: v for k, v in kwargs.items() if not k.startswith('_')}
        # Remove default from field_kwargs since ManyToManyField doesn't handle defaults the same way
        field_kwargs.update({"unique": field.unique})

        is_self_referential = (
            content_type.app_label == APP_LABEL
            and field.custom_object_type.id == custom_object_type_id
        )

        # For now, we'll create the through model with string references
        # and resolve them later in after_model_generation
        # TODO: Check whether later resolution of the model is actually necessary or can be passed as string
        model_string = f"{field.related_object_type.app_label}.{field.related_object_type.model}"
        through = self.get_through_model(field, model_string)

        # Use user-specified related_name if provided; otherwise disable reverse access
        if field.related_name:
            m2m_related_name = field.related_name
            m2m_related_query_name = field.related_name
        else:
            m2m_related_name = "+"
            m2m_related_query_name = "+"

        # For self-referential fields, use 'self' as the target
        m2m_field = CustomManyToManyField(
            to="self" if is_self_referential else model_string,
            through=through,
            through_fields=("source", "target"),
            blank=True,
            related_name=m2m_related_name,
            related_query_name=m2m_related_query_name,
            **field_kwargs
        )

        # Store metadata for later resolution
        m2m_field._custom_object_type_id = field.related_object_type_id
        m2m_field._is_self_referential = is_self_referential

        return m2m_field

    def get_form_field(self, field, for_csv_import=False, **kwargs):
        """
        Returns a form field for multi-object relationships.
        Uses DynamicModelMultipleChoiceField for both custom objects and regular NetBox objects.
        """
        if field.is_polymorphic:
            raise NotImplementedError("Polymorphic multi-object fields are managed via the API")

        content_type = self._get_related_content_type(field)

        has_context = False
        if content_type.app_label == APP_LABEL:
            # This is a custom object type
            from netbox_custom_objects.models import CustomObjectType

            custom_object_type_id = extract_cot_id_from_model_name(content_type.model)
            if custom_object_type_id is None:
                raise ValueError(
                    f"Expected table<id>model name for {APP_LABEL} content type, got {content_type.model!r}"
                )
            custom_object_type = CustomObjectType.objects.get(pk=custom_object_type_id)

            model = custom_object_type.get_model(skip_object_fields=True)
            has_context = bool(getattr(model, '_context_field_ids', []))
        else:
            # This is a regular NetBox model
            model = content_type.model_class()

        if for_csv_import:
            field_class = CSVModelMultipleChoiceField
            # For CSV import, determine to_field_name from the field configuration
            to_field_name = getattr(field, 'to_field_name', None) or 'name'
            return field_class(
                queryset=model.objects.all(),
                required=field.required,
                to_field_name=to_field_name,
            )
        else:
            field_class = DynamicModelMultipleChoiceField
            form_field = field_class(
                queryset=model.objects.all(),
                required=field.required,
                query_params=(
                    field.related_object_filter
                    if hasattr(field, "related_object_filter")
                    else None
                ),
                selector=model._meta.app_label != APP_LABEL,
            )
            if has_context:
                form_field.widget.attrs['ts-parent-field'] = '_context'
            return form_field

    def get_display_value(self, instance, field_name):
        field = getattr(instance, field_name)
        return ", ".join(str(s) for s in field.all())

    def get_table_column_field(self, field, **kwargs):
        return tables.ManyToManyColumn(linkify_item=True, orderable=False)

    def get_serializer_field(self, field, **kwargs):
        if field.is_polymorphic:
            from netbox_custom_objects.api.serializers import PolymorphicObjectSerializerField
            from rest_framework import serializers as drf_serializers
            allowed_ids = {ot.id for ot in field.related_object_types.all()}
            return drf_serializers.ListField(
                child=PolymorphicObjectSerializerField(allowed_content_type_ids=allowed_ids),
                required=field.required,
                allow_null=not field.required,
                allow_empty=True,
            )
        related_model_class = field.related_object_type.model_class()
        if related_model_class._meta.app_label == APP_LABEL:
            from netbox_custom_objects.api.serializers import get_serializer_class
            serializer = get_serializer_class(related_model_class, skip_object_fields=True)
        else:
            serializer = get_serializer_for_model(related_model_class)
        # Construct the ListSerializer explicitly so that allow_null is set only on
        # the outer list (permitting null to clear the whole field) and NOT on the
        # child (preventing individual null items like [null, 3] from slipping
        # through).  Using many=True would forward allow_null to the child via
        # many_init(), widening null acceptance unintentionally.
        from rest_framework import serializers as drf_serializers
        child = serializer(required=False, nested=True)
        meta = getattr(serializer, 'Meta', None)
        list_serializer_class = getattr(meta, 'list_serializer_class', drf_serializers.ListSerializer)
        return list_serializer_class(
            child=child,
            required=field.required,
            allow_null=not field.required,
            allow_empty=True,
        )

    def get_filterform_field(self, field, **kwargs):
        if field.is_polymorphic:
            base_label = field.label or field.name
            result = {}
            for ot in field.related_object_types.all():
                model_class = ot.model_class()
                if model_class is None:
                    continue
                result[f"{field.name}_{ot.app_label}_{ot.model}"] = DynamicModelMultipleChoiceField(
                    queryset=model_class.objects.all(),
                    required=False,
                    label=f"{base_label} ({model_class._meta.verbose_name})",
                    selector=ot.app_label != APP_LABEL,
                )
            return result
        content_type = self._get_related_content_type(field)
        if content_type.app_label == APP_LABEL:
            from netbox_custom_objects.models import CustomObjectType
            custom_object_type_id = extract_cot_id_from_model_name(content_type.model)
            if custom_object_type_id is None:
                raise ValueError(
                    f"Expected table<id>model name for {APP_LABEL} content type, got {content_type.model!r}"
                )
            custom_object_type = CustomObjectType.objects.get(pk=custom_object_type_id)
            model = custom_object_type.get_model()
        else:
            model = content_type.model_class()
        return DynamicModelMultipleChoiceField(
            queryset=model.objects.all(),
            required=False,
            label=field,
            selector=model._meta.app_label != APP_LABEL,
            query_params=(
                field.related_object_filter
                if hasattr(field, "related_object_filter")
                else None
            ),
        )

    def after_model_generation(self, instance, model, field_name):
        """
        After both models are generated, update the field's remote model references
        """
        if instance.is_polymorphic:
            return  # PolymorphicM2MDescriptor needs no post-generation resolution

        field = model._meta.get_field(field_name)

        # Skip model resolution for self-referential fields
        if getattr(field, "_is_self_referential", False):
            field.remote_field.model = model
            through_model = field.remote_field.through

            # Update both source and target fields to point to the same model
            source_field = through_model._meta.get_field("source")
            target_field = through_model._meta.get_field("target")

            # Resolve the foreign key fields to point to the actual model
            source_field.remote_field.model = model
            source_field.related_model = model
            target_field.remote_field.model = model
            target_field.related_model = model

            # Also update the field's to attribute to point to the actual model
            field.to = model

            return

        # For non-self-referential fields, we need to resolve the target model
        # Use the instance parameter which contains the field definition
        content_type = self._get_related_content_type(instance)

        # Now we can safely resolve the target model
        if content_type.app_label == APP_LABEL:
            from netbox_custom_objects.models import CustomObjectType

            custom_object_type_id = extract_cot_id_from_model_name(content_type.model)
            if custom_object_type_id is None:
                raise ValueError(
                    f"Expected table<id>model name for {APP_LABEL} content type, got {content_type.model!r}"
                )
            custom_object_type = CustomObjectType.objects.get(pk=custom_object_type_id)

            # For self-referential fields, we need to resolve them to the current model
            # This doesn't cause recursion because we're not calling get_model() again
            if custom_object_type.id == instance.custom_object_type.id:
                # Self-referential field - resolve to current model
                to_model = model
            else:
                to_model = custom_object_type.get_model()
        else:
            to_ct = f"{content_type.app_label}.{content_type.model}"
            to_model = apps.get_model(to_ct)

        # Update through model's fields
        field.remote_field.model = to_model

        # Update through model's target field
        through_model = field.remote_field.through
        source_field = through_model._meta.get_field("source")
        target_field = through_model._meta.get_field("target")

        # Source field should point to the current model
        source_field.remote_field.model = model
        source_field.related_model = model

        # Target field should point to the related model
        target_field.remote_field.model = to_model
        target_field.related_model = to_model

    def create_m2m_table(self, instance, model, field_name):
        """
        Creates the actual M2M table after models are fully generated
        """
        from django.db import connection

        # Get the field instance
        field = model._meta.get_field(field_name)

        # For self-referential fields, use the current model
        if getattr(field, "_is_self_referential", False):
            to_model = model
        else:
            content_type = self._get_related_content_type(instance)
            if content_type.app_label == APP_LABEL:
                from netbox_custom_objects.models import CustomObjectType

                custom_object_type_id = extract_cot_id_from_model_name(content_type.model)
                if custom_object_type_id is None:
                    raise ValueError(
                        f"Expected table<id>model name for {APP_LABEL} content type, got {content_type.model!r}"
                    )
                custom_object_type = CustomObjectType.objects.get(
                    pk=custom_object_type_id
                )

                to_model = custom_object_type.get_model()
            else:
                to_model = content_type.model_class()

        # Create the through model with actual model references
        through = self.get_through_model(instance, model)

        # Update the through model's foreign key references
        source_field = through._meta.get_field("source")
        target_field = through._meta.get_field("target")

        # Source field should point to the current model
        source_field.remote_field.model = model
        source_field.remote_field.field_name = model._meta.pk.name
        source_field.related_model = model

        # Target field should point to the related model
        target_field.remote_field.model = to_model
        target_field.remote_field.field_name = to_model._meta.pk.name
        target_field.related_model = to_model

        # Register the model with Django's app registry
        apps = model._meta.apps

        try:
            through_model = apps.get_model(APP_LABEL, instance.through_model_name)
        except LookupError:
            apps.register_model(APP_LABEL, through)
            through_model = through

        # Update the M2M field's through model and target model
        field.remote_field.through = through_model
        field.remote_field.model = to_model
        field.remote_field.field_name = to_model._meta.pk.name

        # Create the through table
        with connection.schema_editor() as schema_editor:
            table_name = through_model._meta.db_table
            with connection.cursor() as cursor:
                tables = connection.introspection.table_names(cursor)
                if table_name not in tables:
                    schema_editor.create_model(through_model)

    def get_polymorphic_through_model(self, field_instance, source_model_string):
        """
        Creates a through model for a polymorphic MultiObject field.
        Columns: source_id (FK to custom object), content_type_id (FK to ContentType),
        object_id (PositiveBigIntegerField).
        """
        meta = type(
            "Meta",
            (),
            {
                "db_table": field_instance.through_table_name,
                "app_label": APP_LABEL,
                "apps": apps,
                "managed": True,
                "unique_together": (("source", "content_type", "object_id"),),
                "indexes": [
                    models.Index(
                        fields=["content_type", "object_id"],
                        name=_safe_index_name(
                            f"co_{field_instance.custom_object_type_id}"
                            f"_{field_instance.name}_pgfk"
                        ),
                    )
                ],
            },
        )

        attrs = {
            "__module__": "netbox_custom_objects.models",
            "Meta": meta,
            "id": models.AutoField(primary_key=True),
            "source": models.ForeignKey(
                source_model_string,
                on_delete=models.CASCADE,
                related_name="+",
                db_column="source_id",
            ),
            "content_type": models.ForeignKey(
                "contenttypes.ContentType",
                on_delete=models.CASCADE,
                related_name="+",
                db_column="content_type_id",
            ),
            "object_id": models.PositiveBigIntegerField(db_column="object_id"),
        }

        return generate_model(field_instance.through_model_name, (models.Model,), attrs)

    def create_polymorphic_m2m_table(self, field_instance, model, schema_editor):
        """
        Creates the DB table for a polymorphic MultiObject through model.

        ``schema_editor`` must be supplied by the caller for the same reason as
        ``remove_polymorphic_object_columns``: all DDL in a single operation
        should share one schema editor context.  Opening a nested
        ``with connection.schema_editor()`` here would flush deferred SQL
        prematurely on PostgreSQL.
        """
        source_model_string = f"{APP_LABEL}.{model.__name__}"
        through = self.get_polymorphic_through_model(field_instance, source_model_string)

        # Update source FK to point to the actual model
        source_field = through._meta.get_field("source")
        source_field.remote_field.model = model
        source_field.related_model = model

        # Register with Django's app registry
        _apps = model._meta.apps
        try:
            through_model = _apps.get_model(APP_LABEL, field_instance.through_model_name)
        except LookupError:
            _apps.register_model(APP_LABEL, through)
            through_model = through

        table_name = through_model._meta.db_table
        with connection.cursor() as cursor:
            existing_tables = connection.introspection.table_names(cursor)
            if table_name not in existing_tables:
                schema_editor.create_model(through_model)

    def drop_polymorphic_m2m_table(self, field_instance, model, schema_editor):
        """
        Drops the DB table for a polymorphic MultiObject through model.

        ``schema_editor`` must be supplied by the caller for the same reason as
        ``remove_polymorphic_object_columns``: all DDL in a single operation
        should share one schema editor context.
        """
        _apps = model._meta.apps
        try:
            through_model = _apps.get_model(APP_LABEL, field_instance.through_model_name)
            schema_editor.delete_model(through_model)
        except LookupError:
            pass  # Already dropped or never created


class PolymorphicResultList:
    """
    Lazy result returned by PolymorphicManyToManyManager.all().

    The underlying DB queries are deferred until first access and cached
    within this object's lifetime.  Because PolymorphicM2MDescriptor creates
    a new manager on every attribute access, and the manager's all() creates a
    new PolymorphicResultList, the cache only helps *within a single call
    chain* — e.g. a template that calls ``|length`` and then iterates the same
    ``all()`` return value will only issue one round of queries.  Calling
    ``obj.poly_field.all()`` twice, however, creates two separate instances and
    issues two rounds of queries.

    This is intentionally NOT a QuerySet — the objects come from multiple
    model classes and cannot be combined into a single SQL result set.
    It supports the subset of the list/queryset interface that templates and
    common callers need: iteration, ``len()``, ``bool()``, and index access.
    """

    __slots__ = ("_factory", "_cache")

    def __init__(self, factory):
        # factory is a zero-argument callable that returns an iterator of objects.
        self._factory = factory
        self._cache = None

    def _evaluate(self):
        if self._cache is None:
            self._cache = list(self._factory())
        return self._cache

    def __iter__(self):
        return iter(self._evaluate())

    def __len__(self):
        return len(self._evaluate())

    def __bool__(self):
        return bool(self._evaluate())

    def __getitem__(self, index):
        return self._evaluate()[index]

    def __repr__(self):
        return repr(self._evaluate())


class PolymorphicManyToManyManager:
    """
    Manager for polymorphic many-to-many relationships.
    Handles objects from multiple model types via a through table with
    (source_id, content_type_id, object_id) columns.
    """

    def __init__(self, instance, field_name, through_model_name):
        self.instance = instance
        self.field_name = field_name
        self.through_model_name = through_model_name

    def _get_through_model(self):
        return apps.get_model(APP_LABEL, self.through_model_name)

    def _get_objects(self):
        through = self._get_through_model()
        rows = list(
            through.objects.filter(source_id=self.instance.pk)
            .values_list("content_type_id", "object_id")
            .order_by("id")
        )

        # Group object IDs by content type so we can batch-fetch per model class
        # (one SELECT per type) rather than issuing one SELECT per row.
        by_ct: dict[int, list] = {}
        for ct_id, obj_id in rows:
            by_ct.setdefault(ct_id, []).append(obj_id)

        # Build a lookup map: (ct_id, obj_id) → object, preserving row order below.
        obj_map: dict[tuple, object] = {}
        for ct_id, obj_ids in by_ct.items():
            ct = ContentType.objects.get_for_id(ct_id)
            model_class = ct.model_class()
            if model_class is None:
                continue
            for obj in model_class.objects.filter(pk__in=obj_ids):
                obj_map[(ct_id, obj.pk)] = obj

        # Collect objects and yield in consistent string-sorted order.
        objects = []
        for ct_id, obj_id in rows:
            obj = obj_map.get((ct_id, obj_id))
            if obj is not None:
                objects.append(obj)
        yield from sorted(objects, key=str)

    def all(self):
        return PolymorphicResultList(self._get_objects)

    def count(self):
        return self._get_through_model().objects.filter(source_id=self.instance.pk).count()

    def exists(self):
        return self._get_through_model().objects.filter(source_id=self.instance.pk).exists()

    def add(self, *objs):
        through = self._get_through_model()
        for obj in objs:
            ct = ContentType.objects.get_for_model(obj)
            through.objects.get_or_create(
                source_id=self.instance.pk,
                content_type_id=ct.pk,
                object_id=obj.pk,
            )

    def remove(self, *objs):
        through = self._get_through_model()
        for obj in objs:
            ct = ContentType.objects.get_for_model(obj)
            through.objects.filter(
                source_id=self.instance.pk,
                content_type_id=ct.pk,
                object_id=obj.pk,
            ).delete()

    def clear(self):
        self._get_through_model().objects.filter(source_id=self.instance.pk).delete()

    def set(self, objs, clear=False):
        if clear:
            self.clear()
            self.add(*objs)
        else:
            # Diff-based replacement: add new, remove old.  Matches Django's
            # standard ManyRelatedManager.set(clear=False) behaviour.
            objs = tuple(objs)
            through = self._get_through_model()
            existing = {
                (ct_id, obj_id)
                for ct_id, obj_id in through.objects.filter(source_id=self.instance.pk)
                .values_list("content_type_id", "object_id")
            }
            # Pre-compute (ct_id, obj_pk) once per object to avoid duplicate CT lookups.
            new_items = [
                (ContentType.objects.get_for_model(obj).pk, obj.pk, obj) for obj in objs
            ]
            new_keys = {(ct_id, obj_pk) for ct_id, obj_pk, _ in new_items}
            to_add = [obj for ct_id, obj_pk, obj in new_items if (ct_id, obj_pk) not in existing]
            to_remove = existing - new_keys
            if to_add:
                self.add(*to_add)
            for ct_id, obj_id in to_remove:
                through.objects.filter(
                    source_id=self.instance.pk,
                    content_type_id=ct_id,
                    object_id=obj_id,
                ).delete()

    def __iter__(self):
        return iter(self.all())


class PolymorphicM2MDescriptor:
    """
    Descriptor for polymorphic many-to-many fields.
    Added directly to the model's class attrs during model generation.
    """

    def __init__(self, through_model_name):
        self.through_model_name = through_model_name
        self.field_name = None

    def __set_name__(self, owner, name):
        self.field_name = name

    def contribute_to_class(self, cls, name):
        self.field_name = name
        setattr(cls, name, self)

    def __get__(self, instance, owner=None):
        if instance is None:
            return self
        return PolymorphicManyToManyManager(
            instance=instance,
            field_name=self.field_name,
            through_model_name=self.through_model_name,
        )

    def __set__(self, instance, value):
        raise AttributeError(
            f"Direct assignment to '{self.field_name}' is not supported. "
            f"Use '{self.field_name}.set(objs)' to update polymorphic M2M fields."
        )

    @property
    def many_to_many(self):
        return True

    @property
    def concrete(self):
        return False


FIELD_TYPE_CLASS = {
    CustomFieldTypeChoices.TYPE_TEXT: TextFieldType,
    CustomFieldTypeChoices.TYPE_LONGTEXT: LongTextFieldType,
    CustomFieldTypeChoices.TYPE_INTEGER: IntegerFieldType,
    CustomFieldTypeChoices.TYPE_DECIMAL: DecimalFieldType,
    CustomFieldTypeChoices.TYPE_BOOLEAN: BooleanFieldType,
    CustomFieldTypeChoices.TYPE_DATE: DateFieldType,
    CustomFieldTypeChoices.TYPE_DATETIME: DateTimeFieldType,
    CustomFieldTypeChoices.TYPE_URL: URLFieldType,
    CustomFieldTypeChoices.TYPE_JSON: JSONFieldType,
    CustomFieldTypeChoices.TYPE_SELECT: SelectFieldType,
    CustomFieldTypeChoices.TYPE_MULTISELECT: MultiSelectFieldType,
    CustomFieldTypeChoices.TYPE_OBJECT: ObjectFieldType,
    CustomFieldTypeChoices.TYPE_MULTIOBJECT: MultiObjectFieldType,
}
