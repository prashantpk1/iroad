from django.db import migrations, models
import uuid


class Migration(migrations.Migration):
    dependencies = [
        ('tenant_workspace', '0039_drivermaster_user_account_fk'),
    ]

    operations = [
        migrations.CreateModel(
            name='TruckSettings',
            fields=[
                ('settings_id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('default_truck_status', models.CharField(choices=[('Active', 'Active'), ('In Maintenance', 'In Maintenance'), ('Inactive', 'Inactive')], default='Active', max_length=20)),
                ('maintenance_reminder_days', models.PositiveIntegerField(default=30)),
                ('insurance_expiry_alert_days', models.PositiveIntegerField(default=30)),
                ('registration_expiry_alert_days', models.PositiveIntegerField(default=30)),
                ('fuel_consumption_tracking_enabled', models.BooleanField(default=True)),
                ('driver_assignment_required', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Truck Settings',
                'verbose_name_plural': 'Truck Settings',
                'db_table': 'tenant_truck_settings',
            },
        ),
    ]

