"""
Tests for all UI views.
"""
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse
from extras.models import CustomFieldChoiceSet
from users.models import ObjectPermission
from utilities.testing import ViewTestCases, create_test_user

from netbox_custom_objects.models import CustomObjectType, CustomObjectTypeField
from .base import CustomObjectsTestCase
from core.models.object_types import ObjectType

try:
    import netbox_branching  # noqa: F401
    _HAS_BRANCHING = True
except ImportError:
    _HAS_BRANCHING = False


class _SkipQueryCountsWhenBranching:
    """Skip query-count assertion when netbox-branching is installed.

    Branching adds per-request queries (branch lookup, schema check, etc.) that
    are not present in the recorded baselines.  The counts are adequately tested
    by the non-branching matrix jobs.
    """

    def test_list_objects_with_permission(self):
        if _HAS_BRANCHING:
            self.skipTest('query-count baselines not valid with netbox-branching installed')
        super().test_list_objects_with_permission()


class CustomObjectTypeViewTestCase(_SkipQueryCountsWhenBranching, CustomObjectsTestCase, ViewTestCases.PrimaryObjectViewTestCase):
    """Test cases for CustomObjectType views."""

    model = CustomObjectType

    @classmethod
    def setUpTestData(cls):
        """Set up test data."""

        # Create test custom object types
        cls.custom_object_type1 = CustomObjectType.objects.create(
            name="TestObject1",
            description="First test custom object type",
            verbose_name_plural="Test Objects 1",
            slug="test-objects-1",
        )

        cls.custom_object_type2 = CustomObjectType.objects.create(
            name="TestObject2",
            description="Second test custom object type",
            verbose_name_plural="Test Objects 2",
            slug="test-objects-2",
        )

        cls.form_data = {
            'name': 'custom_object_type_1',
            'slug': 'custom-object-type-1s',
        }

    def setUp(self):
        """Set up test data."""
        super().setUp()

    def _get_base_url(self):
        """
        Return the base format for a URL for the test's model. Override this to test for a model which belongs
        to a different app (e.g. testing Interfaces within the virtualization app).
        """
        return 'plugins:{}:{}_{{}}'.format(
            self.model._meta.app_label,
            self.model._meta.model_name
        )

    def _get_url(self, action, instance=None):
        """
        Return the URL name for a specific action and optionally a specific instance
        """
        url_format = self._get_base_url()

        # If no instance was provided, assume we don't need a unique identifier
        if instance is None:
            return reverse(url_format.format(action))

        return reverse(url_format.format(action), kwargs={'pk': instance.pk})

    def test_create_object_with_permission(self):
        ...

    def test_create_object_with_constrained_permission(self):
        ...

    def test_bulk_edit_objects_with_permission(self):
        ...

    def test_bulk_edit_objects_with_constrained_permission(self):
        ...

    def test_bulk_update_objects_with_permission(self):
        ...

    def test_bulk_import_objects_with_permission(self):
        ...

    def test_bulk_import_objects_with_constrained_permission(self):
        ...

    def test_bulk_delete_objects_with_permission(self):
        ...

    def test_bulk_delete_objects_with_constrained_permission(self):
        ...


class CustomObjectTypeFieldViewTestCase(CustomObjectsTestCase, ViewTestCases.PrimaryObjectViewTestCase):
    """Test cases for CustomObjectTypeField views."""

    model = CustomObjectTypeField

    @classmethod
    def setUpTestData(cls):
        """Set up test data."""

        # Create a custom object type
        cls.custom_object_type = CustomObjectType.objects.create(
            name="TestObject",
            description="Test custom object type",
            verbose_name_plural="Test Objects",
            slug="test-objects",
        )

        # Create test fields
        cls.field1 = CustomObjectTypeField.objects.create(
            custom_object_type=cls.custom_object_type,
            name="field1",
            label="Field 1",
            type="text",
            description="First test field"
        )

        cls.field2 = CustomObjectTypeField.objects.create(
            custom_object_type=cls.custom_object_type,
            name="field2",
            label="Field 2",
            type="integer",
            description="Second test field"
        )

        cls.form_data = {
            "custom_object_type": cls.custom_object_type.id,
            "name": "field3",
            "type": "text",
            "filter_logic": "loose",
            "ui_visible": "always",
            "ui_editable": "yes",
            "weight": 100,
        }

    def setUp(self):
        """Set up test data."""
        super().setUp()

    def _get_base_url(self):
        """
        Return the base format for a URL for the test's model. Override this to test for a model which belongs
        to a different app (e.g. testing Interfaces within the virtualization app).
        """
        return 'plugins:{}:{}_{{}}'.format(
            self.model._meta.app_label,
            self.model._meta.model_name
        )

    def _get_url(self, action, instance=None):
        """
        Return the URL name for a specific action and optionally a specific instance
        """
        url_format = self._get_base_url()

        # If no instance was provided, assume we don't need a unique identifier
        if instance is None:
            return reverse(url_format.format(action))

        return reverse(url_format.format(action), kwargs={'pk': instance.pk})

    def test_list_objects_anonymous(self):
        ...

    def test_list_objects_with_permission(self):
        ...

    def test_list_objects_without_permission(self):
        ...

    def test_list_objects_with_constrained_permission(self):
        ...

    def test_get_object_with_permission(self):
        ...

    def test_get_object_with_constrained_permission(self):
        ...

    def test_get_object_changelog(self):
        ...

    def test_export_objects(self):
        ...

    def test_export_objects_anonymous(self):
        ...

    def test_bulk_edit_objects_with_permission(self):
        ...

    def test_bulk_edit_objects_without_permission(self):
        ...

    def test_bulk_edit_objects_with_constrained_permission(self):
        ...

    def test_bulk_update_objects_with_permission(self):
        ...

    def test_bulk_import_objects_with_permission(self):
        ...

    def test_bulk_import_objects_without_permission(self):
        ...

    def test_bulk_import_objects_with_constrained_permission(self):
        ...

    def test_bulk_delete_objects_without_permission(self):
        ...

    def test_bulk_delete_objects_with_permission(self):
        ...

    def test_bulk_delete_objects_with_constrained_permission(self):
        ...


