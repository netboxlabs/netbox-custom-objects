import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0021_job_queue_name'),
        ('extras', '0134_owner'),
        ('netbox_custom_objects', '0006_customobjecttypefield_related_name_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='customobjecttype',
            name='next_schema_id',
            field=models.PositiveIntegerField(default=0, editable=False),
        ),
        migrations.AddField(
            model_name='customobjecttype',
            name='schema_document',
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='customobjecttypefield',
            name='deprecated',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='customobjecttypefield',
            name='deprecated_since',
            field=models.CharField(blank=True, max_length=50),
        ),
        # related_name field already added by 0006_customobjecttypefield_related_name_and_more
        migrations.AddField(
            model_name='customobjecttypefield',
            name='scheduled_removal',
            field=models.CharField(blank=True, max_length=50),
        ),
        migrations.AddField(
            model_name='customobjecttypefield',
            name='schema_id',
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        # Switch COT.name to single-regex validator (replaces the two-validator pattern on feature)
        migrations.AlterField(
            model_name='customobjecttype',
            name='name',
            field=models.CharField(
                max_length=100,
                unique=True,
                validators=[
                    django.core.validators.RegexValidator(
                        message=(
                            'Only lowercase alphanumeric characters and underscores are allowed. '
                            'Names may not start or end with an underscore, and double underscores are not permitted.'
                        ),
                        regex='^[a-z0-9]+(_[a-z0-9]+)*$',
                    ),
                ]
            ),
        ),
        migrations.AlterField(
            model_name='customobjecttype',
            name='version',
            field=models.CharField(blank=True, max_length=50),
        ),
        # Switch COTF.name to single-regex validator (overrides the two-validator pattern from 0006)
        migrations.AlterField(
            model_name='customobjecttypefield',
            name='name',
            field=models.CharField(
                max_length=50,
                validators=[
                    django.core.validators.RegexValidator(
                        message=(
                            'Only lowercase alphanumeric characters and underscores are allowed. '
                            'Names may not start or end with an underscore, and double underscores are not permitted.'
                        ),
                        regex='^[a-z0-9]+(_[a-z0-9]+)*$',
                    ),
                ]
            ),
        ),
        # unique_related_name constraint already added by 0006_customobjecttypefield_related_name_and_more
        migrations.AddConstraint(
            model_name='customobjecttypefield',
            constraint=models.UniqueConstraint(
                condition=models.Q(('schema_id__isnull', False)),
                fields=('schema_id', 'custom_object_type'),
                name='netbox_custom_objects_customobjecttypefield_unique_schema_id',
            ),
        ),
    ]
