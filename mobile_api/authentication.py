"""
mobile_api/authentication.py

DRF Custom Authentication class for Mobile API.

Flow:
  1. Extract Bearer token from Authorization header
  2. Verify JWT signature and expiry
  3. Return (user_representation, token_payload) on success
  4. Return None if no token (anonymous request)
  5. Raise AuthenticationFailed if token present but invalid

Usage:
  Configured globally in REST_FRAMEWORK settings.
  Individual views can override with authentication_classes = []
  to allow unauthenticated access (e.g. login endpoint).
"""
import jwt
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from django.utils.translation import gettext_lazy as _

from django_tenants.utils import schema_context

from mobile_api.helpers.auth import (
    get_token_from_request,
    verify_token,
    TOKEN_TYPE_ACCESS,
)


class MobileUser:
    """
    Lightweight user representation for authenticated mobile requests.
    Not a Django model — just carries token claims.
    No database hit required.
    """
    def __init__(self, payload: dict):
        self.user_id = payload.get('user_id')
        self.tenant_schema = payload.get('tenant_schema')
        self.jti = payload.get('jti')
        self.payload = payload
        self.is_authenticated = True

    @property
    def pk(self):
        """DRF ``UserRateThrottle`` uses ``request.user.pk`` for cache keys."""
        return self.user_id

    def __str__(self):
        return f"MobileUser({self.user_id}@{self.tenant_schema})"


class MobileJWTAuthentication(BaseAuthentication):
    """
    Custom DRF authentication for Mobile API JWT tokens.

    Authenticates requests using:
      Authorization: Bearer <access_token>

    Returns:
      (MobileUser instance, token_payload) on success
      None if no Authorization header (anonymous)

    Raises:
      AuthenticationFailed if token present but invalid/expired
    """

    def authenticate(self, request):
        token = get_token_from_request(request)

        # No token — anonymous request
        # Let permission class decide if this is allowed
        if token is None:
            return None

        # Token present — must be valid
        try:
            payload = verify_token(
                token,
                expected_type=TOKEN_TYPE_ACCESS,
            )
        except jwt.ExpiredSignatureError:
            raise AuthenticationFailed(
                _('mobile.auth.token_expired')
            )
        except jwt.InvalidTokenError:
            raise AuthenticationFailed(
                _('mobile.auth.token_invalid')
            )

        if payload is None:
            raise AuthenticationFailed(
                _('mobile.auth.token_invalid')
            )

        user_id = str(payload.get('user_id') or '').strip()
        tenant_schema = str(payload.get('tenant_schema') or '').strip()
        if not user_id or not tenant_schema:
            raise AuthenticationFailed(_('mobile.auth.token_invalid'))
        try:
            from tenant_workspace.models import TenantUser

            with schema_context(tenant_schema):
                tu = TenantUser.all_objects.filter(pk=user_id).only('is_deleted').first()
        except Exception:
            raise AuthenticationFailed(_('mobile.auth.token_invalid')) from None
        if tu is None:
            raise AuthenticationFailed(_('mobile.auth.unauthorized'))
        if getattr(tu, 'is_deleted', False):
            raise AuthenticationFailed(
                _('mobile.auth.account_deleted'),
                code='account_deleted',
            )

        # Build lightweight user object from token claims
        mobile_user = MobileUser(payload)
        return (mobile_user, payload)

    def authenticate_header(self, request):
        """
        Return string for WWW-Authenticate header on 401 responses.
        """
        return 'Bearer realm="Mobile API"'

