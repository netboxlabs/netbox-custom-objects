"""
Tests for the portable-schema UI tabs/screens:

- ``CustomObjectTypeExportView`` — the read-only "Export" tab on the COT detail
  page, which renders the COT's portable-schema export as JSON.
- ``CustomObjectTypeEditView`` (add) — the add page at ``/add/`` with a **JSON**
  tab for pasted portable-schema documents.

These views build on the portable-schema backend (exporter / comparator /
executor / validation); the tests assert the UI wiring, not the schema logic
itself (covered by tests/schema/).
"""
import json

from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from netbox_custom_objects.models import CustomObjectType, CustomObjectTypeField
from netbox_custom_objects.views import _parse_schema_text

from .base import CustomObjectsTestCase, TransactionCleanupMixin


def _new_cot_document(slug='gadget', name='gadget'):
    """A minimal, valid single-COT schema document for a brand-new COT."""
    return {
        'schema_version': '1',
        'types': [
            {
                'name': name,
                'slug': slug,
                'description': 'A gadget',
                'fields': [
                    {'id': 1, 'name': 'label', 'type': 'text', 'primary': True},
                    {'id': 2, 'name': 'quantity', 'type': 'integer'},
                ],
            }
        ],
    }


class ParseSchemaTextTestCase(TestCase):
    def test_parse_yaml_document_rejected(self):
        try:
            import yaml
        except ImportError:
            self.skipTest('pyyaml not installed')

        document = yaml.safe_dump(_new_cot_document(slug='nsm_address_group'))
        with self.assertRaises(ValueError) as ctx:
            _parse_schema_text(document)
        self.assertIn('JSON', str(ctx.exception))

    def test_parse_json_document(self):
        parsed = _parse_schema_text(json.dumps(_new_cot_document()))
        self.assertEqual(parsed['types'][0]['slug'], 'gadget')


class _SuperuserMixin:
    """Elevate the base test user to a superuser so add/change perms pass."""

    def setUp(self):
        super().setUp()
        self.user.is_superuser = True
        self.user.is_staff = True
        self.user.save()
        self.client.force_login(self.user)


# ===========================================================================
# Export tab
# ===========================================================================

class SchemaExportTabTestCase(_SuperuserMixin, CustomObjectsTestCase, TestCase):
    """The Export tab renders the COT's portable-schema document as text."""

    @classmethod
    def setUpTestData(cls):
        cls.cot = cls.create_custom_object_type(
            name='widget',
            slug='widget',
            description='A widget',
        )
        cls.field = cls.create_custom_object_type_field(
            cls.cot, name='label', type='text', primary=True,
        )

    def _url(self):
        return reverse(
            'plugins:netbox_custom_objects:customobjecttype_export',
            kwargs={'pk': self.cot.pk},
        )

    def test_export_tab_renders_document_text(self):
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'netbox_custom_objects/customobjecttype_export.html')
        content = response.content.decode()
        self.assertIn('widget', content)
        self.assertIn('label', content)
        self.assertIn('cot-export-json-text', content)
        self.assertIn('JSON', content)

    def test_export_tab_shows_choice_sets_objects_exclusion_hint(self):
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('alert alert-info', content)
        self.assertIn('choice_sets', content)
        self.assertIn('objects', content)
        self.assertIn('schema_version', content)
        self.assertIn('types', content)
        self.assertIn('add or manage those separately', content)

    def test_export_tab_json_is_valid_and_complete(self):
        response = self.client.get(self._url())
        document = response.context['schema_json']
        parsed = json.loads(document)
        self.assertEqual(parsed['schema_version'], '1')
        self.assertEqual(len(parsed['types']), 1)
        type_def = parsed['types'][0]
        self.assertEqual(type_def['slug'], 'widget')
        self.assertEqual(type_def['fields'][0]['name'], 'label')


