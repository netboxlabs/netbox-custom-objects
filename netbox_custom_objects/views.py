import logging

from core.models import ObjectChange
from core.tables import ObjectChangeTable
from django.contrib.contenttypes.models import ContentType
from django.db.models import Q
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.generic import View
from extras.choices import CustomFieldUIVisibleChoices
from extras.forms import JournalEntryForm
from extras.models import JournalEntry
from extras.tables import JournalEntryTable
from netbox.forms import (
    NetBoxModelBulkEditForm,
    NetBoxModelFilterSetForm,
    NetBoxModelImportForm,
)
from netbox.views import generic
from netbox.views.generic.mixins import TableMixin
from utilities.forms import ConfirmationForm
from utilities.forms.fields import DynamicModelChoiceField, DynamicModelMultipleChoiceField, TagFilterField
from utilities.htmx import htmx_partial
from utilities.object_types import object_type_name
from utilities.views import ConditionalLoginRequiredMixin, ViewTab, get_viewname, register_model_view

from netbox_custom_objects.filtersets import get_filterset_class
from netbox_custom_objects.tables import CustomObjectTable, CustomObjectTypeFieldTable
from . import field_types, filtersets, forms, tables
from .models import CustomObject, CustomObjectType, CustomObjectTypeField
from extras.choices import CustomFieldTypeChoices
from netbox_custom_objects.constants import APP_LABEL
from netbox_custom_objects.utilities import extract_cot_id_from_model_name, is_in_branch

logger = logging.getLogger("netbox_custom_objects.views")


# ---------------------------------------------------------------------------
# Sub-field naming helpers for polymorphic form fields
#
# Polymorphic GFK and M2M fields are split into one form sub-field per allowed
# content type, named "{field_name}__{app_label}__{model}".  These helpers
# centralise that convention so it isn't repeated across build and parse sites.
# ---------------------------------------------------------------------------

def _poly_sub_name(field_name: str, app_label: str, model: str) -> str:
    """Return the form sub-field name for one content type of a polymorphic field."""
    return f"{field_name}__{app_label}__{model}"


def _parse_poly_sub_name(field_name: str, sub_name: str) -> tuple[str, str]:
    """Parse a polymorphic sub-field name and return (app_label, model)."""
    suffix = sub_name[len(field_name) + 2:]   # strip "{field_name}__"
    app_label, model = suffix.split("__", 1)
    return app_label, model


def _build_poly_subfields(field, set_initial: bool = False):
    """
    Build per-type form sub-fields for a polymorphic Object or MultiObject field.

    Yields ``(sub_name, sub_field)`` pairs — one per allowed object type whose
    model class can be resolved.  Types whose ``model_class()`` returns ``None``
    (e.g. orphaned ContentType rows) are silently skipped.

    Args:
        field: A ``CustomObjectTypeField`` instance with ``is_polymorphic=True``.
        set_initial: When ``True``, sets ``sub_field.initial = None`` on each
            generated field (required for bulk-edit forms).
    """
    is_multi = field.type == CustomFieldTypeChoices.TYPE_MULTIOBJECT
    field_class = DynamicModelMultipleChoiceField if is_multi else DynamicModelChoiceField
    field_label = field.label or field.name.replace("_", " ").title()

    for ot in field.related_object_types.all():
        sub_model = ot.model_class()
        if sub_model is None:
            continue
        sub_name = _poly_sub_name(field.name, ot.app_label, ot.model)
        sub_field = field_class(
            queryset=sub_model.objects.all(),
            required=False,
            label=f"{field_label} ({object_type_name(ot)})",
            selector=ot.app_label != APP_LABEL,
        )
        if set_initial:
            sub_field.initial = None
        yield sub_name, sub_field


class CustomJournalEntryForm(JournalEntryForm):
    """
    Custom journal entry form that handles return URLs for custom objects.
    """

    def __init__(self, *args, **kwargs):
        self.custom_object = kwargs.pop("custom_object", None)
        super().__init__(*args, **kwargs)

    def get_return_url(self):
        """
        Override to return the correct URL for custom objects.
        """
        if self.custom_object:
            return reverse(
                "plugins:netbox_custom_objects:customobject_journal",
                kwargs={
                    "custom_object_type": self.custom_object.custom_object_type.slug,
                    "pk": self.custom_object.pk,
                },
            )
        return super().get_return_url()


