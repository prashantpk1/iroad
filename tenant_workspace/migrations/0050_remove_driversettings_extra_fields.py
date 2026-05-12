# Align DriverSettings with simplified tenant UI: drop unused columns and labels.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenant_workspace', '0049_driverattachment_record_status'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='driversettings',
            name='license_expiry_alert_days',
        ),
        migrations.RemoveField(
            model_name='driversettings',
            name='medical_expiry_alert_days',
        ),
        migrations.RemoveField(
            model_name='driversettings',
            name='notification_audience',
        ),
        migrations.AlterField(
            model_name='driversettings',
            name='default_driver_status',
            field=models.CharField(
                choices=[
                    ('Active', 'Active'),
                    ('Suspended', 'Suspended'),
                    ('Inactive', 'Inactive'),
                ],
                default='Active',
                help_text='Status assigned to newly created driver records.',
                max_length=20,
                verbose_name='Default Driver Status',
            ),
        ),
        migrations.AlterField(
            model_name='driversettings',
            name='document_expiry_alert_days',
            field=models.PositiveIntegerField(
                default=30,
                help_text='Days before document expiry alerts.',
                verbose_name='Document Expiry Reminder Days',
            ),
        ),
        migrations.AlterField(
            model_name='driversettings',
            name='document_upload_mandatory',
            field=models.BooleanField(
                default=False,
                help_text=(
                    'If enabled, drivers cannot be set to Active '
                    'without core identity documents.'
                ),
            ),
        ),
        migrations.AlterField(
            model_name='driversettings',
            name='driver_assignment_required',
            field=models.BooleanField(
                default=False,
                help_text='Truck cannot be dispatched without an assigned driver.',
                verbose_name='Driver Assignment Required for Truck',
            ),
        ),
    ]