class SchemaListExportTestCase(_SuperuserMixin, CustomObjectsTestCase, TestCase):
    """The COT list export dropdown can download a portable schema JSON file."""

    @classmethod
    def setUpTestData(cls):
        cls.cot = cls.create_custom_object_type(
            name='widget',
            slug='widget',
            description='A widget',
        )
        cls.create_custom_object_type_field(
            cls.cot, name='label', type='text', primary=True,
        )

    def test_list_export_schema_returns_json(self):
        url = reverse('plugins:netbox_custom_objects:customobjecttype_list')
        response = self.client.get(url, {'export': 'schema'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/json')
        document = json.loads(response.content)
        self.assertEqual(document['schema_version'], '1')
        slugs = {t['slug'] for t in document['types']}
        self.assertIn('widget', slugs)

    def test_list_page_shows_schema_export_in_dropdown(self):
        response = self.client.get(
            reverse('plugins:netbox_custom_objects:customobjecttype_list')
        )
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('export=schema', content)
        self.assertIn('Portable schema', content)


# ===========================================================================
# Import — read-only paths (GET / preview / validation)
# ===========================================================================

class SchemaImportReadOnlyTestCase(_SuperuserMixin, CustomObjectsTestCase, TestCase):
    """GET, preview and validation-error paths make no DB changes."""

    def _url(self):
        return reverse('plugins:netbox_custom_objects:customobjecttype_add')

    def test_get_renders_json_tab(self):
        response = self.client.get(self._url(), {'tab': 'json'})
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'netbox_custom_objects/customobjecttype_edit.html')
        content = response.content.decode()
        self.assertIn('JSON', content)
        self.assertIn('id_document_text', content)
        self.assertIn('id="json-import"', content)

    def test_preview_valid_document_shows_diff_without_applying(self):
        document = json.dumps(_new_cot_document())
        response = self.client.post(
            self._url(),
            {'document_text': document, 'action': 'preview', 'import_method': 'schema'},
        )
        self.assertEqual(response.status_code, 200)
        diffs = response.context['diffs']
        self.assertIsNotNone(diffs)
        self.assertEqual(len(diffs), 1)
        self.assertTrue(diffs[0].is_new)
        self.assertEqual(diffs[0].slug, 'gadget')
        self.assertFalse(CustomObjectType.objects.filter(slug='gadget').exists())

    def test_invalid_document_reports_schema_errors(self):
        bad = json.dumps({'types': _new_cot_document()['types']})
        response = self.client.post(
            self._url(), {'document_text': bad, 'action': 'apply'}
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['schema_errors'])
        self.assertFalse(CustomObjectType.objects.filter(slug='gadget').exists())

    def test_unparseable_text_reports_parse_error(self):
        response = self.client.post(
            self._url(), {'document_text': '{not valid json: [', 'action': 'preview'}
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['parse_error'])

    def test_add_page_defaults_to_create_tab(self):
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context.get('active_tab', 'create'), 'create')
        content = response.content.decode()
        self.assertIn('id="json-tab"', content)
        self.assertIn('show active', content.split('id="json-import"')[0])

    def test_csv_bulk_import_has_no_json_tab(self):
        response = self.client.get(
            reverse('plugins:netbox_custom_objects:customobjecttype_bulk_import')
        )
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('Direct Import', content)
        self.assertNotIn('id="json-tab"', content)


# ===========================================================================
# Import via text — apply (creates COTs; needs TransactionTestCase for DDL)
# ===========================================================================

class SchemaImportApplyTestCase(
    _SuperuserMixin, TransactionCleanupMixin, CustomObjectsTestCase, TransactionTestCase
):
    """Applying a pasted document creates the COT(s) via the executor."""

    def _url(self):
        return reverse('plugins:netbox_custom_objects:customobjecttype_add')

    def test_apply_creates_custom_object_type(self):
        document = json.dumps(_new_cot_document())
        response = self.client.post(
            self._url(),
            {'document_text': document, 'action': 'apply', 'import_method': 'schema'},
        )
        self.assertEqual(response.status_code, 302)
        cot = CustomObjectType.objects.get(slug='gadget')
        field_names = set(
            CustomObjectTypeField.objects.filter(custom_object_type=cot)
            .values_list('name', flat=True)
        )
        self.assertEqual(field_names, {'label', 'quantity'})

    def test_apply_rejects_yaml(self):
        try:
            import yaml
        except ImportError:
            self.skipTest('pyyaml not installed')

        document = yaml.safe_dump(_new_cot_document(slug='sprocket', name='sprocket'))
        response = self.client.post(
            self._url(),
            {'document_text': document, 'action': 'apply', 'import_method': 'schema'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['parse_error'])
        self.assertFalse(CustomObjectType.objects.filter(slug='sprocket').exists())
