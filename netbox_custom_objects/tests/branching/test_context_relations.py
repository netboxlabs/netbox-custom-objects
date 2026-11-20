"""
Object fields between custom object types, after one process has used main and a branch.

Main and each branch build their own class for a type, while the app registry holds main's.
An object field must target its own context's class, or querying or assigning it by an
instance of that class fails Django's isinstance checks.
"""

from django.utils import timezone

from netbox_custom_objects.models import CustomObjectType, CustomObjectTypeField

from .base import HAS_BRANCHING, BranchingTestBase, _make_request, _provision_branch, _TestBase

if HAS_BRANCHING:
    from netbox_branching.utilities import activate_branch

    from netbox.context_managers import event_tracking


def _field_names(model):
    return [f.name for f in model._meta.fields]


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
                related_name='services',
            )
            service_model, product_model = self._models(self.services, self.products)
            service_model.objects.create(name='svc-1', product=product_model.objects.create(name='prod-1'))
        self.branch = _provision_branch('Context Branch', 'iterative', self.user)
        with activate_branch(self.branch), event_tracking(_make_request(self.user)):
            CustomObjectTypeField.objects.create(custom_object_type=self.products, name='sku', type='text')
            CustomObjectTypeField.objects.create(custom_object_type=self.services, name='tier', type='text')
        self.addCleanup(CustomObjectType.clear_model_cache)

    def _models(self, *cots):
        return [CustomObjectType.objects.get(pk=cot.pk).get_model() for cot in cots]

    def _check(self, first, second):
        """Load the two types' models in this order, then use the object field both ways."""
        loaded = dict(zip((first, second), self._models(first, second), strict=True))
        service_model, product_model = loaded[self.services], loaded[self.products]
        self.assertIs(service_model._meta.get_field('product').related_model, product_model)
        product = product_model.objects.get(name='prod-1')
        self.assertEqual(list(service_model.objects.filter(product=product).values_list('name', flat=True)), ['svc-1'])
        self.assertIs(service_model(name='new', product=product).product, product)
        # The reverse accessor is on this context's target class and returns this context's sources.
        services = product.services.all()
        self.assertIs(services.model, service_model)
        self.assertEqual([s.name for s in services], ['svc-1'])
        return service_model, product_model

    def _check_main_and_branch(self, first, second):
        # Main, the branch, then both again: each switch leaves the other context's classes
        # cached in the process.
        for _ in range(2):
            service_model, product_model = self._check(first, second)
            self.assertNotIn('sku', _field_names(product_model))
            self.assertNotIn('tier', _field_names(service_model))
            with activate_branch(self.branch):
                service_model, product_model = self._check(first, second)
                self.assertIn('sku', _field_names(product_model))
                self.assertIn('tier', _field_names(service_model))

    def test_target_type_loaded_first(self):
        self._check_main_and_branch(self.products, self.services)

    def test_source_type_loaded_first(self):
        self._check_main_and_branch(self.services, self.products)

    def test_related_name_change_in_branch_moves_accessor(self):
        self._check_main_and_branch(self.products, self.services)
        with activate_branch(self.branch), event_tracking(_make_request(self.user)):
            field = CustomObjectTypeField.objects.get(custom_object_type=self.services, name='product')
            field.related_name = 'offerings'
            field.save()
            service_model, product_model = self._models(self.services, self.products)
            product = product_model.objects.get(name='prod-1')
            self.assertFalse(hasattr(product_model, 'services'))
            self.assertIs(product.offerings.model, service_model)
        # Main's accessor is unchanged.
        service_model, product_model = self._models(self.services, self.products)
        self.assertIs(product_model.objects.get(name='prod-1').services.model, service_model)

    def test_field_delete_in_branch_removes_accessor(self):
        self._check_main_and_branch(self.products, self.services)
        with activate_branch(self.branch), event_tracking(_make_request(self.user)):
            CustomObjectTypeField.objects.get(custom_object_type=self.services, name='product').delete()
            _service_model, product_model = self._models(self.services, self.products)
            self.assertFalse(hasattr(product_model, 'services'))
        service_model, product_model = self._models(self.services, self.products)
        self.assertIs(product_model.objects.get(name='prod-1').services.model, service_model)

    def test_out_of_date_target_class_is_not_used(self):
        with activate_branch(self.branch):
            (stale_product_model,) = self._models(self.products)
            # As if another worker changed the type: its cached class is now out of date.
            CustomObjectType.objects.filter(pk=self.products.pk).update(cache_timestamp=timezone.now())
            CustomObjectType.clear_model_cache(self.services.pk)
            (service_model,) = self._models(self.services)
            self.assertIsNot(service_model._meta.get_field('product').related_model, stale_product_model)
            self._check(self.services, self.products)


class SelfReferentialObjectFieldBranchContextTestCase(BranchingTestBase, _TestBase):
    def setUp(self):
        super().setUp()
        with event_tracking(self.request):
            self.devices = CustomObjectType.objects.create(name='device', slug='devices')
            CustomObjectTypeField.objects.create(
                custom_object_type=self.devices, name='name', type='text', primary=True
            )
            CustomObjectTypeField.objects.create(
                custom_object_type=self.devices,
                name='parent',
                type='object',
                related_object_type=self.devices.object_type,
                related_name='children',
            )
            model = self._model()
            model.objects.create(name='child', parent=model.objects.create(name='parent'))
        self.branch = _provision_branch('Context Branch', 'iterative', self.user)
        with activate_branch(self.branch), event_tracking(_make_request(self.user)):
            CustomObjectTypeField.objects.create(custom_object_type=self.devices, name='rack', type='text')
        self.addCleanup(CustomObjectType.clear_model_cache)

    def _model(self):
        return CustomObjectType.objects.get(pk=self.devices.pk).get_model()

    def _check(self):
        model = self._model()
        self.assertIs(model._meta.get_field('parent').related_model, model)
        parent = model.objects.get(name='parent')
        self.assertEqual(list(model.objects.filter(parent=parent).values_list('name', flat=True)), ['child'])
        self.assertIs(model(name='new', parent=parent).parent, parent)
        children = parent.children.all()
        self.assertIs(children.model, model)
        self.assertEqual([c.name for c in children], ['child'])
        return model

    def test_main_and_branch(self):
        for _ in range(2):
            self.assertNotIn('rack', _field_names(self._check()))
            with activate_branch(self.branch):
                self.assertIn('rack', _field_names(self._check()))
