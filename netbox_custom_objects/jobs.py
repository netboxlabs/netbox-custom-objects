from core.choices import JobIntervalChoices
from core.models import ObjectType
from django.db import transaction
from extras.choices import CustomFieldTypeChoices
from extras.models import CachedValue
from netbox.jobs import JobRunner, system_job
from netbox.search import FieldTypes
from netbox.search.backends import CachedValueSearchBackend, get_backend

from netbox_custom_objects.constants import DISPLAY_EXPRESSION_SEARCH_WEIGHT

_RELATED_FIELD_TYPES = (CustomFieldTypeChoices.TYPE_OBJECT, CustomFieldTypeChoices.TYPE_MULTIOBJECT)


class ReindexCustomObjectTypeJob(JobRunner):
    """
    Background job to reindex all CustomObject instances for a given CustomObjectType.

    Triggered when a CustomObjectTypeField's search_weight changes, a new searchable
    field is added, or a searchable field is deleted; and when a type's
    display_expression changes.
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
    Hourly refresh of the cached display names of Custom Object Types with a
    display_expression.

    The rendered expression is cached for search when an object is saved, but it can
    reference related objects (e.g. ``{{ interface.device.name }}``), and changes to those
    don't touch the custom object. This bounds how long the cached text can be stale.
    An expression that names no related field can't drift, so for those types only objects
    with no cached display name (e.g. ones saved before display names were indexed) are rendered.
    """

    class Meta:
        name = 'Refresh custom object display names in search'

    def run(self, *args, **kwargs):
        # Deferred to avoid circular import: models.py imports this module at the top level
        from netbox_custom_objects.models import CustomObjectType
        for cot in CustomObjectType.objects.exclude(display_expression=''):
            related = [f for f in cot.get_display_expression_fields() if f.type in _RELATED_FIELD_TYPES]
            refresh_display_search_cache(cot, related, missing_only=not related)


def refresh_display_search_cache(cot, related_fields, missing_only=False):
    """
    Re-render a type's display names and rewrite only the cached ``_display`` values
    that changed. With a search backend other than NetBox's, reindex the type instead.

    With ``missing_only``, render only objects that have no cached display name. Objects
    whose name renders empty have none, so they're rendered again on each run.
    """
    model = cot.get_model()
    queryset = model.objects.all()
    if not isinstance(get_backend(), CachedValueSearchBackend):
        # Other backends can't tell which objects are missing a value; those need a reindex.
        if not missing_only:
            get_backend().cache(queryset)
        return

    # Load what the expression names; polymorphic relations can't be joined this way.
    direct = [f for f in related_fields if not f.is_polymorphic]
    queryset = queryset.select_related(
        *(f.name for f in direct if f.type == CustomFieldTypeChoices.TYPE_OBJECT)
    ).prefetch_related(
        *(f.name for f in direct if f.type == CustomFieldTypeChoices.TYPE_MULTIOBJECT)
    )

    object_type = ObjectType.objects.get_for_model(model)
    cached_rows = CachedValue.objects.filter(object_type=object_type, field='_display')
    if missing_only:
        queryset = queryset.exclude(pk__in=cached_rows.values('object_id'))
        cached = {}
    else:
        cached = dict(cached_rows.values_list('object_id', 'value'))
    changed, new_values = [], []
    for instance in queryset.iterator(chunk_size=2000):
        value = instance._display
        if value == cached.get(instance.pk):
            continue
        changed.append(instance.pk)
        if value:
            new_values.append(CachedValue(
                object_type=object_type,
                object_id=instance.pk,
                field='_display',
                type=FieldTypes.STRING,
                weight=DISPLAY_EXPRESSION_SEARCH_WEIGHT,
                value=value,
            ))

    with transaction.atomic():
        if changed and not missing_only:
            # A raw DELETE, as core's search backend does, so rows aren't loaded and
            # delete signal receivers don't run for each one.
            stale = cached_rows.filter(object_id__in=changed)
            stale._raw_delete(using=stale.db)
        CachedValue.objects.bulk_create(new_values, batch_size=2000)