class CustomJournalEntryEditView(generic.ObjectEditView):
    """
    Custom journal entry edit view that handles return URLs for custom objects.
    """

    queryset = JournalEntry.objects.all()
    form = CustomJournalEntryForm

    def alter_object(self, obj, request, args, kwargs):
        if not obj.pk:
            obj.created_by = request.user
        return obj

    def get_return_url(self, request, instance):
        """
        Override to return the correct URL for custom objects.
        """
        if instance.assigned_object and hasattr(
            instance.assigned_object, "custom_object_type"
        ):
            # This is a custom object
            return reverse(
                "plugins:netbox_custom_objects:customobject_journal",
                kwargs={
                    "custom_object_type": instance.assigned_object.custom_object_type.slug,
                    "pk": instance.assigned_object.pk,
                },
            )
        # Fall back to standard behavior for non-custom objects
        if not instance.assigned_object:
            return reverse("extras:journalentry_list")
        obj = instance.assigned_object
        viewname = get_viewname(obj, "journal")
        return reverse(viewname, kwargs={"pk": obj.pk})


class CustomObjectTableMixin(TableMixin):
    def get_table(self, data, request, bulk_actions=True):
        model_fields = self.custom_object_type.fields.all()
        fields = ["id"] + [
            field.name
            for field in model_fields
            if field.ui_visible != CustomFieldUIVisibleChoices.HIDDEN
        ]

        meta = type(
            "Meta",
            (),
            {
                "model": data.model,
                "fields": fields,
                "attrs": {
                    "class": "table table-hover object-list",
                },
            },
        )

        attrs = {
            "Meta": meta,
            "__module__": "database.tables",
        }

        for field in model_fields:
            if field.ui_visible == CustomFieldUIVisibleChoices.HIDDEN:
                continue
            field_type = field_types.FIELD_TYPE_CLASS[field.type]()
            try:
                attrs[field.name] = field_type.get_table_column_field(field)
            except NotImplementedError:
                logger.debug(
                    "table mixin: {} field is not implemented; using a default column".format(
                        field.name
                    )
                )
            # Primary field (if text-based) is linkified to the target Custom Object. Other fields may be
            # rendered via field-specific "render_foo" methods as supported by django-tables2.
            linkable_field_types = [
                CustomFieldTypeChoices.TYPE_TEXT,
                CustomFieldTypeChoices.TYPE_LONGTEXT,
            ]
            if field.primary and field.type in linkable_field_types:
                attrs[f"render_{field.name}"] = field_type.render_table_column_linkified
            else:
                # Define a method "render_table_column" method on any FieldType to customize output
                # See https://django-tables2.readthedocs.io/en/latest/pages/custom-data.html#table-render-foo-methods
                try:
                    attrs[f"render_{field.name}"] = field_type.render_table_column
                except AttributeError:
                    pass

        self.table = type(
            f"{data.model._meta.object_name}Table",
            (CustomObjectTable,),
            attrs,
        )
        return super().get_table(data, request, bulk_actions=bulk_actions)


#
# Custom Object Types
#


@register_model_view(CustomObjectType, "list", path="", detail=False)
class CustomObjectTypeListView(generic.ObjectListView):
    queryset = CustomObjectType.objects.all()
    filterset = filtersets.CustomObjectTypeFilterSet
    filterset_form = forms.CustomObjectTypeFilterForm
    table = tables.CustomObjectTypeTable


@register_model_view(CustomObjectType)
class CustomObjectTypeView(CustomObjectTableMixin, generic.ObjectView):
    queryset = CustomObjectType.objects.all()

    def get_table(self, data, request, bulk_actions=True):
        self.custom_object_type = self.get_object(**self.kwargs)
        model = self.custom_object_type.get_model_with_serializer()
        data = model.objects.all()
        return super().get_table(data, request, bulk_actions=False)

    def get_extra_context(self, request, instance):
        model = instance.get_model_with_serializer()

        # Get fields and group them by group_name
        fields = instance.fields.all().order_by("group_name", "weight", "name")

        # Group fields by group_name
        field_groups = {}
        for field in fields:
            group_name = field.group_name or None  # Use None for ungrouped fields
            if group_name not in field_groups:
                field_groups[group_name] = []
            field_groups[group_name].append(field)

        return {
            "custom_objects": model.objects.all(),
            "table": self.get_table(self.queryset, request),
            "field_groups": field_groups,
        }


@register_model_view(CustomObjectType, "add", detail=False)
@register_model_view(CustomObjectType, "edit")
class CustomObjectTypeEditView(generic.ObjectEditView):
    queryset = CustomObjectType.objects.all()
    form = forms.CustomObjectTypeForm


@register_model_view(CustomObjectType, "delete")
class CustomObjectTypeDeleteView(generic.ObjectDeleteView):
    queryset = CustomObjectType.objects.all()
    default_return_url = "plugins:netbox_custom_objects:customobjecttype_list"

    def _get_dependent_objects(self, obj):
        dependent_objects = super()._get_dependent_objects(obj)
        model = obj.get_model_with_serializer()
        dependent_objects[model] = list(model.objects.all())

        # Find CustomObjectTypeFields that reference this CustomObjectType
        referencing_fields = CustomObjectTypeField.objects.filter(
            related_object_type=obj.object_type
        )

        # Add the CustomObjectTypeFields that reference this CustomObjectType
        if referencing_fields.exists():
            dependent_objects[CustomObjectTypeField] = list(referencing_fields)

        return dependent_objects


