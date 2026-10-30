"""
Tests for the custom object endpoints in the OpenAPI schema.
"""

from django.test import TestCase


class OpenAPISchemaTestCase(TestCase):
    """The custom object endpoints appear in the OpenAPI schema, described generically."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from drf_spectacular.generators import SchemaGenerator

        cls.schema = SchemaGenerator().get_schema(request=None, public=True)

    def test_schema_is_valid(self):
        from drf_spectacular.validation import validate_schema

        validate_schema(self.schema)

    def test_custom_object_paths_and_operations(self):
        paths = self.schema['paths']
        list_path = '/api/plugins/custom-objects/{custom_object_type}/'
        detail_path = '/api/plugins/custom-objects/{custom_object_type}/{id}/'
        self.assertEqual(set(paths[list_path]) - {'parameters'}, {'get', 'post', 'put', 'patch', 'delete'})
        self.assertEqual(set(paths[detail_path]) - {'parameters'}, {'get', 'put', 'patch', 'delete'})

        for path in (list_path, detail_path):
            # Skip a path-level "parameters" list, if present.
            for operation in (op for op in paths[path].values() if isinstance(op, dict)):
                slug = [p for p in operation['parameters'] if p['name'] == 'custom_object_type']
                self.assertEqual(len(slug), 1)
                self.assertEqual(slug[0]['in'], 'path')
                self.assertEqual(slug[0]['schema']['type'], 'string')

        list_response = paths[list_path]['get']['responses']['200']['content']['application/json']['schema']
        self.assertEqual(list_response['$ref'], '#/components/schemas/PaginatedCustomObjectList')

    def test_custom_object_components_allow_custom_fields(self):
        components = self.schema['components']['schemas']
        response = components['CustomObject']
        self.assertTrue(response['additionalProperties'])
        self.assertEqual(
            set(response['properties']), {'id', 'url', 'display', 'owner', 'tags', 'created', 'last_updated'}
        )
        for name in ('CustomObjectRequest', 'PatchedCustomObjectRequest'):
            self.assertTrue(components[name]['additionalProperties'], name)

        # Follow the bulk operations' references: NetBox versions without dedicated
        # bulk-update components reference CustomObjectRequest instead.
        list_path = self.schema['paths']['/api/plugins/custom-objects/{custom_object_type}/']
        for method in ('put', 'patch'):
            body = list_path[method]['requestBody']['content']['application/json']['schema']
            name = body['items']['$ref'].rsplit('/', 1)[-1]
            self.assertTrue(components[name]['additionalProperties'], name)
            if 'Bulk' in name:
                self.assertEqual(components[name]['required'], ['id'])
