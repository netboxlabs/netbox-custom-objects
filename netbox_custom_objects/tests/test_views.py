"""
Tests for all UI views.
"""
import uuid
from datetime import date
from decimal import Decimal
from unittest import skipUnless

from core.choices import ObjectChangeActionChoices
from core.models import Job, ObjectChange
from dcim.models import Site
from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from extras.models import CustomFieldChoiceSet, JournalEntry
from netbox.context import current_request, events_queue, query_cache
from netbox.jobs import AsyncViewJob
from users.models import ObjectPermission
from utilities.request import copy_safe_request
from utilities.testing import ViewTestCases, create_test_user, post_data

from netbox_custom_objects import views
from netbox_custom_objects.choices import ObjectFieldOnDeleteChoices
from netbox_custom_objects.models import CustomObjectType, CustomObjectTypeConstraint, CustomObjectTypeField
from netbox_custom_objects.utilities import is_branching_enabled
from .base import CustomObjectsTestCase
from core.models.object_types import ObjectType

_HAS_BRANCHING = is_branching_enabled()


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


class CustomObjectTypeViewTestCase(
    _SkipQueryCountsWhenBranching, CustomObjectsTestCase, ViewTestCases.PrimaryObjectViewTestCase
):
    """Test cases for CustomObjectType views."""

    model = CustomObjectType

    @classmethod
    def setUpTestData(cls):
        """Set up test data."""

        # Create test custom object types
        cls.custom_object_type1 = CustomObjectType.objects.create(
            name="test_object_1",
            description="First test custom object type",
            verbose_name_plural="Test Objects 1",
            slug="test-objects-1",
        )

        cls.custom_object_type2 = CustomObjectType.objects.create(
            name="test_object_2",
            description="Second test custom object type",
            verbose_name_plural="Test Objects 2",
            slug="test-objects-2",
        )

        cls.custom_object_type3 = CustomObjectType.objects.create(
            name="test_object_3",
            verbose_name_plural="Test Objects 3",
            slug="test-objects-3",
        )

        cls.form_data = {
            'name': 'custom_object_type_1',
            'slug': 'custom-object-type-1s',
        }
        cls.csv_data = (
            "name,slug,description",
            "imported_1,imported-1s,First import",
            "imported_2,imported-2s,Second import",
            "imported_3,imported-3s,Third import",
        )
        cls.csv_update_data = (
            "id,description",
            f"{cls.custom_object_type1.pk},Updated 1",
            f"{cls.custom_object_type2.pk},Updated 2",
            f"{cls.custom_object_type3.pk},Updated 3",
        )
        cls.bulk_edit_data = {"description": "Bulk edited"}

    def setUp(self):
        """Set up test data."""
        super().setUp()

    def test_create_object_with_permission(self):
        """
        As the generic test, except that creating a type records two changes: the
        post_save handler saves it again to assign its object_type.
        """
        obj_perm = ObjectPermission(name="Test permission", actions=["add"])
        obj_perm.save()
        obj_perm.users.add(self.user)
        obj_perm.object_types.add(ObjectType.objects.get_for_model(self.model))
        self.assertHttpStatus(self.client.get(self._get_url("add")), 200)

        data = {**self.form_data, "changelog_message": "Created"}
        self.assertHttpStatus(self.client.post(self._get_url("add"), post_data(data)), 302)
        instance = CustomObjectType.objects.get(slug=self.form_data["slug"])
        self.assertInstanceEqual(instance, self.form_data)
        create, update = ObjectChange.objects.filter(
            changed_object_type=ContentType.objects.get_for_model(instance), changed_object_id=instance.pk
        ).order_by("pk")
        self.assertEqual(create.action, ObjectChangeActionChoices.ACTION_CREATE)
        self.assertEqual(create.message, "Created")
        self.assertEqual(update.action, ObjectChangeActionChoices.ACTION_UPDATE)
        self.assertEqual(update.postchange_data["object_type"], instance.object_type_id)

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


class CustomObjectTypeFieldViewTestCase(
    CustomObjectsTestCase,
    ViewTestCases.GetObjectChangelogViewTestCase,
    ViewTestCases.CreateObjectViewTestCase,
    ViewTestCases.EditObjectViewTestCase,
    ViewTestCases.DeleteObjectViewTestCase,
):
    """Add, edit and delete views for fields, which are listed and shown on their type's page."""

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


class CustomObjectTypeConstraintViewTestCase(
    CustomObjectsTestCase,
    ViewTestCases.GetObjectChangelogViewTestCase,
    ViewTestCases.CreateObjectViewTestCase,
    ViewTestCases.EditObjectViewTestCase,
    ViewTestCases.DeleteObjectViewTestCase,
):
    """Add, edit and delete views for constraints, plus the type's Constraints tab."""

    model = CustomObjectTypeConstraint
    # The form takes field IDs and names; the model stores schema IDs (checked separately).
    validation_excluded_fields = ["field_schema_ids", "condition", "rule"]

    @classmethod
    def setUpTestData(cls):
        cls.custom_object_type = CustomObjectType.objects.create(name="asset", slug="assets")
        cls.f1, cls.f2, cls.f3, cls.f4 = (
            CustomObjectTypeField.objects.create(custom_object_type=cls.custom_object_type, name=name, type="text")
            for name in ("vendor", "serial", "model", "tag")
        )
        for name, fields in (("c1", (cls.f1, cls.f2)), ("c2", (cls.f1, cls.f3)), ("c3", (cls.f2, cls.f3))):
            CustomObjectTypeConstraint.objects.create(
                custom_object_type=cls.custom_object_type, name=name, field_schema_ids=[f.schema_id for f in fields]
            )
        cls.form_data = {
            "custom_object_type": cls.custom_object_type.pk,
            "name": "c4",
            "type": "unique",
            "field_schema_ids": [cls.f1.pk, cls.f4.pk],
            "case_insensitive": True,
            "nulls_distinct": True,
            "condition": '[{"field": "serial", "lookup": "isnull", "value": false}]',
        }

    def _get_base_url(self):
        return 'plugins:{}:{}_{{}}'.format(self.model._meta.app_label, self.model._meta.model_name)

    def test_create_object_with_permission(self):
        super().test_create_object_with_permission()
        constraint = CustomObjectTypeConstraint.objects.get(name="c4")
        self.assertCountEqual(constraint.field_schema_ids, [self.f1.schema_id, self.f4.schema_id])
        self.assertEqual(constraint.condition, [{"field": self.f2.schema_id, "lookup": "isnull", "value": False}])

    def test_condition_uses_field_names(self):
        self.user.is_superuser = True
        self.user.save()
        constraint = CustomObjectTypeConstraint.objects.create(
            custom_object_type=self.custom_object_type, name="c5", field_schema_ids=[self.f4.schema_id],
            condition=[{"field": self.f3.schema_id, "lookup": "isnull", "value": True}],
        )
        url = reverse("plugins:netbox_custom_objects:customobjecttypeconstraint_edit", kwargs={"pk": constraint.pk})
        self.assertContains(self.client.get(url), "&quot;field&quot;: &quot;model&quot;")
        tab_url = reverse(
            "plugins:netbox_custom_objects:customobjecttype_constraints", kwargs={"pk": self.custom_object_type.pk}
        )
        self.assertContains(self.client.get(tab_url), "Model is empty")

        response = self.client.post(url, {
            "custom_object_type": self.custom_object_type.pk, "name": "c5", "type": "unique",
            "field_schema_ids": [self.f4.pk], "nulls_distinct": True,
            "condition": '[{"field": "nope", "lookup": "isnull", "value": true}]',
        })
        self.assertHttpStatus(response, 200)
        self.assertContains(response, "Unknown field(s): nope")

        response = self.client.post(url, {
            "custom_object_type": self.custom_object_type.pk, "name": "c5", "type": "unique",
            "field_schema_ids": [self.f4.pk], "nulls_distinct": True,
            "condition": '[{"field": ["model"], "lookup": "isnull", "value": true}]',
        })
        self.assertHttpStatus(response, 200)
        self.assertContains(response, "Unknown field(s): [&#x27;model&#x27;]")

    def test_check_rule_uses_field_names(self):
        self.user.is_superuser = True
        self.user.save()
        add_url = reverse("plugins:netbox_custom_objects:customobjecttypeconstraint_add")
        response = self.client.post(f"{add_url}?custom_object_type={self.custom_object_type.pk}", {
            # The unique-only checkboxes are left unticked.
            "custom_object_type": self.custom_object_type.pk, "name": "c6", "type": "check",
            "rule": '[{"field": "serial", "lookup": "n", "value": {"field": "model"}}]',
            "error_message": "Serial and model must differ.",
        })
        self.assertHttpStatus(response, 302)
        constraint = CustomObjectTypeConstraint.objects.get(name="c6")
        self.assertEqual(constraint.field_schema_ids, [])
        self.assertEqual(
            constraint.rule, [{"field": self.f2.schema_id, "lookup": "n", "value": {"field": self.f3.schema_id}}]
        )

        url = reverse("plugins:netbox_custom_objects:customobjecttypeconstraint_edit", kwargs={"pk": constraint.pk})
        self.assertContains(self.client.get(url), "&quot;field&quot;: &quot;model&quot;")
        tab_url = reverse(
            "plugins:netbox_custom_objects:customobjecttype_constraints", kwargs={"pk": self.custom_object_type.pk}
        )
        self.assertContains(self.client.get(tab_url), "Serial does not equal Model (or is empty)")

        response = self.client.post(url, {
            "custom_object_type": self.custom_object_type.pk, "name": "c6", "type": "check", "nulls_distinct": True,
            "rule": '[{"field": "serial", "lookup": "n", "value": {"field": "nope"}}]',
        })
        self.assertHttpStatus(response, 200)
        self.assertContains(response, "Unknown field(s): nope")

    def test_constraints_tab(self):
        self.user.is_superuser = True
        self.user.save()
        url = reverse(
            "plugins:netbox_custom_objects:customobjecttype_constraints", kwargs={"pk": self.custom_object_type.pk}
        )
        response = self.client.get(url)
        self.assertHttpStatus(response, 200)
        self.assertContains(response, "c1")
        self.assertContains(response, "Vendor, Serial")

    def test_field_delete_page_lists_constraints(self):
        self.user.is_superuser = True
        self.user.save()
        url = reverse("plugins:netbox_custom_objects:customobjecttypefield_delete", kwargs={"pk": self.f3.pk})
        response = self.client.get(url)
        self.assertHttpStatus(response, 200)
        self.assertContains(response, "will also be deleted: c2, c3")
        # The data-loss warning appears only when objects have a value for the field.
        self.assertNotContains(response, "in use on")
        self.custom_object_type.get_model().objects.create(model="m1")
        self.assertContains(self.client.get(url), "in use on 1 Custom Objects")

    def test_delete_ignores_offsite_return_url(self):
        self.user.is_superuser = True
        self.user.save()
        constraint = CustomObjectTypeConstraint.objects.get(name="c1")
        cases = (
            ("customobjecttypeconstraint_delete", constraint, constraint.get_absolute_url()),
            ("customobjecttypefield_delete", self.f4, self.custom_object_type.get_absolute_url()),
        )
        for viewname, obj, expected in cases:
            with self.subTest(viewname):
                url = reverse(f"plugins:netbox_custom_objects:{viewname}", kwargs={"pk": obj.pk})
                response = self.client.post(f"{url}?return_url=https://evil.example/", {"confirm": True})
                self.assertRedirects(response, expected, fetch_redirect_response=False)

    def test_custom_object_form_reports_duplicate(self):
        self.user.is_superuser = True
        self.user.save()
        model = self.custom_object_type.get_model()
        model.objects.create(vendor="acme", serial="1")
        url = reverse("plugins:netbox_custom_objects:customobject_add", kwargs={"custom_object_type": "assets"})
        response = self.client.post(url, {"vendor": "acme", "serial": "1"})
        self.assertHttpStatus(response, 200)
        self.assertContains(response, "already exists")
        self.assertEqual(model.objects.count(), 1)