@register_model_view(CustomObjectType, 'fields', path='fields')
class CustomObjectTypeFieldsView(generic.ObjectChildrenView):
    queryset = CustomObjectType.objects.all()
    table = CustomObjectTypeFieldTable
    template_name = 'netbox_custom_objects/fields.html'
    tab = ViewTab(
        label=_('Fields'),
        badge=lambda obj: CustomObjectTypeField.objects.filter(custom_object_type=obj).count(),
        permission='netbox_custom_objects.view_customobjecttypefield',
        weight=520,
        hide_if_empty=False
    )

    def get_children(self, request, parent):
        return CustomObjectTypeField.objects.restrict(request.user, 'view').filter(custom_object_type=parent)


#
# Custom Object Type Fields
#


@register_model_view(CustomObjectTypeField, "edit")
class CustomObjectTypeFieldEditView(generic.ObjectEditView):
    queryset = CustomObjectTypeField.objects.all()
    form = forms.CustomObjectTypeFieldForm


@register_model_view(CustomObjectTypeField, "delete")
class CustomObjectTypeFieldDeleteView(generic.ObjectDeleteView):
    template_name = "netbox_custom_objects/field_delete.html"
    queryset = CustomObjectTypeField.objects.all()

    def get_return_url(self, request, obj=None):
        return request.GET.get("return_url") or obj.custom_object_type.get_absolute_url()

    def get(self, request, *args, **kwargs):
        """
        GET request handler.

        Args:
            request: The current request
        """
        obj = self.get_object(**kwargs)
        form = ConfirmationForm(initial=request.GET)

        model = obj.custom_object_type.get_model_with_serializer()
        if obj.is_polymorphic and obj.type == CustomFieldTypeChoices.TYPE_MULTIOBJECT:
            # Polymorphic M2M: count via the through table (field is a descriptor, not a real FK)
            from django.apps import apps as django_apps
            try:
                through = django_apps.get_model(APP_LABEL, obj.through_model_name)
                num_dependent_objects = through.objects.values("source_id").distinct().count()
            except LookupError:
                num_dependent_objects = 0
        elif obj.is_polymorphic and obj.type == CustomFieldTypeChoices.TYPE_OBJECT:
            # Polymorphic Object (GFK): query via the concrete content_type column
            ct_field = f"{obj.name}_content_type__isnull"
            num_dependent_objects = model.objects.filter(**{ct_field: False}).count()
        else:
            num_dependent_objects = model.objects.filter(**{f"{obj.name}__isnull": False}).count()

        # If this is an HTMX request, return only the rendered deletion form as modal content
        if htmx_partial(request):
            viewname = get_viewname(self.queryset.model, action="delete")
            form_url = reverse(viewname, kwargs={"pk": obj.pk})
            return render(
                request,
                "htmx/delete_form.html",
                {
                    "object": obj,
                    "object_type": self.queryset.model._meta.verbose_name,
                    "form": form,
                    "form_url": form_url,
                    "num_dependent_objects": num_dependent_objects,
                    **self.get_extra_context(request, obj),
                },
            )

        return render(
            request,
            self.template_name,
            {
                "object": obj,
                "form": form,
                "return_url": self.get_return_url(request, obj),
                "num_dependent_objects": num_dependent_objects,
                **self.get_extra_context(request, obj),
            },
        )

    def _get_dependent_objects(self, obj):
        dependent_objects = super()._get_dependent_objects(obj)
        model = obj.custom_object_type.get_model_with_serializer()
        kwargs = {
            f"{obj.name}__isnull": False,
        }
        dependent_objects[model] = list(model.objects.filter(**kwargs))
        return dependent_objects


@register_model_view(CustomObjectType, "bulk_import", path="import", detail=False)
class CustomObjectTypeBulkImportView(generic.BulkImportView):
    queryset = CustomObjectType.objects.all()
    model_form = forms.CustomObjectTypeImportForm


@register_model_view(CustomObjectType, "bulk_edit", path="edit", detail=False)
class CustomObjectTypeBulkEditView(generic.BulkEditView):
    queryset = CustomObjectType.objects.all()
    filterset = filtersets.CustomObjectTypeFilterSet
    table = tables.CustomObjectTypeTable
    form = forms.CustomObjectTypeBulkEditForm


@register_model_view(CustomObjectType, "bulk_delete", path="delete", detail=False)
class CustomObjectTypeBulkDeleteView(generic.BulkDeleteView):
    queryset = CustomObjectType.objects.all()
    filterset = filtersets.CustomObjectTypeFilterSet
    table = tables.CustomObjectTypeTable


#
# Custom Objects
#

