from django.urls import path
from iroad_frontend import views

app_name = 'iroad_frontend'

urlpatterns = [
    path('', views.HomePageView.as_view(), name='home'),
]
