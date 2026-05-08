"""
mobile_api/permissions.py

DRF Custom Permission classes for Mobile API.

Permission classes control WHO can access each endpoint.

Usage in views:
  class TruckListView(APIView):
      permission_classes = [IsMobileAuthenticated]

  class LoginView(APIView):
      authentication_classes = []
      permission_classes = [AllowAnyMobile]
"""
from rest_framework.permissions import BasePermission
from django.utils.translation import gettext_lazy as _


class IsMobileAuthenticated(BasePermission):
    """
    Allow access only to authenticated mobile users.
    Token must be present and valid.
    Applied globally via REST_FRAMEWORK settings.
    """
    message = _('mobile.auth.unauthorized')

    def has_permission(self, request, view):
        return (
            request.user is not None
            and hasattr(request.user, 'is_authenticated')
            and request.user.is_authenticated
        )


class AllowAnyMobile(BasePermission):
    """
    Allow any request — authenticated or not.
    Use on public endpoints like login, register, forgot password.

    Example:
        authentication_classes = []
        permission_classes = [AllowAnyMobile]
    """
    message = ''

    def has_permission(self, request, view):
        return True


class IsTenantAdmin(BasePermission):
    """
    Allow access only to tenant admin users.
    Checks is_admin flag set during authentication.
    """
    message = _('mobile.auth.admin_required')

    def has_permission(self, request, view):
        if not (
            request.user is not None
            and hasattr(request.user, 'is_authenticated')
            and request.user.is_authenticated
        ):
            return False
        # Check admin flag in token payload
        payload = getattr(request.user, 'payload', {})
        return payload.get('is_admin', False) is True