class CustomObjectViewTestCase(_SkipQueryCountsWhenBranching, CustomObjectsTestCase, ViewTestCases.PrimaryObjectViewTestCase):
    """Test cases for dynamic CustomObject views."""

    query_count_model_label = 'customobject-simple'

    @classmethod
    def setUpTestData(cls):
        """Set up test data."""

        # Create a custom object type with fields
        cls.custom_object_type = CustomObjectType.objects.create(
            name="TestObject",
            description="Test custom object type",
            verbose_name_plural="Test Objects",
            slug="test-objects",
        )

        # Add a primary field
        CustomObjectTypeField.objects.create(
            custom_object_type=cls.custom_object_type,
            name="name",
            label="Name",
            type="text",
            primary=True,
            required=True
        )

        # Add additional fields
        CustomObjectTypeField.objects.create(
            custom_object_type=cls.custom_object_type,
            name="description",
            label="Description",
            type="text",
            required=False
        )

        CustomObjectTypeField.objects.create(
            custom_object_type=cls.custom_object_type,
            name="count",
            label="Count",
            type="integer",
            validation_minimum=0,
            validation_maximum=100
        )

        # Get the dynamic model
        cls.model = cls.custom_object_type.get_model()

        # Create test instances
        cls.instance1 = cls.model.objects.create(
            name="Test Instance 1",
            description="First test instance",
            count=10
        )

        cls.instance2 = cls.model.objects.create(
            name="Test Instance 2",
            description="Second test instance",
            count=20
        )

    def setUp(self):
        """Set up test data."""
        super().setUp()

    def _get_base_url(self):
        """
        Return the base format for a URL for the test's model. Override this to test for a model which belongs
        to a different app (e.g. testing Interfaces within the virtualization app).
        """
        return 'plugins:{}:customobject_{{}}'.format(self.model._meta.app_label)

    def _get_url(self, action, instance=None):
        """
        Return the URL name for a specific action and optionally a specific instance
        """
        url_format = self._get_base_url()

        custom_object_type = self.model.custom_object_type.slug

        # If no instance was provided, assume we don't need a unique identifier
        if instance is None:
            return reverse(url_format.format(action), kwargs={'custom_object_type': custom_object_type})

        return reverse(url_format.format(action), kwargs={'pk': instance.pk, 'custom_object_type': custom_object_type})

    def test_get_object_with_constrained_permission(self):
        ...

    def test_get_object_changelog(self):
        """Regression #500: changelog tab must return 200, not 500 from deprecated user kwarg."""
        url = self._get_url('changelog', self.instance1)
        self.assertHttpStatus(self.client.get(url), 200)

    def test_create_object_with_permission(self):
        ...

    def test_create_object_with_constrained_permission(self):
        ...

    def test_edit_object_with_permission(self):
        ...

    def test_edit_object_with_constrained_permission(self):
        ...

    def test_bulk_edit_objects_with_permission(self):
        ...

    def test_bulk_edit_objects_with_constrained_permission(self):
        ...

    def test_bulk_update_objects_with_permission(self):
        ...

    def test_bulk_import_objects_with_permission(self):
        ...

    def test_bulk_import_objects_without_permission(self):
        ...

    def test_bulk_import_objects_with_constrained_permission(self):
        ...

    def test_delete_object_with_permission(self):
        ...

    def test_delete_object_with_constrained_permission(self):
        ...

    def test_bulk_delete_objects_with_permission(self):
        ...

    def test_bulk_delete_objects_with_constrained_permission(self):
        ...

    def test_bulk_edit_select_all_respects_full_queryset(self):
        """Regression #380: 'select all matching query' must edit all objects, not just the current page.

        The fix sets self.filterset on BulkEditView so that the _all flag causes the view to
        build pk_list from the full queryset. We verify this by submitting a description update
        with _all set: before the fix, pk_list is empty so zero objects are updated (200 returned,
        no redirect); after the fix, all objects are updated and the view redirects (302).
        """
        model = self.model
        content_type = ContentType.objects.get_for_model(model)
        obj_perm = ObjectPermission(name='bulk-edit-all', actions=['view', 'change'])
        obj_perm.save()
        obj_perm.users.add(self.user)
        obj_perm.object_types.add(content_type)

        extra = [model(name=f"bulk-{i}", count=i) for i in range(60)]
        model.objects.bulk_create(extra)
        total = model.objects.count()
        self.assertGreater(total, 50)

        bulk_edit_url = self._get_url('bulk_edit')
        response = self.client.post(bulk_edit_url, data={
            '_all': 'on',
            '_apply': 'Apply',
            'pk': [],
            'description': 'updated-by-select-all',
        })
        # Successful bulk edit redirects; without the fix pk_list is empty so the view
        # returns a 200 (warning: no objects selected) instead.
        self.assertHttpStatus(response, 302)
        self.assertEqual(model.objects.filter(description='updated-by-select-all').count(), total)

    def test_bulk_delete_select_all_respects_full_queryset(self):
        """Regression #380: 'select all matching query' must delete all objects, not just the current page.

        The fix sets self.filterset on BulkDeleteView so that the _all flag causes the view
        to build pk_list from the full queryset rather than from the submitted pk form field.
        We verify this by passing only 2 PKs in the form's pk field while _all is set:
        before the fix only those 2 would be deleted; after the fix all objects are deleted.
        """
        model = self.model
        content_type = ContentType.objects.get_for_model(model)
        obj_perm = ObjectPermission(name='bulk-delete-all', actions=['view', 'delete'])
        obj_perm.save()
        obj_perm.users.add(self.user)
        obj_perm.object_types.add(content_type)

        extra = [model(name=f"del-{i}", count=i) for i in range(60)]
        model.objects.bulk_create(extra)
        total = model.objects.count()
        self.assertGreater(total, 50)

        # Pass only 2 PKs in the form field — with _all+filterset, the view should
        # delete all objects regardless.
        two_pks = list(model.objects.values_list('pk', flat=True)[:2])
        bulk_delete_url = self._get_url('bulk_delete')
        response = self.client.post(bulk_delete_url, data={
            '_all': 'on',
            '_confirm': '1',
            'pk': two_pks,
            'confirm': 'on',
        })
        self.assertNotIn(response.status_code, [403, 500])
        # All objects deleted (not just the 2 submitted PKs)
        self.assertEqual(model.objects.count(), 0)

    def test_add_permission_is_sufficient_to_access_add_url(self):
        """Regression #396: add-only permission must grant access to the add URL, not require change."""
        model = self.model
        content_type = ContentType.objects.get_for_model(model)
        obj_perm = ObjectPermission(name='add-only', actions=['add'])
        obj_perm.save()
        obj_perm.users.add(self.user)
        obj_perm.object_types.add(content_type)

        add_url = self._get_url('add')
        self.assertHttpStatus(self.client.get(add_url), 200)

        # User with only 'add' must not be able to edit existing objects
        edit_url = self._get_url('edit', self.instance1)
        self.assertHttpStatus(self.client.get(edit_url), 403)

        # Symmetrical: change-only permission must not grant access to the add URL
        obj_perm.actions = ['change']
        obj_perm.save()
        self.assertHttpStatus(self.client.get(add_url), 403)
        self.assertHttpStatus(self.client.get(edit_url), 200)


