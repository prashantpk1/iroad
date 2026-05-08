"""
mobile_api/urls.py

Mobile API URL configuration.
All endpoints versioned under /api/v1/mobile/
"""
from django.urls import path

from mobile_api.views.driver_auth import (
    DriverForgotPasswordView,
    DriverLoginView,
    DriverLogoutView,
    DriverResetPasswordView,
    DriverVerifyOtpView,
)

app_name = 'mobile_api'

urlpatterns = [
    path(
        'driver/auth/login/',
        DriverLoginView.as_view(),
        name='driver_login',
    ),
    path(
        'driver/auth/forgot-password/',
        DriverForgotPasswordView.as_view(),
        name='driver_forgot_password',
    ),
    path(
        'driver/auth/verify-otp/',
        DriverVerifyOtpView.as_view(),
        name='driver_verify_otp',
    ),
    path(
        'driver/auth/reset-password/',
        DriverResetPasswordView.as_view(),
        name='driver_reset_password',
    ),
    path(
        'driver/auth/logout/',
        DriverLogoutView.as_view(),
        name='driver_logout',
    ),
]