class CustomObjectViewTestCase(
    _SkipQueryCountsWhenBranching, CustomObjectsTestCase, ViewTestCases.PrimaryObjectViewTestCase
):
    """Test cases for dynamic CustomObject views."""

    query_count_model_label = 'customobject-simple'
    form_data = {"name": "Edited Instance", "description": "Edited", "count": 30}

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

        cls.instance3 = cls.model.objects.create(
            name="Test Instance 3",
            description="Third test instance",
            count=30
        )

        cls.csv_data = (
            "name,description,count",
            "Imported 1,First import,1",
            "Imported 2,Second import,2",
            "Imported 3,Third import,3",
        )
        cls.csv_update_data = (
            "id,description,count",
            f"{cls.instance1.pk},Updated 1,41",
            f"{cls.instance2.pk},Updated 2,42",
            f"{cls.instance3.pk},Updated 3,43",
        )
        cls.bulk_edit_data = {"count": 50}

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

    @override_settings(EXEMPT_VIEW_PERMISSIONS=['*'])
    def test_get_object_changelog(self):
        """Changelog tab must return 200, not 500 from deprecated user kwarg."""
        url = self._get_url('changelog', self.instance1)
        self.assertHttpStatus(self.client.get(url), 200)

    def test_tabs_with_constrained_permission(self):
        obj_perm = ObjectPermission(name='Test permission', constraints={'pk': self.instance1.pk}, actions=['view'])
        obj_perm.save()
        obj_perm.users.add(self.user)
        obj_perm.object_types.add(ObjectType.objects.get_for_model(self.model))

        for tab in ('journal', 'changelog', 'contacts'):
            with self.subTest(tab):
                self.assertHttpStatus(self.client.get(self._get_url(tab, self.instance1)), 200)
                self.assertHttpStatus(self.client.get(self._get_url(tab, self.instance2)), 404)

    def _assert_get_queryset_does_not_full_scan(self, view_class):
        """Helper for the get_queryset() regression tests.

        All three bulk views (import/edit/delete) previously did
        ``if self.queryset:`` in ``get_queryset()``, whose ``QuerySet.__bool__``
        calls ``_fetch_all()`` — pulling every row into memory. On a type with
        millions of records this spiked server memory and hung the request.

        ``get_queryset()`` is invoked a second time by ``BaseMultiObjectView.
        dispatch()`` after ``setup()`` has assigned the (lazy) queryset, which is
        when the truthiness check evaluated it. We reproduce that state directly
        so the assertion targets the exact regression regardless of each view's
        HTTP method handling.
        """
        request = RequestFactory().get('/')
        request.user = self.user

        view = view_class()
        view.kwargs = {'custom_object_type': self.model.custom_object_type.slug}
        # Post-setup state: dispatch() will have left a lazy, unevaluated queryset.
        view.queryset = self.model.objects.all()

        db_table = self.model._meta.db_table
        full_scans = []

        def tracer(execute, sql, params, many, context):
            normalized = sql.lstrip().upper()
            if (
                db_table in sql
                and normalized.startswith('SELECT')
                and 'LIMIT' not in normalized
                and 'COUNT(' not in normalized
            ):
                full_scans.append(sql)
            return execute(sql, params, many, context)

        with connection.execute_wrapper(tracer):
            view.get_queryset(request)

        self.assertEqual(
            full_scans, [],
            f"{view_class.__name__}.get_queryset() issued an unbounded SELECT "
            f"against {db_table}; the whole table is being loaded into memory:\n"
            + "\n".join(full_scans),
        )

    def test_bulk_import_get_queryset_does_not_full_scan(self):
        """CustomObjectBulkImportView.get_queryset()."""
        self._assert_get_queryset_does_not_full_scan(views.CustomObjectBulkImportView)

    def test_bulk_edit_get_queryset_does_not_full_scan(self):
        """CustomObjectBulkEditView.get_queryset()."""
        self._assert_get_queryset_does_not_full_scan(views.CustomObjectBulkEditView)

    def test_bulk_edit_form_nullable_fields_includes_scalar_fields(self):
        """Bulk edit must offer 'Set null' for real, nullable fields."""
        request = RequestFactory().get('/')
        request.user = self.user

        view = views.CustomObjectBulkEditView()
        view.setup(request, custom_object_type=self.custom_object_type.slug)

        self.assertIn('description', view.form.nullable_fields)
        self.assertIn('count', view.form.nullable_fields)
        # 'name' is required=True; a required field must never offer "Set null",
        # since every custom object column is nullable at the DB level regardless
        # of the field's own required flag.
        self.assertNotIn('name', view.form.nullable_fields)

    def test_bulk_edit_set_null_clears_field(self):
        """Checking 'Set null' for a field must clear it across selected objects."""
        content_type = ContentType.objects.get_for_model(self.model)
        obj_perm = ObjectPermission(name='bulk-edit-set-null', actions=['view', 'change'])
        obj_perm.save()
        obj_perm.users.add(self.user)
        obj_perm.object_types.add(content_type)

        bulk_edit_url = self._get_url('bulk_edit')
        response = self.client.post(bulk_edit_url, data={
            '_apply': 'Apply',
            'pk': [self.instance1.pk, self.instance2.pk],
            '_nullify': ['description'],
            'description': '',
        })
        self.assertHttpStatus(response, 302)
        self.instance1.refresh_from_db()
        self.instance2.refresh_from_db()
        self.assertIsNone(self.instance1.description)
        self.assertIsNone(self.instance2.description)

    def _run_as_background_job(self, view_class, url, data):
        """Run a bulk view as NetBox's AsyncViewJob does: on a copy of the request, without URL kwargs."""
        content_type = ContentType.objects.get_for_model(self.model)
        obj_perm = ObjectPermission(name='background-job', actions=['view', 'add', 'change', 'delete'])
        obj_perm.save()
        obj_perm.users.add(self.user)
        obj_perm.object_types.add(content_type)

        request = RequestFactory().post(url, data={**data, 'background_job': True})
        request.user = self.user
        request.id = uuid.uuid4()
        job = Job.objects.create(name='Background bulk operation', user=self.user, job_id=uuid.uuid4())
        # COMPAT(netbox<4.6): if the view raises, event_tracking() leaves this request current, which
        # would leak into later tests.
        self.addCleanup(current_request.set, None)
        self.addCleanup(events_queue.set, {})
        self.addCleanup(query_cache.set, None)
        request_copy = copy_safe_request(request)
        # COMPAT(netbox<4.7.3): BulkEditView reads request.htmx, which request copies lacked until
        # NetBox 4.7.3, so a background bulk edit failed for every model, core's included.
        if not hasattr(request_copy, 'htmx'):
            request_copy.htmx = False
        AsyncViewJob(job).run(view_cls=view_class, request=request_copy)

    def test_bulk_edit_as_background_job(self):
        self._run_as_background_job(views.CustomObjectBulkEditView, self._get_url('bulk_edit'), {
            '_apply': 'Apply',
            'pk': [self.instance1.pk, self.instance2.pk],
            'description': 'Edited in the background',
        })
        self.instance1.refresh_from_db()
        self.instance2.refresh_from_db()
        self.assertEqual(self.instance1.description, 'Edited in the background')
        self.assertEqual(self.instance2.description, 'Edited in the background')

    def test_bulk_delete_as_background_job(self):
        self._run_as_background_job(views.CustomObjectBulkDeleteView, self._get_url('bulk_delete'), {
            '_confirm': 'Confirm',
            'confirm': True,
            'pk': [self.instance1.pk],
        })
        self.assertFalse(self.model.objects.filter(pk=self.instance1.pk).exists())
        self.assertTrue(self.model.objects.filter(pk=self.instance2.pk).exists())

    def test_bulk_import_as_background_job(self):
        self._run_as_background_job(views.CustomObjectBulkImportView, self._get_url('bulk_import'), {
            'data': 'name,description\nImported in the background,From a job',
            'format': 'csv',
            'csv_delimiter': ',',
        })
        self.assertTrue(self.model.objects.filter(name='Imported in the background', description='From a job').exists())

    def test_bulk_edit_set_null_clears_object_and_multiobject_fields(self):
        """
        Non-polymorphic object/multiobject fields must also support
        'Set null' in bulk edit -- they map to a real, nullable FK column / M2M relation
        (like core's Site.asns), unlike polymorphic fields which are excluded.
        """
        from dcim.models import Site

        cot = self.create_custom_object_type(name='ObjNullTest', slug='obj-null-test')
        self.create_custom_object_type_field(
            cot, name='name', label='Name', type='text', primary=True, required=True,
        )
        self.create_custom_object_type_field(
            cot, name='site', label='Site', type='object',
            related_object_type=self.get_site_object_type(),
        )
        self.create_custom_object_type_field(
            cot, name='sites', label='Sites', type='multiobject',
            related_object_type=self.get_site_object_type(),
        )

        model = cot.get_model()
        site_a = Site.objects.create(name='Site A', slug='site-a')
        site_b = Site.objects.create(name='Site B', slug='site-b')
        obj1 = model.objects.create(name='Obj 1', site=site_a)
        obj1.sites.set([site_a, site_b])
        obj2 = model.objects.create(name='Obj 2', site=site_a)
        obj2.sites.set([site_a, site_b])

        content_type = ContentType.objects.get_for_model(model)
        obj_perm = ObjectPermission(name='bulk-edit-set-null-obj', actions=['view', 'change'])
        obj_perm.save()
        obj_perm.users.add(self.user)
        obj_perm.object_types.add(content_type)

        url_format = 'plugins:{}:customobject_{{}}'.format(model._meta.app_label)
        bulk_edit_url = reverse(url_format.format('bulk_edit'), kwargs={'custom_object_type': cot.slug})
        response = self.client.post(bulk_edit_url, data={
            '_apply': 'Apply',
            'pk': [obj1.pk, obj2.pk],
            '_nullify': ['site', 'sites'],
            'site': '',
            'sites': [],
        })
        self.assertHttpStatus(response, 302)
        obj1.refresh_from_db()
        obj2.refresh_from_db()
        self.assertIsNone(obj1.site)
        self.assertIsNone(obj2.site)
        self.assertEqual(obj1.sites.count(), 0)
        self.assertEqual(obj2.sites.count(), 0)

    def test_bulk_delete_get_queryset_does_not_full_scan(self):
        """CustomObjectBulkDeleteView.get_queryset()."""
        self._assert_get_queryset_does_not_full_scan(views.CustomObjectBulkDeleteView)

    def test_bulk_import_omits_hidden_required_field_from_form(self):
        """
        A Required+Hidden field must be omitted from the bulk import
        form (not disabled, which ignores submitted data and always fails "This field
        is required"). Own COT used to avoid leaking model-cache state into other tests.
        """
        cot = self.create_custom_object_type(name='HiddenFieldImportTest', slug='hidden-field-import-test')
        self.create_custom_object_type_field(
            cot, name='name', label='Name', type='text', primary=True,
        )
        self.create_custom_object_type_field(
            cot,
            name='identifier',
            label='Identifier',
            type='text',
            required=True,
            ui_editable='hidden',
        )

        request = RequestFactory().post('/')
        request.user = self.user

        view = views.CustomObjectBulkImportView()
        view.setup(request, custom_object_type=cot.slug)

        # The hidden required field must not appear in the import form at all...
        self.assertNotIn('identifier', view.model_form.base_fields)

        # ...so a row that omits it (as any importer must, since it can't be set) is valid.
        model = view.queryset.model
        form = view.model_form(data={'name': 'Imported Instance'}, instance=model())
        self.assertTrue(form.is_valid(), form.errors)

    def test_bulk_import_silently_ignores_value_for_hidden_field(self):
        """
        Matches the original bug report's payload (a value supplied
        for the hidden field). It must not error, and the value must be silently
        dropped rather than written, matching core NetBox's own CSV import behavior.
        """
        cot = self.create_custom_object_type(name='HiddenFieldImportTest2', slug='hidden-field-import-test-2')
        self.create_custom_object_type_field(
            cot, name='name', label='Name', type='text', primary=True,
        )
        self.create_custom_object_type_field(
            cot,
            name='identifier',
            label='Identifier',
            type='text',
            required=True,
            ui_editable='hidden',
        )

        request = RequestFactory().post('/')
        request.user = self.user

        view = views.CustomObjectBulkImportView()
        view.setup(request, custom_object_type=cot.slug)

        model = view.queryset.model
        form = view.model_form(
            data={'name': 'Imported Instance', 'identifier': '12345'}, instance=model(),
        )
        self.assertTrue(form.is_valid(), form.errors)

        instance = form.save()
        self.assertIsNone(instance.identifier)

    def test_bulk_import_enforces_validation_regex(self):
        """CSV/bulk import must reject values that don't match validation_regex."""
        cot = self.create_custom_object_type(name='RegexImportTest', slug='regex-import-test')
        self.create_custom_object_type_field(
            cot, name='code', label='Code', type='text', validation_regex=r'^[A-Z]{3}$',
        )
        self.create_custom_object_type_field(
            cot, name='website', label='Website', type='url', validation_regex=r'^https://',
        )

        request = RequestFactory().post('/')
        request.user = self.user

        view = views.CustomObjectBulkImportView()
        view.setup(request, custom_object_type=cot.slug)
        model = view.queryset.model

        form = view.model_form(data={'code': 'abc', 'website': 'http://example.com'}, instance=model())
        self.assertFalse(form.is_valid())
        self.assertIn('code', form.errors)
        self.assertIn('website', form.errors)

        form = view.model_form(data={'code': 'ABC', 'website': 'https://example.com'}, instance=model())
        self.assertTrue(form.is_valid(), form.errors)

    def test_edit_form_omits_hidden_field(self):
        """A hidden field must be omitted from the edit form, not just disabled."""
        cot = self.create_custom_object_type(name='HiddenFieldEditTest', slug='hidden-field-edit-test')
        self.create_custom_object_type_field(
            cot, name='name', label='Name', type='text', primary=True,
        )
        self.create_custom_object_type_field(
            cot, name='hidden', label='Hidden', type='text', ui_editable='hidden',
        )
        self.create_custom_object_type_field(
            cot, name='readonly', label='Readonly', type='text', ui_editable='no',
        )

        request = RequestFactory().get('/')
        request.user = self.user

        view = views.CustomObjectEditView()
        view.setup(request, custom_object_type=cot.slug)

        self.assertNotIn('hidden', view.form.base_fields)
        # A read-only (ui_editable=no) field is disabled, not omitted -- distinct from hidden.
        self.assertIn('readonly', view.form.base_fields)

    def test_bulk_edit_form_omits_hidden_field(self):
        """Same as above, for the bulk edit form."""
        cot = self.create_custom_object_type(name='HiddenFieldBulkEditTest', slug='hidden-field-bulk-edit-test')
        self.create_custom_object_type_field(
            cot, name='name', label='Name', type='text', primary=True,
        )
        self.create_custom_object_type_field(
            cot, name='hidden', label='Hidden', type='text', ui_editable='hidden',
        )
        self.create_custom_object_type_field(
            cot, name='readonly', label='Readonly', type='text', ui_editable='no',
        )

        request = RequestFactory().get('/')
        request.user = self.user

        view = views.CustomObjectBulkEditView()
        view.setup(request, custom_object_type=cot.slug)

        self.assertNotIn('hidden', view.form.base_fields)
        self.assertIn('readonly', view.form.base_fields)

    def test_edit_form_omits_hidden_coordinates_field(self):
        """A hidden coordinates field must omit both its lat/long sub-fields."""
        cot = self.create_custom_object_type(name='HiddenCoordsEditTest', slug='hidden-coords-edit-test')
        self.create_custom_object_type_field(
            cot, name='name', label='Name', type='text', primary=True,
        )
        self.create_custom_object_type_field(
            cot, name='location', label='Location', type='coordinates', ui_editable='hidden',
        )

        request = RequestFactory().get('/')
        request.user = self.user

        view = views.CustomObjectEditView()
        view.setup(request, custom_object_type=cot.slug)

        self.assertNotIn('location_latitude', view.form.base_fields)
        self.assertNotIn('location_longitude', view.form.base_fields)

    def test_bulk_edit_form_omits_hidden_coordinates_field(self):
        """Same as above, for the bulk edit form."""
        cot = self.create_custom_object_type(name='HiddenCoordsBulkEditTest', slug='hidden-coords-bulk-edit-test')
        self.create_custom_object_type_field(
            cot, name='name', label='Name', type='text', primary=True,
        )
        self.create_custom_object_type_field(
            cot, name='location', label='Location', type='coordinates', ui_editable='hidden',
        )

        request = RequestFactory().get('/')
        request.user = self.user

        view = views.CustomObjectBulkEditView()
        view.setup(request, custom_object_type=cot.slug)

        self.assertNotIn('location_latitude', view.form.base_fields)
        self.assertNotIn('location_longitude', view.form.base_fields)

    def test_edit_form_applies_hidden_multiobject_default_on_create(self):
        """
        A hidden non-polymorphic MultiObject field is a real M2M
        model attribute, so omitting it from the rendered form must not also skip
        applying its configured default when creating a new object.
        """
        from dcim.models import Site

        site_a = Site.objects.create(name='Site A', slug='site-a-hidden-mo-create')
        site_b = Site.objects.create(name='Site B', slug='site-b-hidden-mo-create')

        cot = self.create_custom_object_type(name='HiddenMultiObjCreateTest', slug='hidden-multiobj-create-test')
        self.create_custom_object_type_field(
            cot, name='name', label='Name', type='text', primary=True,
        )
        self.create_custom_object_type_field(
            cot, name='sites', label='Sites', type='multiobject',
            related_object_type=self.get_site_object_type(),
            ui_editable='hidden',
            default=[site_a.pk, site_b.pk],
        )

        request = RequestFactory().post('/')
        request.user = self.user

        view = views.CustomObjectEditView()
        view.setup(request, custom_object_type=cot.slug)

        form = view.form(data={'name': 'New Instance'}, instance=view.object)
        self.assertTrue(form.is_valid(), form.errors)
        instance = form.save()

        self.assertEqual(set(instance.sites.values_list('pk', flat=True)), {site_a.pk, site_b.pk})

    def test_edit_form_multiobject_default_for_cot_target_uses_full_cached_model(self):
        """
        Resolving a multi-object default that targets another COT must
        use plain get_model(), so the form build leaves a full model in the cache rather
        than an uncached partial one.
        """
        target = self.create_custom_object_type(name='MoDefaultTarget776', slug='mo-default-target-776')
        self.create_custom_object_type_field(
            target, name='name', label='Name', type='text', primary=True,
        )
        self.create_custom_object_type_field(
            target, name='site', label='Site', type='object',
            related_object_type=self.get_site_object_type(),
        )
        target_model = target.get_model()
        t1 = target_model.objects.create(name='t1')
        t2 = target_model.objects.create(name='t2')

        cot = self.create_custom_object_type(name='MoDefaultSource776', slug='mo-default-source-776')
        self.create_custom_object_type_field(
            cot, name='name', label='Name', type='text', primary=True,
        )
        self.create_custom_object_type_field(
            cot, name='targets', label='Targets', type='multiobject',
            related_object_type=target.object_type,
            default=[t1.pk, t2.pk],
        )

        request = RequestFactory().get('/')
        request.user = self.user

        view = views.CustomObjectEditView()
        view.setup(request, custom_object_type=cot.slug)

        CustomObjectType.clear_model_cache(target.id)
        form = view.form(instance=view.object)

        self.assertEqual(set(form.initial['targets']), {t1.pk, t2.pk})
        self.assertTrue(CustomObjectType.is_model_cached(target.id))
        self.assertIn('site', {f.name for f in target.get_model()._meta.local_fields})

    def test_edit_form_preserves_hidden_multiobject_relation_on_edit(self):
        """
        Editing an object must not clear a hidden MultiObject field's
        existing relation -- there is no rendered input for it to come from, and a
        hidden field is defined as neither displayed nor editable.
        """
        from dcim.models import Site

        site_a = Site.objects.create(name='Site A', slug='site-a-hidden-mo-edit')
        site_b = Site.objects.create(name='Site B', slug='site-b-hidden-mo-edit')

        cot = self.create_custom_object_type(name='HiddenMultiObjEditTest', slug='hidden-multiobj-edit-test')
        self.create_custom_object_type_field(
            cot, name='name', label='Name', type='text', primary=True,
        )
        self.create_custom_object_type_field(
            cot, name='sites', label='Sites', type='multiobject',
            related_object_type=self.get_site_object_type(),
            ui_editable='hidden',
            default=[site_a.pk],
        )

        model = cot.get_model()
        obj = model.objects.create(name='Existing Instance')
        obj.sites.set([site_a.pk, site_b.pk])

        request = RequestFactory().post('/')
        request.user = self.user

        view = views.CustomObjectEditView()
        view.setup(request, custom_object_type=cot.slug, pk=obj.pk)

        form = view.form(data={'name': 'Renamed Instance'}, instance=view.object)
        self.assertTrue(form.is_valid(), form.errors)
        instance = form.save()

        self.assertEqual(set(instance.sites.values_list('pk', flat=True)), {site_a.pk, site_b.pk})

    def test_bulk_edit_select_all_respects_full_queryset(self):
        """'Select all matching query' must edit all objects, not just the current page.

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
        """'Select all matching query' must delete all objects, not just the current page.

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
        """Add-only permission must grant access to the add URL, not require change."""
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


