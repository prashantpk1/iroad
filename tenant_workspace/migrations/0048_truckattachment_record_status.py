# TruckAttachment lifecycle status (Active / Inactive / Pending)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenant_workspace', '0047_alter_truckmaster_is_vendor_same_as_owner_verbose'),
    ]

    operations = [
        migrations.AddField(
            model_name='truckattachment',
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
