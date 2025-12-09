# Models which do not support change logging, but whose database tables
# must be replicated for each branch to ensure proper functionality
INCLUDE_MODELS = (
    "dcim.cablepath",
    "extras.cachedvalue",
)

APP_LABEL = "netbox_custom_objects"

# Field names that are reserved and cannot be used for custom object fields
RESERVED_FIELD_NAMES = [
    # Django model internals
    "_meta",
    "_state",
    "DoesNotExist",
    "MultipleObjectsReturned",
    "objects",
    # Primary key fields
    "id",
    "pk",
    # Django model methods
    "clean",
    "delete",
    "full_clean",
    "refresh_from_db",
    "save",
    # Custom object specific
    "clone",
    "custom_object_type",
    "custom_object_type_id",
    "custom_field_data",
    "model",
    # Change logging
    "created",
    "last_updated",
    "serialize_object",
    "snapshot",
    "to_objectchange",
    # Generic relations from mixins
    "bookmarks",
    "contacts",
    "images",
    "jobs",
    "journal_entries",
    "subscriptions",
    "tags",
    # URL methods
    "get_absolute_url",
]
