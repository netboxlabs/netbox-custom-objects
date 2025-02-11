from django.utils.translation import gettext_lazy as _

from netbox.plugins import PluginMenu, PluginMenuButton, PluginMenuItem

menu = PluginMenu(
    label='Service Mappings',
    groups=(
        (_('Mappings'), (
            PluginMenuItem(
                link='plugins:netbox_service_mappings:service_mapping_type_list',
                link_text=_('Mapping Types'),
                buttons=(
                    # PluginMenuButton('plugins:netbox_service_mappings:mapping_add', _('Add'), 'mdi mdi-plus-thick'),
                    # PluginMenuButton('plugins:netbox_service_mappings:mapping_bulk_import', _('Import'), 'mdi mdi-upload'),
                )
            ),
            PluginMenuItem(
                link='plugins:netbox_service_mappings:service_mapping_list',
                link_text=_('Mappings'),
                buttons=(
                    # PluginMenuButton('plugins:netbox_service_mappings:mapping_add', _('Add'), 'mdi mdi-plus-thick'),
                    # PluginMenuButton('plugins:netbox_service_mappings:mapping_bulk_import', _('Import'), 'mdi mdi-upload'),
                )
            ),
        )),
    ),
    icon_class='mdi mdi-source-branch'
)
