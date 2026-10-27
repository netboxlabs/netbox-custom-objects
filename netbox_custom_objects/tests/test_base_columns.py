"""
Tests for the base-column snapshot feature.

Covers:
- _collect_base_columns() correctly identifies base vs user-defined columns
- _store_base_column_snapshot() writes schema_document["base_columns"] and
  preserves existing schema_document keys
- create_model() automatically stores the snapshot after table creation

The 0010 backfill migration's tests are in ``tests/upgrade/test_data_migrations.py``.
"""

from django.test import TestCase

from netbox_custom_objects.models import CustomObjectType

from .base import CustomObjectsTestCase

# ---------------------------------------------------------------------------
# _collect_base_columns()
# ---------------------------------------------------------------------------


class CollectBaseColumnsTestCase(CustomObjectsTestCase, TestCase):
    """Unit-style tests for CustomObjectType._collect_base_columns()."""

    def _make_cot_and_model(self, name, slug, field_names=()):
        """Create a COT with optional user fields and return (cot, model)."""
        cot = self.create_custom_object_type(name=name, slug=slug)
        for fname in field_names:
            self.create_custom_object_type_field(cot, name=fname, type='text')
        return cot, cot.get_model()

    def test_base_columns_present_when_no_user_fields(self):
        """id, created, and last_updated must always appear as base columns."""
        _cot, model = self._make_cot_and_model('no_fields', 'no-fields')
        base = CustomObjectType._collect_base_columns(model, set())
        names = {c['name'] for c in base}
        self.assertIn('id', names)
        self.assertIn('created', names)
        self.assertIn('last_updated', names)

    def test_user_field_excluded_from_base_columns(self):
        """A user-defined field name must not appear in base columns."""
        _cot, model = self._make_cot_and_model('with_field', 'with-field', field_names=['my_text'])
        user_field_names = {'my_text'}
        base = CustomObjectType._collect_base_columns(model, user_field_names)
        names = {c['name'] for c in base}
        self.assertNotIn('my_text', names)
        self.assertIn('id', names)

    def test_each_entry_has_required_keys(self):
        """Every base-column dict must carry name, field_class, and null."""
        _cot, model = self._make_cot_and_model('keys_check', 'keys-check')
        for entry in CustomObjectType._collect_base_columns(model, set()):
            self.assertIn('name', entry, f"Missing 'name' in {entry}")
            self.assertIn('field_class', entry, f"Missing 'field_class' in {entry}")
            self.assertIn('null', entry, f"Missing 'null' in {entry}")

    def test_id_field_class(self):
        """id should be reported as AutoField (or a subclass name)."""
        _cot, model = self._make_cot_and_model('id_type', 'id-type')
        base = CustomObjectType._collect_base_columns(model, set())
        id_entry = next(c for c in base if c['name'] == 'id')
        self.assertIn('Field', id_entry['field_class'])

    def test_multiple_user_fields_all_excluded(self):
        """All user-defined field names must be excluded regardless of count."""
        _cot, model = self._make_cot_and_model(
            'multi_fields',
            'multi-fields',
            field_names=['alpha', 'beta', 'gamma'],
        )
        user_field_names = {'alpha', 'beta', 'gamma'}
        base = CustomObjectType._collect_base_columns(model, user_field_names)
        names = {c['name'] for c in base}
        self.assertNotIn('alpha', names)
        self.assertNotIn('beta', names)
        self.assertNotIn('gamma', names)


# ---------------------------------------------------------------------------
# _store_base_column_snapshot()
# ---------------------------------------------------------------------------


