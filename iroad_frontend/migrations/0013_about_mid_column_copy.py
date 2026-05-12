from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('iroad_frontend', '0012_pricing_faq_item'),
    ]

    operations = [
        migrations.AddField(
            model_name='aboutpagecontent',
            name='about_mid_body_ar',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='aboutpagecontent',
            name='about_mid_body_en',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='aboutpagecontent',
            name='about_mid_title_ar',
            field=models.CharField(blank=True, default='', max_length=300),
        ),
        migrations.AddField(
            model_name='aboutpagecontent',
            name='about_mid_title_en',
            field=models.CharField(blank=True, default='', max_length=300),
        ),
    ]
