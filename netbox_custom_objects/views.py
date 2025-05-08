from django.contrib import messages
from django.contrib.contenttypes.models import ContentType
from django.shortcuts import get_object_or_404

from netbox.forms import NetBoxModelFilterSetForm
from netbox.views import generic
from utilities.views import ViewTab, register_model_view
# from utilities.tables import get_table_for_model
from . import filtersets, forms, tables
from netbox_custom_objects.tables import CustomObjectTable
from netbox.filtersets import BaseFilterSet, ChangeLoggedModelFilterSet, NetBoxModelFilterSet
from .models import CustomObject, CustomObjectType, CustomObjectRelation, CustomObjectTypeField


#
# Custom Object Types
#

class CustomObjectTypeListView(generic.ObjectListView):
    queryset = CustomObjectType.objects.all()
    # filterset = filtersets.BranchFilterSet
    # filterset_form = forms.BranchFilterForm
    table = tables.CustomObjectTypeTable


@register_model_view(CustomObjectType)
class CustomObjectTypeView(generic.ObjectView):
    queryset = CustomObjectType.objects.all()

    def get_extra_context(self, request, instance):
        model = instance.get_model()
        return {'custom_objects': model.objects.all()}


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

class CustomObjectListView(generic.ObjectListView):
    # queryset = CustomObject.objects.all()
    # filterset = filtersets.BranchFilterSet
    # filterset_form = forms.BranchFilterForm
    # table = tables.CustomObjectTable
    # custom_object_type = None

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.queryset = self.get_queryset(request)
        self.filterset = self.get_filterset()
        self.filterset_form = self.get_filterset_form()

    def get_queryset(self, request):
        if self.queryset:
            return self.queryset
        custom_object_type = self.kwargs.pop('custom_object_type', None)
        object_type = CustomObjectType.objects.get(slug=custom_object_type)
        model = object_type.get_model()
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
        fields = [field.name for field in model._meta.fields]

        meta = type(
            "Meta",
            (),
            {
                "model": model,
                "fields": fields,
            },
        )

        attrs = {
            "model": model,
            # "Meta": meta,
            "__module__": "database.filterset_forms",
        }

        return type(
            f"{model._meta.object_name}FilterForm",
            (
                NetBoxModelFilterSetForm,
            ),
            attrs,
        )

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

        self.table = type(
            f"{data.model._meta.object_name}Table",
            (
                CustomObjectTable,
            ),
            attrs,
        )
        return super().get_table(data, request, bulk_actions=bulk_actions)

    def get(self, request, custom_object_type):
        # Necessary because get() in ObjectListView only takes request and no **kwargs
        return super().get(request)


@register_model_view(CustomObject)
class CustomObjectView(generic.ObjectView):
    queryset = CustomObject.objects.all()

    def get_object(self, **kwargs):
        custom_object_type = self.kwargs.pop('custom_object_type', None)
        object_type = CustomObjectType.objects.get(slug=custom_object_type)
        model = object_type.get_model()
        # kwargs.pop('custom_object_type', None)
        return get_object_or_404(model.objects.all(), **self.kwargs)

    def get_extra_context(self, request, instance):
        content_type = ContentType.objects.get_for_model(instance)
        return {
            'relations': CustomObjectRelation.objects.filter(field__related_object_type=content_type, object_id=instance.pk)
        }


@register_model_view(CustomObject, 'edit')
class CustomObjectEditView(generic.ObjectEditView):
    queryset = CustomObject.objects.all()
    form = forms.CustomObjectForm
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
        object_type = CustomObjectType.objects.get(slug=custom_object_type)
        model = object_type.get_model()
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
        }

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
    queryset = CustomObject.objects.all()
    default_return_url = 'plugins:netbox_custom_objects:customobject_list'
