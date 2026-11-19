from netbox.config import get_config
from utilities.validators import EnhancedURLValidator

__all__ = ('AllowedSchemesURLValidator',)


class AllowedSchemesURLValidator(EnhancedURLValidator):
    """Validate URLs against the current ALLOWED_URL_SCHEMES setting."""

    def __call__(self, value):
        # EnhancedURLValidator keeps the schemes it first read; admins can change them at runtime.
        self.schemes = get_config().ALLOWED_URL_SCHEMES
        return super().__call__(value)