@register_model_view(CustomObject, "list", path="", detail=False)
class CustomObjectListView(CustomObjectTableMixin, generic.ObjectListView):
    queryset = None
    custom_object_type = None
    template_name = "netbox_custom_objects/custom_object_list.html"

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.queryset = self.get_queryset(request)
        self.filterset = self.get_filterset()
        self.filterset_form = self.get_filterset_form()

    def get_queryset(self, request):
        if self.queryset is not None:
            return self.queryset
        custom_object_type = self.kwargs.get("custom_object_type", None)
        self.custom_object_type = get_object_or_404(
            CustomObjectType, slug=custom_object_type
        )
        model = self.custom_object_type.get_model_with_serializer()
        return model.objects.all()

    def get_filterset(self):
        return get_filterset_class(self.queryset.model)

    def get_filterset_form(self):
        model = self.queryset.model

        attrs = {
            "model": model,
            "__module__": "database.filterset_forms",
            "tag": TagFilterField(model),
        }

        for field in self.custom_object_type.fields.all():
            field_type = field_types.FIELD_TYPE_CLASS[field.type]()
            try:
                attrs[field.name] = field_type.get_filterform_field(field)
            except NotImplementedError:
                logger.debug("list view: {} field is not supported".format(field.name))

        return type(
            f"{model._meta.object_name}FilterForm",
            (NetBoxModelFilterSetForm,),
            attrs,
        )

    def get(self, request, custom_object_type):
        # Necessary because get() in ObjectListView only takes request and no **kwargs
        return super().get(request)

    def get_extra_context(self, request):
        return {
            "custom_object_type": self.custom_object_type,
        }


@register_model_view(CustomObject)
class CustomObjectView(generic.ObjectView):
    template_name = "netbox_custom_objects/customobject.html"

    def get_queryset(self, request):
        custom_object_type = self.kwargs.get("custom_object_type", None)
        object_type = get_object_or_404(
            CustomObjectType, slug=custom_object_type
        )
        model = object_type.get_model_with_serializer()
        return model.objects.all()

    def get_object(self, **kwargs):
        custom_object_type = self.kwargs.get("custom_object_type", None)
        object_type = get_object_or_404(
            CustomObjectType, slug=custom_object_type
        )
        model = object_type.get_model_with_serializer()
        # Filter out custom_object_type from kwargs for the object lookup
        lookup_kwargs = {
            k: v for k, v in self.kwargs.items() if k != "custom_object_type"
        }
        return get_object_or_404(model.objects.all(), **lookup_kwargs)

    def get_extra_context(self, request, instance):
        fields = instance.custom_object_type.fields.all().order_by(
            "group_name", "weight", "name"
        )

        # Group fields by group_name
        field_groups = {}
        for field in fields:
            group_name = field.group_name or None  # Use None for ungrouped fields
            if group_name not in field_groups:
                field_groups[group_name] = []
            field_groups[group_name].append(field)

        return {
            "fields": fields,
            "field_groups": field_groups,
        }