class ComplexCustomObjectViewTestCase(
    _SkipQueryCountsWhenBranching, CustomObjectsTestCase, ViewTestCases.PrimaryObjectViewTestCase
):
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

        cls.instance_3 = cls.model.objects.create(
            name="Complex Test Instance 3",
            count=30,
            price=12.25,
            active=True,
            status="choice1",
            multi_tags=["choice1"],
            website="https://example.org",
            metadata={"number": 3}
        )

        cls.form_data = {
            "name": "Complex Edited",
            "description": "Edited",
            "count": 40,
            "price": Decimal("99.99"),
            "active": True,
            "status": "choice3",
            "multi_tags": ["choice1", "choice3"],
            "website": "https://example.net",
            "metadata": '{"edited": true}',
        }
        cls.csv_data = (
            "name,count,price,active,status,multi_tags,website",
            'Imported 1,1,1.50,true,choice1,"choice1,choice2",https://one.example.com',
            "Imported 2,2,2.50,false,choice2,choice2,https://two.example.com",
            "Imported 3,3,3.50,true,choice3,choice3,https://three.example.com",
        )
        cls.csv_update_data = (
            "id,count,status",
            f"{cls.instance_1.pk},51,choice1",
            f"{cls.instance_2.pk},52,choice1",
            f"{cls.instance_3.pk},53,choice2",
        )
        cls.bulk_edit_data = {"count": 60, "status": "choice2"}

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

    @override_settings(EXEMPT_VIEW_PERMISSIONS=['*'])
    def test_get_object_changelog(self):
        """Changelog tab must return 200, not 500 from deprecated user kwarg."""
        url = self._get_url('changelog', self.instance_1)
        self.assertHttpStatus(self.client.get(url), 200)


