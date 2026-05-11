from django.shortcuts import render
from django.views import View

from iroad_frontend.models import HomePageContent


class HomePageView(View):
    def get(self, request):
        home = HomePageContent.get_singleton()
        service_cards = home.service_cards.filter(is_active=True).order_by('order')
        pricing_tiers = home.pricing_tiers.filter(is_active=True).order_by('order')
        testimonials = home.testimonials.filter(is_active=True).order_by('order')
        map_locations = home.map_locations.filter(is_active=True).order_by('order')[:4]
        context = {
            'home': home,
            'service_cards': service_cards,
            'pricing_tiers': pricing_tiers,
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
