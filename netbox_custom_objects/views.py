import django_filters
from django.contrib.postgres.fields import ArrayField
from django.db.models import JSONField
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from extras.choices import CustomFieldUIVisibleChoices
from netbox.filtersets import BaseFilterSet
from netbox.forms import NetBoxModelBulkEditForm, NetBoxModelFilterSetForm
from netbox.views import generic
from netbox.views.generic.mixins import TableMixin
from utilities.forms import ConfirmationForm
from utilities.htmx import htmx_partial
from utilities.views import get_viewname, register_model_view

from netbox_custom_objects.tables import CustomObjectTable

from . import field_types, forms, tables
from .models import CustomObject, CustomObjectType, CustomObjectTypeField


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
                print(
                    f"table mixin: {field.name} field is not implemented; using a default column"
                )
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


class CustomObjectTypeListView(generic.ObjectListView):
    queryset = CustomObjectType.objects.all()
    table = tables.CustomObjectTypeTable


@register_model_view(CustomObjectType)
class CustomObjectTypeView(CustomObjectTableMixin, generic.ObjectView):
    queryset = CustomObjectType.objects.all()

    def get_table(self, data, request, bulk_actions=True):
        self.custom_object_type = self.get_object(**self.kwargs)
        model = self.custom_object_type.get_model()
        data = model.objects.all()
        return super().get_table(data, request, bulk_actions=False)

    def get_extra_context(self, request, instance):
        model = instance.get_model()
        return {
            "custom_objects": model.objects.all(),
            "table": self.get_table(self.queryset, request),
        }


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
        model = obj.get_model()
        dependent_objects[model] = list(model.objects.all())
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
    template_name = 'netbox_custom_objects/field_delete.html'
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

        model = obj.custom_object_type.get_model()
        kwargs = {
            f'{obj.name}__isnull': False,
        }
        num_dependent_objects = model.objects.filter(**kwargs).count()

        # If this is an HTMX request, return only the rendered deletion form as modal content
        if htmx_partial(request):
            viewname = get_viewname(self.queryset.model, action='delete')
            form_url = reverse(viewname, kwargs={'pk': obj.pk})
            return render(request, 'htmx/delete_form.html', {
                'object': obj,
                'object_type': self.queryset.model._meta.verbose_name,
                'form': form,
                'form_url': form_url,
                'num_dependent_objects': num_dependent_objects,
                **self.get_extra_context(request, obj),
            })

        return render(request, self.template_name, {
            'object': obj,
            'form': form,
            'return_url': self.get_return_url(request, obj),
            'num_dependent_objects': num_dependent_objects,
            **self.get_extra_context(request, obj),
        })

    def _get_dependent_objects(self, obj):
        dependent_objects = super()._get_dependent_objects(obj)
        model = obj.custom_object_type.get_model()
        kwargs = {
            f'{obj.name}__isnull': False,
        }
        dependent_objects[model] = list(model.objects.filter(**kwargs))
        return dependent_objects

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
        self.custom_object_type = get_object_or_404(CustomObjectType,
            name__iexact=custom_object_type
        )
        model = self.custom_object_type.get_model()
        return model.objects.all()

    def get_filterset(self):
        model = self.queryset.model
        fields = [field.name for field in model._meta.fields]

        meta = type(
            "Meta",
            (),
            {
                "model": model,
                "fields": fields,
                # TODO: overrides should come from FieldType
                # These are placeholders; should use different logic
                "filter_overrides": {
                    JSONField: {
                        "filter_class": django_filters.CharFilter,
                        "extra": lambda f: {
                            "lookup_expr": "icontains",
                        },
                    },
                    ArrayField: {
                        "filter_class": django_filters.CharFilter,
                        "extra": lambda f: {
                            "lookup_expr": "icontains",
                        },
                    },
                },
            },
        )

        attrs = {
            "Meta": meta,
            "__module__": "database.filtersets",
        }

        return type(
            f"{model._meta.object_name}FilterSet",
            (BaseFilterSet,),  # TODO: Should be a NetBoxModelFilterSet
            attrs,
        )

    def get_filterset_form(self):
        model = self.queryset.model

        attrs = {
            "model": model,
            "__module__": "database.filterset_forms",
        }

        for field in self.custom_object_type.fields.all():
            field_type = field_types.FIELD_TYPE_CLASS[field.type]()
            try:
                attrs[field.name] = field_type.get_filterform_field(field)
            except NotImplementedError:
                print(f"list view: {field.name} field is not supported")

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
        object_type = get_object_or_404(CustomObjectType, name__iexact=custom_object_type)
        model = object_type.get_model()
        return model.objects.all()

    def get_object(self, **kwargs):
        custom_object_type = self.kwargs.get("custom_object_type", None)
        object_type = get_object_or_404(CustomObjectType, name__iexact=custom_object_type)
        model = object_type.get_model()
        # Filter out custom_object_type from kwargs for the object lookup
        lookup_kwargs = {k: v for k, v in self.kwargs.items() if k != "custom_object_type"}
        return get_object_or_404(model.objects.all(), **lookup_kwargs)

    def get_extra_context(self, request, instance):
        fields = instance.custom_object_type.fields.all().order_by("weight")
        return {
            "fields": fields,
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
        object_type = get_object_or_404(CustomObjectType, name__iexact=custom_object_type)
        model = object_type.get_model()
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
        }

        for field in self.object.custom_object_type.fields.all():
            field_type = field_types.FIELD_TYPE_CLASS[field.type]()
            try:
                attrs[field.name] = field_type.get_annotated_form_field(field)
            except NotImplementedError:
                print(f"get_form: {field.name} field is not supported")

        form = type(
            f"{model._meta.object_name}Form",
            (forms.NetBoxModelForm,),
            attrs,
        )

        return form


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
        object_type = get_object_or_404(CustomObjectType, name__iexact=custom_object_type)
        model = object_type.get_model()
        return get_object_or_404(model.objects.all(), **self.kwargs)


@register_model_view(CustomObject, "bulk_edit", path="edit", detail=False)
class CustomObjectBulkEditView(CustomObjectTableMixin, generic.BulkEditView):
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
            name__iexact=custom_object_type
        )
        model = self.custom_object_type.get_model()
        return model.objects.all()

    def get_form(self, queryset):
        attrs = {
            "model": queryset.model,
            "__module__": "database.forms",
        }

        for field in self.custom_object_type.fields.all():
            field_type = field_types.FIELD_TYPE_CLASS[field.type]()
            try:
                attrs[field.name] = field_type.get_annotated_form_field(field)
            except NotImplementedError:
                print(f"bulk edit form: {field.name} field is not supported")

        form = type(
            f"{queryset.model._meta.object_name}BulkEditForm",
            (NetBoxModelBulkEditForm,),
            attrs,
        )

        return form


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
        custom_object_type = self.kwargs.pop("custom_object_type", None)
        self.custom_object_type = CustomObjectType.objects.get(
            name__iexact=custom_object_type
        )
        model = self.custom_object_type.get_model()
        return model.objects.all()
