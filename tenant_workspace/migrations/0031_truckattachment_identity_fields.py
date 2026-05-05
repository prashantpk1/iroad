# Generated manually: add TruckAttachment identity fields

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenant_workspace', '0030_truckattachment_require_file_notes'),
    ]

    operations = [
        migrations.AddField(
            model_name='truckattachment',
            name='arabic_label',
            field=models.CharField(blank=True, default='', max_length=200),
        ),
        migrations.AddField(
            model_name='truckattachment',
            name='attachment_no',
            field=models.CharField(
                blank=True,
                help_text='Auto-generated attachment number',
                max_length=64,
                null=True,
                unique=True,
            ),
        ),
        migrations.AddField(
            model_name='truckattachment',
            name='attachment_sequence',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='truckattachment',
            name='doc_ref_number',
            field=models.CharField(blank=True, default='', max_length=120),
        ),
        migrations.AddField(
            model_name='truckattachment',
            name='english_label',
            field=models.CharField(blank=True, default='', max_length=200),
        ),
    ]
