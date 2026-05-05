# Generated manually — TR-001 truck_images (multiple per truck).

import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenant_workspace', '0028_truckattachment'),
    ]

    operations = [
        migrations.CreateModel(
            name='TruckImage',
            fields=[
                (
                    'image_id',
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    'image',
                    models.ImageField(
                        max_length=500,
                        upload_to='trucks/images/',
                    ),
                ),
                ('uploaded_at', models.DateTimeField(auto_now_add=True)),
                (
                    'truck',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='truck_images',
                        to='tenant_workspace.truckmaster',
                    ),
                ),
            ],
            options={
                'verbose_name': 'Truck Image',
                'verbose_name_plural': 'Truck Images',
                'db_table': 'tenant_truck_images',
                'ordering': ['uploaded_at'],
            },
        ),
    ]
