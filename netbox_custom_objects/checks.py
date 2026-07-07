"""Django system checks.

Enforces conditional version floors that PluginConfig's static
``min_version``/``max_version`` can't express: when netbox-branching is
installed, NetBox and netbox-branching versions are pinned tighter because
the branching integration relies on APIs that only landed in those releases:

- ``serializer_resolver`` / ``register_serializer_resolver`` — NetBox 4.6.2
- ``register_branching_resolver``, ``register_objectchange_field_migrator``,
  and the ``squash_dependency_graph_built`` signal — netbox-branching 1.0.4

Users who never install netbox-branching keep the broader compatibility
window declared by PluginConfig.
"""

from importlib.metadata import PackageNotFoundError, version as _pkg_version

from django.apps import apps
from django.conf import settings
from django.core.checks import Error, Warning, register
from packaging.version import InvalidVersion, Version


# Version floors enforced only when netbox-branching is installed.
REQUIRED_NETBOX_VERSION_FOR_BRANCHING = '4.6.2'
REQUIRED_BRANCHING_VERSION = '1.0.4'

# The package is published under two distribution names depending on the
# release channel; try both before concluding the version is unknowable.
_BRANCHING_DIST_NAMES = ('netboxlabs-netbox-branching', 'netbox-branching')


def _get_branching_version():
    for dist_name in _BRANCHING_DIST_NAMES:
        try:
            return _pkg_version(dist_name)
        except PackageNotFoundError:
            continue
    raise PackageNotFoundError('netbox-branching')


@register()
def check_branching_compatibility(app_configs, **kwargs):
    """Enforce branching-only version floors; no-op without netbox-branching."""
    if not apps.is_installed('netbox_branching'):
        return []

    errors = []

    try:
        netbox_version = Version(settings.RELEASE.version)
        if netbox_version < Version(REQUIRED_NETBOX_VERSION_FOR_BRANCHING):
            errors.append(Error(
                f'netbox-custom-objects requires NetBox >= {REQUIRED_NETBOX_VERSION_FOR_BRANCHING} '
                f'when netbox-branching is installed (detected {netbox_version}).',
                hint='Upgrade NetBox, or remove netbox-branching from PLUGINS '
                     'if you do not need branching support for custom objects.',
                id='netbox_custom_objects.E001',
            ))
    except (AttributeError, InvalidVersion):
        pass  # settings.RELEASE missing/unparseable — other checks surface it

    try:
        branching_version = Version(_get_branching_version())
        if branching_version < Version(REQUIRED_BRANCHING_VERSION):
            errors.append(Error(
                f'netbox-custom-objects requires netbox-branching >= '
                f'{REQUIRED_BRANCHING_VERSION} (detected {branching_version}).',
                hint=f'Upgrade with: pip install "netboxlabs-netbox-branching>={REQUIRED_BRANCHING_VERSION}"',
                id='netbox_custom_objects.E002',
            ))
    except PackageNotFoundError:
        # netbox-branching is an installed app but its distribution metadata
        # isn't discoverable (e.g. an editable checkout without dist-info), so
        # the version floor can't be enforced.  Warn rather than silently pass
        # so an incompatible checkout doesn't slip through unnoticed.
        errors.append(Warning(
            'netbox-branching is installed but its version could not be '
            f'determined, so the >= {REQUIRED_BRANCHING_VERSION} requirement '
            'cannot be verified.',
            hint='If using an editable install, ensure its dist-info metadata '
                 'is present (reinstall with `pip install -e`).',
            id='netbox_custom_objects.W001',
        ))
    except InvalidVersion:
        pass  # unparseable version string — skip rather than emit a confusing error

    return errors
