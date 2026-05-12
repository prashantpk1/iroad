# Generated manually for shared marketing FAQs, pricing benefits repeater,
# pricing page header background, and removal of duplicate pricing FAQ model.

import django.core.validators
import django.db.models.deletion
import iroad_frontend.models
from django.db import migrations, models


def forwards_migrate_faqs_benefits(apps, schema_editor):
    Home = apps.get_model('iroad_frontend', 'HomePageContent')
    HMF = apps.get_model('iroad_frontend', 'HomeMarketingFaqItem')
    HPB = apps.get_model('iroad_frontend', 'HomePricingBenefit')
    PF = apps.get_model('iroad_frontend', 'PricingFaqItem')
    HPT = apps.get_model('iroad_frontend', 'HomePricingTier')

    home = Home.objects.filter(pk=1).first()
    if not home:
        return

    defaults_en = [
        'Get a 30-day free trial',
        'No hidden fees',
        'You can cancel anytime',
    ]

    if not HMF.objects.filter(home=home).exists():
        for row in PF.objects.all().order_by('order', 'pk'):
            HMF.objects.create(
                home=home,
                order=row.order,
                question_en=row.question_en or '',
                question_ar=row.question_ar or '',
                answer_en=row.answer_en or '',
                answer_ar=row.answer_ar or '',
                is_active=row.is_active,
            )

    if not HPB.objects.filter(home=home).exists():
        tier = HPT.objects.filter(home=home).order_by('order').first()
        triple = []
        if tier:
            triple = [
                (tier.pricing_benefit_1_text_en, tier.pricing_benefit_1_text_ar),
                (tier.pricing_benefit_2_text_en, tier.pricing_benefit_2_text_ar),
                (tier.pricing_benefit_3_text_en, tier.pricing_benefit_3_text_ar),
            ]
        for i in range(3):
            if tier and triple:
                en = (triple[i][0] or '').strip()
                ar = (triple[i][1] or '').strip()
            else:
                en, ar = '', ''
            if not en:
                en = defaults_en[i]
            HPB.objects.create(
                home=home,
                order=i,
                text_en=en,
                text_ar=ar,
                is_active=True,
            )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('iroad_frontend', '0007_pricing_page_cms'),
    ]

    operations = [
        migrations.CreateModel(
            name='HomePricingBenefit',
            fields=[
                ('id', models.BigAutoField(
                    auto_created=True, primary_key=True, serialize=False,
                    verbose_name='ID')),
                ('order', models.PositiveSmallIntegerField(default=0)),
                ('text_en', models.CharField(
                    blank=True, default='', max_length=300)),
                ('text_ar', models.CharField(
                    blank=True, default='', max_length=300)),
                ('icon', models.FileField(
                    blank=True, null=True,
                    upload_to=iroad_frontend.models.home_upload_path,
                    validators=[django.core.validators.FileExtensionValidator(
                        allowed_extensions=[
                            'svg', 'png', 'jpg', 'jpeg', 'gif', 'webp',
                            'ico', 'bmp', 'avif', 'tif', 'tiff',
                        ])])),
                ('is_active', models.BooleanField(default=True)),
                ('home', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='pricing_benefits',
                    to='iroad_frontend.homepagecontent')),
            ],
            options={
                'verbose_name': 'Pricing Benefit',
                'verbose_name_plural': 'Pricing Benefits',
                'db_table': 'iroad_frontend_home_pricing_benefit',
                'ordering': ['order'],
            },
        ),
        migrations.CreateModel(
            name='HomeMarketingFaqItem',
            fields=[
                ('id', models.BigAutoField(
                    auto_created=True, primary_key=True, serialize=False,
                    verbose_name='ID')),
                ('order', models.PositiveSmallIntegerField(default=0)),
                ('question_en', models.CharField(
                    blank=True, default='', max_length=500)),
                ('question_ar', models.CharField(
                    blank=True, default='', max_length=500)),
                ('answer_en', models.TextField(blank=True, default='')),
                ('answer_ar', models.TextField(blank=True, default='')),
                ('is_active', models.BooleanField(default=True)),
                ('home', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='marketing_faq_items',
                    to='iroad_frontend.homepagecontent')),
            ],
            options={
                'verbose_name': 'Marketing FAQ Item',
                'verbose_name_plural': 'Marketing FAQ Items',
                'db_table': 'iroad_frontend_home_marketing_faq_item',
                'ordering': ['order'],
            },
        ),
        migrations.AddField(
            model_name='pricingpagecontent',
            name='page_header_background',
            field=models.FileField(
                blank=True,
                help_text=(
                    'Optional background for the pricing page header. '
                    'If empty, a solid theme fallback is used (no stock photo).'
                ),
                null=True,
                upload_to=iroad_frontend.models.pricing_upload_path,
                validators=[django.core.validators.FileExtensionValidator(
                    allowed_extensions=[
                        'svg', 'png', 'jpg', 'jpeg', 'gif', 'webp',
                        'ico', 'bmp', 'avif', 'tif', 'tiff',
                    ])],
                verbose_name='Page header background image',
            ),
        ),
        migrations.RunPython(forwards_migrate_faqs_benefits, noop_reverse),
        migrations.RemoveField(
            model_name='homepricingtier',
            name='pricing_benefit_1_text_ar',
        ),
        migrations.RemoveField(
            model_name='homepricingtier',
            name='pricing_benefit_1_text_en',
        ),
        migrations.RemoveField(
            model_name='homepricingtier',
            name='pricing_benefit_2_text_ar',
        ),
        migrations.RemoveField(
            model_name='homepricingtier',
            name='pricing_benefit_2_text_en',
        ),
        migrations.RemoveField(
            model_name='homepricingtier',
            name='pricing_benefit_3_text_ar',
        ),
        migrations.RemoveField(
            model_name='homepricingtier',
            name='pricing_benefit_3_text_en',
        ),
        migrations.DeleteModel(
            name='PricingFaqItem',
        ),
    ]