# COMPAT(netbox<4.6.0): CustomFieldChoiceSet has no choice_colors.
@skipUnless(hasattr(CustomFieldChoiceSet, 'get_choice_color'), 'NetBox < 4.6.0 has no choice colors')
class SelectFieldColorDetailViewTestCase(CustomObjectsTestCase, TestCase):
    """Regression tests: selection field colors render correctly in the detail view."""

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
        """Select field with a color renders a colored badge in the detail view."""
        response = self.client.get(self._detail_url())
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('text-bg-green', content)
        self.assertIn('Active', content)

    def test_detail_view_renders_multiselect_color_badges(self):
        """Multiselect field with colors renders colored badges in the detail view."""
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


class ObjectFieldViewTestCase(
    _SkipQueryCountsWhenBranching, CustomObjectsTestCase, ViewTestCases.PrimaryObjectViewTestCase
):
    """Test cases for custom objects with object and multi-object fields."""

    query_count_model_label = 'customobject-objectfields'
    # The multi-object field returns related objects rather than PKs; test_edit_object_sets_sites checks it.
    validation_excluded_fields = ["sites"]

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

            cls.instance_3 = cls.model.objects.create(name="Object Test Instance 3")

            cls.site_2 = Site.objects.create(name="Test Site 2", slug="test-site-2")
            cls.device_2 = Device.objects.create(
                name="Test Device 2",
                site=cls.site_2,
                device_type=device_type,
                role=device_role
            )
            cls.form_data = {
                "name": "Object Test Edited",
                "device": cls.device_2.pk,
                "sites": [cls.site.pk, cls.site_2.pk],
            }
            cls.csv_data = (
                "name,device",
                f"Imported 1,{cls.device.name}",
                f"Imported 2,{cls.device_2.name}",
                "Imported 3,",
            )
            cls.csv_update_data = (
                "id,name",
                f"{cls.instance_1.pk},Object Updated 1",
                f"{cls.instance_2.pk},Object Updated 2",
                f"{cls.instance_3.pk},Object Updated 3",
            )
            cls.bulk_edit_data = {"device": cls.device_2.pk}

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

    @override_settings(EXEMPT_VIEW_PERMISSIONS=['*'])
    def test_get_object_changelog(self):
        """Changelog tab must return 200, not 500 from deprecated user kwarg."""
        if self.instance_1 is None:
            self.skipTest("DCIM models not available")
        url = self._get_url('changelog', self.instance_1)
        self.assertHttpStatus(self.client.get(url), 200)

    def test_edit_object_sets_sites(self):
        self.user.is_superuser = True
        self.user.save()
        response = self.client.post(self._get_url("edit", self.instance_3), post_data(self.form_data))
        self.assertHttpStatus(response, 302)
        self.assertCountEqual(self.instance_3.sites.all(), [self.site, self.site_2])

    def test_delete_confirmation_page_with_populated_multiobject_field(self):
        """Delete confirmation page returns 200 and omits through-table model names."""
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
    Regression tests: the HTMX object-selector endpoint must not
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

    def test_bulk_edit_coordinates_not_individually_nullable(self):
        """Regression: latitude/longitude must not get independent Set Null controls."""
        request = RequestFactory().get('/')
        request.user = self.user

        view = views.CustomObjectBulkEditView()
        view.setup(request, custom_object_type=self.cot.slug)
        self.assertNotIn('location_latitude', view.form.nullable_fields)
        self.assertNotIn('location_longitude', view.form.nullable_fields)

    def test_bulk_edit_set_null_clears_coordinates_atomically(self):
        """A single 'Set null' checkbox for the coordinates field clears both halves."""
        from decimal import Decimal
        obj = self.model.objects.create(
            name="Existing2",
            location_latitude=Decimal("40.712800"),
            location_longitude=Decimal("-74.006000"),
        )
        data = {
            "pk": [obj.pk],
            "_apply": "Apply",
            "_nullify": ["location"],
        }
        response = self.client.post(self._bulk_edit_url(), data)
        self.assertEqual(response.status_code, 302, getattr(response, "content", b""))
        obj.refresh_from_db()
        self.assertIsNone(obj.location_latitude)
        self.assertIsNone(obj.location_longitude)


