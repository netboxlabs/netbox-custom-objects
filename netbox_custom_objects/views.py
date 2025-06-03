import django_filters
from django.apps import apps
from django.contrib import messages
from django.contrib.contenttypes.models import ContentType
from django.contrib.postgres.fields import ArrayField
from django.db.models.expressions import field_types
from django.db.models import JSONField
from django.shortcuts import get_object_or_404, render

from netbox.forms import NetBoxModelFilterSetForm, NetBoxModelBulkEditForm
from netbox.views import generic
from netbox.views.generic.mixins import TableMixin
from utilities.views import ViewTab, register_model_view
# from utilities.tables import get_table_for_model
from . import filtersets, forms, tables, field_types
from netbox_custom_objects.tables import CustomObjectTable
from netbox.filtersets import BaseFilterSet, ChangeLoggedModelFilterSet, NetBoxModelFilterSet
from .models import CustomObject, CustomObjectType, CustomObjectRelation, CustomObjectTypeField


class CustomObjectTableMixin(TableMixin):
    def get_table(self, data, request, bulk_actions=True):
        fields = [field.name for field in data.model._meta.fields]

        meta = type(
            "Meta",
            (),
            {
                "model": data.model,
                "fields": fields,
                "attrs": {
                    "class": "table table-hover object-list",
                }
            },
        )

        attrs = {
            "Meta": meta,
            "__module__": "database.tables",
        }

        for field in self.custom_object_type.fields.all():
            field_type = field_types.FIELD_TYPE_CLASS[field.type]()
            try:
                attrs[field.name] = field_type.get_table_column_field(field)
            except NotImplementedError:
                print(f'table mixin: {field.name} field is not supported')

        self.table = type(
            f"{data.model._meta.object_name}Table",
            (
                CustomObjectTable,
            ),
            attrs,
        )
        return super().get_table(data, request, bulk_actions=bulk_actions)


#
# Custom Object Types
#

class CustomObjectTypeListView(generic.ObjectListView):
    queryset = CustomObjectType.objects.all()
    # filterset = filtersets.BranchFilterSet
    # filterset_form = forms.BranchFilterForm
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
            'custom_objects': model.objects.all(),
            'table': self.get_table(self.queryset, request),
        }


@register_model_view(CustomObjectType, 'edit')
class CustomObjectTypeEditView(generic.ObjectEditView):
    queryset = CustomObjectType.objects.all()
    form = forms.CustomObjectTypeForm


@register_model_view(CustomObjectType, 'delete')
class CustomObjectTypeDeleteView(generic.ObjectDeleteView):
    queryset = CustomObjectType.objects.all()
    default_return_url = 'plugins:netbox_custom_objects:customobjecttype_list'


#
# Custom Object Type Fields
#

@register_model_view(CustomObjectTypeField, 'edit')
class CustomObjectTypeFieldEditView(generic.ObjectEditView):
    queryset = CustomObjectTypeField.objects.all()
    form = forms.CustomObjectTypeFieldForm


@register_model_view(CustomObjectTypeField, 'delete')
class CustomObjectTypeFieldDeleteView(generic.ObjectDeleteView):
    queryset = CustomObjectTypeField.objects.all()

    def get_return_url(self, request, obj=None):
        return obj.custom_object_type.get_absolute_url()


#
# Custom Objects
#

class CustomObjectListView(CustomObjectTableMixin, generic.ObjectListView):
    # queryset = CustomObject.objects.all()
    # filterset = filtersets.BranchFilterSet
    # filterset_form = forms.BranchFilterForm
    # table = tables.CustomObjectTable
    queryset = None
    custom_object_type = None
    template_name = 'netbox_custom_objects/custom_object_list.html'

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.queryset = self.get_queryset(request)
        self.filterset = self.get_filterset()
        self.filterset_form = self.get_filterset_form()

    def get_queryset(self, request):
        if self.queryset:
            return self.queryset
        custom_object_type = self.kwargs.get('custom_object_type', None)
        self.custom_object_type = CustomObjectType.objects.get(name__iexact=custom_object_type)
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
                        'filter_class': django_filters.CharFilter,
                        'extra': lambda f: {
                            'lookup_expr': 'icontains',
                        },
                    },
                    ArrayField: {
                        'filter_class': django_filters.CharFilter,
                        'extra': lambda f: {
                            'lookup_expr': 'icontains',
                        },
                    },
                }
            },
        )

        attrs = {
            "Meta": meta,
            "__module__": "database.filtersets",
        }

        return type(
            f"{model._meta.object_name}FilterSet",
            (
                BaseFilterSet,  # TODO: Should be a NetBoxModelFilterSet
            ),
            attrs,
        )

    def get_filterset_form(self):
        model = self.queryset.model
        # fields = [field.name for field in model._meta.fields]

        # meta = type(
        #     "Meta",
        #     (),
        #     {
        #         "model": model,
        #         "fields": fields,
        #     },
        # )

        attrs = {
            "model": model,
            # "Meta": meta,
            "__module__": "database.filterset_forms",
        }

        for field in self.custom_object_type.fields.all():
            field_type = field_types.FIELD_TYPE_CLASS[field.type]()
            try:
                attrs[field.name] = field_type.get_filterform_field(field)
            except NotImplementedError:
                print(f'list view: {field.name} field is not supported')

        return type(
            f"{model._meta.object_name}FilterForm",
            (
                NetBoxModelFilterSetForm,
            ),
            attrs,
        )

    def get(self, request, custom_object_type):
        # Necessary because get() in ObjectListView only takes request and no **kwargs
        return super().get(request)

    def get_extra_context(self, request):
        return {
            'custom_object_type': self.custom_object_type,
        }