class ComplexCustomObjectViewTestCase(_SkipQueryCountsWhenBranching, CustomObjectsTestCase, ViewTestCases.PrimaryObjectViewTestCase):
    """Test cases for complex custom objects with various field types."""

    query_count_model_label = 'customobject-complex'

    @classmethod
    def setUpTestData(cls):
        """Set up test data."""

        # Create choice set
        cls.choice_set = CustomFieldChoiceSet.objects.create(
            name="Test Choices",
            extra_choices=[
                ["choice1", "Choice 1"],
                ["choice2", "Choice 2"],
                ["choice3", "Choice 3"],
            ]
        )

        # Create custom object type with complex fields
        cls.custom_object_type = CustomObjectType.objects.create(
            name="ComplexObject",
            description="Complex test custom object type",
            verbose_name_plural="Complex Objects",
            slug="complex-objects",
        )

        # Add primary field
        CustomObjectTypeField.objects.create(
            custom_object_type=cls.custom_object_type,
            name="name",
            label="Name",
            type="text",
            primary=True,
            required=True
        )

        # Add various field types
        CustomObjectTypeField.objects.create(
            custom_object_type=cls.custom_object_type,
            name="description",
            label="Description",
            type="longtext",
            required=False
        )

        CustomObjectTypeField.objects.create(
            custom_object_type=cls.custom_object_type,
            name="count",
            label="Count",
            type="integer",
            validation_minimum=0,
            validation_maximum=100,
            default=10
        )

        CustomObjectTypeField.objects.create(
            custom_object_type=cls.custom_object_type,
            name="price",
            label="Price",
            type="decimal",
            validation_minimum=0,
            validation_maximum=1000,
            default=50.00
        )

        CustomObjectTypeField.objects.create(
            custom_object_type=cls.custom_object_type,
            name="active",
            label="Active",
            type="boolean",
            default=True
        )

        CustomObjectTypeField.objects.create(
            custom_object_type=cls.custom_object_type,
            name="status",
            label="Status",
            type="select",
            choice_set=cls.choice_set,
            default="choice1"
        )

        CustomObjectTypeField.objects.create(
            custom_object_type=cls.custom_object_type,
            name="multi_tags",
            label="Tags",
            type="multiselect",
            choice_set=cls.choice_set,
            default=["choice1", "choice2"]
        )

        CustomObjectTypeField.objects.create(
            custom_object_type=cls.custom_object_type,
            name="website",
            label="Website",
            type="url",
            validation_regex="^https://.*"
        )

        CustomObjectTypeField.objects.create(
            custom_object_type=cls.custom_object_type,
            name="metadata",
            label="Metadata",
            type="json",
            default={"key": "value"}
        )

        # Get the dynamic model
        cls.model = cls.custom_object_type.get_model()

        # Create test instances
        cls.instance_1 = cls.model.objects.create(
            name="Complex Test Instance 1",
            description="A complex test instance with various field types",
            count=25,
            price=75.50,
            active=False,
            status="choice2",
            multi_tags=["choice2", "choice3"],
            website="https://example.com",
            metadata={"complex": "data", "number": 42}
        )

        cls.instance_2 = cls.model.objects.create(
            name="Complex Test Instance 2",
            description="A complex test instance with various field types",
            count=20,
            price=25.50,
            active=False,
            status="choice3",
            multi_tags=["choice1", "choice3"],
            website="https://example.com",
            metadata={"complex": "data", "number": 42}
        )

    def setUp(self):
        """Set up test data."""
        super().setUp()

    def _get_base_url(self):
        """
        Return the base format for a URL for the test's model. Override this to test for a model which belongs
        to a different app (e.g. testing Interfaces within the virtualization app).
        """
        return 'plugins:{}:customobject_{{}}'.format(self.model._meta.app_label)

    def _get_url(self, action, instance=None):
        """
        Return the URL name for a specific action and optionally a specific instance
        """
        url_format = self._get_base_url()

        custom_object_type = self.model.custom_object_type.slug

        # If no instance was provided, assume we don't need a unique identifier
        if instance is None:
            return reverse(url_format.format(action), kwargs={'custom_object_type': custom_object_type})

        return reverse(url_format.format(action), kwargs={'pk': instance.pk, 'custom_object_type': custom_object_type})

    def test_get_object_with_constrained_permission(self):
        ...

    def test_get_object_changelog(self):
        """Regression #500: changelog tab must return 200, not 500 from deprecated user kwarg."""
        url = self._get_url('changelog', self.instance_1)
        self.assertHttpStatus(self.client.get(url), 200)

    def test_create_object_with_permission(self):
        ...

    def test_create_object_with_constrained_permission(self):
        ...

    def test_edit_object_with_permission(self):
        ...

    def test_edit_object_with_constrained_permission(self):
        ...

    def test_bulk_edit_objects_with_permission(self):
        ...

    def test_bulk_edit_objects_with_constrained_permission(self):
        ...

    def test_bulk_update_objects_with_permission(self):
        ...

    def test_bulk_import_objects_with_permission(self):
        ...

    def test_bulk_import_objects_without_permission(self):
        ...

    def test_bulk_import_objects_with_constrained_permission(self):
        ...

    def test_delete_object_with_permission(self):
        ...

    def test_delete_object_with_constrained_permission(self):
        ...

    def test_bulk_delete_objects_with_permission(self):
        ...

    def test_bulk_delete_objects_with_constrained_permission(self):
        ...


