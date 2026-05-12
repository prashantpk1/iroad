from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('iroad_frontend', '0013_about_mid_column_copy'),
    ]

    operations = [
        migrations.AddField(
            model_name='pricingpagecontent',
            name='partner_email_label_ar',
            field=models.CharField(
                blank=True,
                default='البريد الإلكتروني',
                max_length=100,
            ),
        ),
    ]
