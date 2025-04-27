from django.contrib import messages
from django.contrib.contenttypes.models import ContentType

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

    def get_extra_context(self, request, instance):
        content_type = ContentType.objects.get_for_model(instance)
        return {
            'relations': CustomObjectRelation.objects.filter(field__related_object_type=content_type, object_id=instance.pk)
        }


@register_model_view(CustomObject, 'edit')
class CustomObjectEditView(generic.ObjectEditView):
    queryset = CustomObject.objects.all()
    form = forms.CustomObjectForm


@register_model_view(CustomObject, 'delete')
class CustomObjectDeleteView(generic.ObjectDeleteView):
    queryset = CustomObject.objects.all()
    default_return_url = 'plugins:netbox_custom_objects:customobject_list'