@register_model_view(CustomObject, "edit")
class CustomObjectEditView(generic.ObjectEditView):
    template_name = "netbox_custom_objects/customobject_edit.html"
    form = None
    queryset = None
    object = None

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.object = self.get_object()
        model = self.object._meta.model
        self.form = self.get_form(model)

    def get_queryset(self, request):
        model = self.object._meta.model
        return model.objects.all()

    def get_object(self, **kwargs):
        if self.object:
            return self.object
        custom_object_type = self.kwargs.pop("custom_object_type", None)
        object_type = get_object_or_404(
            CustomObjectType, slug=custom_object_type
        )
        model = object_type.get_model_with_serializer()

        if not self.kwargs.get("pk", None):
            # We're creating a new object
            return model()
        return get_object_or_404(model.objects.all(), **self.kwargs)

    def get_form(self, model):
        # Collect raw GFK column names to exclude from the auto-generated form fields.
        # For each polymorphic Object field "foo", Django adds "foo_content_type" and
        # "foo_object_id" as real model columns; we replace those with per-type selects.
        poly_obj_raw_exclude = []
        for f in self.object.custom_object_type.fields.filter(
            type=CustomFieldTypeChoices.TYPE_OBJECT, is_polymorphic=True
        ):
            poly_obj_raw_exclude += [f"{f.name}_content_type", f"{f.name}_object_id"]

        meta = type(
            "Meta",
            (),
            {
                "model": model,
                "fields": "__all__",
                "exclude": poly_obj_raw_exclude,
            },
        )

        attrs = {
            "Meta": meta,
            "__module__": "database.forms",
            "_errors": None,
            "custom_object_type_fields": {},
            "custom_object_type_field_groups": {},
            # Maps polymorphic M2M field name → list of sub-field names (one per allowed type)
            "custom_object_type_poly_m2m_fields": {},
            # Maps polymorphic Object field name → list of sub-field names (one per allowed type)
            "custom_object_type_poly_obj_fields": {},
        }

        # Process custom object type fields (with grouping)
        for field in self.object.custom_object_type.fields.prefetch_related(
            'related_object_types'
        ).order_by("group_name", "weight", "name"):
            field_type = field_types.FIELD_TYPE_CLASS[field.type]()
            group_name = field.group_name or None

            # Polymorphic object/multiobject: one form sub-field per allowed type
            if field.is_polymorphic and field.type in (
                CustomFieldTypeChoices.TYPE_OBJECT,
                CustomFieldTypeChoices.TYPE_MULTIOBJECT,
            ):
                sub_names = []
                for sub_name, sub_field in _build_poly_subfields(field):
                    attrs[sub_name] = sub_field
                    sub_names.append(sub_name)
                    if group_name not in attrs["custom_object_type_field_groups"]:
                        attrs["custom_object_type_field_groups"][group_name] = []
                    attrs["custom_object_type_field_groups"][group_name].append(sub_name)

                dest_key = (
                    "custom_object_type_poly_obj_fields"
                    if field.type == CustomFieldTypeChoices.TYPE_OBJECT
                    else "custom_object_type_poly_m2m_fields"
                )
                attrs[dest_key][field.name] = sub_names
                continue

            try:
                field_name = field.name
                attrs[field_name] = field_type.get_annotated_form_field(field)

                # Annotate the field in the list of CustomField form fields
                attrs["custom_object_type_fields"][field_name] = field

                # Group fields by group_name (similar to NetBox custom fields)
                if group_name not in attrs["custom_object_type_field_groups"]:
                    attrs["custom_object_type_field_groups"][group_name] = []
                attrs["custom_object_type_field_groups"][group_name].append(field_name)

            except NotImplementedError:
                logger.debug("get_form: {} field is not supported".format(field.name))

        form_class = type(
            f"{model._meta.object_name}Form",
            (forms.NetBoxModelForm,),
            attrs,
        )

        # Create a custom __init__ method to set instance attributes
        def custom_init(self, *args, **kwargs):
            # Set the grouping info as instance attributes from the outer scope
            self.custom_object_type_fields = attrs["custom_object_type_fields"]
            self.custom_object_type_field_groups = attrs["custom_object_type_field_groups"]
            self.custom_object_type_poly_m2m_fields = attrs["custom_object_type_poly_m2m_fields"]
            self.custom_object_type_poly_obj_fields = attrs["custom_object_type_poly_obj_fields"]

            instance = kwargs.get('instance', None)

            if 'initial' not in kwargs:
                kwargs['initial'] = {}

            # Set initial values for non-polymorphic MultiObject defaults on new instances
            if not instance or not instance.pk:
                for field_name, field_obj in self.custom_object_type_fields.items():
                    if field_obj.type == CustomFieldTypeChoices.TYPE_MULTIOBJECT:
                        if field_obj.default and isinstance(field_obj.default, list):
                            content_type = field_obj.related_object_type
                            if content_type.app_label == APP_LABEL:
                                from netbox_custom_objects.models import CustomObjectType
                                custom_object_type_id = extract_cot_id_from_model_name(content_type.model)
                                custom_object_type = CustomObjectType.objects.get(pk=custom_object_type_id)
                                related_model = custom_object_type.get_model(skip_object_fields=True)
                            else:
                                related_model = content_type.model_class()
                            try:
                                initial_ids = list(
                                    related_model.objects.filter(pk__in=field_obj.default)
                                    .values_list('pk', flat=True)
                                )
                                kwargs['initial'][field_name] = initial_ids
                            except Exception:
                                logger.debug(
                                    "Failed to load default initial values for field %r",
                                    field_name, exc_info=True,
                                )

            # Set initial values for polymorphic sub-fields from the existing instance
            if instance and instance.pk:
                from django.contrib.contenttypes.models import ContentType as CT
                from django.apps import apps as django_apps

                # M2M: read through-table rows and group by content type
                for field_name, sub_names in self.custom_object_type_poly_m2m_fields.items():
                    try:
                        field_obj = instance.custom_object_type.fields.get(name=field_name)
                        through = django_apps.get_model(APP_LABEL, field_obj.through_model_name)
                        rows = through.objects.filter(source_id=instance.pk).values_list(
                            "content_type_id", "object_id"
                        )
                        by_ct = {}
                        for ct_id, obj_id in rows:
                            by_ct.setdefault(ct_id, []).append(obj_id)

                        for sub_name in sub_names:
                            app_label, model_name = _parse_poly_sub_name(field_name, sub_name)
                            try:
                                ct = CT.objects.get(app_label=app_label, model=model_name)
                                kwargs['initial'][sub_name] = by_ct.get(ct.pk, [])
                            except CT.DoesNotExist:
                                pass
                    except Exception:
                        logger.debug(
                            "Failed to load polymorphic M2M initial values for field %r",
                            field_name, exc_info=True,
                        )

                # GFK: pre-populate the matching type's sub-field
                for field_name, sub_names in self.custom_object_type_poly_obj_fields.items():
                    try:
                        gfk_value = getattr(instance, field_name, None)
                        if gfk_value is not None:
                            ct = CT.objects.get_for_model(gfk_value)
                            for sub_name in sub_names:
                                app_label, model_name = _parse_poly_sub_name(field_name, sub_name)
                                if ct.app_label == app_label and ct.model == model_name:
                                    kwargs['initial'][sub_name] = gfk_value.pk
                                    break
                    except Exception:
                        logger.debug(
                            "Failed to load polymorphic GFK initial value for field %r",
                            field_name, exc_info=True,
                        )

            # Now call the parent __init__ with the modified kwargs
            forms.NetBoxModelForm.__init__(self, *args, **kwargs)

        # Create a custom save method to properly handle M2M fields
        def custom_save(self, commit=True):
            # First save the instance to get the primary key
            instance = forms.NetBoxModelForm.save(self, commit=False)

            if commit:
                instance.save()

                # Handle non-polymorphic M2M fields
                for field_name, field_obj in self.custom_object_type_fields.items():
                    if field_obj.type == CustomFieldTypeChoices.TYPE_MULTIOBJECT:
                        current_value = self.cleaned_data.get(field_name, [])
                        instance_field = getattr(instance, field_name)
                        if hasattr(instance_field, 'clear') and hasattr(instance_field, 'set'):
                            instance_field.clear()
                            if current_value:
                                instance_field.set(current_value)

                # Handle polymorphic single-object sub-fields: use the first non-empty selection
                for field_name, sub_names in self.custom_object_type_poly_obj_fields.items():
                    chosen = None
                    for sub_name in sub_names:
                        val = self.cleaned_data.get(sub_name)
                        if val is not None:
                            chosen = val
                            break
                    setattr(instance, field_name, chosen)
                if self.custom_object_type_poly_obj_fields:
                    instance.save()

                # Handle polymorphic M2M sub-fields: aggregate per-type selections
                for field_name, sub_names in self.custom_object_type_poly_m2m_fields.items():
                    combined = []
                    for sub_name in sub_names:
                        combined.extend(self.cleaned_data.get(sub_name, []))
                    instance_field = getattr(instance, field_name)
                    instance_field.set(combined)

                # Save M2M relationships
                self.save_m2m()

            return instance

        def custom_clean(self):
            # Call parent for side effects (custom field processing etc.).
            # CheckLastUpdatedMixin.clean() does not propagate its return value,
            # so the chain returns None; read self.cleaned_data directly instead.
            forms.NetBoxModelForm.clean(self)
            # Enforce that at most one sub-field is filled for each polymorphic
            # single-object field.  Multiple non-None values are ambiguous and
            # would otherwise be silently resolved by "first non-empty wins".
            for field_name, sub_names in self.custom_object_type_poly_obj_fields.items():
                filled = [sn for sn in sub_names if self.cleaned_data.get(sn) is not None]
                if len(filled) > 1:
                    for sub_name in filled:
                        self.add_error(
                            sub_name,
                            _("Only one type may be selected for this field — clear all but one."),
                        )
            return self.cleaned_data

        form_class.__init__ = custom_init
        form_class.clean = custom_clean
        form_class.save = custom_save

        return form_class

    def get_extra_context(self, request, obj):
        return {
            'branch_warning': is_in_branch(),
        }