class SelectFieldColorDetailViewTestCase(CustomObjectsTestCase, TestCase):
    """Regression tests for #529: selection field colors render correctly in the detail view."""

    def setUp(self):
        super().setUp()
        self.colored_choice_set = CustomFieldChoiceSet.objects.create(
            name='Colored Status Choices',
            extra_choices=[['active', 'Active'], ['planned', 'Planned'], ['retired', 'Retired']],
            choice_colors={'active': 'green', 'planned': 'blue', 'retired': 'red'},
        )
        self.cot = CustomObjectType.objects.create(
            name='ColorTestObject',
            verbose_name_plural='Color Test Objects',
            slug='color-test-objects',
        )
        CustomObjectTypeField.objects.create(
            custom_object_type=self.cot,
            name='name', label='Name', type='text', primary=True, required=True,
        )
        CustomObjectTypeField.objects.create(
            custom_object_type=self.cot,
            name='status', label='Status', type='select',
            choice_set=self.colored_choice_set,
        )
        CustomObjectTypeField.objects.create(
            custom_object_type=self.cot,
            name='phases', label='Phases', type='multiselect',
            choice_set=self.colored_choice_set,
        )
        self.model = self.cot.get_model()
        self.instance = self.model.objects.create(
            name='Test Instance', status='active', phases=['planned', 'retired'],
        )
        content_type = ContentType.objects.get_for_model(self.model)
        perm = ObjectPermission(name='color-test-view', actions=['view'])
        perm.save()
        perm.users.add(self.user)
        perm.object_types.add(content_type)

    def _detail_url(self, instance=None):
        return reverse(
            'plugins:netbox_custom_objects:customobject',
            kwargs={'pk': (instance or self.instance).pk, 'custom_object_type': self.cot.slug},
        )

    def test_detail_view_renders_select_color_badge(self):
        """Regression #529: select field with a color renders a colored badge in the detail view."""
        response = self.client.get(self._detail_url())
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('text-bg-green', content)
        self.assertIn('Active', content)

    def test_detail_view_renders_multiselect_color_badges(self):
        """Regression #529: multiselect field with colors renders colored badges in the detail view."""
        response = self.client.get(self._detail_url())
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('text-bg-blue', content)
        self.assertIn('text-bg-red', content)
        self.assertIn('Planned', content)
        self.assertIn('Retired', content)

    def test_detail_view_renders_label_for_uncolored_select_field(self):
        """A select field with no colors configured renders the human-readable label without error."""
        uncolored_choice_set = CustomFieldChoiceSet.objects.create(
            name='Uncolored Choices',
            extra_choices=[['yes', 'Yes'], ['no', 'No']],
        )
        CustomObjectTypeField.objects.create(
            custom_object_type=self.cot,
            name='flag', label='Flag', type='select', choice_set=uncolored_choice_set,
        )
        model = self.cot.get_model(no_cache=True)
        instance = model.objects.create(name='Uncolored Test', status='active', flag='yes')
        response = self.client.get(self._detail_url(instance))
        self.assertEqual(response.status_code, 200)
        # The human-readable label "Yes" must appear, not the raw stored value "yes"
        self.assertIn('Yes', response.content.decode())


