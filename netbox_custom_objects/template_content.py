from netbox.plugins import PluginTemplateExtension

__all__ = (
    "CustomObjectSchema",
    "MappingElements",
    "template_extensions",
)


class CustomObjectSchema(PluginTemplateExtension):
    models = ["netbox_custom_objects.customobjecttype"]

    def full_width_page(self):
        # TODO: Implement this
        return ""


class MappingElements(PluginTemplateExtension):
    models = ["netbox_custom_objects.customobject"]

    def full_width_page(self):
        # TODO: Implement this
        return ""


# The "Custom Objects linking to this object" left-column panel that previously
# lived here (CustomObjectLink) has been superseded by the combined "Custom
# Objects" tab (see related_tabs/). The tab surfaces the same relationships with
# filtering, sorting, and — unlike the old panel — per-row view-permission
# enforcement (.restrict(user, "view")).
template_extensions = (
    CustomObjectSchema,
    MappingElements,
)
