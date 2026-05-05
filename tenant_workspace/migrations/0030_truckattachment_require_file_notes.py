# TR-ATT-001: file_notes required; backfill blanks for existing tenant rows.

from django.db import migrations, models


def forwards_fill_empty_file_notes(apps, schema_editor):
    TruckAttachment = apps.get_model('tenant_workspace', 'TruckAttachment')
    TruckAttachment.objects.filter(file_notes='').update(file_notes='N/A')


def backwards_noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('tenant_workspace', '0029_truckimage'),
    ]

    operations = [
        migrations.RunPython(forwards_fill_empty_file_notes, backwards_noop),
        migrations.AlterField(
            model_name='truckattachment',
            name='file_notes',
            field=models.TextField(
                help_text='Notes about this attachment document',
            ),
        ),
    ]
