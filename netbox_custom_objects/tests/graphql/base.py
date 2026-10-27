"""
Shared setup for tests that drive the real ``/graphql/`` endpoint.
"""

import json
from unittest import mock

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from core.models import ObjectType
from dcim.models import Site
from users.models import ObjectPermission

from netbox_custom_objects.graphql import live as live_module

from ..base import create_token


class GraphQLClientMixin:
    """
    Drive the real ``/graphql/`` HTTP endpoint as the test user.

    Patches the startup guard off so the live schema is built and bound to the
    (monkey-patched) GraphQL view per request, and authenticates with a token —
    the same path a real client takes.
    """

    def setUp(self):
        super().setUp()
        live_module.reset_cache()
        self.addCleanup(live_module.reset_cache)
        patcher = mock.patch(
            'netbox_custom_objects.CustomObjectsPluginConfig.should_skip_dynamic_model_creation',
            return_value=False,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        # A fresh APIClient using token auth (not the session login set up by the
        # base class), mirroring how an API client reaches the endpoint.
        self.client = APIClient()
        token_key = create_token(self.user)
        self.header = {'HTTP_AUTHORIZATION': f'Token {token_key}'}
        self.url = reverse('graphql')

    def _grant(self, model, actions=('view',), constraints=None, enabled=True):
        perm = ObjectPermission.objects.create(
            name=f'perm-{model._meta.model_name}-{"-".join(actions)}',
            actions=list(actions),
            constraints=constraints,
            enabled=enabled,
        )
        perm.users.add(self.user)
        perm.object_types.add(ObjectType.objects.get_for_model(model))
        return perm


class GraphQLEndpointMixin(GraphQLClientMixin):
    """
    Query the endpoint as a superuser, so these tests focus on schema/resolution
    correctness rather than permission wiring (permissions are covered in
    test_permissions).
    """

    def setUp(self):
        super().setUp()
        self.user.is_superuser = True
        self.user.save()

    def _gql(self, query):
        response = self.client.post(self.url, data={'query': query}, format='json', **self.header)
        self.assertEqual(response.status_code, status.HTTP_200_OK, getattr(response, 'content', response))
        payload = json.loads(response.content)
        self.assertNotIn('errors', payload, msg=str(payload.get('errors')))
        return payload['data']

    def _make_site(self, name='Site', slug='site', region=None):
        return Site.objects.create(name=name, slug=slug, region=region)
