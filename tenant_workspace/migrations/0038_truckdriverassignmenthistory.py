from django.db import migrations, models
import django.db.models.deletion
from django.utils import timezone
import uuid


def forwards_seed_current_assignments(apps, schema_editor):
    TruckMaster = apps.get_model('tenant_workspace', 'TruckMaster')
    TruckDriverAssignmentHistory = apps.get_model(
        'tenant_workspace', 'TruckDriverAssignmentHistory'
    )

    for truck in TruckMaster.objects.exclude(default_driver_id__isnull=True).iterator():
        exists_current = TruckDriverAssignmentHistory.objects.filter(
            truck_id=truck.pk,
            assigned_to__isnull=True,
        ).exists()
        if exists_current:
            continue
        assigned_from = getattr(truck, 'updated_at', None) or getattr(truck, 'created_at', None) or timezone.now()
        TruckDriverAssignmentHistory.objects.create(
            truck_id=truck.pk,
            driver_id=truck.default_driver_id_id,
            assigned_from=assigned_from,
            assigned_to=None,
        )


class Migration(migrations.Migration):
    dependencies = [
        ('tenant_workspace', '0037_truckmaster_default_driver_fk'),
    ]

    operations = [
        migrations.CreateModel(
            name='TruckDriverAssignmentHistory',
            fields=[
                ('assignment_id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('assigned_from', models.DateTimeField(default=timezone.now)),
                ('assigned_to', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('driver', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='truck_assignment_history', to='tenant_workspace.drivermaster')),
                ('truck', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='driver_assignments', to='tenant_workspace.truckmaster')),
            ],
            options={
                'verbose_name': 'Truck Driver Assignment History',
                'verbose_name_plural': 'Truck Driver Assignment History',
                'db_table': 'tenant_truck_driver_assignment_history',
                'ordering': ['-assigned_from', '-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='truckdriverassignmenthistory',
            index=models.Index(fields=['truck', 'assigned_to'], name='truck_drv_assign_cur_idx'),
        ),
        migrations.AddIndex(
            model_name='truckdriverassignmenthistory',
            index=models.Index(fields=['driver', 'assigned_from'], name='drv_assign_hist_idx'),
        ),
        migrations.RunPython(forwards_seed_current_assignments, migrations.RunPython.noop),
    ]

