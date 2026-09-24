"""
Every ``COMPAT(<package><<version>)`` marker must still be reachable.

Code that exists only to support dependency releases older than some version is
tagged ``COMPAT(netbox<4.6.1)`` (or ``COMPAT(netbox-branching<...)``).  Once the
plugin's supported floor for that package reaches the marker's version, the code
is dead; these tests fail so that raising the floor also removes it.
"""

import re
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase
from packaging.version import Version

import netbox_custom_objects
from netbox_custom_objects import CustomObjectsPluginConfig
from netbox_custom_objects import checks

MARKER_RE = re.compile(r"COMPAT\(([a-z][a-z0-9-]*)<([0-9][0-9.]*)\)")
PACKAGE_ROOT = Path(netbox_custom_objects.__file__).parent


def _floors():
    return {
        "netbox": Version(CustomObjectsPluginConfig.min_version),
        "netbox-branching": Version(checks.REQUIRED_BRANCHING_VERSION),
    }


def _markers():
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        rel = path.relative_to(PACKAGE_ROOT)
        if rel.parts[0] == "tests":
            continue
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            for match in MARKER_RE.finditer(line):
                yield f"{rel}:{lineno}", match.group(1), Version(match.group(2))


def _stale_and_unknown_markers():
    floors = _floors()
    stale, unknown = [], []
    for location, package, version in _markers():
        if package not in floors:
            unknown.append(f"{location}: COMPAT({package}<{version})")
        elif floors[package] >= version:
            stale.append(f"{location}: COMPAT({package}<{version}), floor is {floors[package]}")
    return stale, unknown


class CompatMarkerTestCase(SimpleTestCase):

    def test_markers_are_found(self):
        self.assertTrue(list(_markers()))

    def test_no_marker_at_or_below_supported_floor(self):
        stale, unknown = _stale_and_unknown_markers()
        self.assertEqual(unknown, [], "COMPAT marker names a package with no known version floor")
        self.assertEqual(
            stale, [],
            "The supported floor has reached these markers: remove the compatibility code they tag.",
        )

    def test_raising_floor_flags_markers(self):
        with mock.patch.object(CustomObjectsPluginConfig, "min_version", "99.0"):
            stale, _ = _stale_and_unknown_markers()
        self.assertTrue(stale)
        self.assertTrue(all("COMPAT(netbox<" in entry for entry in stale))
