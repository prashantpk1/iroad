import tenant_workspace.models
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenant_workspace', '0054_merge_20260513_2052'),
    ]

    operations = [
        migrations.AddField(
            model_name='tenantshipmentsurcharge',
            name='attachment_file',
            field=models.FileField(
                blank=True,
                max_length=500,
                null=True,
                upload_to=tenant_workspace.models.surcharge_attachment_upload_to,
            ),
        ),
    ]
