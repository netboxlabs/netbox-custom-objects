from django.utils.translation import gettext_lazy as _

from netbox.plugins import PluginMenu, PluginMenuButton, PluginMenuItem

menu = PluginMenu(
    label='Custom Objects',
    groups=(
        (_('Objects'), (
            PluginMenuItem(
                link='plugins:netbox_service_mappings:customobjecttype_list',
                link_text=_('Custom Object Types'),
                buttons=(
                    PluginMenuButton('plugins:netbox_service_mappings:customobjecttype_add', _('Add'), 'mdi mdi-plus-thick'),
                    # PluginMenuButton('plugins:netbox_service_mappings:mapping_bulk_import', _('Import'), 'mdi mdi-upload'),
                )
            ),
            PluginMenuItem(
                link='plugins:netbox_service_mappings:customobject_list',
                link_text=_('Custom Objects'),
                buttons=(
                    PluginMenuButton('plugins:netbox_service_mappings:customobject_add', _('Add'), 'mdi mdi-plus-thick'),
                    # PluginMenuButton('plugins:netbox_service_mappings:mapping_bulk_import', _('Import'), 'mdi mdi-upload'),
                )
            ),
        )),
    ),
    icon_class='mdi mdi-source-branch'
)
