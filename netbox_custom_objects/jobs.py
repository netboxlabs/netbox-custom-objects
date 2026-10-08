from core.choices import JobIntervalChoices
from netbox.jobs import JobRunner, system_job
from netbox.search.backends import get_backend


class ReindexCustomObjectTypeJob(JobRunner):
    """
    Background job to reindex all CustomObject instances for a given CustomObjectType.

    Triggered when a CustomObjectTypeField's search_weight changes, a new searchable
    field is added, or a searchable field is deleted; when a type's display_expression
    changes; and hourly for types with a display_expression (see
    RefreshDisplayExpressionSearchJob).
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


@system_job(interval=JobIntervalChoices.INTERVAL_HOURLY)
class RefreshDisplayExpressionSearchJob(JobRunner):
    """
    Hourly reindex of every Custom Object Type with a display_expression.

    The rendered expression is cached for search when an object is saved, but it can
    reference related objects (e.g. ``{{ interface.device.name }}``), and changes to those
    don't touch the custom object. This bounds how long the cached text can be stale.
    """

    class Meta:
        name = 'Refresh custom object display names in search'

    def run(self, *args, **kwargs):
        # Deferred to avoid circular import: models.py imports this module at the top level
        from netbox_custom_objects.models import CustomObjectType
        for cot_id in CustomObjectType.objects.exclude(display_expression='').values_list('pk', flat=True):
            ReindexCustomObjectTypeJob.enqueue(cot_id=cot_id)
