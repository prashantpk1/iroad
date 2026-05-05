# Generated manually for TR-ATT-001 TruckAttachment

import datetime
import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenant_workspace', '0027_truckmaster_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='TruckAttachment',
            fields=[
                (
                    'attachment_id',
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    'attachment_date',
                    models.DateField(default=datetime.date.today),
                ),
                ('is_expiry_applicable', models.BooleanField(default=False)),
                ('expiry_date', models.DateField(blank=True, null=True)),
                (
                    'attachment_file',
                    models.FileField(
                        max_length=500,
                        upload_to='trucks/attachments/',
                    ),
                ),
                ('file_notes', models.TextField(blank=True, default='')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                (
                    'truck',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='attachments',
                        to='tenant_workspace.truckmaster',
                    ),
                ),
            ],
            options={
                'verbose_name': 'Truck Attachment',
                'verbose_name_plural': 'Truck Attachments',
                'db_table': 'tenant_truck_attachments',
                'ordering': ['-attachment_date', '-created_at'],
            },
        ),
    ]
