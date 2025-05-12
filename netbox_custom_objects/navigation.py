from packaging import version

from django.utils.translation import gettext_lazy as _
from django.urls import reverse
from django.apps import apps
from django.conf import settings

from utilities.string import title
from netbox.plugins import PluginMenu, PluginMenuButton, PluginMenuItem


custom_object_type_plugin_menu_item = PluginMenuItem(
    link='plugins:netbox_custom_objects:customobjecttype_list',
    link_text=_('Custom Object Types'),
    buttons=(
        PluginMenuButton('plugins:netbox_custom_objects:customobjecttype_add', _('Add'), 'mdi mdi-plus-thick'),
    )
)


def get_menu():
    CustomObjectType = apps.get_model('netbox_custom_objects', 'CustomObjectType')
    menu_items = []
    for custom_object_type in CustomObjectType.objects.all():
        model = custom_object_type.get_model()
        menu_items.append(PluginMenuItem(
            link=None,
            url=reverse('plugins:netbox_custom_objects:customobject_list', kwargs={'custom_object_type': custom_object_type.name.lower()}),
            link_text=_(title(model._meta.verbose_name_plural)),
            buttons=(
                PluginMenuButton(
                    None,
                    _('Add'),
                    'mdi mdi-plus-thick',
                    url=reverse('plugins:netbox_custom_objects:customobject_add', kwargs={'custom_object_type': custom_object_type.name.lower()}),
                ),
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


current_version = version.parse(settings.RELEASE.version)
if current_version < version.parse('4.4.0'):
    menu = PluginMenu(
        label='Custom Objects',
        groups=(
                (_('Object Types'), (custom_object_type_plugin_menu_item,)),
            ),
        icon_class='mdi mdi-source-branch'
    )
else:
    menu = get_menu
