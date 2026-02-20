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
    auth_required=True,
)


class CustomObjectTypeMenuItems:
    group_name = ""

    def __init__(self, group_name=""):
        self.group_name = group_name

    def __iter__(self):
        CustomObjectType = apps.get_model(APP_LABEL, "CustomObjectType")
        for custom_object_type in CustomObjectType.objects.filter(group_name=self.group_name):
            model = custom_object_type.get_model()
            add_button = PluginMenuButton(
                None,
                _("Add"),
                "mdi mdi-plus-thick",
            )
            add_button.url = reverse_lazy(
                f"plugins:{APP_LABEL}:customobject_add",
                kwargs={
                    "custom_object_type": custom_object_type.slug
                },
            )
            bulk_import_button = PluginMenuButton(
                None,
                _('Import'),
                'mdi mdi-upload'
            )
            bulk_import_button.url = reverse_lazy(
                f"plugins:{APP_LABEL}:customobject_bulk_import",
                kwargs={
                    "custom_object_type": custom_object_type.slug
                },
            )
            menu_item = PluginMenuItem(
                link=None,
                link_text=_(title(model._meta.verbose_name_plural)),
                buttons=(add_button, bulk_import_button),
                auth_required=True,
            )
            menu_item.url = reverse_lazy(
                f"plugins:{APP_LABEL}:customobject_list",
                kwargs={"custom_object_type": custom_object_type.slug},
            )
            yield menu_item


current_version = version.parse(settings.RELEASE.version)


def get_grouped_menu_items():
    CustomObjectType = apps.get_model(APP_LABEL, "CustomObjectType")
    groups = []
    for group_name in set(CustomObjectType.objects.exclude(group_name="").values_list("group_name", flat=True)):
        groups.append((group_name, CustomObjectTypeMenuItems(group_name=group_name)))
    return groups


def get_groups():
    return [
        (_("Object Types"), (custom_object_type_plugin_menu_item,))
    ] + get_grouped_menu_items() + [
        (_("Objects"), CustomObjectTypeMenuItems())
    ]


groups = get_groups()


menu = PluginMenu(
    label=_("Custom Objects"),
    groups=tuple(groups),
    icon_class="mdi mdi-toy-brick-outline",
)