class ObjectFieldViewTestCase(_SkipQueryCountsWhenBranching, CustomObjectsTestCase, ViewTestCases.PrimaryObjectViewTestCase):
    """Test cases for custom objects with object and multi-object fields."""

    query_count_model_label = 'customobject-objectfields'

    @classmethod
    def setUpTestData(cls):
        """Set up test data."""

        # Get content types for object fields
        cls.device_content_type = ObjectType.objects.get(app_label='dcim', model='device')
        cls.site_content_type = ObjectType.objects.get(app_label='dcim', model='site')

        # Create custom object type with object fields
        cls.custom_object_type = CustomObjectType.objects.create(
            name="ObjectTestObject",
            description="Test custom object type with object fields",
            verbose_name_plural="Object Test Objects",
            slug="object-test-objects",
        )

        # Add primary field
        CustomObjectTypeField.objects.create(
            custom_object_type=cls.custom_object_type,
            name="name",
            label="Name",
            type="text",
            primary=True,
            required=True
        )

        # Add object field
        CustomObjectTypeField.objects.create(
            custom_object_type=cls.custom_object_type,
            name="device",
            label="Device",
            type="object",
            related_object_type=cls.device_content_type
        )

        # Add multi-object field
        CustomObjectTypeField.objects.create(
            custom_object_type=cls.custom_object_type,
            name="sites",
            label="Sites",
            type="multiobject",
            related_object_type=cls.site_content_type
        )

        # Get the dynamic model
        cls.model = cls.custom_object_type.get_model()

        # Create test instances (if DCIM models are available)
        try:
            from dcim.models import Device, Site, DeviceRole, DeviceType, Manufacturer

            # Create test site
            cls.site = Site.objects.create(name="Test Site", slug="test-site")

            # Create test device
            manufacturer = Manufacturer.objects.create(name="Test Manufacturer", slug="test-manufacturer")
            device_type = DeviceType.objects.create(
                manufacturer=manufacturer,
                model="Test Model",
                slug="test-model"
            )
            device_role = DeviceRole.objects.create(name="Test Role", slug="test-role")
            cls.device = Device.objects.create(
                name="Test Device",
                site=cls.site,
                device_type=device_type,
                role=device_role
            )

            # Create custom object instances
            cls.instance_1 = cls.model.objects.create(
                name="Object Test Instance 1",
                device=cls.device
            )
            cls.instance_1.sites.add(cls.site)

            cls.instance_2 = cls.model.objects.create(
                name="Object Test Instance 2",
                device=cls.device
            )

        except ImportError:
            # Skip if DCIM models are not available
            cls.site = None
            cls.device = None
            cls.instance_1 = None

    def setUp(self):
        """Set up test data."""
        super().setUp()

    def _get_base_url(self):
        """
        Return the base format for a URL for the test's model. Override this to test for a model which belongs
        to a different app (e.g. testing Interfaces within the virtualization app).
        """
        return 'plugins:{}:customobject_{{}}'.format(self.model._meta.app_label)

    def _get_url(self, action, instance=None):
        """
        Return the URL name for a specific action and optionally a specific instance
        """
        url_format = self._get_base_url()

        custom_object_type = self.model.custom_object_type.slug

        # If no instance was provided, assume we don't need a unique identifier
        if instance is None:
            return reverse(url_format.format(action), kwargs={'custom_object_type': custom_object_type})

        return reverse(url_format.format(action), kwargs={'pk': instance.pk, 'custom_object_type': custom_object_type})

    def test_get_object_with_constrained_permission(self):
        ...

    def test_get_object_changelog(self):
        """Regression #500: changelog tab must return 200, not 500 from deprecated user kwarg."""
        if self.instance_1 is None:
            self.skipTest("DCIM models not available")
        url = self._get_url('changelog', self.instance_1)
        self.assertHttpStatus(self.client.get(url), 200)

    def test_create_object_with_permission(self):
        ...

    def test_create_object_with_constrained_permission(self):
        ...

    def test_edit_object_with_permission(self):
        ...

    def test_edit_object_with_constrained_permission(self):
        ...

    def test_bulk_edit_objects_with_permission(self):
        ...

    def test_bulk_edit_objects_with_constrained_permission(self):
        ...

    def test_bulk_update_objects_with_permission(self):
        ...

    def test_bulk_import_objects_with_permission(self):
        ...

    def test_bulk_import_objects_without_permission(self):
        ...

    def test_bulk_import_objects_with_constrained_permission(self):
        ...

    def test_delete_object_with_permission(self):
        ...

    def test_delete_object_with_constrained_permission(self):
        ...

    def test_bulk_delete_objects_with_permission(self):
        ...

    def test_bulk_delete_objects_with_constrained_permission(self):
        ...

    def test_delete_confirmation_page_with_populated_multiobject_field(self):
        """Regression #477: delete confirmation page returns 200 and omits through-table model names."""
        if self.instance_1 is None:
            self.skipTest("DCIM models not available")
        # Dynamic models have unpredictable permission names (table{id}model), so grant
        # superuser access rather than using add_permissions().
        self.user.is_superuser = True
        self.user.save()
        url = self._get_url('delete', self.instance_1)
        response = self.client.get(url)
        self.assertHttpStatus(response, 200)
        # M2M through-table rows must not appear on the confirmation page —
        # they are implementation details, not user-facing business objects.
        self.assertNotIn(b'through_', response.content)


