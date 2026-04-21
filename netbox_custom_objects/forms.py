from django import forms
from django.utils.translation import gettext_lazy as _
from extras.choices import CustomFieldTypeChoices
from extras.forms import CustomFieldForm
from netbox.forms import (NetBoxModelBulkEditForm, NetBoxModelFilterSetForm,
                          NetBoxModelForm, NetBoxModelImportForm)
from utilities.forms.fields import (CommentField, ContentTypeChoiceField,
                                    ContentTypeMultipleChoiceField,
                                    DynamicModelChoiceField, SlugField, TagFilterField)
from utilities.forms.rendering import FieldSet
from utilities.forms.utils import get_field_value
from utilities.object_types import object_type_name

from netbox_custom_objects.choices import SearchWeightChoices
from netbox_custom_objects.utilities import extract_cot_id_from_model_name
from netbox_custom_objects.constants import APP_LABEL
from netbox_custom_objects.models import (CustomObjectObjectType,
                                          CustomObjectType,
                                          CustomObjectTypeField)

__all__ = (
    "CustomObjectTypeForm",
    "CustomObjectTypeBulkEditForm",
    "CustomObjectTypeImportForm",
    "CustomObjectTypeFilterForm",
    "CustomObjectTypeFieldForm",
    "CustomObjectType",
)


class CustomObjectTypeForm(NetBoxModelForm):
    name = forms.CharField(
        label=_("Internal name"),
        max_length=100,
        required=True,
        help_text=_("Internal lowercased object name, e.g. \"vendor_policy\""),
    )
    verbose_name = forms.CharField(
        label=_("Display name (singular)"),
        max_length=100,
        required=False,
        help_text=_("Displayed object type name, e.g. \"Vendor Policy\""),
    )
    verbose_name_plural = forms.CharField(
        label=_("Display name (plural)"),
        max_length=100,
        required=False,
        help_text=_("Displayed plural object type name, e.g. \"Vendor Policies\""),
    )
    slug = SlugField(
        label=_("URL path/slug"),
        slug_source="verbose_name_plural",
        help_text=_(
            "Unique plural shorthand for use as a URL component, e.g. \"vendor-policies\" for "
            "\"/plugins/custom-objects/vendor-policies/\""
        ),
    )

    fieldsets = (
        FieldSet(
            "name", "verbose_name", "verbose_name_plural", "slug",
            "version", "description", "group_name", "tags",
        ),
    )
    comments = CommentField()

    class Meta:
        model = CustomObjectType
        fields = (
            "name", "verbose_name", "verbose_name_plural", "slug", "version", "description",
            "group_name", "comments", "tags",
        )


class CustomObjectTypeBulkEditForm(NetBoxModelBulkEditForm):
    description = forms.CharField(
        label=_("Description"), max_length=200, required=False
    )
    comments = CommentField()

    model = CustomObjectType
    fieldsets = (FieldSet("description"),)
    nullable_fields = (
        "description",
        "comments",
    )


class CustomObjectTypeImportForm(NetBoxModelImportForm):

    class Meta:
        model = CustomObjectType
        fields = (
            "name",
            "slug",
            "description",
            "comments",
            "tags",
        )


class CustomObjectTypeFilterForm(NetBoxModelFilterSetForm):
    model = CustomObjectType
    fieldsets = (FieldSet("q", "filter_id", "tag"),)
    tag = TagFilterField(model)


class CustomContentTypeChoiceField(ContentTypeChoiceField):

    def label_from_instance(self, obj):
        if obj.app_label == APP_LABEL:
            custom_object_type_id = extract_cot_id_from_model_name(obj.model)
            if custom_object_type_id is not None:
                try:
                    return CustomObjectType.get_content_type_label(
                        custom_object_type_id
                    )
                except CustomObjectType.DoesNotExist:
                    pass
        try:
            return object_type_name(obj)
        except AttributeError:
            return super().label_from_instance(obj)


class CustomContentTypeMultipleChoiceField(ContentTypeMultipleChoiceField):
    """Multi-select version of CustomContentTypeChoiceField for polymorphic object fields."""

    def label_from_instance(self, obj):
        if obj.app_label == APP_LABEL:
            custom_object_type_id = extract_cot_id_from_model_name(obj.model)
            if custom_object_type_id is not None:
                try:
                    return CustomObjectType.get_content_type_label(
                        custom_object_type_id
                    )
                except CustomObjectType.DoesNotExist:
                    pass
        try:
            return object_type_name(obj)
        except AttributeError:
            return super().label_from_instance(obj)


