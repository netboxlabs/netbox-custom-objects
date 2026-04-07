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

    @classmethod
    def enqueue(cls, *args, **kwargs):
        # All imports deferred to avoid circular import: models.py imports this module at the top level
        from core.choices import JobStatusChoices
        from core.models import Job
        from netbox_custom_objects.models import CustomObjectType

        cot_id = kwargs.get('cot_id')

        # Deduplicate: if a pending or running job for this COT already exists, return it unchanged
        if not kwargs.get('immediate') and cot_id is not None:
            existing = Job.objects.filter(
                status__in=JobStatusChoices.ENQUEUED_STATE_CHOICES,
                data__cot_id=cot_id,
                data__job_class=cls.__name__,
            ).first()
            if existing:
                return existing

        # Include the COT name in the job name for observability in the jobs list
        if 'name' not in kwargs and cot_id is not None:
            try:
                cot_name = CustomObjectType.objects.values_list('name', flat=True).get(pk=cot_id)
                kwargs['name'] = f'{cls.name}: {cot_name}'
            except CustomObjectType.DoesNotExist:
                pass

        job = super().enqueue(*args, **kwargs)

        # Persist cot_id in Job.data so it is visible in the UI and queryable for deduplication.
        # Merge rather than overwrite in case super().enqueue() populates data itself.
        if job is not None:
            job.data = {**(job.data or {}), 'cot_id': cot_id, 'job_class': cls.__name__}
            job.save(update_fields=['data'])

        return job

    def run(self, *args, **kwargs):
        # Deferred to avoid circular import: models.py imports this module at the top level
        from netbox_custom_objects.models import CustomObjectType
        cot_id = kwargs.get('cot_id')
        if not cot_id:
            raise ValueError('cot_id is required to run ReindexCustomObjectTypeJob')
        cot = CustomObjectType.objects.get(pk=cot_id)
        get_backend().cache(cot.get_model().objects.all())
