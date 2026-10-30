"""
Custom object filters through the GraphQL endpoint.

End-to-end tests that drive the real ``/graphql/`` HTTP endpoint with token
authentication, modelled on NetBox's own GraphQL test pattern.  They patch the
startup guard off so the live schema (installed via the view patch in
``__init__.py``) is built and bound per request, exactly as in production.
"""

import decimal
import json

from django.test import TestCase, override_settings

from core.models import ObjectType
from dcim.models import Device, DeviceRole, DeviceType, Manufacturer
from extras.models import Tag
from netbox_custom_objects.constants import APP_LABEL

from ..base import CustomObjectsTestCase
from .base import GraphQLEndpointMixin


@override_settings(LOGIN_REQUIRED=True)
class GraphQLFilterTestCase(GraphQLEndpointMixin, CustomObjectsTestCase, TestCase):
    """Server-side ``filters:`` on custom object list queries."""

    def _names(self, list_field, filters):
        data = self._gql(f'{{ {list_field}(filters: {filters}) {{ name }} }}')
        return sorted(row['name'] for row in data[list_field])

    def test_scalar_field_filters(self):
        cot = self.create_custom_object_type(name='Asset', slug='asset')
        self.create_custom_object_type_field(cot, name='name', label='Name', type='text', primary=True, required=True)
        self.create_custom_object_type_field(cot, name='count', label='Count', type='integer')
        self.create_custom_object_type_field(cot, name='price', label='Price', type='decimal')
        self.create_custom_object_type_field(cot, name='active', label='Active', type='boolean')
        self.create_custom_object_type_field(cot, name='bought', label='Bought', type='date')
        self.create_custom_object_type_field(cot, name='seen', label='Seen', type='datetime')
        self.create_custom_object_type_field(cot, name='notes', label='Notes', type='longtext')
        self.create_custom_object_type_field(cot, name='link', label='Link', type='url')
        self.create_custom_object_type_field(cot, name='shade', label='Shade', type='color')
        self.create_custom_object_type_field(cot, name='mac', label='MAC', type='mac_address')
        model = cot.get_model()
        model.objects.create(
            name='Alpha',
            count=1,
            price=decimal.Decimal('1.50'),
            active=True,
            bought='2026-01-15',
            seen='2026-01-15T10:00:00Z',
            notes='Rack 4, top shelf',
            link='https://example.com/alpha',
            shade='ff0000',
            mac='00:1a:2b:3c:4d:5e',
        )
        model.objects.create(
            name='Beta',
            count=5,
            price=decimal.Decimal('9.99'),
            active=False,
            bought='2026-06-01',
            seen='2026-06-01T10:00:00Z',
            notes='Spare',
            link='https://example.org/beta',
            shade='00ff00',
            mac='00:1a:2b:3c:4d:ff',
        )
        model.objects.create(name='Gamma', count=10)

        field = 'custom_objects_asset_list'
        self.assertEqual(self._names(field, '{name: {i_contains: "ET"}}'), ['Beta'])
        self.assertEqual(self._names(field, '{count: {gte: 5}}'), ['Beta', 'Gamma'])
        self.assertEqual(self._names(field, '{count: {range: {start: 2, end: 6}}}'), ['Beta'])
        self.assertEqual(self._names(field, '{price: {lt: "5"}}'), ['Alpha'])
        self.assertEqual(self._names(field, '{active: {exact: false}}'), ['Beta'])
        self.assertEqual(self._names(field, '{active: {is_null: true}}'), ['Gamma'])
        self.assertEqual(self._names(field, '{bought: {gte: "2026-03-01"}}'), ['Beta'])
        self.assertEqual(self._names(field, '{bought: {year: {exact: 2026}}}'), ['Alpha', 'Beta'])
        self.assertEqual(self._names(field, '{seen: {lt: "2026-03-01T00:00:00Z"}}'), ['Alpha'])
        self.assertEqual(self._names(field, '{notes: {i_contains: "shelf"}}'), ['Alpha'])
        self.assertEqual(self._names(field, '{link: {ends_with: ".org/beta"}}'), ['Beta'])
        self.assertEqual(self._names(field, '{shade: {exact: "00ff00"}}'), ['Beta'])
        rows = self._gql(f'{{ {field}(filters: {{shade: {{exact: "ff0000"}}}}) {{ shade }} }}')[field]
        self.assertEqual(rows, [{'shade': 'ff0000'}])
        self.assertEqual(self._names(field, '{mac: {exact: "00-1A-2B-3C-4D-FF"}}'), ['Beta'])
        self.assertEqual(self._names(field, '{mac: {i_contains: "3c:4d"}}'), ['Alpha', 'Beta'])
        rows = self._gql(f'{{ {field}(filters: {{name: {{exact: "Alpha"}}}}) {{ mac }} }}')[field]
        self.assertEqual(rows, [{'mac': '00:1A:2B:3C:4D:5E'}])

    def test_choice_field_filters(self):
        cot = self.create_custom_object_type(name='Box', slug='box')
        self.create_custom_object_type_field(cot, name='name', label='Name', type='text', primary=True, required=True)
        choice_set = self.create_choice_set()
        self.create_custom_object_type_field(
            cot,
            name='status',
            label='Status',
            type='select',
            choice_set=choice_set,
        )
        self.create_custom_object_type_field(
            cot,
            name='labels',
            label='Labels',
            type='multiselect',
            choice_set=choice_set,
        )
        model = cot.get_model()
        model.objects.create(name='A', status='choice1', labels=['choice1', 'choice2'])
        model.objects.create(name='B', status='choice2', labels=['choice3'])

        field = 'custom_objects_box_list'
        self.assertEqual(self._names(field, '{status: {exact: "choice2"}}'), ['B'])
        self.assertEqual(self._names(field, '{status: {in_list: ["choice1", "choice2"]}}'), ['A', 'B'])
        self.assertEqual(self._names(field, '{labels: {contains: ["choice2"]}}'), ['A'])
        self.assertEqual(self._names(field, '{labels: {overlap: ["choice2", "choice3"]}}'), ['A', 'B'])

    def test_integer_filter_beyond_32_bits(self):
        cot = self.create_custom_object_type(name='Counter', slug='counter')
        self.create_custom_object_type_field(cot, name='name', label='Name', type='text', primary=True, required=True)
        self.create_custom_object_type_field(cot, name='total', label='Total', type='integer')
        model = cot.get_model()
        model.objects.create(name='small', total=1)
        model.objects.create(name='big', total=2147483648)

        field = 'custom_objects_counter_list'
        self.assertEqual(self._names(field, '{total: {exact: 2147483648}}'), ['big'])
        self.assertEqual(self._names(field, '{total: {gt: 2147483647}}'), ['big'])

    def test_field_named_like_a_lookup(self):
        """Fields named like strawberry-django lookups (in_list, is_null, ...) filter on themselves."""
        cot = self.create_custom_object_type(name='Alias', slug='alias')
        self.create_custom_object_type_field(cot, name='name', label='Name', type='text', primary=True, required=True)
        self.create_custom_object_type_field(cot, name='in_list', label='In list', type='text')
        self.create_custom_object_type_field(cot, name='is_null', label='Is null', type='integer')
        model = cot.get_model()
        model.objects.create(name='a1', in_list='example', is_null=1)
        model.objects.create(name='a2', in_list='other', is_null=2)

        field = 'custom_objects_alias_list'
        self.assertEqual(self._names(field, '{in_list: {exact: "example"}}'), ['a1'])
        self.assertEqual(self._names(field, '{is_null: {gte: 2}}'), ['a2'])
        self.assertEqual(
            self._names(field, '{in_list: {i_contains: "OTH"}, OR: {is_null: {exact: 1}}}'),
            [
                'a1',
                'a2',
            ],
        )

    def test_json_field_filter(self):
        cot = self.create_custom_object_type(name='Config', slug='config')
        self.create_custom_object_type_field(cot, name='name', label='Name', type='text', primary=True, required=True)
        self.create_custom_object_type_field(cot, name='data', label='Data', type='json')
        model = cot.get_model()
        model.objects.create(name='c1', data={'env': 'prod', 'replicas': 3})
        model.objects.create(name='c2', data={'env': 'dev', 'replicas': 1})

        field = 'custom_objects_config_list'
        self.assertEqual(self._names(field, '{data: {path: "env", lookup: {string_lookup: {exact: "prod"}}}}'), ['c1'])
        self.assertEqual(
            self._names(field, '{data: {path: "replicas", lookup: {int_comparison_lookup: {lt: 2}}}}'), ['c2']
        )

    def test_base_filters_and_logical_operators(self):
        cot = self.create_custom_object_type(name='Thing', slug='thing')
        self.create_custom_object_type_field(cot, name='name', label='Name', type='text', primary=True, required=True)
        model = cot.get_model()
        first = model.objects.create(name='One')
        second = model.objects.create(name='Two')
        model.objects.create(name='Three')
        tag = Tag.objects.create(name='Gold', slug='gold')
        second.tags.add(tag)

        field = 'custom_objects_thing_list'
        self.assertEqual(self._names(field, f'{{id: {{in_list: ["{first.pk}", "{second.pk}"]}}}}'), ['One', 'Two'])
        self.assertEqual(self._names(field, '{tags: {slug: {exact: "gold"}}}'), ['Two'])
        self.assertEqual(self._names(field, '{name: {exact: "One"}, OR: {name: {exact: "Three"}}}'), ['One', 'Three'])
        self.assertEqual(self._names(field, '{NOT: {name: {exact: "One"}}}'), ['Three', 'Two'])

    def test_object_field_filters(self):
        cot = self.create_custom_object_type(name='Server', slug='server')
        self.create_custom_object_type_field(cot, name='name', label='Name', type='text', primary=True, required=True)
        self.create_custom_object_type_field(
            cot,
            name='site',
            label='Site',
            type='object',
            related_object_type=self.get_site_object_type(),
        )
        site_a = self._make_site(name='Site A', slug='site-a')
        site_b = self._make_site(name='Site B', slug='site-b')
        model = cot.get_model()
        model.objects.create(name='s1', site=site_a)
        model.objects.create(name='s2', site=site_b)
        model.objects.create(name='s3')

        field = 'custom_objects_server_list'
        self.assertEqual(self._names(field, f'{{site_id: "{site_b.pk}"}}'), ['s2'])
        self.assertEqual(self._names(field, '{site: {slug: {exact: "site-a"}}}'), ['s1'])
        self.assertEqual(
            self._names(field, f'{{site: {{id: {{in_list: ["{site_a.pk}", "{site_b.pk}"]}}}}}}'),
            [
                's1',
                's2',
            ],
        )

    def test_multiobject_field_filters(self):
        cot = self.create_custom_object_type(name='Rack Group', slug='rack-group')
        self.create_custom_object_type_field(cot, name='name', label='Name', type='text', primary=True, required=True)
        self.create_custom_object_type_field(
            cot,
            name='sites',
            label='Sites',
            type='multiobject',
            related_object_type=self.get_site_object_type(),
        )
        site_a = self._make_site(name='Site A', slug='site-a')
        site_b = self._make_site(name='Site B', slug='site-b')
        model = cot.get_model()
        model.objects.create(name='g1').sites.set([site_a, site_b])
        model.objects.create(name='g2').sites.set([site_b])
        model.objects.create(name='g3')

        field = 'custom_objects_rack_group_list'
        self.assertEqual(self._names(field, '{sites: {slug: {exact: "site-a"}}}'), ['g1'])
        both_sites = f'{{sites: {{id: {{in_list: ["{site_a.pk}", "{site_b.pk}"]}}}}, DISTINCT: true}}'
        self.assertEqual(self._names(field, both_sites), ['g1', 'g2'])

    def test_custom_object_relationship_filters(self):
        """Fields targeting another custom object type nest that type's own filter."""
        vendor = self.create_custom_object_type(name='Vendor', slug='vendor')
        self.create_custom_object_type_field(
            vendor, name='name', label='Name', type='text', primary=True, required=True
        )
        self.create_custom_object_type_field(vendor, name='tier', label='Tier', type='integer')
        vendor_object_type = ObjectType.objects.get_for_model(vendor.get_model())

        product = self.create_custom_object_type(name='Product', slug='product')
        self.create_custom_object_type_field(
            product, name='name', label='Name', type='text', primary=True, required=True
        )
        self.create_custom_object_type_field(
            product,
            name='vendor',
            label='Vendor',
            type='object',
            related_object_type=vendor_object_type,
        )
        self.create_custom_object_type_field(
            product,
            name='resellers',
            label='Resellers',
            type='multiobject',
            related_object_type=vendor_object_type,
        )
        # Source model first, then the refreshed target, so both share one target class.
        product_model = product.get_model()
        vendor.refresh_from_db()
        vendor_model = vendor.get_model()
        acme = vendor_model.objects.create(name='Acme', tier=1)
        globex = vendor_model.objects.create(name='Globex', tier=2)
        p1 = product_model.objects.create(name='p1', vendor=acme)
        p1.resellers.set([globex])
        p2 = product_model.objects.create(name='p2', vendor=globex)
        p2.resellers.set([acme, globex])

        field = 'custom_objects_product_list'
        self.assertEqual(self._names(field, '{vendor: {tier: {exact: 2}}}'), ['p2'])
        self.assertEqual(self._names(field, f'{{vendor_id: "{acme.pk}"}}'), ['p1'])
        self.assertEqual(self._names(field, '{resellers: {name: {exact: "Acme"}}}'), ['p2'])

        # The multi-object field's own list now takes filters too.
        data = self._gql(
            '{ custom_objects_product_list(filters: {name: {exact: "p2"}}) '
            '{ resellers(filters: {tier: {exact: 1}}) { name } } }'
        )
        self.assertEqual(data[field][0]['resellers'], [{'name': 'Acme'}])

    def test_self_referential_object_field_filter(self):
        """The edge closing a relationship cycle has no nested filter, only "<field>_id"."""
        cot = self.create_custom_object_type(name='Node', slug='node')
        self.create_custom_object_type_field(cot, name='name', label='Name', type='text', primary=True, required=True)
        model = cot.get_model()
        self.create_custom_object_type_field(
            cot,
            name='parent',
            label='Parent',
            type='object',
            related_object_type=ObjectType.objects.get_for_model(model),
        )
        model = cot.get_model()
        root = model.objects.create(name='root')
        model.objects.create(name='child', parent=root)

        self.assertEqual(self._names('custom_objects_node_list', f'{{parent_id: "{root.pk}"}}'), ['child'])

        data = self._gql('{ __type(name: "Table%sModelFilter") { inputFields { name } } }' % cot.pk)  # noqa: UP031
        names = {f['name'] for f in data['__type']['inputFields']}
        self.assertIn('parent_id', names)
        self.assertNotIn('parent', names)

    def test_fields_with_filtering_disabled_have_no_filter(self):
        cot = self.create_custom_object_type(name='Server', slug='server')
        self.create_custom_object_type_field(cot, name='name', label='Name', type='text', primary=True, required=True)
        self.create_custom_object_type_field(
            cot,
            name='serial',
            label='Serial',
            type='text',
            filter_logic='disabled',
        )
        self.create_custom_object_type_field(
            cot,
            name='site',
            label='Site',
            type='object',
            related_object_type=self.get_site_object_type(),
            filter_logic='disabled',
        )
        self.create_custom_object_type_field(
            cot,
            name='sites',
            label='Sites',
            type='multiobject',
            related_object_type=self.get_site_object_type(),
            filter_logic='disabled',
        )
        model = cot.get_model()
        model.objects.create(name='s1', serial='ABC')

        data = self._gql('{ __type(name: "Table%sModelFilter") { inputFields { name } } }' % cot.pk)  # noqa: UP031
        names = {f['name'] for f in data['__type']['inputFields']}
        self.assertIn('name', names)
        for excluded in ('serial', 'site', 'site_id', 'sites'):
            self.assertNotIn(excluded, names)

        response = self.client.post(
            self.url,
            data={'query': '{ custom_objects_server_list(filters: {serial: {exact: "ABC"}}) { name } }'},
            format='json',
            **self.header,
        )
        payload = json.loads(response.content)
        self.assertIn('errors', payload)
        self.assertIn('serial', ' '.join(error['message'] for error in payload['errors']))

    def _make_device(self, name, site):
        manufacturer, _ = Manufacturer.objects.get_or_create(name='Mfr', slug='mfr')
        device_type, _ = DeviceType.objects.get_or_create(manufacturer=manufacturer, model='Model', slug='model')
        role, _ = DeviceRole.objects.get_or_create(name='Role', slug='role')
        return Device.objects.create(name=name, device_type=device_type, role=role, site=site)

    def _polymorphic_type(self, slug='link', **field_kwargs):
        cot = self.create_custom_object_type(name=slug.title(), slug=slug)
        self.create_custom_object_type_field(cot, name='name', label='Name', type='text', primary=True, required=True)
        targets = [self.get_site_object_type(), self.get_device_object_type()]
        self.create_polymorphic_field(cot, targets, name='target', type='object', **field_kwargs)
        self.create_polymorphic_field(cot, targets, name='targets', type='multiobject', **field_kwargs)
        return cot

    def _filter_input_names(self, cot):
        data = self._gql('{ __type(name: "Table%sModelFilter") { inputFields { name } } }' % cot.pk)  # noqa: UP031
        return {f['name'] for f in data['__type']['inputFields']}

    def test_coordinates_field_filters(self):
        cot = self.create_custom_object_type(name='Spot', slug='spot')
        self.create_custom_object_type_field(cot, name='name', label='Name', type='text', primary=True, required=True)
        self.create_custom_object_type_field(cot, name='location', label='Location', type='coordinates')
        model = cot.get_model()
        D = decimal.Decimal
        model.objects.create(name='north', location_latitude=D('60'), location_longitude=D('10'))
        model.objects.create(name='south', location_latitude=D('-30'), location_longitude=D('20'))
        model.objects.create(name='nowhere')

        field = 'custom_objects_spot_list'
        self.assertEqual(self._names(field, '{location_latitude: {gt: "0"}}'), ['north'])
        self.assertEqual(self._names(field, '{location_longitude: {range: {start: "15", end: "25"}}}'), ['south'])
        self.assertEqual(self._names(field, '{location_latitude: {is_null: true}}'), ['nowhere'])

    def test_polymorphic_object_field_filters(self):
        model = self._polymorphic_type().get_model()
        site_a = self._make_site(name='Site A', slug='site-a')
        site_b = self._make_site(name='Site B', slug='site-b')
        device = self._make_device('dev-1', site_a)
        model.objects.create(name='to-site-a', target=site_a)
        model.objects.create(name='to-site-b', target=site_b)
        model.objects.create(name='to-device', target=device)
        model.objects.create(name='to-nothing')

        field = 'custom_objects_link_list'
        self.assertEqual(self._names(field, '{target_dcim_site: {slug: {exact: "site-b"}}}'), ['to-site-b'])
        self.assertEqual(self._names(field, '{target_dcim_device: {name: {exact: "dev-1"}}}'), ['to-device'])
        self.assertEqual(self._names(field, f'{{target_dcim_site_id: "{site_a.pk}"}}'), ['to-site-a'])
        # Each filter is scoped to its own type, even when primary keys coincide.
        self.assertNotIn('to-site-a', self._names(field, f'{{target_dcim_device_id: "{site_a.pk}"}}'))
        self.assertEqual(self._names(field, f'{{target_dcim_device_id: "{device.pk}"}}'), ['to-device'])

    def test_null_polymorphic_filters_are_ignored(self):
        """A null filter value is skipped, as for any strawberry-django filter field."""
        model = self._polymorphic_type().get_model()
        site = self._make_site(name='Site A', slug='site-a')
        model.objects.create(name='to-site', target=site).targets.set([site])
        model.objects.create(name='to-nothing')

        field = 'custom_objects_link_list'
        for filters in (
            '{target_dcim_site_id: null}',
            '{target_dcim_site: null}',
            '{targets_dcim_site: null}',
        ):
            self.assertEqual(self._names(field, filters), ['to-nothing', 'to-site'], filters)

    def test_polymorphic_multiobject_field_filters(self):
        model = self._polymorphic_type().get_model()
        site_a = self._make_site(name='Site A', slug='site-a')
        site_b = self._make_site(name='Site B', slug='site-b')
        device = self._make_device('dev-1', site_a)
        model.objects.create(name='both').targets.set([site_a, site_b, device])
        model.objects.create(name='device-only').targets.set([device])
        model.objects.create(name='none')

        field = 'custom_objects_link_list'
        # Both sites match "both", which must still be returned once.
        self.assertEqual(self._names(field, '{targets_dcim_site: {name: {starts_with: "Site"}}}'), ['both'])
        self.assertEqual(self._names(field, '{targets_dcim_device: {name: {exact: "dev-1"}}}'), ['both', 'device-only'])
        self.assertNotIn('targets_dcim_site_id', self._filter_input_names(model.custom_object_type))

    def test_polymorphic_filters_nested_from_another_type(self):
        link_model = self._polymorphic_type().get_model()
        site_a = self._make_site(name='Site A', slug='site-a')
        site_b = self._make_site(name='Site B', slug='site-b')
        link_a = link_model.objects.create(name='link-a', target=site_a)
        link_a.targets.set([site_a])
        link_b = link_model.objects.create(name='link-b', target=site_b)

        holder = self.create_custom_object_type(name='Holder', slug='holder')
        self.create_custom_object_type_field(
            holder, name='name', label='Name', type='text', primary=True, required=True
        )
        self.create_custom_object_type_field(
            holder,
            name='link',
            label='Link',
            type='object',
            related_object_type=ObjectType.objects.get_for_model(link_model),
        )
        holder_model = holder.get_model()
        link_cot = link_model.custom_object_type
        link_cot.refresh_from_db()
        link_model = link_cot.get_model()
        holder_model.objects.create(name='holds-a', link=link_model.objects.get(pk=link_a.pk))
        holder_model.objects.create(name='holds-b', link=link_model.objects.get(pk=link_b.pk))

        field = 'custom_objects_holder_list'
        self.assertEqual(self._names(field, '{link: {target_dcim_site: {slug: {exact: "site-b"}}}}'), ['holds-b'])
        self.assertEqual(self._names(field, '{link: {targets_dcim_site: {slug: {exact: "site-a"}}}}'), ['holds-a'])

    def test_polymorphic_custom_object_target_and_cycle(self):
        """A polymorphic field allowing its own type gets only "<field>_<type>_id" for that type."""
        cot = self.create_custom_object_type(name='Node', slug='node')
        self.create_custom_object_type_field(cot, name='name', label='Name', type='text', primary=True, required=True)
        self_ot = ObjectType.objects.get_for_model(cot.get_model())
        self.create_polymorphic_field(cot, [self_ot, self.get_site_object_type()], name='parent', type='object')
        model = cot.get_model()
        root = model.objects.create(name='root')
        model.objects.create(name='child', parent=root)

        type_name = f'{APP_LABEL}_{self_ot.model}'
        names = self._filter_input_names(cot)
        self.assertIn(f'parent_{type_name}_id', names)
        self.assertNotIn(f'parent_{type_name}', names)
        self.assertIn('parent_dcim_site', names)
        self.assertEqual(self._names('custom_objects_node_list', f'{{parent_{type_name}_id: "{root.pk}"}}'), ['child'])

    def test_polymorphic_and_coordinates_filters_respect_disabled_filtering(self):
        cot = self._polymorphic_type(filter_logic='disabled')
        self.create_custom_object_type_field(
            cot,
            name='location',
            label='Location',
            type='coordinates',
            filter_logic='disabled',
        )
        cot.get_model()

        names = self._filter_input_names(cot)
        self.assertIn('name', names)
        self.assertFalse(
            {n for n in names if n.startswith(('target', 'location'))},
            'disabled polymorphic/coordinates fields must have no filters',
        )