class CustomObjectTypeFieldForm(CustomFieldForm):
    # This field should be removed or at least "required" should be defeated
    object_types = forms.CharField(
        label=_("Object types"),
        help_text=_("The type(s) of object that have this custom field"),
        required=False,
    )
    custom_object_type = DynamicModelChoiceField(
        queryset=CustomObjectType.objects.all(),
        required=True,
        label=_("Custom object type"),
    )
    related_object_type = CustomContentTypeChoiceField(
        label=_("Related object type"),
        queryset=CustomObjectObjectType.objects.public(),
        required=False,
        help_text=_("Type of the related object (for non-polymorphic object/multi-object fields)"),
    )
    related_object_types = CustomContentTypeMultipleChoiceField(
        label=_("Related object types"),
        queryset=CustomObjectObjectType.objects.public(),
        required=False,
        help_text=_(
            "Allowed object types for a polymorphic field (select one or more). "
            "Only used when 'Polymorphic' is enabled."
        ),
    )
    search_weight = forms.ChoiceField(
        choices=SearchWeightChoices,
        required=False,
        help_text=_(
            "Weighting for search. Lower values are considered more important. Fields with a search weight of 0 "
            "will be ignored."
        ),
    )

    fieldsets = (
        FieldSet(
            "custom_object_type",
            "name",
            "label",
            "primary",
            "context",
            "group_name",
            "description",
            "type",
            "required",
            "unique",
            "default",
            name=_("Field"),
        ),
        FieldSet(
            "is_polymorphic",
            "related_object_type",
            "related_object_types",
            "related_object_filter",
            name=_("Related Object"),
        ),
        FieldSet(
            "search_weight",
            "filter_logic",
            "ui_visible",
            "ui_editable",
            "weight",
            "is_cloneable",
            name=_("Behavior"),
        ),
    )

    class Meta:
        model = CustomObjectTypeField
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Toggling the polymorphic checkbox should re-render the form so only the
        # relevant related-object field is shown.
        self.fields['is_polymorphic'].widget.attrs.update({
            'hx-get': '.',
            'hx-include': '#form_fields',
            'hx-target': '#form_fields',
        })

        # Determine current field type and polymorphic state.
        # For existing instances is_polymorphic cannot be changed, so read it from the
        # instance directly; for new fields use whatever the form currently carries.
        field_type = get_field_value(self, 'type')
        if self.instance.pk:
            is_polymorphic = self.instance.is_polymorphic
        elif self.is_bound:
            # get_field_value() falls back to initial for BooleanField (no valid_value);
            # read the submitted checkbox value from self.data directly instead.
            is_polymorphic = bool(self.data.get('is_polymorphic'))
        else:
            is_polymorphic = bool(get_field_value(self, 'is_polymorphic'))

        # Show only the relevant related-object field and rebuild fieldsets cleanly.
        # The parent __init__ inserts a simple FieldSet('related_object_type', ...) for
        # object/multiobject types, which would create a duplicate section; replacing
        # self.fieldsets here keeps a single "Related Object" group.
        if field_type in (CustomFieldTypeChoices.TYPE_OBJECT, CustomFieldTypeChoices.TYPE_MULTIOBJECT):
            if is_polymorphic:
                if 'related_object_type' in self.fields:
                    del self.fields['related_object_type']
                related_obj_fields = ('is_polymorphic', 'related_object_types', 'related_object_filter')
            else:
                if 'related_object_types' in self.fields:
                    del self.fields['related_object_types']
                related_obj_fields = ('is_polymorphic', 'related_object_type', 'related_object_filter')
            self.fieldsets = (
                CustomObjectTypeFieldForm.fieldsets[0],
                FieldSet(*related_obj_fields, name=_('Related Object')),
                CustomObjectTypeFieldForm.fieldsets[2],
            )
        else:
            # Parent already removed related_object_type/related_object_filter;
            # remove the remaining related-object fields too.
            for fname in ('related_object_types', 'is_polymorphic'):
                if fname in self.fields:
                    del self.fields[fname]
            # Drop the Related Object fieldset entirely so no empty section header renders.
            # Filter by checking that every item in a fieldset belongs to the related-object
            # field set (handles both our full FieldSet and any parent-inserted simple one).
            _related_names = frozenset({
                'is_polymorphic', 'related_object_type', 'related_object_types', 'related_object_filter',
            })
            self.fieldsets = tuple(
                fs for fs in self.fieldsets
                if not all(isinstance(item, str) and item in _related_names for item in fs.items)
            )

        # Disable immutable fields on existing instances.
        if self.instance.pk:
            self.fields["custom_object_type"].disabled = True
            if 'is_polymorphic' in self.fields:
                self.fields["is_polymorphic"].disabled = True
            if 'related_object_types' in self.fields:
                self.fields["related_object_types"].disabled = True
            if 'related_object_type' in self.fields:
                self.fields["related_object_type"].disabled = True

        # Multi-object fields may not be set unique
        if field_type == CustomFieldTypeChoices.TYPE_MULTIOBJECT:
            self.fields["unique"].disabled = True

        # Add related_name to the Related Object fieldset for object/multiobject fields.
        # The parent CustomFieldForm.__init__ removes related_object_type from self.fields
        # for non-object types, so we use its presence as a signal.
        if "related_object_type" in self.fields:
            self.fieldsets = tuple(
                FieldSet(*fs.items, "related_name", name=fs.name)
                if "related_object_type" in fs.items
                else fs
                for fs in self.fieldsets
            )
        else:
            del self.fields["related_name"]

    def clean(self):
        cleaned_data = super().clean()
        field_type = cleaned_data.get("type")
        is_polymorphic = cleaned_data.get("is_polymorphic", False)

        if field_type in (
            CustomFieldTypeChoices.TYPE_OBJECT,
            CustomFieldTypeChoices.TYPE_MULTIOBJECT,
        ) and is_polymorphic:
            related_object_types = cleaned_data.get("related_object_types")
            if not related_object_types:
                self.add_error(
                    "related_object_types",
                    _("Polymorphic object fields must specify at least one related object type."),
                )

        return cleaned_data

    def clean_primary(self):
        primary_fields = self.cleaned_data["custom_object_type"].fields.filter(
            primary=True
        )
        if self.cleaned_data["primary"]:
            primary_fields.update(primary=False)
        return self.cleaned_data["primary"]

    def save(self, commit=True):
        obj = super().save(commit=commit)
        # For polymorphic multiobject fields, skip default value propagation
        if (
            not obj.is_polymorphic
            and obj.type == CustomFieldTypeChoices.TYPE_MULTIOBJECT
            and obj.default
        ):
            qs = obj.related_object_type.model_class().objects.filter(
                pk__in=obj.default
            )
            model = obj.custom_object_type.get_model()
            for model_object in model.objects.all():
                model_field = getattr(model_object, obj.name)
                if not model_field.exists():
                    model_field.set(qs)
        return obj
