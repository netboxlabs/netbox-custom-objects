"""Django system checks.

Enforces conditional version floors that PluginConfig's static
``min_version``/``max_version`` can't express: when netbox-branching is
installed, NetBox and netbox-branching versions are pinned tighter.
"""

from importlib.metadata import PackageNotFoundError, version as _pkg_version

from django.apps import apps
from django.conf import settings
from django.core.checks import Error, register
from packaging.version import InvalidVersion, Version


# Version floors enforced only when netbox-branching is installed.
REQUIRED_NETBOX_VERSION = '4.6.2'
REQUIRED_BRANCHING_VERSION = '1.1.0'


@register()
def check_branching_compatibility(app_configs, **kwargs):
    """Enforce branching-only version floors; no-op without netbox-branching."""
    if not apps.is_installed('netbox_branching'):
        return []

    errors = []

    try:
        netbox_version = Version(settings.RELEASE.version)
        if netbox_version < Version(REQUIRED_NETBOX_VERSION):
            errors.append(Error(
                f'netbox-custom-objects requires NetBox >= {REQUIRED_NETBOX_VERSION} '
                f'when netbox-branching is installed (detected {netbox_version}).',
                hint='Upgrade NetBox, or remove netbox-branching from PLUGINS '
                     'if you do not need branching support for custom objects.',
                id='netbox_custom_objects.E001',
            ))
    except (AttributeError, InvalidVersion):
        pass  # settings.RELEASE missing/unparseable — other checks surface it

    try:
        branching_version = Version(_pkg_version('netbox-branching'))
        if branching_version < Version(REQUIRED_BRANCHING_VERSION):
            errors.append(Error(
                f'netbox-custom-objects requires netbox-branching >= '
                f'{REQUIRED_BRANCHING_VERSION} (detected {branching_version}).',
                hint=f'Upgrade with: pip install "netbox-branching>={REQUIRED_BRANCHING_VERSION}"',
                id='netbox_custom_objects.E002',
            ))
    except (PackageNotFoundError, InvalidVersion):
        pass  # editable install without dist-info — skip rather than warn

    return errors
