from netbox.jobs import JobRunner
from netbox.search.backends import get_backend


class ReindexCustomObjectTypeJob(JobRunner):
    """
    Background job to reindex all CustomObject instances for a given CustomObjectType.

    Triggered when a CustomObjectTypeField's search_weight changes, a new searchable
    field is added, or a searchable field is deleted.
    """

    class Meta:
        name = 'Reindex Custom Object Type'

    def run(self, *args, **kwargs):
        # Deferred to avoid circular import: models.py imports this module at the top level
        from netbox_custom_objects.models import CustomObjectType
        cot = CustomObjectType.objects.get(pk=kwargs['cot_id'])
        get_backend().cache(cot.get_model().objects.all())
