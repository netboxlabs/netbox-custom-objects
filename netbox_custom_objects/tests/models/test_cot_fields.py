"""
Tests for the CustomObjectTypeField model.
"""

from django.apps import apps as django_apps
from django.core.exceptions import ValidationError
from django.db.utils import ProgrammingError
from django.test import TestCase, tag
from django.urls import reverse

from core.models import ObjectType
from dcim.models import Site
from extras.models import CustomFieldChoiceSet
from netbox_custom_objects.api.serializers import get_serializer_class
from netbox_custom_objects.constants import APP_LABEL
from netbox_custom_objects.models import CustomObjectType, CustomObjectTypeField

from ..base import CustomObjectsTestCase


class CustomObjectTypeFieldTestCase(CustomObjectsTestCase, TestCase):
    """Test cases for CustomObjectTypeField model."""

    def setUp(self):
        """Set up test data."""
        super().setUp()
        self.custom_object_type = self.create_custom_object_type(name='TestObject')

    def test_custom_object_type_field_creation(self):
        """Test creating a CustomObjectTypeField."""
        field = self.create_custom_object_type_field(
            self.custom_object_type,
            name='test_field',
            label='Test Field',
            type='text',
            description='A test field',
            required=True,
            unique=True,
        )

        self.assertEqual(field.name, 'test_field')
        self.assertEqual(field.label, 'Test Field')
        self.assertEqual(field.type, 'text')
        self.assertEqual(field.description, 'A test field')
        self.assertTrue(field.required)
        self.assertTrue(field.unique)
        self.assertEqual(str(field), 'Test Field')

    def test_custom_object_type_field_name_validation(self):
        """Test field name validation."""
        invalid_names = [
            'test-field',  # hyphen not allowed
            'test__field',  # double underscore not allowed
            '_test_field',  # leading underscore not allowed
            'test_field_',  # trailing underscore not allowed
        ]
        for invalid_name in invalid_names:
            with self.assertRaises(ValidationError, msg=f'Expected ValidationError for name={invalid_name!r}'):
                field = CustomObjectTypeField(
                    custom_object_type=self.custom_object_type,
                    name=invalid_name,
                    type='text',
                )
                field.full_clean()

    def test_custom_object_type_field_reserved_name_rejected(self):
        """Field names in RESERVED_FIELD_NAMES must be rejected with ValidationError."""
        for reserved in ('owner', 'tags', 'id', 'created', 'last_updated', 'local_context_data'):
            with self.assertRaises(ValidationError, msg=f'Expected ValidationError for reserved name={reserved!r}'):
                field = CustomObjectTypeField(
                    custom_object_type=self.custom_object_type,
                    name=reserved,
                    type='text',
                )
                field.full_clean()

    def test_from_db_populates_original_snapshot(self):
        """
        Loading a field from the database (e.g. via a plain queryset fetch) must
        succeed and populate .original from the row's own values -- this is what
        save() diffs against to detect renames/type changes. Also guards
        from_db()'s signature against Django versions that call it with extra
        keyword arguments (e.g. Django 6.1's fetch_mode).
        """
        created = self.create_custom_object_type_field(
            self.custom_object_type,
            name='test_field',
            label='Test Field',
            type='text',
        )

        fetched = CustomObjectTypeField.objects.get(pk=created.pk)

        self.assertEqual(fetched.original.name, 'test_field')
        self.assertEqual(fetched.original.label, 'Test Field')

    def test_required_toggle_rejected_when_existing_row_is_blank(self):
        """Toggling optional -> required must be rejected if an existing row is blank.

        Otherwise full_clean() (form submission, API PATCH, branch merge) would
        reject that row on its very next save, even for an edit unrelated to
        this field. Mirrors the analogous non-unique-values pre-flight check.
        """
        field = self.create_custom_object_type_field(
            self.custom_object_type,
            name='note',
            type='text',
            required=False,
        )
        model = self.custom_object_type.get_model()
        model.objects.create()

        field = CustomObjectTypeField.objects.get(pk=field.pk)
        field.required = True
        with self.assertRaises(ValidationError):
            field.full_clean()

    def test_required_toggle_allowed_when_no_blank_rows(self):
        """Toggling optional -> required succeeds when no existing row is blank."""
        field = self.create_custom_object_type_field(
            self.custom_object_type,
            name='note',
            type='text',
            required=False,
        )
        model = self.custom_object_type.get_model()
        model.objects.create(note='has a value')

        field = CustomObjectTypeField.objects.get(pk=field.pk)
        field.required = True
        field.full_clean()  # must not raise

    def test_required_toggle_rejected_for_coordinates_with_one_half_blank(self):
        """A coordinates field's latitude/longitude halves are checked independently."""
        field = self.create_custom_object_type_field(
            self.custom_object_type,
            name='geo',
            type='coordinates',
            required=False,
        )
        model = self.custom_object_type.get_model()
        model.objects.create(geo_latitude=10, geo_longitude=None)

        field = CustomObjectTypeField.objects.get(pk=field.pk)
        field.required = True
        with self.assertRaises(ValidationError):
            field.full_clean()

    def test_required_toggle_does_not_crash_for_polymorphic_relationship_fields(self):
        """Polymorphic object/multiobject fields must not crash on toggle.

        Their model field(s) are not a real, directly queryable Django Field at all
        (a polymorphic object field's GenericForeignKey entry; a polymorphic
        multiobject field's PolymorphicM2MDescriptor, which has no .blank attribute)
        -- neither is checked by the required-toggle pre-flight check, but they must
        be skipped cleanly rather than raising AttributeError/FieldError. Plain
        object/multiobject fields, by contrast, ARE checked (see
        test_required_toggle_rejected_when_existing_object_row_is_blank et al).
        """
        device_ot = self.get_device_object_type()
        site_ot = self.get_site_object_type()

        poly_object = self.create_polymorphic_field(
            self.custom_object_type,
            related_object_types=[device_ot, site_ot],
            name='poly_dev',
            type='object',
            required=False,
        )
        poly_multiobject = self.create_polymorphic_field(
            self.custom_object_type,
            related_object_types=[device_ot, site_ot],
            name='poly_devs',
            type='multiobject',
            required=False,
        )

        for field in (poly_object, poly_multiobject):
            with self.subTest(field=field.name):
                field = CustomObjectTypeField.objects.get(pk=field.pk)
                field.required = True
                field.full_clean()  # must not raise

    def test_required_toggle_rejected_when_existing_object_row_is_blank(self):
        """A plain object field's FK is checked by the required-toggle
        pre-flight check, same as every scalar type."""
        site_ot = self.get_site_object_type()
        field = self.create_custom_object_type_field(
            self.custom_object_type,
            name='site',
            type='object',
            related_object_type=site_ot,
            required=False,
        )
        model = self.custom_object_type.get_model()
        model.objects.create()

        field = CustomObjectTypeField.objects.get(pk=field.pk)
        field.required = True
        with self.assertRaises(ValidationError):
            field.full_clean()

    def test_required_toggle_allowed_when_no_blank_object_rows(self):
        """Toggling a plain object field to required succeeds when every existing
        row already has a value."""
        site_ot = self.get_site_object_type()
        site = Site.objects.create(name='Req Toggle Site', slug='req-toggle-site')
        field = self.create_custom_object_type_field(
            self.custom_object_type,
            name='site',
            type='object',
            related_object_type=site_ot,
            required=False,
        )
        model = self.custom_object_type.get_model()
        model.objects.create(site=site)

        field = CustomObjectTypeField.objects.get(pk=field.pk)
        field.required = True
        field.full_clean()  # must not raise

    def test_required_toggle_rejected_when_existing_multiobject_row_is_blank(self):
        """A plain multiobject field's M2M is also checked by the required-toggle
        pre-flight check: even though full_clean() can never validate an M2M
        field, the pre-flight check queries existing data directly via
        values_list(), which still correctly catches a blank row."""
        site_ot = self.get_site_object_type()
        field = self.create_custom_object_type_field(
            self.custom_object_type,
            name='sites',
            type='multiobject',
            related_object_type=site_ot,
            required=False,
        )
        model = self.custom_object_type.get_model()
        model.objects.create()

        field = CustomObjectTypeField.objects.get(pk=field.pk)
        field.required = True
        with self.assertRaises(ValidationError):
            field.full_clean()

    def test_required_toggle_rejected_when_one_of_several_multiobject_rows_is_blank(self):
        """The pre-flight check's values_list() query must use a LEFT OUTER JOIN,
        not an INNER JOIN, across the M2M -- otherwise a blank row would be
        silently excluded from the result rather than surfaced as (None,), and a
        blank row sitting alongside filled ones would slip through undetected."""
        site_ot = self.get_site_object_type()
        site = Site.objects.create(name='Req Toggle Site Mixed', slug='req-toggle-site-mixed')
        field = self.create_custom_object_type_field(
            self.custom_object_type,
            name='sites',
            type='multiobject',
            related_object_type=site_ot,
            required=False,
        )
        model = self.custom_object_type.get_model()
        filled = model.objects.create()
        getattr(filled, 'sites').set([site])
        model.objects.create()  # blank, alongside the filled row above

        field = CustomObjectTypeField.objects.get(pk=field.pk)
        field.required = True
        with self.assertRaises(ValidationError):
            field.full_clean()

    def test_required_toggle_allowed_when_no_blank_multiobject_rows(self):
        """Toggling a plain multiobject field to required succeeds when every
        existing row already has at least one related object."""
        site_ot = self.get_site_object_type()
        site = Site.objects.create(name='Req Toggle Site M2M', slug='req-toggle-site-m2m')
        field = self.create_custom_object_type_field(
            self.custom_object_type,
            name='sites',
            type='multiobject',
            related_object_type=site_ot,
            required=False,
        )
        model = self.custom_object_type.get_model()
        instance = model.objects.create()
        getattr(instance, 'sites').set([site])

        field = CustomObjectTypeField.objects.get(pk=field.pk)
        field.required = True
        field.full_clean()  # must not raise

    def test_custom_object_type_field_unique_name_per_type(self):
        """Test that field names must be unique within a custom object type."""
        self.create_custom_object_type_field(self.custom_object_type, name='test_field', type='text')

        # Should not allow duplicate field name within the same type
        with self.assertRaises(Exception):
            self.create_custom_object_type_field(self.custom_object_type, name='test_field', type='integer')

    def test_custom_object_type_field_validation_regex_text_only(self):
        """Test that regex validation can only be set on text fields."""
        # Should work for text field
        field = CustomObjectTypeField(
            custom_object_type=self.custom_object_type, name='test_field', type='text', validation_regex='^[A-Z]+$'
        )
        field.full_clean()

        # Should fail for integer field
        with self.assertRaises(ValidationError):
            field = CustomObjectTypeField(
                custom_object_type=self.custom_object_type,
                name='test_field2',
                type='integer',
                validation_regex='^[A-Z]+$',
            )
            field.full_clean()

    def test_custom_object_type_field_default_matches_regex_like_objects(self):
        """
        A default is checked against validation_regex with the same search semantics
        RegexValidator applies to object values, so an unanchored regex that accepts an
        object value also accepts it as the field's default.
        """
        field = CustomObjectTypeField(
            custom_object_type=self.custom_object_type,
            name='site',
            type='url',
            validation_regex=r'example\.com',
            default='https://example.com',
        )
        field.full_clean()

        field.default = 'https://example.org'
        with self.assertRaises(ValidationError) as cm:
            field.full_clean()
        self.assertIn('default', cm.exception.error_dict)

    def test_custom_object_type_field_boolean_unique_validation(self):
        """Test that boolean fields cannot be unique."""
        with self.assertRaises(ValidationError):
            field = CustomObjectTypeField(
                custom_object_type=self.custom_object_type, name='test_field', type='boolean', unique=True
            )
            field.full_clean()

    def test_custom_object_type_field_choice_set_validation(self):
        """Test choice set validation for select fields."""
        choice_set = self.create_choice_set()

        # Should require choice set for select field
        with self.assertRaises(ValidationError):
            field = CustomObjectTypeField(custom_object_type=self.custom_object_type, name='test_field', type='select')
            field.full_clean()

        # Should not allow choice set for non-select field
        with self.assertRaises(ValidationError):
            field = CustomObjectTypeField(
                custom_object_type=self.custom_object_type, name='test_field', type='text', choice_set=choice_set
            )
            field.full_clean()

    def test_custom_object_type_field_object_type_validation(self):
        """Test object type validation for object/multiobject fields."""
        # Should require related_object_type for object field
        with self.assertRaises(ValidationError):
            field = CustomObjectTypeField(custom_object_type=self.custom_object_type, name='test_field', type='object')
            field.full_clean()

        # Should not allow related_object_type for non-object field
        device_ct = self.get_device_object_type()
        with self.assertRaises(ValidationError):
            field = CustomObjectTypeField(
                custom_object_type=self.custom_object_type,
                name='test_field',
                type='text',
                related_object_type=device_ct,
            )
            field.full_clean()

    def test_custom_object_type_field_get_absolute_url(self):
        """
        Test get_absolute_url method.
        Note: get_absolute_url for CustomObjectTypeField returns the absolute_url of the COT, because fields
        are not exposed individually in the UI or API.
        """
        field = self.create_custom_object_type_field(self.custom_object_type, name='test_field', type='text')
        expected_url = reverse('plugins:netbox_custom_objects:customobjecttype', args=[field.custom_object_type.pk])
        self.assertEqual(field.get_absolute_url(), expected_url)

    def test_custom_object_type_field_validation_methods(self):
        """Test field validation methods."""
        field = self.create_custom_object_type_field(
            self.custom_object_type, name='test_field', type='integer', validation_minimum=0, validation_maximum=100
        )

        # Test valid value
        field.validate(50)

        # Test value below minimum
        with self.assertRaises(ValidationError):
            field.validate(-1)

        # Test value above maximum
        with self.assertRaises(ValidationError):
            field.validate(101)

    def test_custom_object_type_field_serialization(self):
        """Test field serialization and deserialization."""
        field = self.create_custom_object_type_field(self.custom_object_type, name='test_field', type='text')

        test_value = 'test value'
        serialized = field.serialize(test_value)
        deserialized = field.deserialize(serialized)

        self.assertEqual(deserialized, test_value)


