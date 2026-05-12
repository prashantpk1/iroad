# DriverAttachment lifecycle status (Active / Inactive / Pending)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenant_workspace', '0048_truckattachment_record_status'),
    ]

    operations = [
        migrations.AddField(
            model_name='driverattachment',
            name='record_status',
            field=models.CharField(
                choices=[
                    ('Active', 'Active'),
                    ('Inactive', 'Inactive'),
                    ('Pending', 'Pending'),
                ],
                default='Active',
                help_text='Current status of the attachment',
                max_length=20,
                verbose_name='Status',
            ),
        ),
    ]
