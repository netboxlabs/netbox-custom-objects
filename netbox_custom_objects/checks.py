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
from netbox.plugins import get_plugin_config
from packaging.version import InvalidVersion, Version


# Version floors enforced only when netbox-branching is installed.
REQUIRED_NETBOX_VERSION_FOR_BRANCHING = '4.6.2'
REQUIRED_BRANCHING_VERSION = '1.0.4'


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
        branching_version = Version(_pkg_version('netbox-branching'))
        if branching_version < Version(REQUIRED_BRANCHING_VERSION):
            errors.append(Error(
                f'netbox-custom-objects requires netbox-branching >= '
                f'{REQUIRED_BRANCHING_VERSION} (detected {branching_version}).',
                hint=f'Upgrade with: pip install "netbox-branching>={REQUIRED_BRANCHING_VERSION}"',
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


@register()
def check_related_tabs_registration(app_configs, **kwargs):
    """Re-surface a swallowed register_tabs() failure as a startup warning.

    ``CustomObjectsPluginConfig.ready`` logs and swallows any exception from
    registering the combined "Custom Objects" related tab so it can't break
    NetBox startup.  This check turns that otherwise-invisible failure into a
    warning in ``manage.py check``.
    """
    try:
        app_config = apps.get_app_config('netbox_custom_objects')
    except LookupError:
        return []

    error = getattr(app_config, '_register_tabs_error', None)
    if not error:
        return []

    return [Warning(
        f'The combined "Custom Objects" related tab failed to register at startup '
        f'and will not appear on object detail pages ({error}).',
        hint='Resolve the underlying error (full traceback is in the NetBox log) and restart NetBox.',
        id='netbox_custom_objects.W002',
    )]


@register()
def check_multiobject_display_setting(app_configs, **kwargs):
    """Warn if max_multiobject_display isn't a positive int (the accessor falls back to 3)."""
    value = get_plugin_config('netbox_custom_objects', 'max_multiobject_display')
    if isinstance(value, int) and value >= 1:
        return []
    return [Warning(
        f'PLUGINS_CONFIG max_multiobject_display must be a positive integer (got {value!r}); using the default 3.',
        hint="Set 'max_multiobject_display' to a positive integer under PLUGINS_CONFIG['netbox_custom_objects'].",
        id='netbox_custom_objects.W003',
    )]
