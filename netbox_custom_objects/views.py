from django.contrib import messages
from django.contrib.contenttypes.models import ContentType
from django.shortcuts import get_object_or_404

from netbox.views import generic
from utilities.views import ViewTab, register_model_view
# from utilities.tables import get_table_for_model
from . import filtersets, forms, tables
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
    queryset = CustomObject.objects.all()
    # filterset = filtersets.BranchFilterSet
    # filterset_form = forms.BranchFilterForm
    table = tables.CustomObjectTable
    # custom_object_type = None

    def get_queryset(self, request):
        custom_object_type = self.kwargs.pop('custom_object_type', None)
        object_type = CustomObjectType.objects.get(slug=custom_object_type)
        model = object_type.get_model()
        return model.objects.all()

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
        # apps = GeneratedModelAppsProxy(manytomany_models, app_label)
        meta = type(
            "Meta",
            (),
            {
                "model": model,
                "fields": "__all__",
                # "apps": apps,
                # "managed": managed,
                # "db_table": self.get_database_table_name(),
                # "app_label": app_label,
                # "ordering": ["order", "id"],
                # "indexes": indexes,
                # "verbose_name": self.get_verbose_name(),
                # "verbose_name_plural": self.get_verbose_name_plural(),
            },
        )

        attrs = {
            "Meta": meta,
            "__module__": "database.forms",
            # An indication that the model is a generated table model.
            # "_generated_table_model": True,
            # "baserow_table": self,
            # "baserow_table_id": self.id,
            # "baserow_models": apps.baserow_models,
            # We are using our own table model manager to implement some queryset
            # helpers.
            # "objects": models.Manager(),
            # "objects": RestrictedQuerySet.as_manager(),
            # "objects_and_trash": TableModelTrashAndObjectsManager(),
            # "__str__": __str__,
            # "get_absolute_url": get_absolute_url,
        }

        form = type(
            f"{model._meta.object_name}Form",
            (
                # GeneratedTableModel,
                # TrashableModelMixin,
                # CreatedAndUpdatedOnMixin,
                forms.NetBoxModelForm,
            ),
            attrs,
        )

        return form


@register_model_view(CustomObject, 'delete')
class CustomObjectDeleteView(generic.ObjectDeleteView):
    queryset = CustomObject.objects.all()
    default_return_url = 'plugins:netbox_custom_objects:customobject_list'