@register_model_view(CustomObject)
class CustomObjectView(generic.ObjectView):
    queryset = CustomObject.objects.all()

    def get_object(self, **kwargs):
        custom_object_type = self.kwargs.pop('custom_object_type', None)
        object_type = CustomObjectType.objects.get(name__iexact=custom_object_type)
        model = object_type.get_model()
        # kwargs.pop('custom_object_type', None)
        return get_object_or_404(model.objects.all(), **self.kwargs)

    # def get_extra_context(self, request, instance):
    #     content_type = ContentType.objects.get_for_model(instance)
    #     return {
    #         'relations': CustomObjectRelation.objects.filter(field__related_object_type=content_type, object_id=instance.pk)
    #     }


@register_model_view(CustomObject, 'edit')
class CustomObjectEditView(generic.ObjectEditView):
    # queryset = CustomObject.objects.all()
    # form = forms.CustomObjectForm
    form = None
    queryset = None
    object = None

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.object = self.get_object()
        model = self.object._meta.model
        self.form = self.get_form(model)

    # def dispatch(self, request, *args, **kwargs):
    #     result = super().dispatch(request, *args, **kwargs)
    #     model = self.get_object()._meta.model
    #     self.form = self.get_form(model)
    #     return result

    def get_queryset(self, request):
        model = self.object._meta.model
        return model.objects.all()

    def get_object(self, **kwargs):
        if self.object:
            return self.object
        custom_object_type = self.kwargs.pop('custom_object_type', None)
        object_type = CustomObjectType.objects.get(name__iexact=custom_object_type)
        model = object_type.get_model()
        if not self.kwargs.get('pk', None):
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
                attrs[field.name] = field_type.get_form_field(field)
            except NotImplementedError:
                print(f'get_form: {field.name} field is not supported')

        form = type(
            f"{model._meta.object_name}Form",
            (
                forms.NetBoxModelForm,
            ),
            attrs,
        )

        return form


@register_model_view(CustomObject, 'delete')
class CustomObjectDeleteView(generic.ObjectDeleteView):
    queryset = None
    object = None
    default_return_url = 'plugins:netbox_custom_objects:customobject_list'

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.object = self.get_object()

    def get_queryset(self, request):
        model = self.object._meta.model
        return model.objects.all()

    def get_object(self, **kwargs):
        if self.object:
            return self.object
        custom_object_type = self.kwargs.pop('custom_object_type', None)
        object_type = CustomObjectType.objects.get(name__iexact=custom_object_type)
        model = object_type.get_model()
        return get_object_or_404(model.objects.all(), **self.kwargs)


@register_model_view(CustomObject, 'bulk_edit', path='edit', detail=False)
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
        custom_object_type = self.kwargs.get('custom_object_type', None)
        self.custom_object_type = CustomObjectType.objects.get(name__iexact=custom_object_type)
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
                attrs[field.name] = field_type.get_bulk_edit_form_field(field)
            except NotImplementedError:
                print(f'bulk edit form: {field.name} field is not supported')

        form = type(
            f"{queryset.model._meta.object_name}BulkEditForm",
            (
                NetBoxModelBulkEditForm,
            ),
            attrs,
        )

        return form


@register_model_view(CustomObject, 'bulk_delete', path='delete', detail=False)
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
        custom_object_type = self.kwargs.pop('custom_object_type', None)
        self.custom_object_type = CustomObjectType.objects.get(name__iexact=custom_object_type)
        model = self.custom_object_type.get_model()
        return model.objects.all()