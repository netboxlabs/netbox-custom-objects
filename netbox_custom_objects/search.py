from netbox.search import SearchIndex, register_search
from . import models


@register_search
class CustomObjectTypeIndex(SearchIndex):
    model = models.CustomObjectType
    fields = (
        ('name', 100),
        ('description', 500),
        ('comments', 5000),
    )
    display_attrs = ('description', 'description')