class RelatedNameTestCase(CustomObjectsTestCase, TestCase):
    """Tests for the related_name field on Object and MultiObject fields."""

    def setUp(self):
        super().setUp()
        # "SLB" is the target (reverse side); "Certificate" holds the forward relation.
        self.slb_cot = self.create_custom_object_type(name='SLB', slug='slb')
        self.create_custom_object_type_field(self.slb_cot, name='name', label='Name', type='text', primary=True)
        self.slb_object_type = ObjectType.objects.get(
            app_label='netbox_custom_objects',
            model=self.slb_cot.get_table_model_name(self.slb_cot.id).lower(),
        )

        self.cert_cot = self.create_custom_object_type(name='Certificate', slug='certificate')
        self.create_custom_object_type_field(self.cert_cot, name='name', label='Name', type='text', primary=True)

    # ------------------------------------------------------------------ #
    # Validation                                                           #
    # ------------------------------------------------------------------ #

    def test_related_name_rejected_on_non_object_field(self):
        """related_name cannot be set on non-object field types."""
        for field_type in ('text', 'integer', 'boolean', 'date'):
            with self.subTest(field_type=field_type):
                field = CustomObjectTypeField(
                    custom_object_type=self.cert_cot,
                    name='some_field',
                    type=field_type,
                    related_name='my_reverse',
                )
                with self.assertRaises(ValidationError) as cm:
                    field.full_clean()
                self.assertIn('related_name', cm.exception.message_dict)

    def test_related_name_invalid_characters_rejected(self):
        """related_name must contain only lowercase alphanumeric characters and underscores."""
        for bad_value in ('My-Name', 'has space', 'UPPER', 'has--double', 'has__double'):
            with self.subTest(value=bad_value):
                field = CustomObjectTypeField(
                    custom_object_type=self.cert_cot,
                    name='slb',
                    type='object',
                    related_object_type=self.slb_object_type,
                    related_name=bad_value,
                )
                with self.assertRaises(ValidationError):
                    field.full_clean()

    def test_duplicate_related_name_same_target_rejected(self):
        """Two fields with the same related_name pointing at the same related_object_type raise ValidationError."""
        self.create_custom_object_type_field(
            self.cert_cot,
            name='slb',
            type='object',
            related_object_type=self.slb_object_type,
            related_name='certificates',
        )
        field = CustomObjectTypeField(
            custom_object_type=self.cert_cot,
            name='slb2',
            type='object',
            related_object_type=self.slb_object_type,
            related_name='certificates',
        )
        with self.assertRaises(ValidationError) as cm:
            field.full_clean()
        self.assertIn('related_name', cm.exception.message_dict)

    def test_same_related_name_different_targets_allowed(self):
        """The same related_name is allowed when the related_object_type differs."""
        site_ct = self.get_site_object_type()
        self.create_custom_object_type_field(
            self.cert_cot,
            name='slb',
            type='object',
            related_object_type=self.slb_object_type,
            related_name='certificates',
        )
        # Same related_name but targeting a different model — should not raise.
        field = CustomObjectTypeField(
            custom_object_type=self.cert_cot,
            name='site',
            type='object',
            related_object_type=site_ct,
            related_name='certificates',
        )
        field.full_clean()  # Should not raise.

    def test_blank_related_name_allows_multiple_fields_same_target(self):
        """Multiple fields with no related_name targeting the same object type are allowed."""
        self.create_custom_object_type_field(
            self.cert_cot,
            name='slb',
            type='object',
            related_object_type=self.slb_object_type,
        )
        field = CustomObjectTypeField(
            custom_object_type=self.cert_cot,
            name='slb2',
            type='object',
            related_object_type=self.slb_object_type,
        )
        field.full_clean()  # blank related_name is excluded from the uniqueness constraint.

    @tag('regression')
    def test_related_name_colliding_with_target_model_attribute_rejected(self):
        """related_name must not overwrite an existing attribute on a core target model."""
        site_ct = self.get_site_object_type()
        original_save = Site.save
        # A method, a concrete field, and a core NetBox reverse accessor.
        for field_type in ('object', 'multiobject'):
            for name in ('save', 'slug', 'devices'):
                with self.subTest(field_type=field_type, related_name=name):
                    field = CustomObjectTypeField(
                        custom_object_type=self.cert_cot,
                        name='site',
                        type=field_type,
                        related_object_type=site_ct,
                        related_name=name,
                    )
                    with self.assertRaises(ValidationError) as cm:
                        field.full_clean()
                    self.assertIn('related_name', cm.exception.message_dict)
        self.assertIs(Site.save, original_save)

    @tag('regression')
    def test_related_name_colliding_with_static_plugin_reverse_accessor_rejected(self):
        """Reverse accessors from the plugin's own static models share APP_LABEL but must not be overwritten."""
        choice_set_ot = ObjectType.objects.get_for_model(CustomFieldChoiceSet)
        original = CustomFieldChoiceSet.choices_for_object_type
        for field_type in ('object', 'multiobject'):
            with self.subTest(field_type=field_type):
                field = CustomObjectTypeField(
                    custom_object_type=self.cert_cot,
                    name='choice_set_ref',
                    type=field_type,
                    related_object_type=choice_set_ot,
                    related_name='choices_for_object_type',
                )
                with self.assertRaises(ValidationError) as cm:
                    field.full_clean()
                self.assertIn('related_name', cm.exception.message_dict)
        self.assertIs(CustomFieldChoiceSet.choices_for_object_type, original)

    @tag('regression')
    def test_polymorphic_related_name_rejects_stale_non_polymorphic_accessor(self):
        """A polymorphic field must not claim a name held by a stale non-polymorphic accessor.

        The polymorphic wiring paths refuse to replace a non-polymorphic accessor, so accepting
        the name would save the field without ever installing its reverse descriptor.
        """
        site_ct = self.get_site_object_type()
        name = 'stale_certificates'
        self.addCleanup(lambda: name in Site.__dict__ and delattr(Site, name))

        # Leave a stale FK reverse accessor behind on Site.
        np_field = self.create_custom_object_type_field(
            self.cert_cot,
            name='site',
            type='object',
            related_object_type=site_ct,
            related_name=name,
        )
        self.cert_cot.refresh_from_db()
        self.cert_cot.get_model()
        np_field.delete()
        self.assertIn(name, Site.__dict__)

        poly_field = self.create_custom_object_type_field(
            self.cert_cot,
            name='assigned_to',
            type='object',
            is_polymorphic=True,
        )
        poly_field.related_object_types.set([site_ct])
        poly_field = CustomObjectTypeField.objects.get(pk=poly_field.pk)
        poly_field.related_name = name
        with self.assertRaises(ValidationError) as cm:
            poly_field.full_clean()
        self.assertIn('related_name', cm.exception.message_dict)

    @tag('regression')
    def test_related_name_collision_rejected_when_making_existing_field_unique(self):
        """The clobber guard must run before the uniqueness probe installs the reverse accessor."""
        site_ct = self.get_site_object_type()
        original_save = Site.save
        # Restore Site.save even if the guard regresses, so the clobber doesn't leak into other tests.
        self.addCleanup(setattr, Site, 'save', original_save)
        created = self.create_custom_object_type_field(
            self.cert_cot,
            name='site',
            type='object',
            related_object_type=site_ct,
        )
        # Load from the DB so _original is populated and the unique=False -> True probe runs.
        field = CustomObjectTypeField.objects.get(pk=created.pk)
        field.unique = True
        field.related_name = 'save'
        with self.assertRaises(ValidationError) as cm:
            field.full_clean()
        self.assertIn('related_name', cm.exception.message_dict)
        self.assertIs(Site.save, original_save)

    @tag('regression')
    def test_related_name_colliding_with_custom_object_target_field_rejected(self):
        """related_name must not overwrite a field on a custom object target model."""
        self.slb_cot.get_model()
        for field_type in ('object', 'multiobject'):
            with self.subTest(field_type=field_type):
                field = CustomObjectTypeField(
                    custom_object_type=self.cert_cot,
                    name='slb',
                    type=field_type,
                    related_object_type=self.slb_object_type,
                    related_name='name',
                )
                with self.assertRaises(ValidationError) as cm:
                    field.full_clean()
                self.assertIn('related_name', cm.exception.message_dict)

    @tag('regression')
    def test_existing_field_owning_reverse_accessor_revalidates(self):
        """Re-cleaning a field whose own reverse accessor is already installed must succeed."""
        for field_type, name in (('object', 'certificates'), ('multiobject', 'certificate_set')):
            with self.subTest(field_type=field_type):
                field = self.create_custom_object_type_field(
                    self.cert_cot,
                    name=f'slb_{field_type}',
                    type=field_type,
                    related_object_type=self.slb_object_type,
                    related_name=name,
                )
                self.cert_cot.refresh_from_db()
                self.cert_cot.get_model()
                self.assertTrue(hasattr(self.slb_object_type.model_class(), name))
                field.refresh_from_db()
                field.full_clean()  # Should not raise.

    # ------------------------------------------------------------------ #
    # Object (FK) reverse accessor                                        #
    # ------------------------------------------------------------------ #

    def test_object_field_with_related_name_creates_reverse_accessor(self):
        """A named reverse accessor is available on the related model after an Object field is saved."""
        self.create_custom_object_type_field(
            self.cert_cot,
            name='slb',
            type='object',
            related_object_type=self.slb_object_type,
            related_name='certificates',
        )
        # Generate Certificate's model so it contributes the FK (and its reverse) to SLB's class.
        # Refresh slb_cot so its Python-side cache_timestamp matches the DB value bumped by the
        # signal (creating a TYPE_OBJECT field bumps the related COT's cache_timestamp).
        self.cert_cot.get_model()
        self.slb_cot.refresh_from_db()
        slb_model = self.slb_cot.get_model()
        self.assertTrue(
            hasattr(slb_model, 'certificates'),
            "Expected reverse accessor 'certificates' on SLB model.",
        )

    def test_object_field_reverse_accessor_returns_correct_objects(self):
        """The reverse FK manager returns only the Certificate instances that reference a given SLB."""
        self.create_custom_object_type_field(
            self.cert_cot,
            name='slb',
            type='object',
            related_object_type=self.slb_object_type,
            related_name='certificates',
        )
        cert_model = self.cert_cot.get_model()
        self.slb_cot.refresh_from_db()
        slb_model = self.slb_cot.get_model()

        slb_a = slb_model.objects.create(name='SLB-A')
        slb_b = slb_model.objects.create(name='SLB-B')
        cert_1 = cert_model.objects.create(name='Cert-1', slb=slb_a)
        cert_2 = cert_model.objects.create(name='Cert-2', slb=slb_a)
        cert_model.objects.create(name='Cert-3', slb=slb_b)

        result = list(slb_a.certificates.all())
        self.assertIn(cert_1, result)
        self.assertIn(cert_2, result)
        self.assertEqual(len(result), 2)
        self.assertEqual(slb_b.certificates.count(), 1)

    def test_object_field_without_related_name_uses_auto_generated_name(self):
        """Without related_name, the auto-generated accessor follows the {table}_{field}_set convention."""
        self.create_custom_object_type_field(
            self.cert_cot,
            name='slb',
            type='object',
            related_object_type=self.slb_object_type,
        )
        self.cert_cot.get_model()
        self.slb_cot.refresh_from_db()
        slb_model = self.slb_cot.get_model()

        table_model_name = self.cert_cot.get_table_model_name(self.cert_cot.id).lower()
        expected_accessor = f'{table_model_name}_slb_set'
        self.assertTrue(
            hasattr(slb_model, expected_accessor),
            f"Expected auto-generated reverse accessor '{expected_accessor}' on SLB model.",
        )

    # ------------------------------------------------------------------ #
    # MultiObject (M2M) reverse accessor                                  #
    # ------------------------------------------------------------------ #

    def test_multiobject_field_with_related_name_creates_reverse_manager(self):
        """A named reverse manager is available on the related model after a MultiObject field is saved."""
        self.create_custom_object_type_field(
            self.cert_cot,
            name='slbs',
            type='multiobject',
            related_object_type=self.slb_object_type,
            related_name='certificates',
        )
        self.cert_cot.get_model()
        slb_model = self.slb_cot.get_model()
        self.assertTrue(
            hasattr(slb_model, 'certificates'),
            "Expected reverse manager 'certificates' on SLB model.",
        )

    def test_multiobject_field_reverse_manager_returns_correct_objects(self):
        """The reverse M2M manager returns only the Certificate instances linked to a given SLB."""
        self.create_custom_object_type_field(
            self.cert_cot,
            name='slbs',
            type='multiobject',
            related_object_type=self.slb_object_type,
            related_name='certificates',
        )
        cert_model = self.cert_cot.get_model()
        slb_model = self.slb_cot.get_model()

        slb_a = slb_model.objects.create(name='SLB-A')
        slb_b = slb_model.objects.create(name='SLB-B')
        cert_1 = cert_model.objects.create(name='Cert-1')
        cert_2 = cert_model.objects.create(name='Cert-2')
        cert_1.slbs.add(slb_a)
        cert_2.slbs.add(slb_a, slb_b)

        result = list(slb_a.certificates.all())
        self.assertIn(cert_1, result)
        self.assertIn(cert_2, result)
        self.assertEqual(len(result), 2)
        self.assertEqual(slb_b.certificates.count(), 1)
        self.assertIn(cert_2, slb_b.certificates.all())

    def test_multiobject_field_without_related_name_has_no_reverse_accessor(self):
        """Without related_name, a MultiObject field has no reverse accessor on the related model."""
        self.create_custom_object_type_field(
            self.cert_cot,
            name='slbs',
            type='multiobject',
            related_object_type=self.slb_object_type,
        )
        self.cert_cot.get_model()
        slb_model = self.slb_cot.get_model()

        # No user-defined or auto-generated reverse accessor should exist.
        table_model_name = self.cert_cot.get_table_model_name(self.cert_cot.id).lower()
        self.assertFalse(
            hasattr(slb_model, 'slbs'),
            'MultiObject field without related_name should not create a reverse accessor.',
        )
        self.assertFalse(
            hasattr(slb_model, f'{table_model_name}_slbs_set'),
            'MultiObject field without related_name should not create an auto-generated reverse accessor.',
        )

    @tag('regression')
    def test_deleting_multiobject_field_deregisters_through_model(self):
        """
        After a multiobject field is deleted its through model must be removed
        from Django's app registry. Without the fix the cascade-delete collector
        still finds the model, queries the dropped table, and raises
        ProgrammingError when deleting any object that was a relation target
        of the field.
        """
        field = self.create_custom_object_type_field(
            self.cert_cot,
            name='related_slbs',
            type='multiobject',
            related_object_type=self.slb_object_type,
        )
        through_model_name = field.through_model_name.lower()
        self.assertIn(through_model_name, django_apps.all_models.get(APP_LABEL, {}))

        slb_model = self.slb_cot.get_model()
        slb_instance = slb_model.objects.create(name='SLB-To-Delete')

        field.delete()

        # Primary assertion: deleting a relation target must not raise ProgrammingError.
        # Without the fix this line raises:
        #   ProgrammingError: relation "custom_objects_<id>_related_slbs" does not exist
        # because the stale through model is still in apps.all_models and the
        # cascade-delete collector queries the already-dropped table.
        try:
            slb_instance.delete()
        except ProgrammingError as exc:
            self.fail(f'Deleting a related object raised ProgrammingError after field deletion: {exc}')

        self.assertNotIn(through_model_name, django_apps.all_models.get(APP_LABEL, {}))