class StoreBaseColumnSnapshotTestCase(CustomObjectsTestCase, TestCase):
    """Tests for CustomObjectType._store_base_column_snapshot()."""

    def test_snapshot_written_to_schema_document(self):
        """schema_document['base_columns'] must be populated after the call."""
        cot = self.create_custom_object_type(name='snap_test', slug='snap-test')
        model = cot.get_model()
        # Wipe base_columns so we can test the write explicitly
        CustomObjectType.objects.filter(pk=cot.pk).update(schema_document=None)
        cot.schema_document = None

        cot._store_base_column_snapshot(model)

        cot.refresh_from_db()
        self.assertIsNotNone(cot.schema_document)
        self.assertIn('base_columns', cot.schema_document)
        self.assertIsInstance(cot.schema_document['base_columns'], list)
        self.assertTrue(len(cot.schema_document['base_columns']) > 0)

    def test_existing_schema_document_keys_preserved(self):
        """Calling _store_base_column_snapshot must not remove pre-existing keys."""
        cot = self.create_custom_object_type(name='preserve_keys', slug='preserve-keys')
        # Seed schema_document with an existing key
        existing_doc = {'schema_version': '1', 'types': []}
        CustomObjectType.objects.filter(pk=cot.pk).update(schema_document=existing_doc)
        cot.refresh_from_db()

        model = cot.get_model()
        cot._store_base_column_snapshot(model)

        cot.refresh_from_db()
        self.assertIn('schema_version', cot.schema_document)
        self.assertEqual(cot.schema_document['schema_version'], '1')
        self.assertIn('base_columns', cot.schema_document)

    def test_in_memory_schema_document_updated(self):
        """The in-memory cot.schema_document must reflect the write immediately."""
        cot = self.create_custom_object_type(name='in_memory', slug='in-memory')
        CustomObjectType.objects.filter(pk=cot.pk).update(schema_document=None)
        cot.schema_document = None

        model = cot.get_model()
        cot._store_base_column_snapshot(model)

        self.assertIsNotNone(cot.schema_document)
        self.assertIn('base_columns', cot.schema_document)

    def test_base_columns_does_not_include_user_fields(self):
        """User-defined fields must not appear in the stored base_columns."""
        cot = self.create_custom_object_type(name='excl_user', slug='excl-user')
        self.create_custom_object_type_field(cot, name='my_field', type='text')
        model = cot.get_model()

        CustomObjectType.objects.filter(pk=cot.pk).update(schema_document=None)
        cot.schema_document = None
        cot._store_base_column_snapshot(model)

        cot.refresh_from_db()
        names = {c['name'] for c in cot.schema_document['base_columns']}
        self.assertNotIn('my_field', names)
        self.assertIn('id', names)

    def test_resnapshot_excludes_field_added_after_creation(self):
        """Re-taking the snapshot after a user field is added must still leave that field out."""
        cot = self.create_custom_object_type(name='user_excl', slug='user-excl')
        self.create_custom_object_type_field(cot, name='extra_col', type='text')
        # Force a fresh snapshot by calling _store_base_column_snapshot again,
        # simulating what create_model does at COT birth (user field didn't exist yet)
        CustomObjectType.objects.filter(pk=cot.pk).update(schema_document=None)
        cot.refresh_from_db()
        model = cot.get_model()
        cot._store_base_column_snapshot(model)
        cot.refresh_from_db()

        names = {c['name'] for c in cot.schema_document['base_columns']}
        self.assertNotIn('extra_col', names)


# ---------------------------------------------------------------------------
# create_model() automatic snapshot
# ---------------------------------------------------------------------------


class CreateModelSnapshotTestCase(CustomObjectsTestCase, TestCase):
    """Tests that create_model() stores a base-column snapshot automatically."""

    def test_schema_document_has_base_columns_after_creation(self):
        """A freshly created COT must have base_columns in schema_document."""
        cot = self.create_custom_object_type(name='auto_snap', slug='auto-snap')
        cot.refresh_from_db()
        self.assertIsNotNone(cot.schema_document)
        self.assertIn('base_columns', cot.schema_document)

    def test_base_columns_includes_id_created_last_updated(self):
        """The mandatory base columns must always be present."""
        cot = self.create_custom_object_type(name='mandatory_cols', slug='mandatory-cols')
        cot.refresh_from_db()
        names = {c['name'] for c in cot.schema_document['base_columns']}
        self.assertIn('id', names)
        self.assertIn('created', names)
        self.assertIn('last_updated', names)
