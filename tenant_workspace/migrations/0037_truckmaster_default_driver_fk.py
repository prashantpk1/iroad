from django.db import migrations, models
import django.db.models.deletion


def forwards_backfill_default_driver_fk(apps, schema_editor):
    """
    Backfill TruckMaster.default_driver_id FK using the legacy stored value:
    - old implementation stored `driver_code` in TruckMaster.default_driver_id (CharField)
    - new FK should point to DriverMaster where DriverMaster.driver_code matches
    """
    TruckMaster = apps.get_model('tenant_workspace', 'TruckMaster')
    DriverMaster = apps.get_model('tenant_workspace', 'DriverMaster')

    # Build driver_code -> pk map for quick lookup.
    driver_code_map = {}
    for d in DriverMaster.objects.all().only('driver_code', 'pk'):
        code = (d.driver_code or '').strip()
        if code:
            driver_code_map[code] = d.pk

    qs = TruckMaster.objects.all().only('default_driver_id', 'pk')
    # Historical model also has the temporary field `default_driver_fk`.
    for truck in qs.iterator():
        legacy_code = (getattr(truck, 'default_driver_id', '') or '').strip()
        if not legacy_code:
            continue
        driver_pk = driver_code_map.get(legacy_code)
        if not driver_pk:
            continue
        truck.default_driver_fk_id = driver_pk
        truck.save()


class Migration(migrations.Migration):
    dependencies = [
        ('tenant_workspace', '0036_driverattachment_arabic_label_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='truckmaster',
            name='default_driver_fk',
            field=models.ForeignKey(
                null=True,
                blank=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='trucks_as_default_driver',
                to='tenant_workspace.drivermaster',
            ),
        ),
        migrations.RunPython(forwards_backfill_default_driver_fk, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name='truckmaster',
            name='default_driver_id',
        ),
        migrations.RenameField(
            model_name='truckmaster',
            old_name='default_driver_fk',
            new_name='default_driver_id',
        ),
    ]