@register_model_view(CustomObject, "delete")
class CustomObjectDeleteView(generic.ObjectDeleteView):
    queryset = None
    object = None
    default_return_url = "plugins:netbox_custom_objects:customobject_list"

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.object = self.get_object()

    def get_queryset(self, request):
        model = self.object._meta.model
        return model.objects.all()

    def get_object(self, **kwargs):
        if self.object:
            return self.object
        custom_object_type = self.kwargs.pop("custom_object_type", None)
        object_type = get_object_or_404(
            CustomObjectType, slug=custom_object_type
        )
        model = object_type.get_model_with_serializer()
        return get_object_or_404(model.objects.all(), **self.kwargs)

    def get_return_url(self, request, obj=None):
        """
        Return the URL to redirect to after deleting a custom object.
        """
        if obj:
            # Get the custom object type from the object directly
            custom_object_type = obj.custom_object_type.slug
        else:
            # Fallback to getting it from kwargs if object is not available
            custom_object_type = self.kwargs.get("custom_object_type")

        return reverse(
            "plugins:netbox_custom_objects:customobject_list",
            kwargs={"custom_object_type": custom_object_type},
        )


@register_model_view(CustomObject, "bulk_edit", path="edit", detail=False)
class CustomObjectBulkEditView(CustomObjectTableMixin, generic.BulkEditView):
    template_name = "netbox_custom_objects/custom_object_bulk_edit.html"
    queryset = None
    custom_object_type = None
    table = None
    form = None

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.queryset = self.get_queryset(request)
        self.form = self.get_form(self.queryset)
        self.table = self.get_table(self.queryset, request).__class__

    def get_queryset(self, request):
        if self.queryset:
            return self.queryset
        custom_object_type = self.kwargs.get("custom_object_type", None)
        self.custom_object_type = CustomObjectType.objects.get(
            slug=custom_object_type
        )
        model = self.custom_object_type.get_model_with_serializer()
        return model.objects.all()

    def get_form(self, queryset):
        poly_obj_raw_exclude = []
        for f in self.custom_object_type.fields.filter(
            type=CustomFieldTypeChoices.TYPE_OBJECT, is_polymorphic=True
        ):
            poly_obj_raw_exclude += [f"{f.name}_content_type", f"{f.name}_object_id"]

        meta = type(
            "Meta",
            (),
            {
                "model": queryset.model,
                "fields": "__all__",
                "exclude": poly_obj_raw_exclude,
            },
        )

        attrs = {
            "Meta": meta,
            "__module__": "database.forms",
            "_poly_obj_field_map": {},   # field_name → [sub_names]
            "_poly_m2m_field_map": {},   # field_name → [sub_names]
        }

        for field in self.custom_object_type.fields.prefetch_related('related_object_types').all():
            field_type = field_types.FIELD_TYPE_CLASS[field.type]()

            # Polymorphic object/multiobject: one form sub-field per allowed type
            if field.is_polymorphic and field.type in (
                CustomFieldTypeChoices.TYPE_OBJECT,
                CustomFieldTypeChoices.TYPE_MULTIOBJECT,
            ):
                sub_names = []
                for sub_name, sub_field in _build_poly_subfields(field, set_initial=True):
                    attrs[sub_name] = sub_field
                    sub_names.append(sub_name)

                dest_key = (
                    "_poly_obj_field_map"
                    if field.type == CustomFieldTypeChoices.TYPE_OBJECT
                    else "_poly_m2m_field_map"
                )
                attrs[dest_key][field.name] = sub_names
                continue

            try:
                form_field = field_type.get_annotated_form_field(field)
                # In bulk edit forms, all fields should be optional and start blank.
                form_field.required = False
                form_field.widget.is_required = False
                form_field.initial = None
                attrs[field.name] = form_field
            except NotImplementedError:
                logger.debug(
                    "bulk edit form: {} field is not supported".format(field.name)
                )

        form = type(
            f"{queryset.model._meta.object_name}BulkEditForm",
            (NetBoxModelBulkEditForm,),
            attrs,
        )
        form.model = queryset.model
        return form

    def post_save_operations(self, form, obj):
        super().post_save_operations(form, obj)

        # Apply polymorphic single-object sub-fields (first non-empty selection wins)
        needs_save = False
        for field_name, sub_names in form._poly_obj_field_map.items():
            for sub_name in sub_names:
                val = form.cleaned_data.get(sub_name)
                if val is not None:
                    setattr(obj, field_name, val)
                    needs_save = True
                    break
        if needs_save:
            obj.save()

        # Apply polymorphic M2M sub-fields (union of all selected types).
        # set() replaces existing values, matching NetBox's standard bulk-edit
        # behavior for direct M2M fields (see BulkEditView lines 718-723).
        # Fields left blank are skipped so existing data is preserved.
        for field_name, sub_names in form._poly_m2m_field_map.items():
            combined = []
            has_any = False
            for sub_name in sub_names:
                vals = form.cleaned_data.get(sub_name) or []
                if vals:
                    has_any = True
                    combined.extend(vals)
            if has_any:
                getattr(obj, field_name).set(combined)

    def get_extra_context(self, request):
        return {
            'branch_warning': is_in_branch(),
        }


