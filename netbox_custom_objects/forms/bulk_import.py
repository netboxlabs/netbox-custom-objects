from netbox.forms import NetBoxModelImportForm

from netbox_custom_objects.models import CustomObjectType

__all__ = ('CustomObjectTypeImportForm',)


class CustomObjectTypeImportForm(NetBoxModelImportForm):
    class Meta:
        model = CustomObjectType
        fields = (
            'name',
            'slug',
            'verbose_name',
            'verbose_name_plural',
            'display_expression',
            'group_name',
            'description',
            'comments',
            'tags',
        )
