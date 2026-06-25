from django.db import transaction

from netbox.jobs import JobRunner
from netbox.search.backends import get_backend


def schedule_reindex_custom_object_type(cot_id):
    """
    Queue a reindex job for *cot_id* after the current transaction commits.

    Multiple field saves in one transaction (e.g. portable-schema apply) register
    at most one enqueue per COT when the transaction commits.
    """
    connection = transaction.get_connection()
    pending = getattr(connection, '_pending_cot_reindex_ids', None)
    if pending is None:
        pending = set()
        connection._pending_cot_reindex_ids = pending

        def flush():
            # Deferred import: models.py imports this module at load time.
            from netbox_custom_objects.models import CustomObjectType

            cot_ids = getattr(connection, '_pending_cot_reindex_ids', set())
            connection._pending_cot_reindex_ids = set()
            for pending_cot_id in cot_ids:
                try:
                    cot = CustomObjectType.objects.get(pk=pending_cot_id)
                except CustomObjectType.DoesNotExist:
                    continue
                # Nothing to index until the type has at least one instance (e.g. schema import).
                try:
                    if not cot.get_model().objects.exists():
                        continue
                except Exception:
                    continue
                ReindexCustomObjectTypeJob.enqueue(cot_id=pending_cot_id)

        transaction.on_commit(flush)

    pending.add(cot_id)


class ReindexCustomObjectTypeJob(JobRunner):
    """
    Background job to reindex all CustomObject instances for a given CustomObjectType.

    Triggered when a CustomObjectTypeField's search_weight changes, a new searchable
    field is added, or a searchable field is deleted.
    """

    class Meta:
        name = 'Reindex Custom Object Type'

    @classmethod
    def get_jobs(cls, instance=None):
        """Match pending jobs by linked COT instance (supports custom job names)."""
        from core.models import Job

        jobs = Job.objects.filter(data__job_class=cls.__name__)
        if instance is not None:
            jobs = jobs.filter(data__cot_id=instance.pk)
        return jobs

    @classmethod
    def enqueue(cls, *args, **kwargs):
        # All imports deferred to avoid circular import: models.py imports this module at the top level
        from netbox_custom_objects.models import CustomObjectType

        cot_id = kwargs.get('cot_id')
        immediate = kwargs.get('immediate', False)

        cot = kwargs.get('instance')
        if cot is None and cot_id is not None:
            try:
                cot = CustomObjectType.objects.get(pk=cot_id)
            except CustomObjectType.DoesNotExist:
                cot = None

        if cot is not None:
            # Store cot_id in job.data only — NetBox Job rejects CustomObjectType as object_type.
            kwargs.pop('instance', None)
            kwargs.setdefault('cot_id', cot.pk)

        if cot is not None and 'name' not in kwargs:
            kwargs['name'] = f'{cls.name}: {cot.name}'

        if immediate or cot is None:
            job = super().enqueue(*args, **kwargs)
        else:
            # JobRunner.enqueue_once() ends with cls.enqueue(), which would re-enter
            # this override and recurse forever — dedupe manually, then call super().
            from core.choices import JobStatusChoices

            existing = (
                cls.get_jobs(instance=cot)
                .filter(status__in=JobStatusChoices.ENQUEUED_STATE_CHOICES)
                .first()
            )
            if existing:
                job = existing
            else:
                job = super().enqueue(*args, **kwargs)

        if job is not None and cot_id is not None:
            merged = {**(job.data or {}), 'cot_id': cot_id, 'job_class': cls.__name__}
            if job.data != merged:
                job.data = merged
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
