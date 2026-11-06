"""
Object fields between custom object types, after one process has used main and a branch.

Main and each branch build their own class for a type, while the app registry holds main's.
An object field must target its own context's class, or querying or assigning it by an
instance of that class fails Django's isinstance checks.
"""

from netbox_custom_objects.models import CustomObjectType, CustomObjectTypeField

from .base import HAS_BRANCHING, BranchingTestBase, _make_request, _provision_branch, _TestBase

if HAS_BRANCHING:
    from netbox_branching.utilities import activate_branch

    from netbox.context_managers import event_tracking


class ObjectFieldBranchContextTestCase(BranchingTestBase, _TestBase):
    def setUp(self):
        super().setUp()
        with event_tracking(self.request):
            self.products = CustomObjectType.objects.create(name='product', slug='products')
            CustomObjectTypeField.objects.create(
                custom_object_type=self.products, name='name', type='text', primary=True
            )
            self.services = CustomObjectType.objects.create(name='service', slug='services')
            CustomObjectTypeField.objects.create(
                custom_object_type=self.services, name='name', type='text', primary=True
            )
            CustomObjectTypeField.objects.create(
                custom_object_type=self.services,
                name='product',
                type='object',
                related_object_type=self.products.object_type,
            )
            service_model, product_model = self._models(self.services, self.products)
            service_model.objects.create(name='svc-1', product=product_model.objects.create(name='prod-1'))
        self.branch = _provision_branch('Context Branch', 'iterative', self.user)
        with activate_branch(self.branch), event_tracking(_make_request(self.user)):
            CustomObjectTypeField.objects.create(custom_object_type=self.products, name='sku', type='text')
        self.addCleanup(CustomObjectType.clear_model_cache)

    def _models(self, *cots):
        return [CustomObjectType.objects.get(pk=cot.pk).get_model() for cot in cots]

    def _check(self, first, second):
        """Load the two types' models in this order, then query and assign the object field by instance."""
        loaded = dict(zip((first, second), self._models(first, second), strict=True))
        service_model, product_model = loaded[self.services], loaded[self.products]
        self.assertIs(service_model._meta.get_field('product').related_model, product_model)
        product = product_model.objects.get(name='prod-1')
        self.assertEqual(list(service_model.objects.filter(product=product).values_list('name', flat=True)), ['svc-1'])
        self.assertIs(service_model(name='new', product=product).product, product)
        return product_model

    def _check_main_and_branch(self, first, second):
        # Main, the branch, then both again: each switch leaves the other context's classes
        # cached in the process.
        for _ in range(2):
            product_model = self._check(first, second)
            self.assertNotIn('sku', [f.name for f in product_model._meta.fields])
            with activate_branch(self.branch):
                product_model = self._check(first, second)
                self.assertIn('sku', [f.name for f in product_model._meta.fields])

    def test_target_type_loaded_first(self):
        self._check_main_and_branch(self.products, self.services)

    def test_source_type_loaded_first(self):
        self._check_main_and_branch(self.services, self.products)
