from django.contrib import messages
from django.contrib.contenttypes.models import ContentType

from netbox.views import generic
from utilities.views import ViewTab, register_model_view
from utilities.tables import get_table_for_model
from . import filtersets, forms, tables
from .models import ServiceMapping, ServiceMappingType, MappingRelation


#
# Custom Object Types
#

class ServiceMappingTypeListView(generic.ObjectListView):
    queryset = ServiceMappingType.objects.all()
    # filterset = filtersets.BranchFilterSet
    # filterset_form = forms.BranchFilterForm
    table = tables.ServiceMappingTypeTable


@register_model_view(ServiceMappingType)
class ServiceMappingTypeView(generic.ObjectView):
    queryset = ServiceMappingType.objects.all()


@register_model_view(ServiceMappingType, 'edit')
class ServiceMappingTypeEditView(generic.ObjectEditView):
    queryset = ServiceMappingType.objects.all()
    form = forms.ServiceMappingTypeForm


@register_model_view(ServiceMappingType, 'delete')
class ServiceMappingTypeDeleteView(generic.ObjectDeleteView):
    queryset = ServiceMappingType.objects.all()
    default_return_url = 'plugins:netbox_service_mappings:servicemappingtype_list'


#
# Custom Objects
#

class ServiceMappingListView(generic.ObjectListView):
    queryset = ServiceMapping.objects.all()
    # filterset = filtersets.BranchFilterSet
    # filterset_form = forms.BranchFilterForm
    table = tables.ServiceMappingTable


@register_model_view(ServiceMapping)
class ServiceMappingView(generic.ObjectView):
    queryset = ServiceMapping.objects.all()

    def get_extra_context(self, request, instance):


        content_type = ContentType.objects.get_for_model(instance)
        return {
            'relations': MappingRelation.objects.filter(field__content_type=content_type, object_id=instance.pk)
        }

@register_model_view(ServiceMapping, 'edit')
class ServiceMappingEditView(generic.ObjectEditView):
    queryset = ServiceMapping.objects.all()
    form = forms.ServiceMappingForm


@register_model_view(ServiceMapping, 'delete')
class ServiceMappingDeleteView(generic.ObjectDeleteView):
    queryset = ServiceMapping.objects.all()
    default_return_url = 'plugins:netbox_service_mappings:servicemapping_list'
