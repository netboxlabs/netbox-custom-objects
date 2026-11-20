from netbox_custom_objects.validators import AllowedSchemesURLValidator
from utilities.forms.fields import LaxURLField

__all__ = ('LaxURLFormField',)


class LaxURLFormField(LaxURLField):
    """NetBox's LaxURLField, validated against the current ALLOWED_URL_SCHEMES setting."""

    default_validators = [AllowedSchemesURLValidator()]
