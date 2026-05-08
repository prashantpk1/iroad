"""
mobile_api/throttling.py

Custom throttle classes for Mobile API rate limiting.

Throttle rates configured in REST_FRAMEWORK settings:
  'mobile_auth': '10/minute'   — login, refresh
  'mobile_otp': '5/minute'     — OTP request/verify
  'anon': '30/minute'          — unauthenticated general
  'user': '100/minute'         — authenticated general

Usage in views:
  class LoginView(APIView):
      throttle_classes = [MobileAuthThrottle]

  class OtpView(APIView):
      throttle_classes = [MobileOtpThrottle]
"""
from rest_framework.throttling import (
    AnonRateThrottle,
    UserRateThrottle,
)


class MobileAuthThrottle(AnonRateThrottle):
    """
    Strict throttle for auth endpoints.
    login, refresh, logout.
    Rate: 10 requests/minute per IP.
    """
    scope = 'mobile_auth'


class MobileOtpThrottle(AnonRateThrottle):
    """
    Strictest throttle for OTP endpoints.
    Request OTP, verify OTP.
    Rate: 5 requests/minute per IP.
    """
    scope = 'mobile_otp'


class MobileUserThrottle(UserRateThrottle):
    """
    Standard throttle for authenticated endpoints.
    Rate: 100 requests/minute per user.
    """
    scope = 'user'


class MobileAnonThrottle(AnonRateThrottle):
    """
    Standard throttle for unauthenticated endpoints.
    Rate: 30 requests/minute per IP.
    """
    scope = 'anon'

