"""
Shared helpers for the branching integration tests.

Requires netbox-branching to be installed alongside this plugin.  All tests
are skipped when netbox-branching is absent so the suite remains clean in
environments that don't use branching.

These tests use TransactionTestCase (not TestCase) because branch schemas live
in separate PostgreSQL schemas backed by distinct database connections that
cannot be rolled back inside a single SAVEPOINT-based transaction.
"""

import logging
import os
import time
import uuid

from django.contrib.auth import get_user_model
from django.db import connections
from django.test import RequestFactory, TransactionTestCase, tag
from django.urls import reverse

from netbox_custom_objects.tests.base import (
    TransactionCleanupMixin,
    _recreate_contenttypes,
)
from netbox_custom_objects.utilities import is_branching_enabled

# Installed but not in PLUGINS, importing netbox_branching.models raises RuntimeError
# rather than ImportError, so check that it's enabled instead.
HAS_BRANCHING = is_branching_enabled()
if HAS_BRANCHING:
    from netbox_branching.choices import BranchStatusChoices
    from netbox_branching.models import Branch


logger = logging.getLogger(__name__)
User = get_user_model()


def _make_request(user):
    """Return a fresh request object suitable for event_tracking."""
    request = RequestFactory().get(reverse('home'))
    request.id = uuid.uuid4()
    request.user = user
    return request


# When netbox-branching is not installed, use ``object`` as the base so that
# none of the classes below are discovered by Django's test runner as test
# cases.  This avoids any interaction between the (skipped) TransactionTestCase
# machinery and the regular TestCase tests in the plugin's other test modules.
_TestBase = TransactionTestCase if HAS_BRANCHING else object

# Provisioning timeout for branch tests. Override via the
# ``NETBOX_CO_BRANCH_PROVISION_TIMEOUT`` env var (seconds) when CI flakes.
BRANCH_PROVISION_TIMEOUT = float(os.environ.get('NETBOX_CO_BRANCH_PROVISION_TIMEOUT', '30'))


def _provision_branch(name, merge_strategy=None, user=None, timeout=None):
    """Create and wait for a branch to reach READY status.

    ``merge_strategy`` is optional (the Branch field is nullable) — only tests that
    actually merge or revert need it; read-only tests (e.g. GraphQL, which can never
    write, merge, or sync) leave it ``None``.
    """
    if timeout is None:
        timeout = BRANCH_PROVISION_TIMEOUT
    branch = Branch(name=name, merge_strategy=merge_strategy)
    branch.save(provision=False)
    branch.provision(user=user)
    deadline = time.time() + timeout
    while time.time() < deadline:
        branch.refresh_from_db()
        if branch.status == BranchStatusChoices.READY:
            return branch
        time.sleep(0.1)
    raise TimeoutError(f'Branch {name!r} did not reach READY within {timeout:.0f} s (status={branch.status!r})')


def _close_branch_connections():
    """Close any open branch database connections.

    Best-effort cleanup between tests.  A ``DatabaseError`` here typically
    just means the connection was already closed by a previous teardown
    pass; we log at DEBUG so a genuine bug isn't silently hidden but normal
    multi-pass teardown stays quiet.
    """
    from django.db.utils import DatabaseError

    for branch in Branch.objects.all():
        try:
            connections[branch.connection_name].close()
        except DatabaseError:
            logger.debug(
                'failed to close branch connection %r',
                branch.connection_name,
                exc_info=True,
            )


@tag('branching')
class BranchingTestBase(TransactionCleanupMixin):
    """
    Common per-test lifecycle for branching-aware test classes.

    Centralises the ``_recreate_contenttypes`` / ``_make_request`` /
    ``_close_branch_connections`` boilerplate that was repeated across every
    branch test class so a new test class doesn't accidentally skip a step.

    Subclasses still need to inherit from ``TransactionTestCase`` (directly,
    not via this mixin) because branch schemas live in separate PostgreSQL
    schemas backed by distinct DB connections that can't be rolled back
    inside a single SAVEPOINT-based transaction.

    Tagged ``branching`` (subclasses inherit the tag), so CI can run these slow
    tests as a job of their own with ``--tag branching``.
    """

    def setUp(self):
        # → TransactionCleanupMixin.setUp() → _purge_stale_generated_models()
        super().setUp()
        _recreate_contenttypes()
        self.user = User.objects.create_user(username='testuser')
        self.request = _make_request(self.user)

    def tearDown(self):
        _close_branch_connections()
        # → TransactionCleanupMixin.tearDown() → TransactionTestCase
        super().tearDown()