@register_model_view(CustomObject, "bulk_delete", path="delete", detail=False)
class CustomObjectBulkDeleteView(CustomObjectTableMixin, generic.BulkDeleteView):
    queryset = None
    custom_object_type = None
    table = None
    form = None

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.queryset = self.get_queryset(request)
        self.table = self.get_table(self.queryset, request).__class__

    def get_queryset(self, request):
        if self.queryset:
            return self.queryset
        self.custom_object_type = self.kwargs.pop("custom_object_type", None)
        self.custom_object_type = CustomObjectType.objects.get(
            slug=self.custom_object_type
        )
        model = self.custom_object_type.get_model_with_serializer()
        return model.objects.all()


@register_model_view(CustomObject, "bulk_import", path="import", detail=False)
class CustomObjectBulkImportView(generic.BulkImportView):
    template_name = "netbox_custom_objects/custom_object_bulk_import.html"
    queryset = None
    model_form = None
    custom_object_type = None

    def get(self, request, custom_object_type):
        # Necessary because get() in BulkImportView only takes request and no **kwargs
        return super().get(request)

    def post(self, request, custom_object_type):
        # Necessary because post() in BulkImportView only takes request and no **kwargs
        return super().post(request)

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.queryset = self.get_queryset(request)
        self.model_form = self.get_model_form(self.queryset)

    def get_queryset(self, request):
        if self.queryset:
            return self.queryset
        custom_object_type = self.kwargs.get("custom_object_type", None)
        self.custom_object_type = CustomObjectType.objects.get(
            slug=custom_object_type
        )
        model = self.custom_object_type.get_model_with_serializer()
        return model.objects.all()

    def get_model_form(self, queryset):
        meta = type(
            "Meta",
            (),
            {
                "model": queryset.model,
                "fields": "__all__",
            },
        )

        attrs = {
            "Meta": meta,
            "__module__": "database.forms",
        }

        for field in self.custom_object_type.fields.all():
            field_type = field_types.FIELD_TYPE_CLASS[field.type]()
            try:
                attrs[field.name] = field_type.get_annotated_form_field(
                    field, for_csv_import=True
                )
            except NotImplementedError:
                print(f"bulk import form: {field.name} field is not supported")

        form = type(
            f"{queryset.model._meta.object_name}BulkImportForm",
            (NetBoxModelImportForm,),
            attrs,
        )

        return form

    def get_extra_context(self, request):
        return {
            'branch_warning': is_in_branch(),
        }


