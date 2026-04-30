"""
Management command to refresh the GraphQL schema for custom objects.
"""

from django.core.management.base import BaseCommand

from netbox_custom_objects.graphql.schema import trigger_schema_refresh


class Command(BaseCommand):
    help = 'Refresh the GraphQL schema for custom objects'

    def handle(self, *args, **options):
        self.stdout.write('Refreshing GraphQL schema for custom objects...')
        try:
            trigger_schema_refresh()
            self.stdout.write(self.style.SUCCESS('GraphQL schema refreshed successfully!'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Error refreshing GraphQL schema: {e}'))