class TargetsCustomObjectTypeTestCase(CustomObjectsTestCase, TestCase):
    """Unit tests for CustomObjectTypeField.targets_custom_object_type."""

    def test_true_for_field_targeting_another_cot(self):
        target_type = self.create_custom_object_type(name='TargetsCOTTarget', slug='targets-cot-target')
        source_type = self.create_custom_object_type(name='TargetsCOTSource', slug='targets-cot-source')
        target_ct = ObjectType.objects.get_for_model(target_type.get_model())
        field = self.create_custom_object_type_field(
            source_type,
            name='ref',
            type='object',
            related_object_type=target_ct,
        )
        self.assertTrue(field.targets_custom_object_type)

    def test_false_for_field_targeting_core_model(self):
        source_type = self.create_custom_object_type(name='TargetsCoreSource', slug='targets-core-source')
        field = self.create_custom_object_type_field(
            source_type,
            name='device',
            type='object',
            related_object_type=self.get_device_object_type(),
        )
        self.assertFalse(field.targets_custom_object_type)

    def test_false_for_polymorphic_field(self):
        source_type = self.create_custom_object_type(name='TargetsPolySource', slug='targets-poly-source')
        field = self.create_custom_object_type_field(
            source_type,
            name='owner',
            type='object',
            is_polymorphic=True,
        )
        self.assertFalse(field.targets_custom_object_type)

    def test_false_for_field_targeting_custom_object_type_model_itself(self):
        # netbox_custom_objects.customobjecttype shares APP_LABEL with dynamic
        # COT models but isn't one -- extract_cot_id_from_model_name() must
        # return None for it, not raise. Building this in memory rather than
        # saving: a real save() rejects this related_object_type outright, since
        # CustomObjectType itself isn't a "table<id>model" dynamic model, but the
        # property's own guard against it is worth testing directly.
        source_type = self.create_custom_object_type(name='TargetsCOTModelSource', slug='targets-cot-model-source')
        cot_ct = ObjectType.objects.get_for_model(CustomObjectType)
        field = CustomObjectTypeField(
            custom_object_type=source_type,
            name='cot_ref',
            type='object',
            related_object_type=cot_ct,
        )
        self.assertFalse(field.targets_custom_object_type)

    def test_false_for_no_related_object_type(self):
        source_type = self.create_custom_object_type(name='TargetsNoneSource', slug='targets-none-source')
        field = self.create_custom_object_type_field(source_type, name='label', type='text')
        self.assertFalse(field.targets_custom_object_type)


