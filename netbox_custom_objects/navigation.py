from django.apps import apps
from django.conf import settings
from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _
from netbox.plugins import PluginMenu, PluginMenuButton, PluginMenuItem
from packaging import version
from utilities.string import title

from netbox_custom_objects.constants import APP_LABEL

custom_object_type_plugin_menu_item = PluginMenuItem(
    link="plugins:netbox_custom_objects:customobjecttype_list",
    link_text=_("Custom Object Types"),
    buttons=(
        PluginMenuButton(
            "plugins:netbox_custom_objects:customobjecttype_add",
            _("Add"),
            "mdi mdi-plus-thick",
        ),
    ),
)


class CustomObjectTypeMenuItems:

    def __iter__(self):
        CustomObjectType = apps.get_model(APP_LABEL, "CustomObjectType")
        for custom_object_type in CustomObjectType.objects.all():
            model = custom_object_type.get_model()
            add_button = PluginMenuButton(
                None,
                _("Add"),
                "mdi mdi-plus-thick",
            )
            add_button.url = reverse_lazy(
                f"plugins:{APP_LABEL}:customobject_add",
                kwargs={
                    "custom_object_type": custom_object_type.name.lower()
                },
            )
            menu_item = PluginMenuItem(
                link=None,
                link_text=_(title(model._meta.verbose_name_plural)),
                buttons=(add_button,),
            )
            menu_item.url = reverse_lazy(
                f"plugins:{APP_LABEL}:customobject_list",
                kwargs={"custom_object_type": custom_object_type.name.lower()},
            )
            yield menu_item


current_version = version.parse(settings.RELEASE.version)

groups = [
    (_("Object Types"), (custom_object_type_plugin_menu_item,)),
    (_("Objects"), CustomObjectTypeMenuItems()),
]

menu = PluginMenu(
    label=_("Custom Objects"),
    groups=tuple(groups),
    icon_class="mdi mdi-toy-brick-outline",
)
