import re

# Matches the generated model name produced by CustomObjectType.get_table_model_name().
# Capturing group 1 is the numeric COT id.
TABLE_MODEL_RE = re.compile(r'^table(\d+)model$', re.IGNORECASE)

# Models which do not support change logging, but whose database tables
# must be replicated for each branch to ensure proper functionality
INCLUDE_MODELS = (
    "dcim.cablepath",
    "extras.cachedvalue",
)

APP_LABEL = "netbox_custom_objects"

# Convention for config-context aggregation (issue #98): a single, non-polymorphic
# OBJECT field named as the key and pointing at the given (app_label, model) feeds the
# matching ConfigContext assignment dimension read by
# ConfigContextQuerySet.get_for_object(). Region / site-group / tenant-group /
# cluster-type / cluster-group are derived from site/tenant/cluster and need no field.
CONFIG_CONTEXT_DIMENSION_FIELDS = {
    "site": ("dcim", "site"),
    "tenant": ("tenancy", "tenant"),
    "role": ("dcim", "devicerole"),
    "platform": ("dcim", "platform"),
    "location": ("dcim", "location"),
    "device_type": ("dcim", "devicetype"),
    "cluster": ("virtualization", "cluster"),
}

# Field names that are reserved and cannot be used for custom object fields.
# Keep in alphabetical order for ease of reading error message.
RESERVED_FIELD_NAMES = [
    "_meta",
    "_state",
    "DoesNotExist",
    "MultipleObjectsReturned",
    "bookmarks",
    "clean",
    "clone",
    "contacts",
    "created",
    "custom_field_data",
    "custom_object_type",
    "custom_object_type_id",
    "delete",
    "full_clean",
    "get_absolute_url",
    "id",
    "images",
    "jobs",
    "journal_entries",
    "last_updated",
    "local_context_data",
    "model",
    "objects",
    "owner",
    "pk",
    "refresh_from_db",
    "save",
    "serialize_object",
    "snapshot",
    "subscriptions",
    "tags",
    "to_objectchange",
]