class NullRelatedObjectTypeTestCase(CustomObjectsTestCase, TestCase):
    """Regression tests for graceful handling of OBJECT/MULTIOBJECT fields whose
    related_object_type_id is NULL or points to a deleted ContentType.

    A NULL FK can occur when a COT field is created via direct DB manipulation or
    when the referenced ContentType is deleted.  All code paths that build the
    dynamic model or serializer must skip such fields rather than crashing.

    Covers the fixes in _fetch_and_generate_field_attrs (ContentType.DoesNotExist →
    NotImplementedError) and get_serializer_class (Meta.fields/attrs mismatch guard).
    """

    def _make_cot_with_null_object_field(self, name, slug, field_name='broken_ref'):
        cot = self.create_custom_object_type(name=name, slug=slug)
        self.create_custom_object_type_field(
            cot,
            name='title',
            label='Title',
            type='text',
            primary=True,
            required=True,
        )
        field = self.create_custom_object_type_field(
            cot,
            name=field_name,
            label='Broken Ref',
            type='object',
            related_object_type=self.get_site_object_type(),
        )
        # Force the FK to NULL to simulate stale/corrupt data (e.g. ContentType deleted)
        CustomObjectTypeField.objects.filter(pk=field.pk).update(related_object_type=None)
        CustomObjectType.clear_model_cache()
        return cot

    def test_get_model_skips_object_field_with_null_related_object_type(self):
        """get_model() must succeed and silently skip an OBJECT field whose
        related_object_type_id is NULL rather than raising ContentType.DoesNotExist."""
        cot = self._make_cot_with_null_object_field('NullRelObj', 'null-rel-obj')
        model = cot.get_model()
        self.assertIsNotNone(model)
        model_field_names = {f.name for f in model._meta.local_fields} | {
            f.name for f in model._meta.local_many_to_many
        }
        self.assertNotIn('broken_ref', model_field_names, 'Field with null FK must be absent from the generated model')
        self.assertIn('title', model_field_names, 'Normal fields must still be present')

    def test_get_serializer_class_handles_null_related_object_type(self):
        """get_serializer_class() must not raise AttributeError when an OBJECT field
        was skipped during model generation due to a NULL related_object_type_id.
        Regression for the Meta.fields/attrs mismatch that caused DRF to raise a
        validation error at serializer initialization time."""
        cot = self._make_cot_with_null_object_field('NullRelSerializer', 'null-rel-serializer')
        model = cot.get_model()
        serializer_cls = get_serializer_class(model)
        self.assertIsNotNone(serializer_cls)
        self.assertNotIn(
            'broken_ref', serializer_cls.Meta.fields, 'Null-FK field must not appear in serializer Meta.fields'
        )
        self.assertIn('title', serializer_cls.Meta.fields, 'Normal fields must still be present in serializer')