class CustomObjectJournalView(ConditionalLoginRequiredMixin, View):
    """
    Custom journal view for CustomObject instances.
    Shows all journal entries for a custom object.
    """

    base_template = None
    tab = ViewTab(
        label=_("Journal"), permission="extras.view_journalentry", weight=5000
    )

    def get(self, request, custom_object_type, **kwargs):
        # Get the custom object type and model
        object_type = get_object_or_404(
            CustomObjectType, slug=custom_object_type
        )
        model = object_type.get_model_with_serializer()

        # Get the specific object
        lookup_kwargs = {k: v for k, v in kwargs.items() if k != "custom_object_type"}
        obj = get_object_or_404(model.objects.all(), **lookup_kwargs)

        # Get journal entries for this object
        content_type = ContentType.objects.get_for_model(model)
        journal_entries = (
            JournalEntry.objects.restrict(request.user, "view")
            .prefetch_related("created_by")
            .filter(
                assigned_object_type=content_type,
                assigned_object_id=obj.pk,
            )
        )

        journal_table = JournalEntryTable(
            data=journal_entries, orderable=False, user=request.user
        )
        journal_table.configure(request)
        journal_table.columns.hide("assigned_object_type")
        journal_table.columns.hide("assigned_object")

        # Create form for new journal entry if user has permission
        if request.user.has_perm("extras.add_journalentry"):
            form = CustomJournalEntryForm(
                custom_object=obj,
                initial={
                    "assigned_object_type": content_type,
                    "assigned_object_id": obj.pk,
                },
            )
        else:
            form = None

        # Set base template
        if self.base_template is None:
            self.base_template = "netbox_custom_objects/customobject.html"

        return render(
            request,
            "netbox_custom_objects/object_journal.html",
            {
                "object": obj,
                "form": form,
                "table": journal_table,
                "base_template": self.base_template,
                "tab": "journal",
                "form_action": reverse(
                    "plugins:netbox_custom_objects:custom_journalentry_add"
                ),
            },
        )


class CustomObjectChangeLogView(ConditionalLoginRequiredMixin, View):
    """
    Custom changelog view for CustomObject instances.
    Shows all changes made to a custom object.
    """

    base_template = None
    tab = ViewTab(
        label=_("Changelog"), permission="core.view_objectchange", weight=10000
    )

    def get(self, request, custom_object_type, **kwargs):
        # Get the custom object type and model
        object_type = get_object_or_404(
            CustomObjectType, slug=custom_object_type
        )
        model = object_type.get_model_with_serializer()

        # Get the specific object
        lookup_kwargs = {k: v for k, v in kwargs.items() if k != "custom_object_type"}
        obj = get_object_or_404(model.objects.all(), **lookup_kwargs)

        # Gather all changes for this object (and its related objects)
        content_type = ContentType.objects.get_for_model(model)
        objectchanges = (
            ObjectChange.objects.restrict(request.user, "view")
            .prefetch_related("user", "changed_object_type")
            .filter(
                Q(changed_object_type=content_type, changed_object_id=obj.pk)
                | Q(related_object_type=content_type, related_object_id=obj.pk)
            )
        )

        objectchanges_table = ObjectChangeTable(
            data=objectchanges, orderable=False, user=request.user
        )
        objectchanges_table.configure(request)

        # Set base template
        if self.base_template is None:
            self.base_template = "netbox_custom_objects/customobject.html"

        return render(
            request,
            "extras/object_changelog.html",
            {
                "object": obj,
                "table": objectchanges_table,
                "base_template": self.base_template,
                "tab": "changelog",
            },
        )
