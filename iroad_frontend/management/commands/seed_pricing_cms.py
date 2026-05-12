"""
Seed Pricing Page CMS (singleton + interactive steps) from designer pricing.html.
Idempotent for child rows (order + pricing). Refreshes singleton text fields each run.

FAQ accordion content is managed under About Page CMS (AboutFaqItem) and reused
on the public Pricing page — not seeded here.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from iroad_frontend.models import (
    PricingInteractiveStep,
    PricingPageContent,
)


def _pricing_singleton_text():
    return {
        'page_title_en': 'IRoad - SaaS Transport Management System Pricing Page',
        'page_title_ar': 'آيرواد - صفحة أسعار نظام إدارة النقل SaaS',
        'meta_description_en': (
            'IRoad pricing plans for transport teams — Starter, Business, and '
            'Enterprise. Compare modules, trials, and support.'
        ),
        'meta_description_ar': (
            'خطط أسعار آيرواد لفرق النقل — المبتدئ والأعمال والمؤسسات. قارن '
            'الوحدات والتجارب والدعم.'
        ),
        'page_header_h1_en': (
            'IRoad - SaaS Transport Management System Pricing Page'
        ),
        'page_header_h1_ar': 'صفحة أسعار آيرواد - نظام إدارة النقل SaaS',
        'breadcrumb_current_en': 'Pricing plans',
        'breadcrumb_current_ar': 'خطط الأسعار',
        'pricing_kicker_en': 'Pricing Plans',
        'pricing_kicker_ar': 'خطط الأسعار',
        'pricing_heading_en': (
            'Flexible pricing designed for transport businesses of all sizes'
        ),
        'pricing_heading_ar': (
            'أسعار مرنة مصممة لشركات النقل بجميع الأحجام'
        ),
        'interactive_kicker_en': 'How it works',
        'interactive_kicker_ar': 'كيف يعمل',
        'interactive_heading_en': 'Streamline your transport workflows',
        'interactive_heading_ar': 'بسّط سير عمل النقل لديك',
        'partner_kicker_en': 'Trusted by Transport Businesses Across Regions',
        'partner_kicker_ar': 'موثوق به من شركات النقل عبر المناطق',
        'partner_heading_en': 'Trusted by Transport Businesses Across Regions',
        'partner_heading_ar': 'موثوق به من شركات النقل عبر المناطق',
        'partner_body_en': (
            'IRoad helps logistics companies digitize operations and scale '
            'efficiently.'
        ),
        'partner_body_ar': (
            'تساعد آيرواد شركات اللوجستيات على رقمنة العمليات والتوسع بكفاءة.'
        ),
        'partner_cta_label_en': 'Book a Demo',
        'partner_cta_label_ar': 'احجز عرضاً',
        'partner_cta_url': '/contact/',
        'partner_email_label_en': 'Email',
        'partner_email_label_ar': 'البريد الإلكتروني',
        'partner_email_value': 'support@iroad.com',
        'partner_platform_label_en': 'Cloud-Based Platform (Accessible Anywhere)',
        'partner_platform_label_ar': 'منصة سحابية (متاحة من أي مكان)',
        'counter_1_value': '10+',
        'counter_1_label_en': 'Modules',
        'counter_1_label_ar': 'وحدات',
        'counter_2_value': '99.9%',
        'counter_2_label_en': 'Uptime',
        'counter_2_label_ar': 'وقت التشغيل',
        'counter_3_value': '100+',
        'counter_3_label_en': 'Companies',
        'counter_3_label_ar': 'شركة',
        'counter_4_value': '10K+',
        'counter_4_label_en': 'Orders Managed',
        'counter_4_label_ar': 'طلب مُدار',
        'counter_5_value': '24/7',
        'counter_5_label_en': 'Access',
        'counter_5_label_ar': 'وصول',
        'testimonials_kicker_en': 'Our Testimonials',
        'testimonials_kicker_ar': 'آراء عملائنا',
        'testimonials_heading_en': 'What transport teams say about IRoad',
        'testimonials_heading_ar': 'ماذا يقول فرق النقل عن آيرواد',
        'faq_kicker_en': 'FAQs',
        'faq_kicker_ar': 'الأسئلة الشائعة',
        'faq_heading_en': 'Answers to common questions about IRoad',
        'faq_heading_ar': 'إجابات عن أسئلة شائعة حول آيرواد',
        'faq_intro_en': (
            'Find clear, detailed answers about IRoad TMS, plans, modules, '
            'support, and data security.'
        ),
        'faq_intro_ar': (
            'اعثر على إجابات واضحة ومفصلة عن نظام آيرواد لإدارة النقل والخطط '
            'والوحدات والدعم وأمان البيانات.'
        ),
        'faq_view_all_label_en': 'View all FAQs',
        'faq_view_all_label_ar': 'عرض كل الأسئلة الشائعة',
        'faq_view_all_url': '/faqs/',
        'updated_by': 'seed_pricing_cms',
    }


INTERACTIVE_STEPS = [
    {
        'order': 0,
        'title_en': 'Smart Workflow Automation',
        'title_ar': 'أتمتة ذكية لسير العمل',
        'subtitle_en': '',
        'subtitle_ar': '',
        'body_en': 'Automate order, booking, and shipment processes',
        'body_ar': 'أتمتة عمليات الطلب والحجز والشحن',
        'detail_url': '#',
    },
    {
        'order': 1,
        'title_en': 'Real-Time Dashboard',
        'title_ar': 'لوحة تحكم لحظية',
        'subtitle_en': '',
        'subtitle_ar': '',
        'body_en': (
            'Get live insights on operations, fleet, and performance'
        ),
        'body_ar': (
            'احصل على رؤى حية للعمليات والأسطول والأداء'
        ),
        'detail_url': '#',
    },
    {
        'order': 2,
        'title_en': 'Scalable SaaS Platform',
        'title_ar': 'منصة SaaS قابلة للتوسع',
        'subtitle_en': '',
        'subtitle_ar': '',
        'body_en': 'Grow your business with flexible and scalable system',
        'body_ar': 'نمّ عملك بنظام مرن وقابل للتوسع',
        'detail_url': '#',
    },
    {
        'order': 3,
        'title_en': '24/7 Platform Access',
        'title_ar': 'وصول للمنصة على مدار الساعة',
        'subtitle_en': '',
        'subtitle_ar': '',
        'body_en': 'Access your system anytime, anywhere',
        'body_ar': 'الوصول إلى نظامك في أي وقت ومن أي مكان',
        'detail_url': '#',
    },
]


class Command(BaseCommand):
    help = 'Seed PricingPageContent and interactive steps (FAQs: use seed_about_cms).'

    @transaction.atomic
    def handle(self, *args, **options):
        pricing = PricingPageContent.get_singleton()
        data = _pricing_singleton_text()
        for key, val in data.items():
            setattr(pricing, key, val)
        pricing.save()

        for spec in INTERACTIVE_STEPS:
            order = spec['order']
            PricingInteractiveStep.objects.update_or_create(
                pricing=pricing,
                order=order,
                defaults={
                    'title_en': spec['title_en'],
                    'title_ar': spec.get('title_ar', ''),
                    'subtitle_en': spec.get('subtitle_en', ''),
                    'subtitle_ar': spec.get('subtitle_ar', ''),
                    'body_en': spec['body_en'],
                    'body_ar': spec.get('body_ar', ''),
                    'detail_url': spec.get('detail_url', '#'),
                    'is_active': True,
                },
            )

        self.stdout.write(
            self.style.SUCCESS(
                'Pricing CMS seeded (singleton + interactive steps).'
            )
        )
