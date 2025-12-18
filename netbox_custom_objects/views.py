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
from utilities.forms.fields import TagFilterField
from utilities.htmx import htmx_partial
from utilities.views import ConditionalLoginRequiredMixin, ViewTab, get_viewname, register_model_view

from netbox_custom_objects.filtersets import get_filterset_class
from netbox_custom_objects.tables import CustomObjectTable
from . import field_types, filtersets, forms, tables
from .models import CustomObject, CustomObjectType, CustomObjectTypeField
from extras.choices import CustomFieldTypeChoices
from netbox_custom_objects.constants import APP_LABEL
from netbox_custom_objects.utilities import is_in_branch

logger = logging.getLogger("netbox_custom_objects.views")


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
        return obj.custom_object_type.get_absolute_url()

    def get(self, request, *args, **kwargs):
        """
        GET request handler.

        Args:
            request: The current request
        """
        obj = self.get_object(**kwargs)
        form = ConfirmationForm(initial=request.GET)

        model = obj.custom_object_type.get_model_with_serializer()
        kwargs = {
            f"{obj.name}__isnull": False,
        }
        num_dependent_objects = model.objects.filter(**kwargs).count()

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
        if self.queryset:
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
        meta = type(
            "Meta",
            (),
            {
                "model": model,
                "fields": "__all__",
            },
        )

        attrs = {
            "Meta": meta,
            "__module__": "database.forms",
            "_errors": None,
            "custom_object_type_fields": {},
            "custom_object_type_field_groups": {},
        }

        # Process custom object type fields (with grouping)
        for field in self.object.custom_object_type.fields.all().order_by(
            "group_name", "weight", "name"
        ):
            field_type = field_types.FIELD_TYPE_CLASS[field.type]()
            try:
                field_name = field.name
                attrs[field_name] = field_type.get_annotated_form_field(field)

                # Annotate the field in the list of CustomField form fields
                attrs["custom_object_type_fields"][field_name] = field

                # Group fields by group_name (similar to NetBox custom fields)
                group_name = field.group_name or None  # Use None for ungrouped fields
                if group_name not in attrs["custom_object_type_field_groups"]:
                    attrs["custom_object_type_field_groups"][group_name] = []
                attrs["custom_object_type_field_groups"][group_name].append(field_name)

            except NotImplementedError:
                logger.debug("get_form: {} field is not supported".format(field.name))

        # Note: Regular model fields (non-custom fields) are automatically included
        # by the "fields": "__all__" setting in the Meta class, so we don't need
        # to manually add them to the form attributes or grouping structure.
        # The template will be able to access them directly through the form.

        form_class = type(
            f"{model._meta.object_name}Form",
            (forms.NetBoxModelForm,),
            attrs,
        )

        # Create a custom __init__ method to set instance attributes
        def custom_init(self, *args, **kwargs):
            # Set the grouping info as instance attributes from the outer scope
            self.custom_object_type_fields = attrs["custom_object_type_fields"]
            self.custom_object_type_field_groups = attrs[
                "custom_object_type_field_groups"
            ]

            # Handle default values for MultiObject fields BEFORE calling parent __init__
            # This ensures the initial values are set before Django processes the form
            instance = kwargs.get('instance', None)
            if not instance or not instance.pk:
                # Only set defaults for new instances (not when editing existing ones)
                for field_name, field_obj in self.custom_object_type_fields.items():
                    if field_obj.type == CustomFieldTypeChoices.TYPE_MULTIOBJECT:
                        if field_obj.default and isinstance(field_obj.default, list):
                            # Get the related model
                            content_type = field_obj.related_object_type
                            if content_type.app_label == APP_LABEL:
                                # Custom object type
                                from netbox_custom_objects.models import CustomObjectType
                                custom_object_type_id = content_type.model.replace("table", "").replace("model", "")
                                custom_object_type = CustomObjectType.objects.get(pk=custom_object_type_id)
                                model = custom_object_type.get_model(skip_object_fields=True)
                            else:
                                # Regular NetBox model
                                model = content_type.model_class()

                            try:
                                # Query the database to get the actual objects
                                initial_objects = model.objects.filter(pk__in=field_obj.default)
                                # Convert to list of IDs for ModelMultipleChoiceField
                                initial_ids = list(initial_objects.values_list('pk', flat=True))

                                # Set the initial value in the form's initial data
                                if 'initial' not in kwargs:
                                    kwargs['initial'] = {}
                                kwargs['initial'][field_name] = initial_ids
                            except Exception:
                                # If there's an error, don't set initial values
                                pass

            # Now call the parent __init__ with the modified kwargs
            forms.NetBoxModelForm.__init__(self, *args, **kwargs)

        # Create a custom save method to properly handle M2M fields
        def custom_save(self, commit=True):
            # First save the instance to get the primary key
            instance = forms.NetBoxModelForm.save(self, commit=False)

            if commit:
                instance.save()

                # Handle M2M fields manually to ensure proper clearing and setting
                for field_name, field_obj in self.custom_object_type_fields.items():
                    if field_obj.type == CustomFieldTypeChoices.TYPE_MULTIOBJECT:
                        # Get the current value from the form
                        current_value = self.cleaned_data.get(field_name, [])

                        # Get the field from the instance
                        instance_field = getattr(instance, field_name)

                        # Clear existing relationships and set new ones
                        if hasattr(instance_field, 'clear') and hasattr(instance_field, 'set'):
                            instance_field.clear()

                            if current_value:
                                instance_field.set(current_value)

                # Save M2M relationships
                self.save_m2m()

            return instance

        form_class.__init__ = custom_init
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
                attrs[field.name] = field_type.get_annotated_form_field(field)
            except NotImplementedError:
                logger.debug(
                    "bulk edit form: {} field is not supported".format(field.name)
                )

        form = type(
            f"{queryset.model._meta.object_name}BulkEditForm",
            (NetBoxModelBulkEditForm,),
            attrs,
        )

        # Set the model attribute that NetBox form mixins expect
        form.model = queryset.model

        return form

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
