# Models which do not support change logging, but whose database tables
# must be replicated for each branch to ensure proper functionality
INCLUDE_MODELS = (
    "dcim.cablepath",
    "extras.cachedvalue",
)

APP_LABEL = "netbox_custom_objects"

# Field names that are reserved and cannot be used for custom object fields
RESERVED_FIELD_NAMES = [
    "bookmarks",
    "contacts",
    "created",
    "custom_field_data",
    "id",
    "images",
    "jobs",
    "journal_entries",
    "last_updated",
    "pk",
    "subscriptions",
    "tags",
]
