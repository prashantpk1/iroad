"""
mobile_api/serializers/driver_auth.py

Serializers for Driver Authentication APIs.
Input validation only — no model binding.
"""
from rest_framework import serializers
from django.utils.translation import gettext_lazy as _


class DriverLoginSerializer(serializers.Serializer):
    """
    Validates login input.
    API 1: POST /api/v1/mobile/driver/auth/login/
    """
    email = serializers.EmailField(
        required=True,
        error_messages={
            'required': _('mobile.auth.email_required'),
            'invalid': _('mobile.auth.email_invalid'),
        }
    )
    password = serializers.CharField(
        required=True,
        min_length=1,
        write_only=True,
        style={'input_type': 'password'},
        error_messages={
            'required': _('mobile.auth.password_required'),
            'blank': _('mobile.auth.password_required'),
        }
    )


class ForgotPasswordSerializer(serializers.Serializer):
    """
    Validates forgot password input.
    API 2: POST /api/v1/mobile/driver/auth/forgot-password/
    """
    email = serializers.EmailField(
        required=True,
        error_messages={
            'required': _('mobile.auth.email_required'),
            'invalid': _('mobile.auth.email_invalid'),
        }
    )


class VerifyOtpSerializer(serializers.Serializer):
    """
    Validates OTP verification input.
    API 3: POST /api/v1/mobile/driver/auth/verify-otp/
    """
    email = serializers.EmailField(
        required=True,
        error_messages={
            'required': _('mobile.auth.email_required'),
            'invalid': _('mobile.auth.email_invalid'),
        }
    )
    otp_code = serializers.CharField(
        required=True,
        min_length=6,
        max_length=6,
        error_messages={
            'required': _('mobile.auth.otp_required'),
            'min_length': _('mobile.auth.otp_invalid_length'),
            'max_length': _('mobile.auth.otp_invalid_length'),
        }
    )

    def validate_otp_code(self, value):
        if not value.isdigit():
            raise serializers.ValidationError(
                _('mobile.auth.otp_digits_only')
            )
        return value


class ResetPasswordSerializer(serializers.Serializer):
    """
    Validates new password input.
    API 4: POST /api/v1/mobile/driver/auth/reset-password/
    """
    email = serializers.EmailField(
        required=True,
        error_messages={
            'required': _('mobile.auth.email_required'),
            'invalid': _('mobile.auth.email_invalid'),
        }
    )
    otp_code = serializers.CharField(
        required=True,
        min_length=6,
        max_length=6,
        error_messages={
            'required': _('mobile.auth.otp_required'),
        }
    )
    new_password = serializers.CharField(
        required=True,
        min_length=8,
        write_only=True,
        style={'input_type': 'password'},
        error_messages={
            'required': _('mobile.auth.password_required'),
            'min_length': _('mobile.auth.password_min_length'),
        }
    )
    confirm_password = serializers.CharField(
        required=True,
        min_length=8,
        write_only=True,
        style={'input_type': 'password'},
        error_messages={
            'required': _('mobile.auth.confirm_password_required'),
        }
    )

    def validate(self, data):
        if data.get('new_password') != data.get('confirm_password'):
            raise serializers.ValidationError({
                'confirm_password':
                    _('mobile.auth.passwords_do_not_match')
            })
        return data

    def validate_otp_code(self, value):
        if not value.isdigit():
            raise serializers.ValidationError(
                _('mobile.auth.otp_digits_only')
            )
        return value


class LogoutSerializer(serializers.Serializer):
    """
    Logout — token comes from Authorization header.
    No body needed. This serializer is a placeholder
    for documentation purposes.
    API 5: POST /api/v1/mobile/driver/auth/logout/
    """
    pass


class DeleteAccountSerializer(serializers.Serializer):
    """
    Validates delete-account request body (current password for confirmation).

    Checks only presence / non-blank input. Matching ``password_hash`` and
    soft-delete side effects belong in the service layer.
    """
    password = serializers.CharField(
        required=True,
        min_length=1,
        write_only=True,
        style={'input_type': 'password'},
        error_messages={
            'required': _('mobile.auth.password_required'),
            'blank': _('mobile.auth.password_required'),
            'null': _('mobile.auth.password_required'),
        },
    )
