"""
Seed default Home Page CMS content (singleton + repeaters) from designer index.
Idempotent: skips child rows that already exist for the same home + order.
Always refreshes HomePageContent text fields (not images) to designer defaults.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from iroad_frontend.models import (
    HomeMapLocation,
    HomePageContent,
    HomePricingTier,
    HomeServiceCard,
    HomeTestimonial,
)

# Designer index.html — English copy (AR left blank unless model had defaults).
TESTIMONIAL_QUOTE_EN = (
    'Their logistics solutions transformed our supply chain. On-time delivery '
    'and real-time tracking have made our operations seamless reliable, '
    'efficient, and professional service every time.'
)


def _home_text_defaults():
    """Char/Text fields only; images use static fallbacks until uploaded in CMS."""
    return {
        'page_title_en': 'IRoad - Transport Management System',
        'page_title_ar': '',
        'meta_description_en': (
            'IRoad is an all-in-one transport management system for modern '
            'logistics companies — manage operations, fleet, and finances in one '
            'powerful SaaS platform.'
        ),
        'meta_description_ar': '',
        'logo_alt_en': 'Logo',
        'logo_alt_ar': 'آيروود',
        'nav_home_en': 'Home',
        'nav_home_ar': 'الرئيسية',
        'nav_about_en': 'About',
        'nav_about_ar': 'عن الشركة',
        'nav_pricing_en': 'Pricing',
        'nav_pricing_ar': 'الأسعار',
        'nav_contact_en': 'Contact',
        'nav_contact_ar': 'اتصل بنا',
        'header_cta_small_title_en': 'Book a Demo',
        'header_cta_small_title_ar': '',
        'header_cta_title_en': 'Book a Demo',
        'header_cta_title_ar': 'احجز عرضاً',
        'header_cta_url': '/contact/',
        'header_sign_in_en': 'Sign In',
        'header_sign_in_ar': 'تسجيل الدخول',
        'header_sign_in_url': '/login/',
        'hero_kicker_en': 'Welcome to IRoad',
        'hero_kicker_ar': '',
        'hero_heading_en': (
            'All-in-One Transport Management System for Modern Logistics Companies'
        ),
        'hero_heading_ar': '',
        'hero_subheading_en': (
            'Manage your operations, fleet, and finances in one powerful SaaS platform.'
        ),
        'hero_subheading_ar': '',
        'hero_cta_label_en': 'Start Free Trial',
        'hero_cta_label_ar': 'ابدأ مجاناً',
        'hero_cta_url': '/contact/',
        'hero_bullet_1_en': 'End-to-End Transport Workflow',
        'hero_bullet_1_ar': '',
        'hero_bullet_2_en': 'Real-Time Operations Visibility',
        'hero_bullet_2_ar': '',
        'hero_bullet_3_en': 'Integrated Finance & Reporting',
        'hero_bullet_3_ar': '',
        'about_kicker_en': 'About IRoad',
        'about_kicker_ar': '',
        'about_heading_en': 'A Complete Transport Management System Built for Efficiency',
        'about_heading_ar': '',
        'about_body_en': (
            'IRoad is a powerful SaaS platform designed to help transport companies '
            'manage operations, fleet, finance, and delivery workflows from a single system.'
        ),
        'about_body_ar': '',
        'about_point_1_title_en': 'Centralized Operations Control',
        'about_point_1_title_ar': '',
        'about_point_1_body_en': (
            'Manage transport orders, bookings, and execution with complete control.'
        ),
        'about_point_1_body_ar': '',
        'about_point_2_title_en': 'Role-Based Access (RBAC)',
        'about_point_2_title_ar': '',
        'about_point_2_body_en': (
            'Securely manage permissions across teams with role-based workflows.'
        ),
        'about_point_2_body_ar': '',
        'about_experience_number': '25',
        'about_experience_suffix_en': '+',
        'about_experience_suffix_ar': '+',
        'about_experience_caption_en': 'Built for Modern Logistics Teams',
        'about_experience_caption_ar': '',
        'about_cta_label_en': 'More About Us',
        'about_cta_label_ar': 'المزيد عنا',
        'about_cta_url': '/about/',
        'services_kicker_en': 'Core Features',
        'services_kicker_ar': '',
        'services_heading_en': (
            'Everything you need to control transport operations in one place'
        ),
        'services_heading_ar': '',
        'services_footer_text_en': "Let's build something great together.",
        'services_footer_text_ar': '',
        'services_footer_link_label_en': 'Start Free Trial',
        'services_footer_link_label_ar': 'ابدأ مجاناً',
        'services_footer_link_url': '/contact/',
        'why_kicker_en': 'Why Choose IRoad',
        'why_kicker_ar': '',
        'why_heading_en': 'Full control, automation, and real-time visibility',
        'why_heading_ar': '',
        'why_point_1_title_en': 'Full Operational Control',
        'why_point_1_title_ar': '',
        'why_point_1_body_en': (
            'Manage roles, permissions, and workflows with complete control.'
        ),
        'why_point_1_body_ar': '',
        'why_point_2_title_en': 'Smart Workflow Automation',
        'why_point_2_title_ar': '',
        'why_point_2_body_en': 'Streamline booking, shipment, and delivery processes.',
        'why_point_2_body_ar': '',
        'why_point_3_title_en': 'Integrated Finance',
        'why_point_3_title_ar': '',
        'why_point_3_body_en': (
            'Handle invoices, payments, and adjustments in one place.'
        ),
        'why_point_3_body_ar': '',
        'features_kicker_en': 'Advanced Capabilities',
        'features_kicker_ar': '',
        'features_heading_en': 'Advanced tools built for control and visibility',
        'features_heading_ar': '',
        'features_footer_text_en': 'Transform how your team operates with IRoad SaaS.',
        'features_footer_text_ar': '',
        'features_footer_link_label_en': 'Start Free Trial',
        'features_footer_link_label_ar': '',
        'features_footer_link_url': '/contact/',
        'features_rating_value': '4.9/5',
        'features_review_count_label_en': '4,200+ reviews',
        'features_review_count_label_ar': '+4,200 تقييم',
        'feature_a_title_en': 'Real-Time Dashboard Insights',
        'feature_a_title_ar': '',
        'feature_a_body_en': (
            'Get live operational insights across orders, bookings, and shipments.'
        ),
        'feature_a_body_ar': '',
        'feature_b_title_en': 'Shipment Lifecycle Tracking',
        'feature_b_title_ar': '',
        'feature_b_body_en': (
            'Track every stage and capture Proof of Delivery (POD) in one flow.'
        ),
        'feature_b_body_ar': '',
        'feature_b_list_item_1_en': 'Orders',
        'feature_b_list_item_1_ar': '',
        'feature_b_list_item_2_en': 'POD',
        'feature_b_list_item_2_ar': '',
        'feature_c_title_en': 'Multi-Currency Support',
        'feature_c_title_ar': '',
        'feature_c_body_en': (
            'Run invoicing and payments with automated currency conversions.'
        ),
        'feature_c_body_ar': '',
        'feature_d_title_en': 'Vendor & Client Management',
        'feature_d_title_ar': '',
        'feature_d_body_en': (
            'Centralize vendors and client records for faster coordination.'
        ),
        'feature_d_body_ar': '',
        'feature_d_list_item_1_en': 'Vendors',
        'feature_d_list_item_1_ar': '',
        'feature_d_list_item_2_en': 'Clients',
        'feature_d_list_item_2_ar': '',
        'pricing_kicker_en': 'Pricing Plans',
        'pricing_kicker_ar': '',
        'pricing_heading_en': 'Pricing that scales with your transport team',
        'pricing_heading_ar': '',
        'business_counter_value': '200',
        'business_counter_suffix_en': '+',
        'business_counter_suffix_ar': '+',
        'business_counter_caption_en': (
            'Helping logistics businesses digitize and scale operations globally.'
        ),
        'business_counter_caption_ar': '',
        'business_heading_en': 'Used by Transport Companies Across Regions',
        'business_heading_ar': '',
        'business_body_en': (
            'Helping logistics businesses digitize and scale operations globally.'
        ),
        'business_body_ar': '',
        'business_bullet_1_en': 'Digitization',
        'business_bullet_1_ar': '',
        'business_bullet_2_en': 'Scalability',
        'business_bullet_2_ar': '',
        'testimonials_kicker_en': 'Our Testimonials',
        'testimonials_kicker_ar': '',
        'testimonials_heading_en': 'What transport teams say about IRoad',
        'testimonials_heading_ar': '',
        'testimonials_happy_count': '10k',
        'testimonials_happy_label_en': 'Trusted by World Customer',
        'testimonials_happy_label_ar': '',
        'footer_cta_left_en': "Let's Connect",
        'footer_cta_left_ar': 'لنتواصل',
        'footer_brand_text_en': 'IRoad',
        'footer_brand_text_ar': 'آيروود',
        'footer_cta_right_en': 'Book a Demo',
        'footer_cta_right_ar': 'احجز عرضاً',
        'footer_about_blurb_en': (
            'IRoad is a modern SaaS platform for transport companies to manage '
            'operations, fleet, and finance efficiently.'
        ),
        'footer_about_blurb_ar': '',
        'footer_social_pinterest_url': '#',
        'footer_social_x_url': '#',
        'footer_social_facebook_url': '#',
        'footer_social_instagram_url': '#',
        'footer_column_title_en': 'Features',
        'footer_column_title_ar': 'الميزات',
        'footer_link_1_label_en': 'About',
        'footer_link_1_label_ar': 'عن الشركة',
        'footer_link_1_url': '/about/',
        'footer_link_2_label_en': 'Pricing',
        'footer_link_2_label_ar': 'الأسعار',
        'footer_link_2_url': '/pricing/',
        'footer_link_3_label_en': 'Contact',
        'footer_link_3_label_ar': 'اتصل بنا',
        'footer_link_3_url': '/contact/',
        'footer_newsletter_title_en': 'Get Product Updates',
        'footer_newsletter_title_ar': 'احصل على تحديثات المنتج',
        'footer_newsletter_desc_en': (
            'Get IRoad product news, releases, and feature updates.'
        ),
        'footer_newsletter_desc_ar': '',
        'footer_newsletter_placeholder_en': 'Enter your email',
        'footer_newsletter_placeholder_ar': 'أدخل بريدك الإلكتروني',
        'footer_newsletter_action_url': '#',
        'footer_copyright_en': 'Copyright © 2026 All Rights Reserved.',
        'footer_copyright_ar': '© 2026 جميع الحقوق محفوظة.',
        'footer_credit_en': 'Design & Developed by Redspark Technologies',
        'footer_credit_ar': '',
        'updated_by': 'seed_home_cms',
    }


SERVICE_CARDS = [
    {
        'order': 0,
        'title_en': 'Order & Booking Management',
        'summary_en': (
            'Manage transport orders, bookings, and execution with complete control.'
        ),
        'detail_url': '#',
        'cta_label_en': 'Explore Feature',
    },
    {
        'order': 1,
        'title_en': 'Fleet & Driver Management',
        'summary_en': (
            'Manage fleet availability, drivers, and assignments with real-time '
            'scheduling.'
        ),
        'detail_url': '#',
        'cta_label_en': 'Explore Feature',
    },
    {
        'order': 2,
        'title_en': 'Shipment Tracking & POD',
        'summary_en': (
            'Track shipments end-to-end and capture Proof of Delivery (POD) in one '
            'workflow.'
        ),
        'detail_url': '#',
        'cta_label_en': 'Explore Feature',
    },
    {
        'order': 3,
        'title_en': 'Finance & Invoicing',
        'summary_en': (
            'Automate invoices, payments, and adjustments with integrated finance tools.'
        ),
        'detail_url': '#',
        'cta_label_en': 'Explore Feature',
    },
]

PRICING_TIERS = [
    {
        'order': 0,
        'name_en': 'Starter',
        'summary_en': (
            'Basic modules with essential tools for small transport teams.'
        ),
        'price_display_en': '$19<sub>/month</sub>',
        'bullet_1_en': 'Basic modules',
        'bullet_2_en': 'Limited users',
        'bullet_3_en': 'Order and booking management',
        'bullet_4_en': 'Standard support',
        'cta_label_en': 'Start Free Trial',
        'cta_url': '/contact/',
        'is_featured': False,
        'pricing_benefit_1_text_en': 'Get a 30-day free trial',
        'pricing_benefit_2_text_en': 'No hidden fees',
        'pricing_benefit_3_text_en': 'You can cancel anytime',
    },
    {
        'order': 1,
        'name_en': 'Business',
        'summary_en': (
            'Complete access for growing transport operations with analytics.'
        ),
        'price_display_en': '$29<sub>/month</sub>',
        'bullet_1_en': 'Full system access',
        'bullet_2_en': 'Reports and analytics',
        'bullet_3_en': 'Fleet and driver management',
        'bullet_4_en': 'Priority support',
        'cta_label_en': 'Start Free Trial',
        'cta_url': '/contact/',
        'is_featured': True,
    },
    {
        'order': 2,
        'name_en': 'Enterprise',
        'summary_en': (
            'Advanced setup for large-scale operations with tailored controls.'
        ),
        'price_display_en': '$39<sub>/month</sub>',
        'bullet_1_en': 'Custom workflows',
        'bullet_2_en': 'Dedicated support',
        'bullet_3_en': 'Advanced integrations',
        'bullet_4_en': 'Enhanced security controls',
        'cta_label_en': 'Start Free Trial',
        'cta_url': '/contact/',
        'is_featured': False,
    },
]

TESTIMONIALS = [
    {
        'order': 0,
        'author_name_en': 'Darlene Robertson',
        'author_role_en': 'Global Trade Inc.',
    },
    {
        'order': 1,
        'author_name_en': 'Leslie Alexander',
        'author_role_en': 'CEO, Tech Startup',
    },
    {
        'order': 2,
        'author_name_en': 'Courtney Henry',
        'author_role_en': 'Fleet Supervisor',
    },
]

MAP_LOCATIONS = [
    {
        'order': 0,
        'title_en': 'Saudi Arabia',
        'subtitle_en': 'Major hub for North America',
    },
    {
        'order': 1,
        'title_en': 'UAE',
        'subtitle_en': 'Regional logistics hub',
    },
    {
        'order': 2,
        'title_en': 'Kuwait',
        'subtitle_en': 'Regional logistics hub',
    },
    {
        'order': 3,
        'title_en': 'Qatar',
        'subtitle_en': 'Regional logistics hub',
    },
]


class Command(BaseCommand):
    help = 'Seed default home page CMS content (idempotent for repeaters).'

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE('Seeding home page CMS...'))
        try:
            with transaction.atomic():
                self._seed_home_page()
                self._seed_service_cards()
                self._seed_pricing_tiers()
                self._seed_testimonials()
                self._seed_map_locations()
        except Exception as exc:
            self.stderr.write(self.style.ERROR(f'Seed failed: {exc}'))
            raise
        self.stdout.write(self.style.SUCCESS('Done.'))

    def _seed_home_page(self):
        home = HomePageContent.get_singleton()
        data = _home_text_defaults()
        for key, value in data.items():
            setattr(home, key, value)
        home.save()
        self.stdout.write(self.style.SUCCESS('  HomePageContent (pk=1): updated text fields.'))

    def _seed_service_cards(self):
        home = HomePageContent.get_singleton()
        for row in SERVICE_CARDS:
            order = row['order']
            if HomeServiceCard.objects.filter(home=home, order=order).exists():
                self.stdout.write(
                    f'  HomeServiceCard order={order}: already exists, skipped.'
                )
                continue
            HomeServiceCard.objects.create(
                home=home,
                order=order,
                title_en=row['title_en'],
                title_ar='',
                summary_en=row['summary_en'],
                summary_ar='',
                detail_url=row['detail_url'],
                cta_label_en=row['cta_label_en'],
                cta_label_ar='استكشف الميزة',
                is_active=True,
            )
            self.stdout.write(
                self.style.SUCCESS(f'  HomeServiceCard order={order}: created.')
            )

    def _seed_pricing_tiers(self):
        home = HomePageContent.get_singleton()
        for row in PRICING_TIERS:
            order = row['order']
            if HomePricingTier.objects.filter(home=home, order=order).exists():
                self.stdout.write(
                    f'  HomePricingTier order={order}: already exists, skipped.'
                )
                continue
            tier = HomePricingTier(
                home=home,
                order=order,
                name_en=row['name_en'],
                name_ar='',
                summary_en=row['summary_en'],
                summary_ar='',
                price_display_en=row['price_display_en'],
                price_display_ar='',
                bullet_1_en=row.get('bullet_1_en', ''),
                bullet_1_ar='',
                bullet_2_en=row.get('bullet_2_en', ''),
                bullet_2_ar='',
                bullet_3_en=row.get('bullet_3_en', ''),
                bullet_3_ar='',
                bullet_4_en=row.get('bullet_4_en', ''),
                bullet_4_ar='',
                cta_label_en=row['cta_label_en'],
                cta_label_ar='ابدأ مجاناً',
                cta_url=row['cta_url'],
                is_featured=row.get('is_featured', False),
                is_active=True,
                pricing_benefit_1_text_en=row.get('pricing_benefit_1_text_en', ''),
                pricing_benefit_1_text_ar='',
                pricing_benefit_2_text_en=row.get('pricing_benefit_2_text_en', ''),
                pricing_benefit_2_text_ar='',
                pricing_benefit_3_text_en=row.get('pricing_benefit_3_text_en', ''),
                pricing_benefit_3_text_ar='',
            )
            tier.save()
            self.stdout.write(
                self.style.SUCCESS(
                    f'  HomePricingTier order={order} ({row["name_en"]}): created.'
                )
            )

    def _seed_testimonials(self):
        home = HomePageContent.get_singleton()
        for row in TESTIMONIALS:
            order = row['order']
            if HomeTestimonial.objects.filter(home=home, order=order).exists():
                self.stdout.write(
                    f'  HomeTestimonial order={order}: already exists, skipped.'
                )
                continue
            HomeTestimonial.objects.create(
                home=home,
                order=order,
                quote_en=TESTIMONIAL_QUOTE_EN,
                quote_ar='',
                author_name_en=row['author_name_en'],
                author_name_ar='',
                author_role_en=row['author_role_en'],
                author_role_ar='',
                is_active=True,
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f'  HomeTestimonial order={order} ({row["author_name_en"]}): created.'
                )
            )

    def _seed_map_locations(self):
        home = HomePageContent.get_singleton()
        for row in MAP_LOCATIONS:
            order = row['order']
            if HomeMapLocation.objects.filter(home=home, order=order).exists():
                self.stdout.write(
                    f'  HomeMapLocation order={order}: already exists, skipped.'
                )
                continue
            HomeMapLocation.objects.create(
                home=home,
                order=order,
                title_en=row['title_en'],
                title_ar='',
                subtitle_en=row['subtitle_en'],
                subtitle_ar='',
                is_active=True,
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f'  HomeMapLocation order={order} ({row["title_en"]}): created.'
                )
            )
