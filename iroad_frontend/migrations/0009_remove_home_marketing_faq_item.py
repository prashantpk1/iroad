# Remove HomeMarketingFaqItem; pricing uses AboutFaqItem instead.
# If About has no FAQ rows yet but HomeMarketingFaqItem has data, copy once.

from django.db import migrations


def forwards_copy_hmf_to_about_if_empty(apps, schema_editor):
    About = apps.get_model('iroad_frontend', 'AboutPageContent')
    AboutFaq = apps.get_model('iroad_frontend', 'AboutFaqItem')
    Home = apps.get_model('iroad_frontend', 'HomePageContent')
    HMF = apps.get_model('iroad_frontend', 'HomeMarketingFaqItem')

    about = About.objects.order_by('pk').first()
    home = Home.objects.order_by('pk').first()
    if not about or not home:
        return
    if AboutFaq.objects.filter(about=about).exists():
        return
    for row in HMF.objects.filter(home=home).order_by('order', 'pk'):
        AboutFaq.objects.create(
            about=about,
            order=row.order,
            question_en=row.question_en or '',
            question_ar=row.question_ar or '',
            answer_en=row.answer_en or '',
            answer_ar=row.answer_ar or '',
            is_active=row.is_active,
        )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('iroad_frontend', '0008_marketing_faq_pricing_benefits_header'),
    ]

    operations = [
        migrations.RunPython(forwards_copy_hmf_to_about_if_empty, noop_reverse),
        migrations.DeleteModel(name='HomeMarketingFaqItem'),
    ]