class ObjectSelectorViewTestCase(TestCase):
    """
    Regression tests for issue #441: the HTMX object-selector endpoint must not
    return a 500 when the requested model is a dynamically-generated custom object
    type model.

    Core's ObjectSelectorView._get_form_class() and _get_filterset_class() use
    import_string() to find classes by convention, which fails for dynamic models.
    The plugin patches those methods in ready(); these tests verify the patch works.
    """

    @classmethod
    def setUpTestData(cls):
        cls.custom_object_type = CustomObjectType.objects.create(
            name="SelectorTestObject",
            description="Custom object type for selector tests",
            verbose_name_plural="Selector Test Objects",
            slug="selector-test-objects",
        )
        CustomObjectTypeField.objects.create(
            custom_object_type=cls.custom_object_type,
            name="name",
            label="Name",
            type="text",
            primary=True,
            required=True,
        )
        cls.model = cls.custom_object_type.get_model()
        cls.model.objects.create(name="Alpha")
        cls.model.objects.create(name="Beta")

    def setUp(self):
        self.user = create_test_user('selector_testuser')
        self.client.force_login(self.user)

    def tearDown(self):
        CustomObjectType.clear_model_cache()

    def _model_label(self):
        ct = ContentType.objects.get_for_model(self.model)
        return f'{ct.app_label}.{ct.model}'

    def test_object_selector_form_load(self):
        """GET /htmx/object-selector/ returns 200 for a custom object model (not 500)."""
        url = reverse('htmx_object_selector')
        response = self.client.get(url, {'_model': self._model_label(), 'target': 'id_field'})
        self.assertEqual(response.status_code, 200)

    def test_object_selector_search(self):
        """GET /htmx/object-selector/?_search returns 200 and renders results."""
        url = reverse('htmx_object_selector')
        response = self.client.get(url, {
            '_model': self._model_label(),
            'target': 'id_field',
            '_search': '1',
            'q': 'Alpha',
        })
        self.assertEqual(response.status_code, 200)