class ArrayFieldViewTest(CustomObjectsTestCase, TestCase):
    """UI behaviour for the array field type: JSON input, CSV import and formatted display."""

    @classmethod
    def setUpTestData(cls):
        cls.cot = CustomObjectType.objects.create(name="ZoneView", verbose_name_plural="Zone Views", slug="zone-views")
        CustomObjectTypeField.objects.create(
            custom_object_type=cls.cot, name="name", type="text", primary=True, required=True
        )
        CustomObjectTypeField.objects.create(
            custom_object_type=cls.cot, name="points", type="array", array_element_type="integer",
            array_tuple_size=3, array_min_length=2,
        )
        CustomObjectTypeField.objects.create(
            custom_object_type=cls.cot, name="days", type="array", array_element_type="date",
        )
        cls.model = cls.cot.get_model()

    def setUp(self):
        super().setUp()
        self.user.is_superuser = True
        self.user.save()

    def _url(self, name, **kwargs):
        return reverse(f"plugins:netbox_custom_objects:{name}", kwargs={"custom_object_type": self.cot.slug, **kwargs})

    def test_create_and_edit(self):
        response = self.client.get(self._url("customobject_add"))
        self.assertContains(response, "A list of 3-value integer tuples, at least 2.")

        response = self.client.post(self._url("customobject_add"), {
            "name": "Zone", "points": "[[0, 0, 0], [10, 0, 5]]", "days": '["2026-01-01"]',
        })
        self.assertEqual(response.status_code, 302, response.content)
        obj = self.model.objects.get(name="Zone")
        self.assertEqual(obj.points, [[0, 0, 0], [10, 0, 5]])
        self.assertEqual(obj.days, [date(2026, 1, 1)])

        # The edit form shows the stored values compactly, dates included.
        edit_url = self._url("customobject_edit", pk=obj.pk)
        response = self.client.get(edit_url)
        self.assertContains(response, "    [0, 0, 0],\n    [10, 0, 5]\n]")
        self.assertContains(response, "[&quot;2026-01-01&quot;]")
        response = self.client.post(edit_url, {"name": "Zone", "points": "[[0, 0, 0], [1, 1, 1]]", "days": ""})
        self.assertEqual(response.status_code, 302, response.content)
        obj.refresh_from_db()
        self.assertEqual((obj.points, obj.days), ([[0, 0, 0], [1, 1, 1]], None))

    def test_invalid_value_rejected(self):
        response = self.client.post(self._url("customobject_add"), {"name": "Bad", "points": "[[0, 0, 0], [1, 1]]"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.model.objects.filter(name="Bad").exists())
        # The model's message, not the JSON form field's "Enter a valid JSON."
        self.assertContains(response, "Item 2 must have exactly 3 values.")
        self.assertNotContains(response, "Enter a valid JSON")

        response = self.client.post(self._url("customobject_add"), {"name": "Bad", "points": "[[0, 0, 0], 1]"})
        self.assertContains(response, "Each item must itself be a list of values.")

        # Values that aren't of the element type are rejected, not coerced or a server error.
        cases = {
            "a JSON string": {"points": '"abc"'},
            "a number on dates": {"days": "[1]"},
            "a fraction on integers": {"points": "[[0, 0, 0], [1, 1, 1.5]]"},
            "a boolean on integers": {"points": "[[0, 0, 0], [1, 1, true]]"},
        }
        for label, values in cases.items():
            with self.subTest(label):
                response = self.client.post(self._url("customobject_add"), {"name": "Bad", **values})
                self.assertEqual(response.status_code, 200)
                self.assertFalse(self.model.objects.filter(name="Bad").exists())

    def test_empty_list_is_no_value(self):
        # Stored as NULL, as blank input is; the minimum length applies only to a non-empty list.
        response = self.client.post(self._url("customobject_add"), {"name": "Empty", "points": "[]", "days": "[]"})
        self.assertEqual(response.status_code, 302, response.content)
        obj = self.model.objects.get(name="Empty")
        self.assertEqual((obj.points, obj.days), (None, None))

    def test_csv_import(self):
        csv_data = 'name,points,days\nImported,"[[0, 0, 0], [1, 2, 3]]","[""2026-03-01""]"'
        response = self.client.post(
            self._url("customobject_bulk_import"), {"data": csv_data, "format": "csv", "csv_delimiter": ","}
        )
        self.assertEqual(response.status_code, 302, response.content)
        obj = self.model.objects.get(name="Imported")
        self.assertEqual(obj.points, [[0, 0, 0], [1, 2, 3]])
        self.assertEqual(obj.days, [date(2026, 3, 1)])

        csv_data = 'name,points,days\nCoerced,"[[0, 0, 0], [1, 2, 3.5]]",\nEmpty,[],[]'
        response = self.client.post(
            self._url("customobject_bulk_import"), {"data": csv_data, "format": "csv", "csv_delimiter": ","}
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.model.objects.filter(name__in=["Coerced", "Empty"]).exists())

        csv_data = 'name,points,days\nEmpty,[],[]'
        response = self.client.post(
            self._url("customobject_bulk_import"), {"data": csv_data, "format": "csv", "csv_delimiter": ","}
        )
        self.assertEqual(response.status_code, 302, response.content)
        obj = self.model.objects.get(name="Empty")
        self.assertEqual((obj.points, obj.days), (None, None))

    def test_list_and_detail_display(self):
        obj = self.model.objects.create(name="Shown", points=[[0, 0, 0], [10, 0, 5]], days=[date(2026, 1, 1)])
        self.assertContains(self.client.get(self._url("customobject_list")), "(0, 0, 0), (10, 0, 5)")
        response = self.client.get(self._url("customobject", pk=obj.pk))
        self.assertContains(response, "(0, 0, 0), (10, 0, 5)")
        self.assertContains(response, "2026-01-01")

    def test_field_form_shows_array_settings(self):
        from netbox_custom_objects.forms import CustomObjectTypeFieldForm

        # The type choices include this plugin's own field types, not only core's.
        response = self.client.get(
            reverse("plugins:netbox_custom_objects:customobjecttypefield_add") + f"?custom_object_type={self.cot.pk}"
        )
        self.assertContains(response, '<option value="array"')
        self.assertContains(response, '<option value="coordinates"')

        form = CustomObjectTypeFieldForm(initial={
            "custom_object_type": self.cot.pk, "type": "array", "array_element_type": "integer",
        })
        for name in ("array_element_type", "array_tuple_size", "array_min_length", "array_max_length",
                     "validation_minimum", "validation_maximum"):
            self.assertIn(name, form.fields)
        self.assertNotIn("validation_regex", form.fields)
        # Element limits describe each element, not the whole value.
        self.assertEqual(form.fields["validation_minimum"].help_text, "Minimum allowed value for each element")

        form = CustomObjectTypeFieldForm(initial={
            "custom_object_type": self.cot.pk, "type": "array", "array_element_type": "text",
        })
        self.assertIn("validation_regex", form.fields)
        self.assertEqual(form.fields["validation_regex"].help_text, "Regular expression each element must match")
        self.assertNotIn("validation_minimum", form.fields)

        form = CustomObjectTypeFieldForm(initial={"custom_object_type": self.cot.pk, "type": "text"})
        self.assertNotIn("array_element_type", form.fields)

        # An existing array's shape can't be edited.
        form = CustomObjectTypeFieldForm(instance=self.cot.fields.get(name="points"))
        self.assertTrue(form.fields["array_element_type"].disabled)
        self.assertTrue(form.fields["array_tuple_size"].disabled)
        self.assertFalse(form.fields["array_max_length"].disabled)


class ColorFieldViewTest(CustomObjectsTestCase, TestCase):
    """UI behaviour for the color field type."""

    @classmethod
    def setUpTestData(cls):
        cls.cot = CustomObjectType.objects.create(name="Paint", verbose_name_plural="Paints", slug="paints")
        CustomObjectTypeField.objects.create(
            custom_object_type=cls.cot, name="name", type="text", primary=True, required=True
        )
        CustomObjectTypeField.objects.create(custom_object_type=cls.cot, name="shade", type="color")
        cls.model = cls.cot.get_model()

    def setUp(self):
        super().setUp()
        perm = ObjectPermission(name="paint all", actions=["view", "add", "change", "delete"])
        perm.save()
        perm.users.add(self.user)
        perm.object_types.add(ObjectType.objects.get_for_model(self.model))

    def _url(self, action, **kwargs):
        return reverse(
            f"plugins:netbox_custom_objects:customobject{action}",
            kwargs={"custom_object_type": self.cot.slug, **kwargs},
        )

    def test_field_default_uses_the_color_picker(self):
        perm = ObjectPermission(name="paint fields", actions=["add", "change", "view"])
        perm.save()
        perm.users.add(self.user)
        perm.object_types.add(ObjectType.objects.get_for_model(CustomObjectTypeField))
        perm.object_types.add(ObjectType.objects.get_for_model(CustomObjectType))
        add_url = reverse("plugins:netbox_custom_objects:customobjecttypefield_add")
        response = self.client.get(f"{add_url}?custom_object_type={self.cot.pk}&type=color")
        self.assertRegex(response.content.decode(), r'<select name="default"[^>]*color-select')

        data = {
            "custom_object_type": self.cot.pk, "name": "trim", "type": "color", "default": "4caf50",
            "search_weight": 500, "filter_logic": "loose", "weight": 100, "ui_visible": "always",
            "ui_editable": "yes",
        }
        response = self.client.post(f"{add_url}?custom_object_type={self.cot.pk}", data)
        self.assertEqual(response.status_code, 302, response.content)
        trim = self.cot.fields.get(name="trim")
        self.assertEqual(trim.default, "4caf50")

        # The field list shows the default as a swatch, not JSON; other types' defaults stay JSON.
        CustomObjectTypeField.objects.create(custom_object_type=self.cot, name="finish", type="text", default="matte")
        response = self.client.get(reverse("plugins:netbox_custom_objects:customobjecttype_fields", args=[self.cot.pk]))
        self.assertContains(response, '<span class="color-label" style="background-color: #4caf50">&nbsp;</span>')
        self.assertNotContains(response, "&quot;4caf50&quot;")
        self.assertContains(response, "&quot;matte&quot;</pre>")

        # A blank choice clears it.
        response = self.client.post(
            reverse("plugins:netbox_custom_objects:customobjecttypefield_edit", args=[trim.pk]),
            {**data, "default": ""},
        )
        self.assertEqual(response.status_code, 302, response.content)
        trim.refresh_from_db()
        self.assertIsNone(trim.default)

    def test_type_choices_include_plugin_types(self):
        perm = ObjectPermission(name="paint fields", actions=["add"])
        perm.save()
        perm.users.add(self.user)
        perm.object_types.add(ObjectType.objects.get_for_model(CustomObjectTypeField))
        response = self.client.get(
            reverse("plugins:netbox_custom_objects:customobjecttypefield_add") + f"?custom_object_type={self.cot.pk}"
        )
        self.assertContains(response, '<option value="color"')
        self.assertContains(response, '<option value="coordinates"')

    def test_create_and_display(self):
        response = self.client.get(self._url("_add"))
        self.assertContains(response, 'color-select"')

        response = self.client.post(self._url("_add"), {"name": "Red", "shade": "f44336"})
        self.assertEqual(response.status_code, 302)
        obj = self.model.objects.get(name="Red")
        self.assertEqual(obj.shade, "f44336")

        # Rendered as NetBox renders its own color fields: a bare swatch.
        response = self.client.get(obj.get_absolute_url())
        self.assertContains(response, '<span class="badge color-label" style="background-color: #f44336">&nbsp;</span>')
        response = self.client.get(self._url("_list"))
        self.assertContains(response, '<span class="color-label" style="background-color: #f44336">&nbsp;</span>')

    def test_invalid_color_rejected(self):
        response = self.client.post(self._url("_add"), {"name": "Bad", "shade": "#F44336"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Enter a valid hexadecimal RGB color code.")
        self.assertFalse(self.model.objects.filter(name="Bad").exists())

    def test_edit_keeps_a_custom_color(self):
        obj = self.model.objects.create(name="Custom", shade="123456")
        response = self.client.get(self._url("_edit", pk=obj.pk))
        self.assertRegex(response.content.decode(), r'value="123456"\s+selected')

    def test_csv_import_accepts_exported_values(self):
        csv_data = "name,shade\nImported,#00FF00"
        data = {"data": csv_data, "format": "csv", "csv_delimiter": ","}
        response = self.client.post(self._url("_bulk_import"), data)
        self.assertEqual(response.status_code, 302, response.content)
        self.assertEqual(self.model.objects.get(name="Imported").shade, "00ff00")


class MACAddressFieldViewTest(CustomObjectsTestCase, TestCase):
    """UI behaviour for the MAC address field type."""

    @classmethod
    def setUpTestData(cls):
        cls.cot = CustomObjectType.objects.create(name="Port", verbose_name_plural="Ports", slug="ports")
        CustomObjectTypeField.objects.create(
            custom_object_type=cls.cot, name="name", type="text", primary=True, required=True
        )
        CustomObjectTypeField.objects.create(custom_object_type=cls.cot, name="mac", type="mac_address")
        cls.model = cls.cot.get_model()

    def setUp(self):
        super().setUp()
        perm = ObjectPermission(name="port all", actions=["view", "add", "change", "delete"])
        perm.save()
        perm.users.add(self.user)
        perm.object_types.add(ObjectType.objects.get_for_model(self.model))
        perm.object_types.add(ObjectType.objects.get_for_model(CustomObjectTypeField))
        perm.object_types.add(ObjectType.objects.get_for_model(CustomObjectType))

    def _url(self, action, **kwargs):
        return reverse(
            f"plugins:netbox_custom_objects:customobject{action}",
            kwargs={"custom_object_type": self.cot.slug, **kwargs},
        )

    def test_create_and_display(self):
        response = self.client.post(self._url("_add"), {"name": "eth0", "mac": "00-1a-2b-3c-4d-5e"})
        self.assertEqual(response.status_code, 302)
        obj = self.model.objects.get(name="eth0")
        self.assertEqual(str(obj.mac), "00:1A:2B:3C:4D:5E")
        for url in (obj.get_absolute_url(), self._url("_list"), self._url("_edit", pk=obj.pk)):
            with self.subTest(url=url):
                self.assertContains(self.client.get(url), "00:1A:2B:3C:4D:5E")

    def test_invalid_rejected(self):
        response = self.client.post(self._url("_add"), {"name": "bad", "mac": "00:1a:2b:3c:4d"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Invalid MAC address format")
        self.assertFalse(self.model.objects.filter(name="bad").exists())

    def test_csv_import(self):
        data = {"data": "name,mac\neth1,001a.2b3c.4d5e", "format": "csv", "csv_delimiter": ","}
        response = self.client.post(self._url("_bulk_import"), data)
        self.assertEqual(response.status_code, 302, response.content)
        self.assertEqual(str(self.model.objects.get(name="eth1").mac), "00:1A:2B:3C:4D:5E")

    def test_field_default_is_plain_text(self):
        add_url = reverse("plugins:netbox_custom_objects:customobjecttypefield_add")
        response = self.client.get(f"{add_url}?custom_object_type={self.cot.pk}&type=mac_address")
        self.assertRegex(response.content.decode(), r'<input type="text" name="default"')

        data = {
            "custom_object_type": self.cot.pk, "name": "uplink", "type": "mac_address", "default": "aabb.ccdd.eeff",
            "search_weight": 500, "filter_logic": "loose", "weight": 100, "ui_visible": "always",
            "ui_editable": "yes",
        }
        response = self.client.post(f"{add_url}?custom_object_type={self.cot.pk}", data)
        self.assertEqual(response.status_code, 302, response.content)
        self.assertEqual(self.cot.fields.get(name="uplink").default, "AA:BB:CC:DD:EE:FF")

        response = self.client.get(reverse("plugins:netbox_custom_objects:customobjecttype_fields", args=[self.cot.pk]))
        self.assertContains(response, "AA:BB:CC:DD:EE:FF")
        self.assertNotContains(response, "&quot;AA:BB:CC:DD:EE:FF&quot;")

        bad = {**data, "name": "bad", "default": "x"}
        response = self.client.post(f"{add_url}?custom_object_type={self.cot.pk}", bad)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Invalid MAC address format")


class URLFieldLinkTitleViewTest(CustomObjectsTestCase, TestCase):
    """UI form behaviour for the url field type's link-title support."""

    @classmethod
    def setUpTestData(cls):
        cls.cot = CustomObjectType.objects.create(
            name="LinkView",
            verbose_name_plural="Link Views",
            slug="link-views",
        )
        CustomObjectTypeField.objects.create(
            custom_object_type=cls.cot, name="name", type="text", primary=True, required=True
        )
        CustomObjectTypeField.objects.create(
            custom_object_type=cls.cot, name="website", type="url"
        )
        cls.model = cls.cot.get_model()

    def setUp(self):
        super().setUp()
        perm = ObjectPermission(
            name="link view all", actions=["view", "add", "change", "delete"]
        )
        perm.save()
        perm.users.add(self.user)
        perm.object_types.add(ObjectType.objects.get_for_model(self.model))

    def _add_url(self):
        return reverse(
            "plugins:netbox_custom_objects:customobject_add",
            kwargs={"custom_object_type": self.cot.slug},
        )

    def _list_url(self):
        return reverse(
            "plugins:netbox_custom_objects:customobject_list",
            kwargs={"custom_object_type": self.cot.slug},
        )

    def test_add_form_renders_url_and_title_inputs(self):
        response = self.client.get(self._add_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "website")
        self.assertContains(response, "website_title")

    def test_create_valid_url_with_title(self):
        data = {
            "name": "Box",
            "website": "https://example.com/",
            "website_title": "Example Site",
        }
        response = self.client.post(self._add_url(), data)
        self.assertEqual(response.status_code, 302, getattr(response, "content", b""))
        obj = self.model.objects.get(name="Box")
        self.assertEqual(obj.website, "https://example.com/")
        self.assertEqual(obj.website_title, "Example Site")

    def test_create_valid_url_with_no_title(self):
        """A URL with no title is accepted (no both-required rule, unlike coordinates)."""
        data = {"name": "Box", "website": "https://example.com/"}
        response = self.client.post(self._add_url(), data)
        self.assertEqual(response.status_code, 302, getattr(response, "content", b""))
        obj = self.model.objects.get(name="Box")
        self.assertEqual(obj.website, "https://example.com/")
        self.assertEqual(obj.website_title, "")

    def test_list_view_renders_title_as_link_text(self):
        """
        The list view must render the same title-as-link-text HTML as the detail
        page, rather than the raw URL or two separate columns for the URL and
        title -- keeps the list view consistent with the detail view.
        """
        self.model.objects.create(name="Titled", website="https://example.com/", website_title="Example Site")
        response = self.client.get(self._list_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<a href="https://example.com/">Example Site</a>', html=False)
        self.assertNotContains(response, ">https://example.com/<")

    def test_list_view_falls_back_to_url_when_no_title(self):
        self.model.objects.create(name="Untitled", website="https://example.org/")
        response = self.client.get(self._list_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<a href="https://example.org/">https://example.org/</a>', html=False)

    def test_list_view_shows_placeholder_when_url_unset(self):
        """A title with no URL renders the standard empty-field placeholder, not a bare title."""
        self.model.objects.create(name="TitleOnly", website="", website_title="Orphan Title")
        response = self.client.get(self._list_url())
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Orphan Title")


class ValidationRegexViewTest(CustomObjectsTestCase, TestCase):
    """The standard add/edit form must enforce validation_regex (via the UI form field)."""

    @classmethod
    def setUpTestData(cls):
        cls.cot = CustomObjectType.objects.create(
            name="RegexView", verbose_name_plural="Regex Views", slug="regex-views",
        )
        CustomObjectTypeField.objects.create(
            custom_object_type=cls.cot, name="name", type="text", primary=True, required=True,
        )
        for name, field_type, regex in (
            ("code", "text", r"^[A-Z]{3}$"),
            ("notes", "longtext", r"^ok"),
            ("website", "url", r"^https://"),
        ):
            CustomObjectTypeField.objects.create(
                custom_object_type=cls.cot, name=name, type=field_type, validation_regex=regex,
            )
        cls.model = cls.cot.get_model()

    def setUp(self):
        super().setUp()
        perm = ObjectPermission(name="regex view all", actions=["view", "add", "change", "delete"])
        perm.save()
        perm.users.add(self.user)
        perm.object_types.add(ObjectType.objects.get_for_model(self.model))

    def _add_url(self):
        return reverse(
            "plugins:netbox_custom_objects:customobject_add",
            kwargs={"custom_object_type": self.cot.slug},
        )

    def test_add_view_rejects_values_not_matching_regex(self):
        for field_name, value, regex in (
            ("code", "abc", "^[A-Z]{3}$"),
            ("notes", "not ok", "^ok"),
            ("website", "http://example.com", "^https://"),
        ):
            with self.subTest(field=field_name):
                response = self.client.post(self._add_url(), {"name": "Box", field_name: value})
                # Re-rendered form, not a redirect
                self.assertEqual(response.status_code, 200)
                self.assertIn(field_name, response.context["form"].errors)
                # The UI message shows the regex in a <code> tag, and only once
                # (the model-level validator must not add a duplicate error).
                self.assertContains(response, f"<code>{regex}</code>", count=1, html=False)
        self.assertFalse(self.model.objects.exists())

    def test_add_view_accepts_values_matching_regex(self):
        data = {"name": "Box", "code": "ABC", "notes": "ok then", "website": "https://example.com"}
        response = self.client.post(self._add_url(), data)
        self.assertEqual(response.status_code, 302, getattr(response, "content", b""))
        obj = self.model.objects.get(name="Box")
        self.assertEqual(obj.code, "ABC")
        self.assertEqual(obj.website, "https://example.com")

    def test_edit_view_rejects_value_not_matching_regex(self):
        instance = self.model.objects.create(name="Box", code="ABC")
        url = reverse(
            "plugins:netbox_custom_objects:customobject_edit",
            kwargs={"pk": instance.pk, "custom_object_type": self.cot.slug},
        )
        response = self.client.post(url, {"name": "Box", "code": "abcd"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("code", response.context["form"].errors)
        instance.refresh_from_db()
        self.assertEqual(instance.code, "ABC")


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
    """Config context tab on custom object instances."""

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


class FieldDeleteViewTestCase(CustomObjectsTestCase, TestCase):
    """The field delete page warns how many objects hold a value for the field."""

    def setUp(self):
        super().setUp()
        self.user.is_superuser = True
        self.user.save()
        self.cot = self.create_custom_object_type(name="widget", slug="widgets")
        self.create_custom_object_type_field(self.cot, name="name", label="Name", type="text", primary=True)

    def _url(self, field):
        return reverse("plugins:netbox_custom_objects:customobjecttypefield_delete", kwargs={"pk": field.pk})

    def assertWarnsOf(self, response, count):
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["num_dependent_objects"], count)
        self.assertContains(response, f"in use on {count} Custom Objects")

    def test_url_field_counts_objects_with_only_a_title(self):
        field = self.create_custom_object_type_field(self.cot, name="link", label="Link", type="url")
        model = self.cot.get_model()
        model.objects.create(name="url", link="https://example.com")
        model.objects.create(name="title", link_title="Docs")
        model.objects.create(name="neither")
        self.assertWarnsOf(self.client.get(self._url(field)), 2)

    def test_polymorphic_multiobject_field_counts_each_object_once(self):
        field = self.create_polymorphic_field(
            self.cot, [ObjectType.objects.get_for_model(Site)], name="sites", type="multiobject",
        )
        model = self.cot.get_model()
        first, second = Site.objects.create(name="A", slug="a"), Site.objects.create(name="B", slug="b")
        model.objects.create(name="both").sites.set([first, second])
        model.objects.create(name="one").sites.set([first])
        model.objects.create(name="none")
        self.assertWarnsOf(self.client.get(self._url(field)), 2)

    def test_htmx_request_renders_modal(self):
        field = self.create_custom_object_type_field(self.cot, name="code", label="Code", type="text")
        self.cot.get_model().objects.create(name="coded", code="X1")
        response = self.client.get(self._url(field), HTTP_HX_REQUEST="true")
        self.assertTemplateUsed(response, "netbox_custom_objects/htmx/delete_form.html")
        self.assertTemplateNotUsed(response, "netbox_custom_objects/field_delete.html")
        self.assertWarnsOf(response, 1)

    def test_unused_field_has_no_warning(self):
        field = self.create_custom_object_type_field(self.cot, name="code", label="Code", type="text")
        self.cot.get_model().objects.create(name="uncoded")
        response = self.client.get(self._url(field))
        self.assertEqual(response.context["num_dependent_objects"], 0)
        self.assertNotContains(response, "in use on")


class ProtectedObjectDeleteViewTestCase(CustomObjectsTestCase, TestCase):
    """A custom object referenced through a Protect field can't be deleted from the UI."""

    def setUp(self):
        super().setUp()
        self.user.is_superuser = True
        self.user.save()
        rack_cot = self.create_custom_object_type(name="rack", slug="racks")
        self.create_custom_object_type_field(rack_cot, name="name", label="Name", type="text", primary=True)
        mount_cot = self.create_custom_object_type(name="mount", slug="mounts")
        self.create_custom_object_type_field(mount_cot, name="name", label="Name", type="text", primary=True)
        self.create_custom_object_type_field(
            mount_cot, name="rack", label="Rack", type="object",
            related_object_type=ObjectType.objects.get_for_model(rack_cot.get_model()),
            on_delete_behavior=ObjectFieldOnDeleteChoices.PROTECT,
        )
        # Generate the referencing model before the target's (see CustomObjectType.get_model).
        mount_model = mount_cot.get_model()
        rack_cot.refresh_from_db()
        self.rack_model = rack_cot.get_model()
        self.rack = self.rack_model.objects.create(name="R1")
        mount_model.objects.create(name="M1", rack=self.rack)
        self.url = reverse(
            "plugins:netbox_custom_objects:customobject_delete",
            kwargs={"custom_object_type": "racks", "pk": self.rack.pk},
        )

    def test_delete_page_redirects_to_object(self):
        response = self.client.get(self.url, follow=True)
        self.assertRedirects(response, self.rack.get_absolute_url())
        self.assertContains(response, "M1")

    def test_htmx_delete_redirects_to_object(self):
        response = self.client.get(self.url, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["HX-Redirect"], self.rack.get_absolute_url())

    def test_delete_is_refused(self):
        response = self.client.post(self.url, {"confirm": True})
        self.assertRedirects(response, self.rack.get_absolute_url(), fetch_redirect_response=False)
        self.assertTrue(self.rack_model.objects.filter(pk=self.rack.pk).exists())


class QuickAddConstraintTestCase(CustomObjectsTestCase, TestCase):
    """Quick add refuses an object outside the user's add constraint."""

    def setUp(self):
        super().setUp()
        self.cot = self.create_custom_object_type(name="target", slug="targets")
        self.create_custom_object_type_field(self.cot, name="name", label="Name", type="text", primary=True)
        self.model = self.cot.get_model()
        perm = ObjectPermission.objects.create(name="add allowed", actions=["add"], constraints={"name": "allowed"})
        perm.users.add(self.user)
        perm.object_types.add(ObjectType.objects.get_for_model(self.model))
        self.url = reverse("plugins:netbox_custom_objects:customobject_add", kwargs={"custom_object_type": "targets"})

    def _quick_add(self, name):
        return self.client.post(f"{self.url}?_quickadd=True", {"quickadd-name": name, "_quickadd": ""})

    def test_object_inside_constraint_is_created(self):
        response = self._quick_add("allowed")
        self.assertTemplateUsed(response, "htmx/quick_add_created.html")
        self.assertTrue(self.model.objects.filter(name="allowed").exists())

    def test_object_outside_constraint_is_refused(self):
        response = self._quick_add("other")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "netbox_custom_objects/htmx/co_quick_add.html")
        self.assertIn("permissions violation", " ".join(response.context["form"].non_field_errors()))
        self.assertFalse(self.model.objects.exists())


class CustomObjectJournalTestCase(CustomObjectsTestCase, TestCase):
    """Journal entries are added from a custom object's Journal tab."""

    def setUp(self):
        super().setUp()
        self.cot = self.create_custom_object_type(name="server", slug="servers")
        self.create_custom_object_type_field(self.cot, name="name", label="Name", type="text", primary=True)
        self.model = self.cot.get_model()
        self.obj = self.model.objects.create(name="S1")
        self.content_type = ContentType.objects.get_for_model(self.model)
        self.journal_url = reverse(
            "plugins:netbox_custom_objects:customobject_journal",
            kwargs={"custom_object_type": "servers", "pk": self.obj.pk},
        )
        self.add_url = reverse("plugins:netbox_custom_objects:custom_journalentry_add")

    def _grant(self, model, actions):
        perm = ObjectPermission.objects.create(name=f"{model._meta.model_name} {actions}", actions=actions)
        perm.users.add(self.user)
        perm.object_types.add(ObjectType.objects.get_for_model(model))

    def _add_entry(self):
        return self.client.post(self.add_url, {
            "assigned_object_type": self.content_type.pk,
            "assigned_object_id": self.obj.pk,
            "kind": "info",
            "comments": "Checked the fans",
        })

    def test_add_entry_returns_to_journal(self):
        self._grant(self.model, ["view"])
        self._grant(JournalEntry, ["view", "add"])
        response = self.client.get(self.journal_url)
        self.assertContains(response, f'action="{self.add_url}"')

        response = self._add_entry()
        self.assertRedirects(response, self.journal_url, fetch_redirect_response=False)
        entry = JournalEntry.objects.get()
        self.assertEqual((entry.assigned_object, entry.created_by), (self.obj, self.user))
        self.assertContains(self.client.get(self.journal_url), "Checked the fans")

    def test_no_form_without_add_permission(self):
        self._grant(self.model, ["view"])
        self._grant(JournalEntry, ["view"])
        response = self.client.get(self.journal_url)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["form"])
        self.assertNotContains(response, f'action="{self.add_url}"')
        self.assertEqual(self._add_entry().status_code, 403)
        self.assertFalse(JournalEntry.objects.exists())


class UnknownTypeSlugTestCase(CustomObjectsTestCase, TestCase):
    """The custom object views return 404 for a Custom Object Type slug that doesn't exist."""

    def test_unknown_slug_is_404(self):
        self.user.is_superuser = True
        self.user.save()
        for action in ("list", "add", "bulk_edit", "bulk_delete", "bulk_import"):
            url = reverse(f"plugins:netbox_custom_objects:customobject_{action}", kwargs={"custom_object_type": "nope"})
            for method in ("get", "post"):
                with self.subTest(action=action, method=method):
                    self.assertEqual(getattr(self.client, method)(url).status_code, 404)


class FieldPrimaryTestCase(CustomObjectsTestCase, TestCase):
    """Making a field primary through the field form demotes the type's existing primary field."""

    def setUp(self):
        super().setUp()
        self.user.is_superuser = True
        self.user.save()
        self.cot = self.create_custom_object_type(name="zone", slug="zones")
        self.name = self.create_custom_object_type_field(self.cot, name="name", label="Name", type="text", primary=True)
        self.code = self.create_custom_object_type_field(self.cot, name="code", label="Code", type="text")

    def _post(self, url, name, primary=True):
        data = {
            "custom_object_type": self.cot.pk, "name": name, "label": name.title(), "type": "text",
            "ui_visible": "always", "ui_editable": "yes", "filter_logic": "loose", "search_weight": 500, "weight": 100,
        }
        if primary:
            data["primary"] = "on"
        return self.client.post(url, data)

    def _primaries(self):
        return list(self.cot.fields.filter(primary=True).values_list("name", flat=True))

    def test_new_primary_field_demotes_the_old_one(self):
        response = self._post(reverse("plugins:netbox_custom_objects:customobjecttypefield_add"), "title")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._primaries(), ["title"])

    def test_demotion_is_change_logged(self):
        # Logged like any other change, so a branch merge replays the demotion too.
        self._post(reverse("plugins:netbox_custom_objects:customobjecttypefield_add"), "title")
        change = ObjectChange.objects.filter(
            changed_object_type=ContentType.objects.get_for_model(CustomObjectTypeField),
            changed_object_id=self.name.pk,
        ).latest("pk")
        self.assertEqual(change.action, ObjectChangeActionChoices.ACTION_UPDATE)
        self.assertEqual((change.prechange_data["primary"], change.postchange_data["primary"]), (True, False))

    def test_failed_submission_keeps_the_primary_field(self):
        # A duplicate name fails validation after the primary field has been cleaned.
        response = self._post(reverse("plugins:netbox_custom_objects:customobjecttypefield_add"), "code")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors)
        self.assertEqual(self._primaries(), ["name"])

    def test_editing_the_primary_field_keeps_it_primary(self):
        url = reverse("plugins:netbox_custom_objects:customobjecttypefield_edit", kwargs={"pk": self.name.pk})
        self.assertEqual(self._post(url, "name").status_code, 302)
        self.assertEqual(self._primaries(), ["name"])

    def test_editing_another_field_keeps_the_primary_field(self):
        url = reverse("plugins:netbox_custom_objects:customobjecttypefield_edit", kwargs={"pk": self.code.pk})
        self.assertEqual(self._post(url, "code", primary=False).status_code, 302)
        self.assertEqual(self._primaries(), ["name"])
