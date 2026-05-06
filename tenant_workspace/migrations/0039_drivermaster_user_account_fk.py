from django.db import migrations, models
import django.db.models.deletion


def forwards_backfill_user_account_fk(apps, schema_editor):
    DriverMaster = apps.get_model('tenant_workspace', 'DriverMaster')
    TenantUser = apps.get_model('tenant_workspace', 'TenantUser')

    existing_user_ids = set(str(v) for v in TenantUser.objects.values_list('user_id', flat=True))

    for row in DriverMaster.objects.all().iterator():
        raw_value = (getattr(row, 'user_account_id', '') or '').strip()
        if not raw_value:
            continue
        if raw_value not in existing_user_ids:
            continue
        row.user_account_fk_id = raw_value
        row.save()


class Migration(migrations.Migration):
    dependencies = [
        ('tenant_workspace', '0038_truckdriverassignmenthistory'),
    ]

    operations = [
        migrations.AddField(
            model_name='drivermaster',
            name='user_account_fk',
            field=models.ForeignKey(
                blank=True,
                null=True,
                db_column='user_account_fk_id',
                on_delete=django.db.models.deletion.PROTECT,
                related_name='legacy_linked_driver_profile',
                to='tenant_workspace.tenantuser',
            ),
        ),
        migrations.RunPython(forwards_backfill_user_account_fk, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name='drivermaster',
            name='user_account_id',
        ),
        migrations.RenameField(
            model_name='drivermaster',
            old_name='user_account_fk',
            new_name='user_account_id',
        ),
        migrations.AlterField(
            model_name='drivermaster',
            name='user_account_id',
            field=models.OneToOneField(
                blank=True,
                null=True,
                db_column='user_account_id',
                on_delete=django.db.models.deletion.PROTECT,
                related_name='linked_driver_profile',
                to='tenant_workspace.tenantuser',
            ),
        ),
    ]

