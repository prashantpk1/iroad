from django.urls import path

from iroad_frontend.views import (
    AboutPageView,
    ContactFormSubmitView,
    ContactPageView,
    HomePageView,
    PricingPageView,
)

app_name = 'iroad_frontend'

urlpatterns = [
    path('about/', AboutPageView.as_view(), name='about'),
    path('pricing/', PricingPageView.as_view(), name='pricing'),
    path('contact/', ContactPageView.as_view(), name='contact'),
    path(
        'contact/submit/',
        ContactFormSubmitView.as_view(),
        name='contact_submit',
    ),
    path('', HomePageView.as_view(), name='home'),
]
