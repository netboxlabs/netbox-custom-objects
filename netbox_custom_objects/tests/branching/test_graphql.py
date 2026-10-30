"""
The GraphQL schema and endpoint follow the active branch.
"""

import json
import unittest
from unittest import mock

from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from netbox_custom_objects.models import (
    CustomObjectType,
    CustomObjectTypeField,
)
from netbox_custom_objects.tests.base import (
    create_token,
)

from .base import (
    HAS_BRANCHING,
    BranchingTestBase,
    User,
    _make_request,
    _provision_branch,
    _TestBase,
)

if HAS_BRANCHING:
    from netbox_branching.utilities import activate_branch

    from netbox.context_managers import event_tracking


@unittest.skipUnless(HAS_BRANCHING, 'netbox-branching is not installed')
class GraphQLBranchIsolationTestCase(BranchingTestBase, _TestBase):
    """
    GraphQL resolves against whichever branch netbox-branching activated for the
    request (X-NetBox-Branch header, ``?_branch=``, or the active_branch cookie),
    exactly like the REST API and the UI; with no branch active it is main.  The
    schema is built per-branch, so a branch's custom object types appear only for
    requests scoped to that branch and never leak into main.
    """

    def setUp(self):
        super().setUp()
        from netbox_custom_objects.graphql import live as live_module

        self.live = live_module
        live_module.reset_cache()
        self.addCleanup(live_module.reset_cache)
        # The schema is built per user; a superuser's includes every type.
        self.superuser = User.objects.create_superuser(username='graphql-branch-admin')
        # The startup guard returns True during the test run; patch it off so the
        # live schema machinery runs exactly as it does in production.
        patcher = mock.patch(
            'netbox_custom_objects.CustomObjectsPluginConfig.should_skip_dynamic_model_creation',
            return_value=False,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_schema_signature_is_branch_aware(self):
        # The signature drives rebuilds; computed under a branch it must see that
        # branch's custom object types (so the branch gets its own schema), while
        # main — unaffected by the unmerged branch — keeps its own.
        main_sig = self.live.schema_signature()

        branch = _provision_branch('GraphQL Sig Branch', user=self.user)
        branch_request = _make_request(self.user)
        with activate_branch(branch), event_tracking(branch_request):
            cot = CustomObjectType.objects.create(name='branch_only_sig', slug='branch-only-sig')
            CustomObjectTypeField.objects.create(
                custom_object_type=cot,
                name='name',
                label='Name',
                type='text',
                primary=True,
                required=True,
            )
            self.assertNotEqual(main_sig, self.live.schema_signature())

        # Back on main, the signature is unchanged by the unmerged branch.
        self.assertEqual(main_sig, self.live.schema_signature())

    def test_live_schema_reflects_active_branch(self):
        # With a branch active (as the X-NetBox-Branch header path leaves it), the
        # schema reflects that branch's custom object types; main, unaffected by the
        # unmerged branch, does not.
        baseline = self.live.get_live_schema(self.superuser)
        self.assertIsNotNone(baseline)
        self.assertNotIn('custom_objects_branch_only', str(baseline))

        branch = _provision_branch('GraphQL Branch Schema', user=self.user)
        branch_request = _make_request(self.user)
        with activate_branch(branch), event_tracking(branch_request):
            cot = CustomObjectType.objects.create(name='branch only', slug='branch-only')
            CustomObjectTypeField.objects.create(
                custom_object_type=cot,
                name='name',
                label='Name',
                type='text',
                primary=True,
                required=True,
            )
            # The branch's own schema gains the branch-only type...
            self.assertIn('custom_objects_branch_only', str(self.live.get_live_schema(self.superuser)))

        # ...but main never does (the branch isn't merged).
        self.assertNotIn('custom_objects_branch_only', str(self.live.get_live_schema(self.superuser)))

    def test_branch_deletion_evicts_cached_schema(self):
        # Building a branch's schema caches it under the branch pk; deleting the
        # branch must evict that entry so it doesn't leak for the process lifetime.
        branch = _provision_branch('GraphQL Evict', user=self.user)
        branch_request = _make_request(self.user)
        with activate_branch(branch), event_tracking(branch_request):
            cot = CustomObjectType.objects.create(name='evict me', slug='evict-me')
            CustomObjectTypeField.objects.create(
                custom_object_type=cot,
                name='name',
                label='Name',
                type='text',
                primary=True,
                required=True,
            )
            self.assertIsNotNone(self.live.get_live_schema(self.superuser))

        # The branch's schema is now cached under its pk...
        self.assertIn(branch.pk, self.live._schema_cache)

        # ...and deleting the branch evicts it.
        branch_pk = branch.pk
        branch.delete()
        self.assertNotIn(branch_pk, self.live._schema_cache)


@unittest.skipUnless(HAS_BRANCHING, 'netbox-branching is not installed')
# override_settings cannot be applied unconditionally: when HAS_BRANCHING is
# False, _TestBase is ``object`` (not TransactionTestCase), so the decorator
# would try to wrap _pre_setup/_post_teardown methods that don't exist.
@(override_settings(LOGIN_REQUIRED=True) if HAS_BRANCHING else lambda cls: cls)
class GraphQLBranchEndpointTestCase(BranchingTestBase, _TestBase):
    """
    End-to-end against the real ``/graphql/`` endpoint: it serves whichever branch
    netbox-branching activated for the request (the ``X-NetBox-Branch`` header or the
    ``?_branch=`` query param) and main otherwise — correct schema AND data in each —
    and a schema change made inside a branch never leaks into main (the branch is not
    merged).
    """

    def setUp(self):
        super().setUp()
        from netbox_custom_objects.graphql import live as live_module

        self.live = live_module
        live_module.reset_cache()
        self.addCleanup(live_module.reset_cache)
        # The startup guard returns True during the test run; patch it off so the
        # live schema machinery runs exactly as it does in production.
        patcher = mock.patch(
            'netbox_custom_objects.CustomObjectsPluginConfig.should_skip_dynamic_model_creation',
            return_value=False,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        # Superuser + token auth, mirroring how an API client reaches the endpoint.
        self.user.is_superuser = True
        self.user.save()
        self.client = APIClient()
        token_key = create_token(self.user)
        self.header = {'HTTP_AUTHORIZATION': f'Token {token_key}'}
        self.url = reverse('graphql')

    def _post(self, query, branch=None, via='header'):
        headers = dict(self.header)
        url = self.url
        if branch is not None:
            if via == 'header':
                # Django maps HTTP_X_NETBOX_BRANCH → the X-NetBox-Branch request
                # header netbox-branching reads to activate the branch.
                headers['HTTP_X_NETBOX_BRANCH'] = branch.schema_id
            elif via == 'query':
                # The ?_branch= query param the UI uses — another branch source
                # netbox-branching honours for any request.
                url = f'{self.url}?_branch={branch.schema_id}'
        response = self.client.post(url, data={'query': query}, format='json', **headers)
        return json.loads(response.content)

    def _data(self, query, branch=None, via='header'):
        payload = self._post(query, branch=branch, via=via)
        self.assertNotIn('errors', payload, msg=str(payload.get('errors')))
        return payload['data']

    def _assert_query_rejected(self, query, branch=None):
        # A field/type absent from the active schema is a GraphQL validation error.
        payload = self._post(query, branch=branch)
        self.assertIn('errors', payload, msg=f'expected schema to reject query: {payload}')

    def test_main_and_branch_isolated_schema_and_data(self):
        # --- main: COT 'server' (field 'name') with one instance ---
        server = CustomObjectType.objects.create(name='server', slug='server')
        CustomObjectTypeField.objects.create(
            custom_object_type=server,
            name='name',
            label='Name',
            type='text',
            primary=True,
            required=True,
        )
        server.get_model().objects.create(name='main-server')

        # --- branch: a branch-only COT 'widget', plus a NEW field 'note' added to
        # the existing 'server' COT and a branch-only server row that uses it ---
        branch = _provision_branch('GraphQL E2E', user=self.user)
        branch_request = _make_request(self.user)
        with activate_branch(branch), event_tracking(branch_request):
            widget = CustomObjectType.objects.create(name='widget', slug='widget')
            CustomObjectTypeField.objects.create(
                custom_object_type=widget,
                name='name',
                label='Name',
                type='text',
                primary=True,
                required=True,
            )
            widget.get_model().objects.create(name='branch-widget')

            server_in_branch = CustomObjectType.objects.get(pk=server.pk)
            CustomObjectTypeField.objects.create(
                custom_object_type=server_in_branch,
                name='note',
                label='Note',
                type='text',
            )
            server_in_branch.get_model().objects.create(name='branch-server', note='hi')

        self.live.reset_cache()

        # --- MAIN (no header): only main's schema and data ---
        main = self._data('{ custom_objects_server_list { name } }')
        self.assertEqual(
            [r['name'] for r in main['custom_objects_server_list']],
            ['main-server'],
            'main must not see the branch-only server row',
        )
        # The branch-added 'note' field is absent from main's schema.
        self._assert_query_rejected('{ custom_objects_server_list { name note } }')
        # The branch-only 'widget' COT is absent from main's schema.
        self._assert_query_rejected('{ custom_objects_widget_list { name } }')

        # --- BRANCH (header): branch schema (note + widget) and branch data ---
        data = self._data(
            '{ custom_objects_server_list { name note } custom_objects_widget_list { name } }',
            branch=branch,
        )
        servers = {r['name']: r['note'] for r in data['custom_objects_server_list']}
        # The branch is a copy of main plus its own change: both rows are visible,
        # and only the branch row carries the branch-only 'note' value.
        self.assertIn('main-server', servers)
        self.assertEqual(servers.get('branch-server'), 'hi')
        self.assertEqual(
            [r['name'] for r in data['custom_objects_widget_list']],
            ['branch-widget'],
        )

        # --- BRANCH via ?_branch= query param (no header): the UI's branch source
        # selects the same branch.  GraphQL honours every branch source NetBox does,
        # not just the header — if it didn't, this would fall back to main and reject
        # the branch-only 'widget' field. ---
        via_param = self._data(
            '{ custom_objects_widget_list { name } }',
            branch=branch,
            via='query',
        )
        self.assertEqual(
            [r['name'] for r in via_param['custom_objects_widget_list']],
            ['branch-widget'],
        )

        # --- MAIN again: still unchanged by the unmerged branch ---
        main_again = self._data('{ custom_objects_server_list { name } }')
        self.assertEqual(
            [r['name'] for r in main_again['custom_objects_server_list']],
            ['main-server'],
        )
