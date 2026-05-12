from django.urls import path
from iroad_frontend import views

app_name = 'iroad_frontend'

urlpatterns = [
    path('about/', views.AboutPageView.as_view(), name='about'),
    path('', views.HomePageView.as_view(), name='home'),
]
