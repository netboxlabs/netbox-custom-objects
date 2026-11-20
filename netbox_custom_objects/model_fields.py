from django.db import models

from netbox_custom_objects.validators import AllowedSchemesURLValidator

__all__ = ('LaxURLModelField',)


class LaxURLModelField(models.URLField):
    """A URL column validated like the form: any allowed scheme, and hostnames without a domain."""

    default_validators = [AllowedSchemesURLValidator()]