class LazyForeignKeySaveResolutionTestCase(CustomObjectsTestCase, TestCase):
    """Regression test for the save() path in CustomObjectTypeField.

    Verifies that creating a TYPE_OBJECT field via save() succeeds even when the
    target COT model has been removed from apps.all_models (the state left by
    tearDown() between test methods that add FK columns to existing COTs).
    """

    @classmethod
    def setUpTestData(cls):
        cls.target_type = CustomObjectType.objects.create(
            name='save_target',
            slug='save-target',
            verbose_name_plural='save_targets',
        )
        CustomObjectTypeField.objects.create(
            custom_object_type=cls.target_type,
            name='name',
            label='Name',
            type='text',
            primary=True,
            required=True,
        )
        cls.target_type.get_model()

    def test_save_object_field_when_target_absent_from_all_models(self):
        """save() must resolve the LazyFK target via cot.get_model() even when
        the target model is absent from apps.all_models."""
        target_model = self.target_type.get_model()
        target_model_name = target_model._meta.model_name

        source_type = CustomObjectType.objects.create(
            name='save_source',
            slug='save-source',
            verbose_name_plural='save_sources',
        )
        CustomObjectTypeField.objects.create(
            custom_object_type=source_type,
            name='name',
            label='Name',
            type='text',
            primary=True,
            required=True,
        )
        source_type.get_model()

        # Simulate tearDown removing the target model from the app registry
        django_apps.all_models.get(APP_LABEL, {}).pop(target_model_name, None)
        CustomObjectType.clear_model_cache()

        # ObjectType lookup works via ContentType DB row — unaffected by registry removal
        target_ot = ObjectType.objects.get(app_label=APP_LABEL, model=target_model_name)

        # This save() call must not raise ValueError about remote_field.model being a string
        field = CustomObjectTypeField.objects.create(
            custom_object_type=source_type,
            name='target_ref',
            label='Target Ref',
            type='object',
            related_object_type=target_ot,
        )
        self.assertEqual(field.name, 'target_ref')
        self.assertEqual(field.type, 'object')
