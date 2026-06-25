from netbox.object_actions import BulkExport


class CustomObjectTypeBulkExport(BulkExport):
    """Export dropdown for the Custom Object Type list (CSV + portable schema JSON)."""

    template_name = 'netbox_custom_objects/buttons/customobjecttype_export.html'
