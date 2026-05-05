from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenant_workspace', '0031_truckattachment_identity_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='truckattachment',
            name='is_deleted',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='truckattachment',
            name='is_deleted_by',
            field=models.CharField(blank=True, default='', max_length=64),
        ),
    ]
