"""
Tests for the conditional netbox-branching version system check
(``netbox_custom_objects.checks.check_branching_compatibility``).

The check is pure version-comparison logic gated on whether netbox-branching
is installed, so every branch is exercised by mocking its three inputs:
``apps.is_installed``, ``settings.RELEASE``, and the importlib.metadata
``version`` lookup.  No database is required, hence ``SimpleTestCase``.
"""
from importlib.metadata import PackageNotFoundError
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from netbox_custom_objects import checks


def _run(*, branching_installed=True, netbox_version='4.6.2', branching_version='1.0.4'):
    """Invoke the check with each input mocked.

    ``netbox_version`` / ``branching_version`` may be a version string, or an
    Exception instance/class to simulate the failure paths (a missing
    ``settings.RELEASE``, an unparseable version, or ``PackageNotFoundError``).
    """
    if isinstance(netbox_version, str):
        release = SimpleNamespace(version=netbox_version)
    else:
        # No RELEASE attribute -> accessing settings.RELEASE raises AttributeError.
        release = None

    def fake_pkg_version(_name):
        if isinstance(branching_version, str):
            return branching_version
        raise branching_version  # Exception instance or class

    settings_stub = SimpleNamespace()
    if release is not None:
        settings_stub.RELEASE = release

    with patch.object(checks.apps, 'is_installed', return_value=branching_installed), \
         patch.object(checks, 'settings', settings_stub), \
         patch.object(checks, '_pkg_version', fake_pkg_version):
        return checks.check_branching_compatibility(app_configs=None)


def _ids(errors):
    return {e.id for e in errors}


class CheckBranchingCompatibilityTest(SimpleTestCase):
    def test_no_branching_is_noop(self):
        """Without netbox-branching installed, the check returns no messages."""
        self.assertEqual(_run(branching_installed=False), [])

    def test_both_versions_satisfied(self):
        """Versions at the floor produce no errors."""
        self.assertEqual(_run(netbox_version='4.6.2', branching_version='1.0.4'), [])

    def test_newer_versions_satisfied(self):
        self.assertEqual(_run(netbox_version='4.7.0', branching_version='1.1.0'), [])

    def test_netbox_too_old(self):
        errors = _run(netbox_version='4.6.1', branching_version='1.0.4')
        self.assertEqual(_ids(errors), {'netbox_custom_objects.E001'})

    def test_branching_too_old(self):
        errors = _run(netbox_version='4.6.2', branching_version='1.0.3')
        self.assertEqual(_ids(errors), {'netbox_custom_objects.E002'})

    def test_both_too_old(self):
        errors = _run(netbox_version='4.5.0', branching_version='0.9.0')
        self.assertEqual(_ids(errors), {'netbox_custom_objects.E001', 'netbox_custom_objects.E002'})

    def test_missing_release_skips_netbox_check(self):
        """A missing settings.RELEASE skips E001 but still evaluates branching."""
        errors = _run(netbox_version=AttributeError, branching_version='1.0.3')
        self.assertEqual(_ids(errors), {'netbox_custom_objects.E002'})

    def test_unparseable_netbox_version_skips_netbox_check(self):
        errors = _run(netbox_version='not-a-version', branching_version='1.0.4')
        self.assertEqual(errors, [])

    def test_unresolvable_branching_version_warns(self):
        """An installed-but-unmeasurable branching dist warns rather than passing silently."""
        errors = _run(netbox_version='4.6.2', branching_version=PackageNotFoundError())
        self.assertEqual(_ids(errors), {'netbox_custom_objects.W001'})


class CheckRelatedTabsRegistrationTest(SimpleTestCase):
    """The check reads ``_register_tabs_error`` off the plugin's app config."""

    def _run(self, error):
        app_config = SimpleNamespace(_register_tabs_error=error)
        with patch.object(checks.apps, 'get_app_config', return_value=app_config):
            return checks.check_related_tabs_registration(app_configs=None)

    def test_no_error_is_noop(self):
        """Successful (or skipped) registration records no error -> no warning."""
        self.assertEqual(self._run(None), [])

    def test_error_warns(self):
        """A recorded failure produces W002 with the cause in the message."""
        errors = self._run('ImportError: boom')
        self.assertEqual(_ids(errors), {'netbox_custom_objects.W002'})
        self.assertIn('boom', errors[0].msg)

    def test_missing_app_config_is_noop(self):
        """A LookupError from get_app_config is tolerated, not raised."""
        with patch.object(checks.apps, 'get_app_config', side_effect=LookupError):
            self.assertEqual(checks.check_related_tabs_registration(app_configs=None), [])
