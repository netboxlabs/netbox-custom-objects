# Models which do not support change logging, but whose database tables
# must be replicated for each branch to ensure proper functionality
INCLUDE_MODELS = (
    'dcim.cablepath',
    'extras.cachedvalue',
)

APP_LABEL = 'netbox_custom_objects'
