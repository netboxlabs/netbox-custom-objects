from netbox.plugins import PluginTemplateExtension

__all__ = (
    "CustomObjectSchema",
    "MappingElements",
    "template_extensions",
)


class CustomObjectSchema(PluginTemplateExtension):
    models = ["netbox_custom_objects.customobjecttype"]

    def full_width_page(self):
        if not (instance := self.context["object"]):
            return ""

        return ""


class MappingElements(PluginTemplateExtension):
    models = ["netbox_custom_objects.customobject"]

    def full_width_page(self):
        if not (instance := self.context["object"]):
            return ""

        return ""


class CustomObjectLink(PluginTemplateExtension):

    def left_page(self):
        # TODO: Implement this
        return ""


template_extensions = (
    CustomObjectSchema,
    MappingElements,
    CustomObjectLink,
)
