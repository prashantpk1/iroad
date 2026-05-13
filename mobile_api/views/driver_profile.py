"""
mobile_api/views/driver_profile.py

Authenticated driver profile and change-password endpoints.

Views validate input, call services, and return MobileAPIView envelopes only.
"""
from django.utils.translation import gettext as _

from rest_framework.parsers import FormParser, MultiPartParser

from mobile_api.views.base import MobileAPIView
from mobile_api.permissions import IsMobileAuthenticated
from mobile_api.throttling import (
    MobileAuthThrottle,
    MobileOtpThrottle,
    MobileUserThrottle,
)
from mobile_api.serializers.driver_profile import (
    DriverChangePasswordSerializer,
    DriverProfilePhotoUpdateSerializer,
    DriverRequestChangePasswordOtpSerializer,
    DriverVerifyChangePasswordOtpSerializer,
)
from mobile_api.services.driver_profile_service import (
    driver_change_password,
    driver_request_change_password_otp,
    driver_verify_change_password_otp,
    get_driver_profile,
    update_driver_profile_photo,
)
from mobile_api.views.driver_auth import get_tenant_schema


def _mobile_jwt_payload(request) -> dict:
    """Token claims from DRF JWT auth (request.auth) or MobileUser.payload."""
    auth = getattr(request, 'auth', None)
    if isinstance(auth, dict):
        return auth
    user = getattr(request, 'user', None)
    payload = getattr(user, 'payload', None)
    return payload if isinstance(payload, dict) else {}


def _mobile_user_id(request) -> str:
    user = getattr(request, 'user', None)
    uid = getattr(user, 'user_id', None)
    return str(uid) if uid is not None else ''


def _mobile_tenant_schema(request) -> str:
    user = getattr(request, 'user', None)
    schema = getattr(user, 'tenant_schema', None)
    if schema:
        return schema
    return get_tenant_schema(request)


class DriverRequestChangePasswordOtpView(MobileAPIView):
    """
    POST /api/v1/mobile/driver/auth/change-password/request-otp/

    Body: { "send_via": "email" | "mobile" }
    """

    permission_classes = [IsMobileAuthenticated]
    throttle_classes = [MobileOtpThrottle]

    def post(self, request):
        serializer = DriverRequestChangePasswordOtpSerializer(data=request.data)
        if not serializer.is_valid():
            return self.error(
                message=_('mobile.validation.failed'),
                data={'errors': serializer.errors},
            )

        send_via = serializer.validated_data['send_via']
        result = driver_request_change_password_otp(
            user_id=_mobile_user_id(request),
            tenant_schema=_mobile_tenant_schema(request),
            send_via=send_via,
            jwt_payload=_mobile_jwt_payload(request),
        )

        if not result.get('success'):
            return self.error(
                message=result.get('error', _('mobile.validation.failed')),
                data={},
            )

        # Empty data: do not expose delivery pipeline status to clients.
        return self.success(
            message=_('mobile.auth.change_password_otp_sent'),
            data={},
        )


class DriverVerifyChangePasswordOtpView(MobileAPIView):
    """
    POST /api/v1/mobile/driver/auth/change-password/verify-otp/

    Body: { "otp_code": "123456" }
    """

    permission_classes = [IsMobileAuthenticated]
    throttle_classes = [MobileOtpThrottle]

    def post(self, request):
        serializer = DriverVerifyChangePasswordOtpSerializer(data=request.data)
        if not serializer.is_valid():
            return self.error(
                message=_('mobile.validation.failed'),
                data={'errors': serializer.errors},
            )

        otp_code = serializer.validated_data['otp_code']
        result = driver_verify_change_password_otp(
            user_id=_mobile_user_id(request),
            tenant_schema=_mobile_tenant_schema(request),
            otp_code=otp_code,
            jwt_payload=_mobile_jwt_payload(request),
        )

        if not result.get('success'):
            data = {
                'attempts_remaining': result.get('attempts_remaining', 0),
                'verified': result.get('verified', False),
            }
            return self.error(
                message=result.get('error', _('mobile.validation.failed')),
                data=data,
            )

        return self.success(
            message=_('mobile.auth.change_password_otp_verified'),
            data={
                'attempts_remaining': result.get('attempts_remaining', 0),
                'verified': result.get('verified', True),
            },
        )


class DriverChangePasswordView(MobileAPIView):
    """
    POST /api/v1/mobile/driver/auth/change-password/

    Body:
      current_password, new_password, confirm_password, otp_code
    """

    permission_classes = [IsMobileAuthenticated]
    throttle_classes = [MobileAuthThrottle]

    def post(self, request):
        serializer = DriverChangePasswordSerializer(data=request.data)
        if not serializer.is_valid():
            return self.error(
                message=_('mobile.validation.failed'),
                data={'errors': serializer.errors},
            )

        payload = _mobile_jwt_payload(request)
        result = driver_change_password(
            user_id=_mobile_user_id(request),
            tenant_schema=_mobile_tenant_schema(request),
            current_password=serializer.validated_data['current_password'],
            new_password=serializer.validated_data['new_password'],
            otp_code=serializer.validated_data['otp_code'],
            jwt_payload=payload,
            access_jti=payload.get('jti'),
            access_exp_ts=payload.get('exp'),
        )

        if not result.get('success'):
            return self.error(
                message=result.get('error', _('mobile.validation.failed')),
                data={},
            )

        return self.success(
            message=_('mobile.auth.password_changed_successfully'),
            data={},
        )


class DriverProfileView(MobileAPIView):
    """
    GET /api/v1/mobile/driver/profile/
    """

    permission_classes = [IsMobileAuthenticated]
    throttle_classes = [MobileUserThrottle]

    def get(self, request):
        result = get_driver_profile(
            user_id=_mobile_user_id(request),
            tenant_schema=_mobile_tenant_schema(request),
            jwt_payload=_mobile_jwt_payload(request),
            request=request,
        )

        if not result.get('success'):
            return self.error(
                message=result.get('error', _('mobile.validation.failed')),
                data={},
            )

        return self.success(
            message=_('mobile.profile.fetch_success'),
            data=result.get('profile') or {},
        )


class DriverProfilePhotoUpdateView(MobileAPIView):
    """
    POST or PATCH /api/v1/mobile/driver/profile/photo/

    Multipart: profile_photo (image file)
    """

    permission_classes = [IsMobileAuthenticated]
    throttle_classes = [MobileUserThrottle]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        return self._update_photo(request)

    def patch(self, request):
        return self._update_photo(request)

    def _update_photo(self, request):
        serializer = DriverProfilePhotoUpdateSerializer(data=request.data)
        if not serializer.is_valid():
            return self.error(
                message=_('mobile.validation.failed'),
                data={'errors': serializer.errors},
            )

        uploaded = serializer.validated_data['profile_photo']
        result = update_driver_profile_photo(
            user_id=_mobile_user_id(request),
            tenant_schema=_mobile_tenant_schema(request),
            uploaded_file=uploaded,
            jwt_payload=_mobile_jwt_payload(request),
            request=request,
        )

        if not result.get('success'):
            return self.error(
                message=result.get('error', _('mobile.validation.failed')),
                data={},
            )

        return self.success(
            message=_('mobile.profile.photo_updated'),
            data={
                'profile_photo_url': result.get('profile_photo_url'),
            },
        )
