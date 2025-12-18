from django import forms
from django.utils.translation import gettext_lazy as _
from extras.choices import CustomFieldTypeChoices
from extras.forms import CustomFieldForm
from netbox.forms import (NetBoxModelBulkEditForm, NetBoxModelFilterSetForm,
                          NetBoxModelForm, NetBoxModelImportForm)
from utilities.forms.fields import (CommentField, ContentTypeChoiceField,
                                    DynamicModelChoiceField, SlugField, TagFilterField)
from utilities.forms.rendering import FieldSet
from utilities.object_types import object_type_name

from netbox_custom_objects.choices import SearchWeightChoices
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
            "name", "verbose_name", "verbose_name_plural", "slug", "version", "description", "tags",
        ),
    )
    comments = CommentField()

    class Meta:
        model = CustomObjectType
        fields = (
            "name", "verbose_name", "verbose_name_plural", "slug", "version", "description",
            "comments", "tags",
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
            custom_object_type_id = obj.model.replace("table", "").replace("model", "")
            if custom_object_type_id.isdigit():
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
        help_text=_("Type of the related object (for object/multi-object fields only)"),
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
            "group_name",
            "description",
            "type",
            "required",
            "unique",
            "default",
            name=_("Field"),
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

        # Disable changing the custom object type or related object type of a field
        if self.instance.pk:
            self.fields["custom_object_type"].disabled = True
            if "related_object_type" in self.fields:
                self.fields["related_object_type"].disabled = True

        # Multi-object fields may not be set unique
        if self.initial["type"] == CustomFieldTypeChoices.TYPE_MULTIOBJECT:
            self.fields["unique"].disabled = True

    def clean_primary(self):
        primary_fields = self.cleaned_data["custom_object_type"].fields.filter(
            primary=True
        )
        if self.cleaned_data["primary"]:
            primary_fields.update(primary=False)
        # It should be possible to have NO primary fields set on an object, and thus for its name to appear
        # as the default of e.g. "Cat 1"; therefore don't try to guarantee that a primary is set
        # else:
        #     if self.instance:
        #         other_primary_fields = primary_fields.exclude(pk=self.instance.id)
        #     else:
        #         other_primary_fields = primary_fields
        #     if not other_primary_fields.exists():
        #         return True
        return self.cleaned_data["primary"]

    def save(self, commit=True):
        obj = super().save(commit=commit)
        if obj.type == CustomFieldTypeChoices.TYPE_MULTIOBJECT and obj.default:
            qs = obj.related_object_type.model_class().objects.filter(
                pk__in=obj.default
            )
            model = obj.custom_object_type.get_model()
            for model_object in model.objects.all():
                model_field = getattr(model_object, obj.name)
                if not model_field.exists():
                    model_field.set(qs)
        return obj
