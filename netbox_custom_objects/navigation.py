from django.utils.translation import gettext_lazy as _
from django.urls import reverse
from django.apps import apps
# from django.contrib.contenttypes.models import ContentType

from netbox.plugins import PluginMenu, PluginMenuButton, PluginMenuItem


custom_object_type_plugin_menu_item = PluginMenuItem(
    link='plugins:netbox_custom_objects:customobjecttype_list',
    link_text=_('Custom Object Types'),
    buttons=(
        PluginMenuButton('plugins:netbox_custom_objects:customobjecttype_add', _('Add'), 'mdi mdi-plus-thick'),
        # PluginMenuButton('plugins:netbox_service_mappings:mapping_bulk_import', _('Import'), 'mdi mdi-upload'),
    )
)

static_menu = PluginMenu(
    label='Custom Objects',
    groups=(
        (_('Objects'), (
            custom_object_type_plugin_menu_item,
            PluginMenuItem(
                link=None,
                url=reverse('plugins:netbox_custom_objects:customobject_list', kwargs={'custom_object_type': 'customer'}),
                link_text=_('Custom Objects'),
                buttons=(
                    PluginMenuButton('plugins:netbox_custom_objects:customobject_add', _('Add'), 'mdi mdi-plus-thick'),
                    # PluginMenuButton('plugins:netbox_service_mappings:mapping_bulk_import', _('Import'), 'mdi mdi-upload'),
                )
            ),
        )),
    ),
    icon_class='mdi mdi-source-branch'
)

def get_menu():
    CustomObjectType = apps.get_model('netbox_custom_objects', 'CustomObjectType')
    menu_items = []
    for custom_object_type in CustomObjectType.objects.all():
        model = custom_object_type.get_model()
        menu_items.append(PluginMenuItem(
            link=None,
            url=reverse('plugins:netbox_custom_objects:customobject_list', kwargs={'custom_object_type': custom_object_type.name.lower()}),
            link_text=_(model._meta.verbose_name_plural),
            buttons=(
                PluginMenuButton('plugins:netbox_custom_objects:customobject_add', _('Add'), 'mdi mdi-plus-thick'),
            )
        ))
    return PluginMenu(
        label='Custom Objects',
        groups=(
                (_('Object Types'), (custom_object_type_plugin_menu_item,)),
                (_('Objects'), tuple(menu_items)),
            ),
        icon_class='mdi mdi-source-branch'
    )

menu = get_menu
