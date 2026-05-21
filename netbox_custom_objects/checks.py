"""
System checks for netbox-custom-objects.

These run as part of Django's check framework (``manage.py check`` and any
command that invokes it — runserver, migrate, test, etc.).  Their purpose is
to enforce *conditional* compatibility requirements that PluginConfig's
unconditional ``min_version`` / ``max_version`` cannot express: when
netbox-branching is installed alongside this plugin, the supported NetBox
and netbox-branching version ranges narrow.  Users who never enable
branching keep the broader compatibility window.
"""

from importlib.metadata import PackageNotFoundError, version as _pkg_version

from django.apps import apps
from django.conf import settings
from django.core.checks import Error, register
from packaging.version import InvalidVersion, Version


# Version floors that apply only when netbox-branching is installed.
# Users without branching are governed by PluginConfig.min_version instead.
REQUIRED_NETBOX_VERSION = '4.6.2'
REQUIRED_BRANCHING_VERSION = '1.1.0'


@register()
def check_branching_compatibility(app_configs, **kwargs):
    """
    If netbox-branching is installed, enforce the version floors required for
    custom objects to integrate with it safely.  Returns no errors when
    netbox-branching is not installed.
    """
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
        # settings.RELEASE missing or unparseable — let other checks surface it.
        pass

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
        # Installed as an app but not discoverable via importlib.metadata
        # (e.g. editable install with a non-standard dist-info).  Skip the
        # version pin rather than emit a confusing error.
        pass

    return errors
