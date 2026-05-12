from django.shortcuts import render
from django.views import View

from iroad_frontend.models import (
    AboutPageContent,
    HomePageContent,
    PricingPageContent,
)


class HomePageView(View):
    def get(self, request):
        home = HomePageContent.get_singleton()
        service_cards = home.service_cards.filter(is_active=True).order_by('order')
        pricing_tiers = home.pricing_tiers.filter(is_active=True).order_by('order')
        testimonials = home.testimonials.filter(is_active=True).order_by('order')
        map_locations = home.map_locations.filter(is_active=True).order_by('order')[:4]
        pricing_benefits = home.pricing_benefits.filter(is_active=True).order_by('order')
        context = {
            'home': home,
            'service_cards': service_cards,
            'pricing_tiers': pricing_tiers,
            'pricing_benefits': pricing_benefits,
            'testimonials': testimonials,
            'map_locations': map_locations,
            'lang': 'en',
            'dir': 'ltr',
        }
        return render(
            request,
            'iroad_frontend/home/index.html',
            context,
        )


class AboutPageView(View):
    def get(self, request):
        about = AboutPageContent.get_singleton()
        home = HomePageContent.get_singleton()
        context = {
            'about': about,
            'home': home,
            'pillars': about.approach_pillars.filter(
                is_active=True).order_by('order'),
            'how_steps': about.how_work_steps.filter(
                is_active=True).order_by('order'),
            'faq_items': about.faq_items.filter(
                is_active=True).order_by('order'),
            'lang': 'en',
            'dir': 'ltr',
        }
        return render(
            request,
            'iroad_frontend/about/index.html',
            context,
        )


class PricingPageView(View):
    def get(self, request):
        pricing = PricingPageContent.get_singleton()
        home = HomePageContent.get_singleton()
        about = AboutPageContent.get_singleton()

        pricing_tiers = home.pricing_tiers.filter(
            is_active=True).order_by('order')
        testimonials = home.testimonials.filter(
            is_active=True).order_by('order')
        map_locations = home.map_locations.filter(
            is_active=True).order_by('order')
        pricing_benefits = home.pricing_benefits.filter(
            is_active=True).order_by('order')

        context = {
            'pricing': pricing,
            'home': home,
            'pricing_tiers': pricing_tiers,
            'pricing_benefits': pricing_benefits,
            'interactive_steps': pricing.interactive_steps.filter(
                is_active=True).order_by('order'),
            'testimonials': testimonials,
            'map_locations': map_locations,
            'faq_items': about.faq_items.filter(
                is_active=True).order_by('order'),
            'lang': 'en',
            'dir': 'ltr',
        }
        return render(
            request,
            'iroad_frontend/pricing/index.html',
            context,
        )


def page_not_found(request, exception=None):
    """
    Custom 404 (handler404 in root URLconf).
    Uses the same chrome as the public site: base layout, header/footer,
    and CMS-driven nav/footer via HomePageContent singleton.
    """
    home = HomePageContent.get_singleton()
    return render(
        request,
        'iroad_frontend/errors/404.html',
        {
            'home': home,
            'lang': 'en',
            'dir': 'ltr',
        },
        status=404,
    )
