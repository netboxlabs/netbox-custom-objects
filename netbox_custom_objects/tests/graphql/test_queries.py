"""
Custom object queries through the GraphQL endpoint.

End-to-end tests that drive the real ``/graphql/`` HTTP endpoint with token
authentication, modelled on NetBox's own GraphQL test pattern.  They patch the
startup guard off so the live schema (installed via the view patch in
``__init__.py``) is built and bound per request, exactly as in production.
"""

import datetime
import decimal
import json

from django.test import TestCase, override_settings

from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Region, Site
from extras.models import ConfigContext, JournalEntry, Tag
from users.models import Owner

from ..base import CustomObjectsTestCase
from .base import GraphQLEndpointMixin


@override_settings(LOGIN_REQUIRED=True)
class GraphQLEndpointTestCase(GraphQLEndpointMixin, CustomObjectsTestCase, TestCase):
    """End-to-end tests against the real ``/graphql/`` HTTP endpoint."""

    def _make_device(self, name='dev1'):
        manufacturer, _ = Manufacturer.objects.get_or_create(name='Mfr', slug='mfr')
        device_type, _ = DeviceType.objects.get_or_create(manufacturer=manufacturer, model='Model', slug='model')
        role, _ = DeviceRole.objects.get_or_create(name='Role', slug='role')
        site = self._make_site()
        return Device.objects.create(name=name, device_type=device_type, role=role, site=site)

    def _site_object_field_type(self, name='Server', slug='server'):
        """A COT with a primary text field and a single-object field → Site."""
        cot = self.create_custom_object_type(name=name, slug=slug)
        self.create_custom_object_type_field(cot, name='name', label='Name', type='text', primary=True, required=True)
        self.create_custom_object_type_field(
            cot,
            name='site',
            label='Site',
            type='object',
            related_object_type=self.get_site_object_type(),
        )
        return cot

    def test_scalar_fields_query(self):
        cot = self.create_complex_custom_object_type(name='Asset', slug='asset')
        model = cot.get_model()
        model.objects.create(name='First', count=7, active=True, status='choice1')

        data = self._gql('{ custom_objects_asset_list { id display name count active status { value label } } }')
        rows = data['custom_objects_asset_list']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['display'], 'First')
        self.assertEqual(rows[0]['name'], 'First')
        self.assertEqual(rows[0]['count'], 7)
        self.assertTrue(rows[0]['active'])
        self.assertEqual(rows[0]['status'], {'value': 'choice1', 'label': 'Choice 1'})

    def test_multiselect_field_query_returns_value_label_pairs(self):
        """A multiselect field's values must each resolve to {value, label}, matching select."""
        cot = self.create_custom_object_type(name='Tagged', slug='tagged')
        self.create_custom_object_type_field(cot, name='name', label='Name', type='text', primary=True, required=True)
        choice_set = self.create_choice_set()
        self.create_custom_object_type_field(
            cot,
            name='tags_field',
            label='Tags',
            type='multiselect',
            choice_set=choice_set,
        )
        model = cot.get_model()
        model.objects.create(name='First', tags_field=['choice1', 'choice3'])

        data = self._gql('{ custom_objects_tagged_list { name tags_field { value label } } }')
        rows = data['custom_objects_tagged_list']
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            rows[0]['tags_field'],
            [
                {'value': 'choice1', 'label': 'Choice 1'},
                {'value': 'choice3', 'label': 'Choice 3'},
            ],
        )

    def test_decimal_field_round_trips(self):
        # A decimal field is annotated with the stdlib ``decimal.Decimal``, which
        # Strawberry maps to its built-in Decimal scalar.  Confirm the value
        # survives the round trip through the GraphQL endpoint with full
        # precision (Strawberry serializes the scalar to a string).
        cot = self.create_custom_object_type(name='Product', slug='product')
        self.create_custom_object_type_field(cot, name='name', label='Name', type='text', primary=True, required=True)
        self.create_custom_object_type_field(cot, name='price', label='Price', type='decimal')
        model = cot.get_model()
        model.objects.create(name='Widget', price=decimal.Decimal('19.99'))

        data = self._gql('{ custom_objects_product_list { name price } }')
        rows = data['custom_objects_product_list']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['name'], 'Widget')
        self.assertEqual(decimal.Decimal(str(rows[0]['price'])), decimal.Decimal('19.99'))

    def test_array_fields_round_trip(self):
        cot = self.create_custom_object_type(name='Zone', slug='zone')
        self.create_custom_object_type_field(cot, name='name', label='Name', type='text', primary=True, required=True)
        self.create_custom_object_type_field(
            cot,
            name='points',
            label='Points',
            type='array',
            array_element_type='integer',
            array_tuple_size=3,
        )
        self.create_custom_object_type_field(
            cot,
            name='days',
            label='Days',
            type='array',
            array_element_type='date',
        )
        model = cot.get_model()
        model.objects.create(name='Z', points=[[0, 0, 0], [10, 0, 5]], days=[datetime.date(2026, 1, 1)])

        rows = self._gql('{ custom_objects_zone_list { name points days } }')['custom_objects_zone_list']
        self.assertEqual(rows, [{'name': 'Z', 'points': [[0, 0, 0], [10, 0, 5]], 'days': ['2026-01-01']}])

    def test_json_field_round_trips(self):
        # A JSON field is annotated with Strawberry's JSON scalar; confirm a
        # structured value survives the round trip through the endpoint intact.
        cot = self.create_custom_object_type(name='Config', slug='config')
        self.create_custom_object_type_field(cot, name='name', label='Name', type='text', primary=True, required=True)
        self.create_custom_object_type_field(cot, name='data', label='Data', type='json')
        model = cot.get_model()
        payload_value = {'enabled': True, 'ports': [80, 443]}
        model.objects.create(name='web', data=payload_value)

        data = self._gql('{ custom_objects_config_list { name data } }')
        rows = data['custom_objects_config_list']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['name'], 'web')
        self.assertEqual(rows[0]['data'], payload_value)

    def test_single_object_query_by_id(self):
        cot = self.create_simple_custom_object_type(name='Note', slug='note')
        model = cot.get_model()
        instance = model.objects.create(name='Hello', description='world')

        data = self._gql(f'{{ custom_objects_note(id: {instance.pk}) {{ id display name description }} }}')
        self.assertEqual(data['custom_objects_note']['name'], 'Hello')
        self.assertEqual(data['custom_objects_note']['description'], 'world')

    def test_object_relationship_resolves_to_native_site_type(self):
        # A single-object field pointing at a Site must resolve to NetBox's
        # SiteType and be fully traversable (including nested relations like
        # region) — not a flat stub.
        region = Region.objects.create(name='West', slug='west')
        site = self._make_site(name='HQ', slug='hq', region=region)
        cot = self._site_object_field_type()
        model = cot.get_model()
        model.objects.create(name='S1', site=site)

        data = self._gql('{ custom_objects_server_list { name site { id name slug region { name } } } }')
        related = data['custom_objects_server_list'][0]['site']
        self.assertEqual(related['id'], str(site.pk))
        self.assertEqual(related['name'], 'HQ')
        self.assertEqual(related['slug'], 'hq')
        # Deep traversal into the related object's own relations proves it is the
        # native SiteType, not the flat CustomObjectRelatedObjectType.
        self.assertEqual(related['region']['name'], 'West')

    def test_multiobject_relationship_resolves_to_native_device_type(self):
        device = self._make_device(name='dev-a')
        # Slug 'group' would collide with NetBox's built-in `group_list` root query
        # field (user groups) — but the `custom_objects_` prefix keeps it distinct,
        # so this exercises both native multiobject resolution and the namespacing.
        cot = self.create_multi_object_custom_object_type(name='Group', slug='group')
        model = cot.get_model()
        instance = model.objects.create(name='G1')
        instance.devices.add(device)

        data = self._gql('{ custom_objects_group_list { name devices { id name role { name } } } }')
        devices = data['custom_objects_group_list'][0]['devices']
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0]['id'], str(device.pk))
        # 'name'/'role' are Device fields → confirms native DeviceType.
        self.assertEqual(devices[0]['name'], 'dev-a')
        self.assertEqual(devices[0]['role']['name'], 'Role')

    def test_polymorphic_object_field_resolves_to_union(self):
        # A polymorphic single-object field exposes a union of its target types;
        # an instance pointing at a Site resolves through the SiteType arm.
        site = self._make_site(name='PolySite', slug='polysite')
        cot = self.create_custom_object_type(name='Binding', slug='binding')
        self.create_custom_object_type_field(cot, name='name', label='Name', type='text', primary=True, required=True)
        self.create_polymorphic_field(
            cot,
            [self.get_site_object_type(), self.get_device_object_type()],
            name='target',
            type='object',
        )
        model = cot.get_model()
        model.objects.create(name='B1', target=site)

        # Alias the per-type 'name' selections: Site.name is String! while
        # Device.name is String, and GraphQL's same-response-shape rule forbids
        # selecting both under one response key.
        data = self._gql(
            '{ custom_objects_binding_list { name target { '
            '... on SiteType { id siteName: name } '
            '... on DeviceType { id deviceName: name } } } }'
        )
        target = data['custom_objects_binding_list'][0]['target']
        self.assertEqual(target['id'], str(site.pk))
        self.assertEqual(target['siteName'], 'PolySite')

    def _make_poly_cot(self):
        """COT with a polymorphic object + multiobject field, each -> Site/Device."""
        cot = self.create_custom_object_type(name='Poly705', slug='poly705')
        self.create_custom_object_type_field(
            cot,
            name='name',
            label='Name',
            type='text',
            primary=True,
            required=True,
        )
        targets = [self.get_site_object_type(), self.get_device_object_type()]
        self.create_polymorphic_field(cot, targets, name='poly_obj', type='object')
        self.create_polymorphic_field(cot, targets, name='poly_objs', type='multiobject')
        return cot

    def _make_poly_row(self, model, i):
        # Not self._make_device(): it creates its own Site with a fixed slug,
        # which collides across repeated calls.
        manufacturer, _ = Manufacturer.objects.get_or_create(name='Mfr', slug='mfr')
        device_type, _ = DeviceType.objects.get_or_create(manufacturer=manufacturer, model='Model', slug='model')
        role, _ = DeviceRole.objects.get_or_create(name='Role', slug='role')
        # Site/Device rows persist across phases below, so slugs must be unique
        # across the whole test, not just within one phase's range(n).
        uniq = Site.objects.count()
        site = self._make_site(name=f'PS{uniq}', slug=f'ps{uniq}')
        device = Device.objects.create(
            name=f'PD{uniq}',
            device_type=device_type,
            role=role,
            site=site,
        )
        obj = model.objects.create(name=f'prow{i}', poly_obj=site)
        obj.poly_objs.set([site, device])
        return obj

    def _query_count(self, query):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as ctx:
            self._gql(query)
        return len(ctx.captured_queries)

    def _assert_query_count_flat(self, model, query, make_row=None):
        """Query count must not scale with row count."""
        make_row = make_row or self._make_poly_row
        # Warm caches (ContentType, schema build, token) with a throwaway row
        # first so they don't skew the first real measurement below.
        make_row(model, 'warmup')
        self._query_count(query)
        model.objects.all().delete()

        for i in range(3):
            make_row(model, i)
        count_at_3 = self._query_count(query)

        model.objects.all().delete()
        for i in range(9):
            make_row(model, i)
        count_at_9 = self._query_count(query)

        self.assertEqual(
            count_at_3,
            count_at_9,
            f'query count scaled with row count ({count_at_3} at 3 rows vs {count_at_9} at 9 rows) -- N+1 regression',
        )

    def test_polymorphic_fields_query_count_stays_flat(self):
        cot = self._make_poly_cot()
        model = cot.get_model()
        query = (
            '{ custom_objects_poly705_list { name '
            'poly_obj { ... on SiteType { id } ... on DeviceType { id } } '
            'poly_objs { ... on SiteType { id } ... on DeviceType { id } } '
            '} }'
        )
        self._assert_query_count_flat(model, query)

    def test_multiobject_field_gets_filters_and_pagination_arguments(self):
        """A plain multiobject field's schema arguments must match tags on
        the same type (both need no custom resolver)."""
        cot = self.create_multi_object_custom_object_type(name='Args707', slug='args707')
        gql_type_name = f'{cot.get_model().__name__}Type'

        data = self._gql('{ __type(name: "%s") { fields { name args { name } } } }' % gql_type_name)  # noqa: UP031
        args_by_field = {f['name']: {a['name'] for a in f['args']} for f in data['__type']['fields']}
        self.assertEqual(args_by_field['tags'], {'filters', 'pagination'})
        # devices -> Device, sites -> Site: both core NetBox types with their
        # own registered filters.
        self.assertEqual(args_by_field['devices'], {'filters', 'pagination'})
        self.assertEqual(args_by_field['sites'], {'filters', 'pagination'})

    def _make_two_devices(self, name1, name2):
        # Not two self._make_device() calls: each creates its own Site
        # internally with a fixed default slug, colliding on the second call.
        manufacturer, _ = Manufacturer.objects.get_or_create(name='Mfr', slug='mfr')
        device_type, _ = DeviceType.objects.get_or_create(manufacturer=manufacturer, model='Model', slug='model')
        role, _ = DeviceRole.objects.get_or_create(name='Role', slug='role')
        site = self._make_site(name='TwoDevSite', slug='two-dev-site')
        return (
            Device.objects.create(name=name1, device_type=device_type, role=role, site=site),
            Device.objects.create(name=name2, device_type=device_type, role=role, site=site),
        )

    def test_multiobject_field_pagination_limits_results(self):
        cot = self.create_multi_object_custom_object_type(name='Page707', slug='page707')
        model = cot.get_model()
        d1, d2 = self._make_two_devices('d1', 'd2')
        obj = model.objects.create(name='obj1')
        obj.devices.set([d1, d2])

        data = self._gql('{ custom_objects_page707_list { devices(pagination: {limit: 1}) { name } } }')
        self.assertEqual(len(data['custom_objects_page707_list'][0]['devices']), 1)

    def test_multiobject_field_filters_restricts_results(self):
        cot = self.create_multi_object_custom_object_type(name='Filt707', slug='filt707')
        model = cot.get_model()
        d1, d2 = self._make_two_devices('keep', 'drop')
        obj = model.objects.create(name='obj1')
        obj.devices.set([d1, d2])

        data = self._gql('{ custom_objects_filt707_list { devices(filters: {name: {exact: "keep"}}) { name } } }')
        devices = data['custom_objects_filt707_list'][0]['devices']
        self.assertEqual([d['name'] for d in devices], ['keep'])

    def test_multiobject_declarative_field_query_count_stays_flat(self):
        """The declarative (resolver-free) path must not reintroduce an N+1."""
        cot = self.create_multi_object_custom_object_type(name='Flat707', slug='flat707')
        model = cot.get_model()

        manufacturer, _ = Manufacturer.objects.get_or_create(name='Mfr', slug='mfr')
        device_type, _ = DeviceType.objects.get_or_create(manufacturer=manufacturer, model='Model', slug='model')
        role, _ = DeviceRole.objects.get_or_create(name='Role', slug='role')

        def make_row(model, i):
            uniq = Site.objects.count()
            site = Site.objects.create(name=f'FS{uniq}', slug=f'fs{uniq}')
            device = Device.objects.create(
                name=f'FD{uniq}',
                device_type=device_type,
                role=role,
                site=site,
            )
            obj = model.objects.create(name=f'frow{i}')
            obj.devices.set([device])
            obj.sites.set([site])

        query = '{ custom_objects_flat707_list { name devices { name } sites { name } } }'
        self._assert_query_count_flat(model, query, make_row=make_row)

    def test_polymorphic_multiobject_field_has_no_filters_or_pagination_arguments(self):
        """Polymorphic fields keep the custom-resolver path (a QuerySet is
        inherently single-model), so they get neither argument."""
        cot = self._make_poly_cot()
        gql_type_name = f'{cot.get_model().__name__}Type'

        data = self._gql('{ __type(name: "%s") { fields { name args { name } } } }' % gql_type_name)  # noqa: UP031
        args_by_field = {f['name']: {a['name'] for a in f['args']} for f in data['__type']['fields']}
        self.assertEqual(args_by_field['poly_obj'], set())
        self.assertEqual(args_by_field['poly_objs'], set())

    def test_object_field_has_no_pagination_argument(self):
        """filters:/pagination: are list-only concepts; a singular OBJECT field
        (unlike MULTIOBJECT) is out of scope."""
        cot = self._site_object_field_type()
        gql_type_name = f'{cot.get_model().__name__}Type'

        data = self._gql('{ __type(name: "%s") { fields { name args { name } } } }' % gql_type_name)  # noqa: UP031
        args_by_field = {f['name']: {a['name'] for a in f['args']} for f in data['__type']['fields']}
        self.assertEqual(args_by_field['site'], set())

    def test_multiple_types_in_one_schema(self):
        # Several custom object types must all be queryable from the same schema.
        a = self.create_simple_custom_object_type(name='Alpha', slug='alpha')
        b = self.create_simple_custom_object_type(name='Beta', slug='beta')
        a.get_model().objects.create(name='a1')
        b.get_model().objects.create(name='b1')

        data = self._gql('{ custom_objects_alpha_list { name } custom_objects_beta_list { name } }')
        self.assertEqual(data['custom_objects_alpha_list'][0]['name'], 'a1')
        self.assertEqual(data['custom_objects_beta_list'][0]['name'], 'b1')

    def test_tags_and_base_fields(self):
        cot = self.create_simple_custom_object_type(name='Doc', slug='doc')
        model = cot.get_model()
        model.objects.create(name='D1')

        data = self._gql('{ custom_objects_doc_list { id display created tags { name } } }')
        row = data['custom_objects_doc_list'][0]
        self.assertEqual(row['display'], 'D1')
        self.assertIsNotNone(row['id'])
        self.assertEqual(row['tags'], [])

    def test_owner_and_journal_entries(self):
        cot = self.create_simple_custom_object_type(name='Owned', slug='owned')
        owner = Owner.objects.create(name='Owner 1')
        obj = cot.get_model().objects.create(name='O1', owner=owner)
        JournalEntry.objects.create(assigned_object=obj, created_by=self.user, comments='First entry')

        data = self._gql('{ custom_objects_owned_list { owner { id name } journal_entries { comments } } }')
        row = data['custom_objects_owned_list'][0]
        self.assertEqual(row['owner'], {'id': str(owner.pk), 'name': 'Owner 1'})
        self.assertEqual(row['journal_entries'], [{'comments': 'First entry'}])

    def test_owner_is_null_when_unset(self):
        cot = self.create_simple_custom_object_type(name='Unowned', slug='unowned')
        cot.get_model().objects.create(name='U1')

        data = self._gql('{ custom_objects_unowned_list { owner { id } journal_entries { id } } }')
        row = data['custom_objects_unowned_list'][0]
        self.assertIsNone(row['owner'])
        self.assertEqual(row['journal_entries'], [])

    def test_owner_field_shadowing_owner_fk(self):
        # Simulate an existing schema from before "owner" became a reserved field name.
        cot = self.create_custom_object_type(name='Legacy', slug='legacy')
        self.create_custom_object_type_field(cot, name='name', label='Name', type='text', primary=True, required=True)
        self.create_custom_object_type_field(cot, name='owner', label='Owner', type='text')
        cot.get_model().objects.create(name='L1', owner='Alice')

        data = self._gql('{ custom_objects_legacy_list { name owner journal_entries { id } } }')
        row = data['custom_objects_legacy_list'][0]
        self.assertEqual(row['owner'], 'Alice')
        self.assertEqual(row['journal_entries'], [])

    def test_local_context_data_when_config_context_enabled(self):
        cot = self.create_custom_object_type(name='Configured', slug='configured', config_context_enabled=True)
        self.create_custom_object_type_field(cot, name='name', label='Name', type='text', primary=True, required=True)
        cot.get_model().objects.create(name='C1', local_context_data={'ntp': '10.0.0.1'})

        data = self._gql('{ custom_objects_configured_list { local_context_data } }')
        self.assertEqual(data['custom_objects_configured_list'][0]['local_context_data'], {'ntp': '10.0.0.1'})

    def test_local_context_data_absent_without_config_context(self):
        cot = self.create_simple_custom_object_type(name='Plain', slug='plain')
        cot.get_model().objects.create(name='P1')

        response = self.client.post(
            self.url,
            data={'query': '{ custom_objects_plain_list { local_context_data } }'},
            format='json',
            **self.header,
        )
        payload = json.loads(response.content)
        self.assertIn('errors', payload)
        self.assertIn('local_context_data', payload['errors'][0]['message'])

    def _config_context_type(self, slug='cc', extra_fields=()):
        cot = self.create_custom_object_type(name=slug.title(), slug=slug, config_context_enabled=True)
        self.create_custom_object_type_field(cot, name='name', label='Name', type='text', primary=True, required=True)
        self.create_custom_object_type_field(
            cot,
            name='site',
            label='Site',
            type='object',
            related_object_type=self.get_site_object_type(),
        )
        for kwargs in extra_fields:
            self.create_custom_object_type_field(cot, **kwargs)
        return cot

    def test_config_context_renders_source_and_local_data(self):
        site = self._make_site()
        site_context = ConfigContext.objects.create(name='site', weight=100, data={'ntp': '10.0.0.1', 'dns': 'a'})
        site_context.sites.add(site)
        tag = Tag.objects.create(name='Gold', slug='gold')
        tag_context = ConfigContext.objects.create(name='gold', weight=200, data={'tier': 'gold'})
        tag_context.tags.add(tag)

        cot = self._config_context_type()
        model = cot.get_model()
        tagged = model.objects.create(name='tagged', site=site, local_context_data={'dns': 'b'})
        tagged.tags.add(tag)
        model.objects.create(name='plain', site=site)

        data = self._gql('{ custom_objects_cc_list { name config_context } }')
        rendered = {row['name']: row['config_context'] for row in data['custom_objects_cc_list']}
        self.assertEqual(rendered['tagged'], {'ntp': '10.0.0.1', 'dns': 'b', 'tier': 'gold'})
        self.assertEqual(rendered['plain'], {'ntp': '10.0.0.1', 'dns': 'a'})

    def test_config_context_absent_without_config_context(self):
        cot = self.create_simple_custom_object_type(name='Plain', slug='plain')
        cot.get_model().objects.create(name='P1')

        response = self.client.post(
            self.url,
            data={'query': '{ custom_objects_plain_list { config_context } }'},
            format='json',
            **self.header,
        )
        payload = json.loads(response.content)
        self.assertIn('errors', payload)
        self.assertIn('config_context', payload['errors'][0]['message'])

    def test_custom_field_named_config_context_takes_precedence(self):
        cot = self._config_context_type(
            extra_fields=[
                {'name': 'config_context', 'label': 'Config context', 'type': 'text'},
            ]
        )
        cot.get_model().objects.create(name='C1', config_context='custom value')

        data = self._gql('{ custom_objects_cc_list { config_context } }')
        self.assertEqual(data['custom_objects_cc_list'][0]['config_context'], 'custom value')

    def test_unexposed_custom_field_named_config_context_still_takes_precedence(self):
        """A coordinates field has no GraphQL representation but still claims the name, as in REST."""
        self._config_context_type(
            extra_fields=[
                {'name': 'config_context', 'label': 'Config context', 'type': 'coordinates'},
            ]
        )

        response = self.client.post(
            self.url,
            data={'query': '{ custom_objects_cc_list { config_context } }'},
            format='json',
            **self.header,
        )
        payload = json.loads(response.content)
        self.assertIn('errors', payload)
        self.assertIn('config_context', payload['errors'][0]['message'])

    def test_config_context_query_count_stays_flat(self):
        site = self._make_site()
        ConfigContext.objects.create(name='site', weight=100, data={'ntp': '10.0.0.1'}).sites.add(site)
        model = self._config_context_type().get_model()

        def make_row(model, i):
            model.objects.create(name=f'row-{i}', site=site, local_context_data={'index': str(i)})

        self._assert_query_count_flat(model, '{ custom_objects_cc_list { name config_context } }', make_row)