class CoordinatesFieldViewTest(CustomObjectsTestCase, TestCase):
    """UI form behaviour for the coordinates field type."""

    @classmethod
    def setUpTestData(cls):
        cls.cot = CustomObjectType.objects.create(
            name="GeoView",
            verbose_name_plural="Geo Views",
            slug="geo-views",
        )
        CustomObjectTypeField.objects.create(
            custom_object_type=cls.cot, name="name", type="text", primary=True, required=True
        )
        CustomObjectTypeField.objects.create(
            custom_object_type=cls.cot, name="location", type="coordinates"
        )
        cls.model = cls.cot.get_model()

    def setUp(self):
        super().setUp()
        perm = ObjectPermission(
            name="geo view all", actions=["view", "add", "change", "delete"]
        )
        perm.save()
        perm.users.add(self.user)
        perm.object_types.add(ObjectType.objects.get_for_model(self.model))

    def _add_url(self):
        return reverse(
            "plugins:netbox_custom_objects:customobject_add",
            kwargs={"custom_object_type": self.cot.slug},
        )

    def test_add_form_renders_two_inputs(self):
        response = self.client.get(self._add_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "location_latitude")
        self.assertContains(response, "location_longitude")

    def test_create_valid_coordinates(self):
        from decimal import Decimal
        data = {
            "name": "Box",
            "location_latitude": "40.712800",
            "location_longitude": "-74.006000",
        }
        response = self.client.post(self._add_url(), data)
        self.assertEqual(response.status_code, 302, getattr(response, "content", b""))
        obj = self.model.objects.get(name="Box")
        self.assertEqual(obj.location_latitude, Decimal("40.712800"))
        self.assertEqual(obj.location_longitude, Decimal("-74.006000"))

    def test_create_half_populated_pair_rejected(self):
        data = {"name": "Bad", "location_latitude": "40.712800"}
        response = self.client.post(self._add_url(), data)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.model.objects.filter(name="Bad").exists())

    def _bulk_edit_url(self):
        return reverse(
            "plugins:netbox_custom_objects:customobject_bulk_edit",
            kwargs={"custom_object_type": self.cot.slug},
        )

    def test_bulk_edit_half_populated_pair_rejected(self):
        """Bulk-editing only one of latitude/longitude is rejected."""
        from decimal import Decimal
        obj = self.model.objects.create(
            name="Existing",
            location_latitude=Decimal("40.712800"),
            location_longitude=Decimal("-74.006000"),
        )
        data = {
            "pk": [obj.pk],
            "_apply": "Apply",
            "location_latitude": "10.000000",
        }
        response = self.client.post(self._bulk_edit_url(), data)
        self.assertEqual(response.status_code, 200)
        obj.refresh_from_db()
        # Values are unchanged because the form failed validation.
        self.assertEqual(obj.location_latitude, Decimal("40.712800"))
        self.assertEqual(obj.location_longitude, Decimal("-74.006000"))


