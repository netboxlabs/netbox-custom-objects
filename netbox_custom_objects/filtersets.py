from netbox.filtersets import NetBoxModelFilterSet

from .models import CustomObjectType

__all__ = ("CustomObjectTypeFilterSet",)


class CustomObjectTypeFilterSet(NetBoxModelFilterSet):
    class Meta:
        model = CustomObjectType
        fields = (
            "id",
            "name",
        )


"""
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
"""
