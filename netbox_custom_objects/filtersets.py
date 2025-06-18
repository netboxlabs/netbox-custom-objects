from django.db.models import Q
from netbox.filtersets import NetBoxModelFilterSet

from .models import *

__all__ = ("CustomObjectFilterSet",)


class CustomObjectFilterSet(NetBoxModelFilterSet):
    class Meta:
        model = CustomObject
        fields = (
            "id",
            "name",
            "custom_object_type",
        )

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(Q(name__icontains=value))
