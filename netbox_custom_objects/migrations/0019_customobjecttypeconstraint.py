import django.contrib.postgres.fields
import django.core.validators
import django.db.models.deletion
import netbox.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('netbox_custom_objects', '0018_alter_customobjecttypefield_schema_id'),
    ]

    operations = [
        migrations.CreateModel(
            name='CustomObjectTypeConstraint',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('created', models.DateTimeField(auto_now_add=True, null=True)),
                ('last_updated', models.DateTimeField(auto_now=True, null=True)),
                ('name', models.CharField(
                    max_length=50,
                    validators=[django.core.validators.RegexValidator(
                        message=(
                            'Only lowercase alphanumeric characters and underscores are allowed. Names may not '
                            'start or end with an underscore, and double underscores are not permitted.'
                        ),
                        regex='^[a-z0-9]+(_[a-z0-9]+)*$',
                    )],
                )),
                ('type', models.CharField(default='unique', max_length=50)),
                ('field_schema_ids', django.contrib.postgres.fields.ArrayField(
                    base_field=models.PositiveIntegerField(),
                )),
                ('case_insensitive', models.BooleanField(default=False)),
                ('nulls_distinct', models.BooleanField(default=True)),
                ('description', models.CharField(blank=True, max_length=200)),
                ('custom_object_type', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='constraints',
                    to='netbox_custom_objects.customobjecttype',
                )),
            ],
            options={
                'verbose_name': 'custom object type constraint',
                'verbose_name_plural': 'custom object type constraints',
                'ordering': ['custom_object_type', 'name'],
                'constraints': [models.UniqueConstraint(
                    fields=('custom_object_type', 'name'),
                    name='netbox_custom_objects_customobjecttypeconstraint_unique_name',
                )],
            },
            bases=(netbox.models.deletion.DeleteMixin, models.Model),
        ),
    ]
