"""
Seed default About Page CMS (singleton + repeaters) from designer about.html.
Idempotent: skips child rows that already exist for the same about + order.
Always refreshes AboutPageContent text fields to the seed defaults.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from iroad_frontend.models import (
    AboutApproachPillar,
    AboutFaqItem,
    AboutHowWorkStep,
    AboutPageContent,
)

# Designer about.html — English copy (ellipsis in comments = full text below).


def _about_singleton_text():
    return {
        'page_title_en': (
            'About IRoad - Transport Management SaaS Platform'
        ),
        'page_header_h1_en': 'About IRoad',
        'breadcrumb_current_en': 'About IRoad',
        'about_kicker_en': 'About IRoad',
        'about_heading_part1_en': 'Driving Transport Efficiency Through',
        'about_heading_part2_en': 'Smart SaaS Technology',
        'about_heading_part3_en': 'for modern logistics teams.',
        'about_counter_1_value': '24/7',
        'about_counter_1_label_en': 'System Availability',
        'about_counter_2_value': '100+',
        'about_counter_2_label_en': 'Companies Using IRoad',
        'about_body_en': (
            'We provide a complete digital ecosystem for transport '
            'companies to manage workflows efficiently and scale '
            'operations.'
        ),
        'about_list_item_1_en': 'End-to-End Workflow Automation',
        'about_list_item_2_en': 'Real-Time Tracking & Insights',
        'about_explore_label_en': 'Explore Platform',
        'about_explore_url': '/about/',
        'about_footer_text_en': (
            'IRoad is a modern transport management system designed to help '
            'logistics companies streamline operations, manage fleet, and '
            'gain full business visibility.'
        ),
        'about_footer_cta_label_en': 'Book a Demo',
        'about_footer_cta_url': '/contact/',
        'about_rating_value': '4.9/5',
        'about_review_label_en': '4,200+ reviews',
        'approach_kicker_en': 'Our Approach',
        'approach_heading_en': 'How IRoad Transforms Transport Operations',
        'approach_body_en': (
            'We focus on automation, visibility, and control to simplify '
            'complex logistics workflows.'
        ),
        'approach_cta_label_en': 'Book a Demo',
        'approach_cta_url': '/contact/',
        'how_kicker_en': 'How It Works',
        'how_heading_en': 'How IRoad streamlines your transport workflows',
        'how_footer_text_en': (
            'Automate operations, improve visibility, and scale with '
            'confidence using IRoad.'
        ),
        'how_footer_link_label_en': 'Start Free Trial',
        'how_footer_link_url': '/contact/',
        'how_rating_value': '4.9/5',
        'how_review_label_en': '4,200+ reviews',
        'faq_kicker_en': 'FAQs',
        'faq_heading_en': 'Answers to common questions about IRoad TMS',
        'faq_intro_en': (
            'Learn how IRoad helps transport companies automate workflows, '
            'manage fleets, and improve operational visibility.'
        ),
        'faq_view_all_label_en': "View all FAQ's",
        'faq_view_all_url': '#',
        'updated_by': 'seed_about_cms',
    }


APPROACH_PILLARS = [
    {
        'order': 1,
        'title_en': 'Our Mission',
        'body_en': (
            'To simplify transport operations through powerful and '
            'easy-to-use SaaS solutions.'
        ),
    },
    {
        'order': 2,
        'title_en': 'Our Vision',
        'body_en': (
            'To become the leading digital platform for transport and '
            'logistics management.'
        ),
    },
    {
        'order': 3,
        'title_en': 'Core Value',
        'body_en': (
            'Efficiency, transparency, scalability, and innovation.'
        ),
    },
]

HOW_WORK_STEPS = [
    {
        'order': 1,
        'step_number': '01',
        'title_en': 'Create Order',
        'body_en': 'Add and manage transport orders.',
    },
    {
        'order': 2,
        'step_number': '02',
        'title_en': 'Assign Fleet & Driver',
        'body_en': 'Allocate vehicles and drivers efficiently.',
    },
    {
        'order': 3,
        'step_number': '03',
        'title_en': 'Track Shipment',
        'body_en': 'Monitor real-time shipment status.',
    },
    {
        'order': 4,
        'step_number': '04',
        'title_en': 'Delivery & POD',
        'body_en': 'Complete delivery with proof and updates.',
    },
]

FAQ_ITEMS = [
    {
        'order': 1,
        'question_en': 'Q1. What is IRoad TMS?',
        'answer_en': (
            'IRoad is a SaaS-based transport management system that '
            'helps companies manage orders, booking, fleet, drivers, '
            'finance, POD, and reports in one platform.'
        ),
    },
    {
        'order': 2,
        'question_en': 'Q2. Can I manage fleet and drivers?',
        'answer_en': (
            'Yes. IRoad includes fleet and driver management tools for '
            'allocation, availability tracking, and assignment control.'
        ),
    },
    {
        'order': 3,
        'question_en': 'Q3. Does it support invoicing & payments?',
        'answer_en': (
            'Yes. IRoad includes integrated finance features for '
            'invoicing, payments, and transport-related financial '
            'tracking.'
        ),
    },
    {
        'order': 4,
        'question_en': 'Q4. Is it suitable for small transport companies?',
        'answer_en': (
            'Absolutely. IRoad is designed for businesses of different '
            'sizes, from small teams to multi-branch transport '
            'operations.'
        ),
    },
    {
        'order': 5,
        'question_en': 'Q5. Can I track shipments in real-time?',
        'answer_en': (
            'Yes. IRoad provides real-time shipment tracking and status '
            'updates from booking through delivery and POD.'
        ),
    },
    {
        'order': 6,
        'question_en': 'Q6. Is data secure?',
        'answer_en': (
            'Yes. IRoad uses secure, role-based access and '
            'platform-level safeguards to protect operational and '
            'financial data.'
        ),
    },
]


class Command(BaseCommand):
    help = 'Seed default about page CMS content (idempotent for repeaters).'

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE('Seeding about page CMS...'))
        try:
            with transaction.atomic():
                self._seed_about_singleton()
                self._seed_approach_pillars()
                self._seed_how_work_steps()
                self._seed_faq_items()
        except Exception as exc:
            self.stderr.write(self.style.ERROR(f'Seed failed: {exc}'))
            raise
        self.stdout.write(self.style.SUCCESS('Done.'))

    def _seed_about_singleton(self):
        about = AboutPageContent.get_singleton()
        data = _about_singleton_text()
        for key, value in data.items():
            setattr(about, key, value)
        about.save()
        self.stdout.write(
            self.style.SUCCESS(
                '  AboutPageContent (pk=1): updated text fields.'
            )
        )

    def _seed_approach_pillars(self):
        about = AboutPageContent.get_singleton()
        for row in APPROACH_PILLARS:
            order = row['order']
            if AboutApproachPillar.objects.filter(
                about=about, order=order
            ).exists():
                self.stdout.write(
                    f'  AboutApproachPillar order={order}: '
                    'already exists, skipped.'
                )
                continue
            AboutApproachPillar.objects.create(
                about=about,
                order=order,
                title_en=row['title_en'],
                title_ar='',
                body_en=row['body_en'],
                body_ar='',
                is_active=True,
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f'  AboutApproachPillar order={order} '
                    f'({row["title_en"]}): created.'
                )
            )

    def _seed_how_work_steps(self):
        about = AboutPageContent.get_singleton()
        for row in HOW_WORK_STEPS:
            order = row['order']
            if AboutHowWorkStep.objects.filter(
                about=about, order=order
            ).exists():
                self.stdout.write(
                    f'  AboutHowWorkStep order={order}: '
                    'already exists, skipped.'
                )
                continue
            AboutHowWorkStep.objects.create(
                about=about,
                order=order,
                step_number=row['step_number'],
                title_en=row['title_en'],
                title_ar='',
                body_en=row['body_en'],
                body_ar='',
                is_active=True,
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f'  AboutHowWorkStep order={order} '
                    f'({row["title_en"]}): created.'
                )
            )

    def _seed_faq_items(self):
        about = AboutPageContent.get_singleton()
        for row in FAQ_ITEMS:
            order = row['order']
            if AboutFaqItem.objects.filter(about=about, order=order).exists():
                self.stdout.write(
                    f'  AboutFaqItem order={order}: already exists, skipped.'
                )
                continue
            AboutFaqItem.objects.create(
                about=about,
                order=order,
                question_en=row['question_en'],
                question_ar='',
                answer_en=row['answer_en'],
                answer_ar='',
                is_active=True,
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f'  AboutFaqItem order={order}: created.'
                )
            )