class QuickAddViewTestCase(CustomObjectsTestCase, TestCase):
    """
    Tests for the quick-add flow in CustomObjectEditView.

    Covers GET (modal renders), POST success (object created, quick_add_created.html
    returned), and POST validation failure (errors re-rendered in our custom template).
    """

    def setUp(self):
        super().setUp()
        self.user.is_superuser = True
        self.user.save()

        # Target COT: objects of this type will be quick-added.
        self.target_cot = self.create_simple_custom_object_type(
            name='Target', slug='target',
        )
        target_ot = ObjectType.objects.get(
            app_label='netbox_custom_objects',
            model=self.target_cot.get_table_model_name(self.target_cot.id).lower(),
        )

        # Source COT: has an object field pointing at Target.
        self.source_cot = self.create_custom_object_type(name='Source', slug='source')
        self.create_custom_object_type_field(
            self.source_cot, name='name', label='Name', type='text',
            primary=True, required=True,
        )
        self.create_custom_object_type_field(
            self.source_cot, name='ref', label='Ref', type='object',
            related_object_type=target_ot,
        )

        self.add_url = reverse(
            'plugins:netbox_custom_objects:customobject_add',
            kwargs={'custom_object_type': self.target_cot.slug},
        )

    def test_quick_add_get_returns_200(self):
        """GET ?_quickadd=True renders the custom quick-add modal without errors."""
        response = self.client.get(
            self.add_url,
            {'_quickadd': 'True', 'target': 'id_ref'},
        )
        self.assertEqual(response.status_code, 200)
        # The custom template (not the core one) is used.
        self.assertContains(response, 'hx-post=')
        self.assertContains(response, f'/plugins/custom-objects/{self.target_cot.slug}/add/')

    def test_quick_add_post_success_creates_object(self):
        """POST with _quickadd in POST data creates the object and returns quick_add_created template."""
        model = self.target_cot.get_model()
        count_before = model.objects.count()

        response = self.client.post(
            f'{self.add_url}?_quickadd=True&target=id_ref',
            data={'quickadd-name': 'quick-created', '_quickadd': ''},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(model.objects.count(), count_before + 1)
        # The success template contains the object PK for JS auto-selection.
        self.assertContains(response, 'quick-add-object')
        self.assertTrue(model.objects.filter(name='quick-created').exists())

    def test_quick_add_post_validation_failure_rerenders(self):
        """POST with missing required field re-renders the quick-add form with errors."""
        response = self.client.post(
            f'{self.add_url}?_quickadd=True&target=id_ref',
            # name is required but omitted
            data={'_quickadd': ''},
        )
        self.assertEqual(response.status_code, 200)
        # Error re-render uses our custom template, not a redirect.
        self.assertContains(response, 'hx-post=')
        # No new object created.
        model = self.target_cot.get_model()
        self.assertFalse(model.objects.exists())


class CustomObjectConfigContextViewTestCase(CustomObjectsTestCase, TestCase):
    """Config context tab on custom object instances (#98)."""

    def _config_context_url(self, cot, instance):
        return reverse(
            'plugins:netbox_custom_objects:customobject_configcontext',
            kwargs={'custom_object_type': cot.slug, 'pk': instance.pk},
        )

    def _grant_view(self, model):
        perm = ObjectPermission(name=f'view-{model._meta.model_name}', actions=['view'])
        perm.save()
        perm.users.add(self.user)
        perm.object_types.add(ObjectType.objects.get_for_model(model))
        return perm

    def test_tab_returns_200_and_shows_local_data_when_enabled(self):
        cot = self.create_custom_object_type(
            name='cc_view', slug='cc-view', config_context_enabled=True,
        )
        self.create_custom_object_type_field(
            cot, name='name', label='Name', type='text', primary=True, required=True,
        )
        model = cot.get_model()
        obj = model.objects.create(name='obj-1', local_context_data={'ntp_servers': ['10.0.0.1']})
        self._grant_view(model)

        response = self.client.get(self._config_context_url(cot, obj))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'ntp_servers')
        self.assertContains(response, 'Config Context')

    def test_tab_lists_aggregated_source_contexts(self):
        """A `site` field surfaces the referenced Site's ConfigContext in the tab."""
        from dcim.models import Site
        from extras.models import ConfigContext

        site = Site.objects.create(name='Tab Site', slug='tab-site')
        cc = ConfigContext.objects.create(name='tab-site-ctx', weight=1000, is_active=True, data={'a': 1})
        cc.sites.add(site)

        cot = self.create_custom_object_type(
            name='cc_src', slug='cc-src', config_context_enabled=True,
        )
        self.create_custom_object_type_field(
            cot, name='name', label='Name', type='text', primary=True, required=True,
        )
        self.create_custom_object_type_field(
            cot, name='site', label='Site', type='object',
            related_object_type=self.get_site_object_type(),
        )
        model = cot.get_model()
        obj = model.objects.create(name='o1', site=site)
        self._grant_view(model)
        # The Source Contexts panel restricts to ConfigContexts the user may view
        # (matching NetBox's ObjectConfigContextView), so grant that too.
        self._grant_view(ConfigContext)

        response = self.client.get(self._config_context_url(cot, obj))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Source Contexts')
        self.assertContains(response, 'tab-site-ctx')

    def test_tab_403_without_view_permission(self):
        """The tab must honour object-level RBAC, not leak local_context_data."""
        cot = self.create_custom_object_type(
            name='cc_rbac', slug='cc-rbac', config_context_enabled=True,
        )
        self.create_custom_object_type_field(
            cot, name='name', label='Name', type='text', primary=True, required=True,
        )
        model = cot.get_model()
        obj = model.objects.create(name='secret', local_context_data={'k': 'v'})

        # No ObjectPermission granted → restricted queryset yields nothing → 404.
        response = self.client.get(self._config_context_url(cot, obj))
        self.assertIn(response.status_code, (403, 404))
        self.assertNotContains(response, 'secret', status_code=response.status_code)

    def test_tab_link_present_only_when_enabled(self):
        """The detail page shows the Config Context tab only for enabled types."""
        enabled = self.create_custom_object_type(
            name='cc_on', slug='cc-on', config_context_enabled=True,
        )
        self.create_custom_object_type_field(
            enabled, name='name', label='Name', type='text', primary=True, required=True,
        )
        disabled = self.create_custom_object_type(name='cc_no', slug='cc-no')
        self.create_custom_object_type_field(
            disabled, name='name', label='Name', type='text', primary=True, required=True,
        )
        on_model = enabled.get_model()
        off_model = disabled.get_model()
        on_obj = on_model.objects.create(name='on')
        off_obj = off_model.objects.create(name='off')

        # The detail view (generic.ObjectView) enforces object-level view perms.
        perm = ObjectPermission(name='view-cc-detail', actions=['view'])
        perm.save()
        perm.users.add(self.user)
        perm.object_types.add(ObjectType.objects.get_for_model(on_model))
        perm.object_types.add(ObjectType.objects.get_for_model(off_model))

        on_detail = self.client.get(on_obj.get_absolute_url())
        self.assertContains(on_detail, self._config_context_url(enabled, on_obj))

        off_detail = self.client.get(off_obj.get_absolute_url())
        self.assertNotContains(off_detail, 'config-context')

    def test_tab_404_when_disabled(self):
        cot = self.create_custom_object_type(name='cc_off_view', slug='cc-off-view')
        self.create_custom_object_type_field(
            cot, name='name', label='Name', type='text', primary=True, required=True,
        )
        obj = cot.get_model().objects.create(name='x')

        response = self.client.get(self._config_context_url(cot, obj))
        self.assertEqual(response.status_code, 404)
