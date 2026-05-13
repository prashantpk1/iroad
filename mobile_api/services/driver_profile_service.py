"""
mobile_api/services/driver_profile_service.py

Authenticated driver profile and change-password (OTP) business logic.

Views must not query the DB directly for these flows — call functions here.

OTP storage reuses ``DriverPasswordResetOTP`` (same lifecycle as forgot-password).
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any

from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import ValidationError
from django.core.files.images import get_image_dimensions
from django.utils import timezone
from django.utils.translation import gettext as _
from django_tenants.utils import schema_context

from mobile_api.helpers.auth import blacklist_token_jti
from mobile_api.models import DriverPasswordResetOTP
from mobile_api.serializers.driver_profile import (
    PROFILE_PHOTO_ALLOWED_EXTENSIONS,
    PROFILE_PHOTO_MAX_SIZE_BYTES,
    safe_media_url,
)
from mobile_api.services.driver_auth_service import (
    generate_otp,
    get_driver_master_by_user,
    send_driver_reset_otp_email,
)

from tenant_workspace.models import DriverMaster, TenantUser

logger = logging.getLogger('mobile_api')


def _get_tenant_user_by_id(user_id: str, tenant_schema: str):
    try:
        with schema_context(tenant_schema):
            return TenantUser.all_objects.filter(pk=user_id).first()
    except Exception as exc:
        logger.error('get_tenant_user_by_id error: %s', exc)
        return None


def _jwt_email_matches_user(tenant_user, jwt_email: str | None) -> bool:
    if not jwt_email:
        return True
    return (
        (tenant_user.email or '').strip().lower()
        == str(jwt_email).strip().lower()
    )


def _resolve_driver_context(
    *,
    user_id: str,
    tenant_schema: str,
    jwt_email: str | None = None,
) -> dict[str, Any]:
    """
    Load TenantUser + DriverMaster for the authenticated mobile identity.

    Returns:
        {'success': True, 'tenant_user': u, 'driver': d}
        {'success': False, 'error': lazy_str}
    """
    if not tenant_schema or not user_id:
        return {'success': False, 'error': _('mobile.auth.unauthorized')}

    tenant_user = _get_tenant_user_by_id(user_id, tenant_schema)
    if tenant_user is None:
        return {'success': False, 'error': _('mobile.auth.unauthorized')}

    if getattr(tenant_user, 'is_deleted', False):
        return {'success': False, 'error': _('mobile.auth.account_deleted')}

    if not _jwt_email_matches_user(tenant_user, jwt_email):
        return {'success': False, 'error': _('mobile.auth.unauthorized')}

    user_status = getattr(tenant_user, 'status', None)
    if user_status and str(user_status).lower() not in ('active', 'Active'):
        return {'success': False, 'error': _('mobile.auth.account_inactive')}

    driver = get_driver_master_by_user(tenant_user, tenant_schema)
    if driver is None:
        return {'success': False, 'error': _('mobile.auth.not_a_driver')}
    if str(driver.driver_status) != 'Active':
        return {'success': False, 'error': _('mobile.auth.driver_inactive')}

    return {
        'success': True,
        'tenant_user': tenant_user,
        'driver': driver,
    }


def _otp_email_key(tenant_user) -> str:
    return (tenant_user.email or '').strip().lower()


def _phone_for_sms(tenant_user, driver) -> str | None:
    """
    Build a single recipient string for SMS gateways.

    Prefer TenantUser mobile_country_code + mobile_no; fall back to driver mobile.
    """
    code = (getattr(tenant_user, 'mobile_country_code', None) or '').strip()
    num = (getattr(tenant_user, 'mobile_no', None) or '').strip()
    combined = f'{code}{num}'.strip()
    if combined:
        return combined
    dm = (getattr(driver, 'mobile_number', None) or '').strip()
    if dm and re.match(r'^\d+$', dm):
        return dm
    return None


def _send_change_password_otp_sms(phone: str, otp_code: str) -> bool:
    try:
        from superadmin.communication_helpers import send_transactional_sms
        from mobile_api.models import OTP_EXPIRY_MINUTES

        body = (
            f'Your iRoad verification code is {otp_code}. '
            f'It expires in {OTP_EXPIRY_MINUTES} minutes.'
        )
        return bool(
            send_transactional_sms(
                phone,
                body,
                trigger_source='Mobile: change password OTP',
            )
        )
    except Exception as exc:
        logger.error('change password SMS send failed: %s', exc)
        return False


def driver_request_change_password_otp(
    *,
    user_id: str,
    tenant_schema: str,
    send_via: str,
    jwt_payload: dict | None = None,
) -> dict[str, Any]:
    """
    Authenticated driver requests OTP to change password.

    Identity comes only from JWT (user_id + tenant_schema + email in payload).
    ``send_via`` is ``email`` or ``mobile`` (SMS uses tenant user phone when set).

    Reuses ``DriverPasswordResetOTP.create_for_email`` (invalidates prior PENDING;
    new row has attempts=0 and fresh expiry).

    Returns:
        {'success': True}
        {'success': False, 'error': ...}
    """
    jwt_email = (jwt_payload or {}).get('email')
    ctx = _resolve_driver_context(
        user_id=user_id,
        tenant_schema=tenant_schema,
        jwt_email=jwt_email,
    )
    if not ctx['success']:
        return {'success': False, 'error': ctx['error']}

    tenant_user = ctx['tenant_user']
    driver = ctx['driver']
    email_key = _otp_email_key(tenant_user)

    otp_code = generate_otp()
    DriverPasswordResetOTP.create_for_email(
        email=email_key,
        tenant_schema=tenant_schema,
        otp_code=otp_code,
        expire_verified=True,
    )

    dispatch_ok = False
    if send_via == 'email':
        dispatch_ok = send_driver_reset_otp_email(
            recipient_email=tenant_user.email,
            otp_code=otp_code,
            tenant_schema=tenant_schema,
            user_name=getattr(tenant_user, 'full_name', '') or email_key,
        )
        if not dispatch_ok:
            logger.warning(
                'Change-password OTP email dispatch failed schema=%s user_id=%s',
                tenant_schema,
                user_id,
            )
    elif send_via == 'mobile':
        phone = _phone_for_sms(tenant_user, driver)
        if not phone:
            return {
                'success': False,
                'error': _('mobile.profile.phone_missing_for_sms'),
            }
        dispatch_ok = _send_change_password_otp_sms(phone, otp_code)
        if not dispatch_ok:
            logger.warning(
                'Change-password OTP SMS dispatch failed schema=%s user_id=%s',
                tenant_schema,
                user_id,
            )
    else:
        return {'success': False, 'error': _('mobile.profile.send_via_invalid')}

    logger.info(
        'Change-password OTP issued schema=%s user_id=%s channel=%s',
        tenant_schema,
        user_id,
        send_via,
    )
    return {'success': True}


def driver_verify_change_password_otp(
    *,
    user_id: str,
    tenant_schema: str,
    otp_code: str,
    jwt_payload: dict | None = None,
) -> dict[str, Any]:
    """
    Verify change-password OTP for the authenticated driver (JWT-bound email).

    Returns:
        {'success': True, 'attempts_remaining': 5}
        {'success': False, 'error': ..., 'attempts_remaining': n?}
    """
    jwt_email = (jwt_payload or {}).get('email')
    ctx = _resolve_driver_context(
        user_id=user_id,
        tenant_schema=tenant_schema,
        jwt_email=jwt_email,
    )
    if not ctx['success']:
        return {'success': False, 'error': ctx['error']}

    tenant_user = ctx['tenant_user']
    email_key = _otp_email_key(tenant_user)

    otp_record = DriverPasswordResetOTP.get_valid_otp(
        email=email_key,
        tenant_schema=tenant_schema,
    )
    if otp_record is None:
        return {
            'success': False,
            'error': _('mobile.auth.otp_not_found'),
            'attempts_remaining': 0,
            'verified': False,
        }

    if otp_record.is_expired:
        otp_record.status = DriverPasswordResetOTP.Status.EXPIRED
        with schema_context(tenant_schema):
            otp_record.save(update_fields=['status'])
        return {
            'success': False,
            'error': _('mobile.validation.otp_expired'),
            'attempts_remaining': 0,
            'verified': False,
        }

    if otp_record.attempts >= 5:
        return {
            'success': False,
            'error': _('mobile.auth.otp_max_attempts'),
            'attempts_remaining': 0,
            'verified': False,
        }

    if otp_record.otp_code != otp_code.strip():
        otp_record.attempts += 1
        with schema_context(tenant_schema):
            otp_record.save(update_fields=['attempts'])
        remaining = max(0, 5 - otp_record.attempts)
        return {
            'success': False,
            'error': _('mobile.validation.invalid_otp'),
            'attempts_remaining': remaining,
            'verified': False,
        }

    otp_record.status = DriverPasswordResetOTP.Status.VERIFIED
    otp_record.verified_at = timezone.now()
    with schema_context(tenant_schema):
        otp_record.save(update_fields=['status', 'verified_at'])

    return {
        'success': True,
        'attempts_remaining': max(0, 5 - otp_record.attempts),
        'verified': True,
    }


def driver_change_password(
    *,
    user_id: str,
    tenant_schema: str,
    current_password: str,
    new_password: str,
    otp_code: str,
    jwt_payload: dict | None = None,
    access_jti: str | None = None,
    access_exp_ts: int | None = None,
) -> dict[str, Any]:
    """
    Change password after OTP verification.

    - Verifies ``current_password`` against ``TenantUser.password_hash``.
    - Consumes latest ``VERIFIED`` OTP matching ``otp_code`` (marks ``USED``).
    - Blacklists the current access token JTI when provided (session invalidation).

    Does not log passwords or OTP codes.
    """
    jwt_email = (jwt_payload or {}).get('email')
    ctx = _resolve_driver_context(
        user_id=user_id,
        tenant_schema=tenant_schema,
        jwt_email=jwt_email,
    )
    if not ctx['success']:
        return {'success': False, 'error': ctx['error']}

    tenant_user = ctx['tenant_user']
    email_key = _otp_email_key(tenant_user)

    if not check_password(current_password, tenant_user.password_hash):
        return {
            'success': False,
            'error': _('mobile.auth.current_password_invalid'),
        }

    otp_record = DriverPasswordResetOTP.get_verified_otp(
        email=email_key,
        tenant_schema=tenant_schema,
    )
    if otp_record is None:
        return {
            'success': False,
            'error': _('mobile.auth.change_password_requires_verified_otp'),
        }

    if otp_record.otp_code != otp_code.strip():
        return {
            'success': False,
            'error': _('mobile.validation.invalid_otp'),
        }

    if otp_record.is_expired:
        otp_record.status = DriverPasswordResetOTP.Status.EXPIRED
        with schema_context(tenant_schema):
            otp_record.save(update_fields=['status'])
        return {
            'success': False,
            'error': _('mobile.validation.otp_expired'),
        }

    if otp_record.status == DriverPasswordResetOTP.Status.USED:
        return {
            'success': False,
            'error': _('mobile.auth.change_password_requires_verified_otp'),
        }

    if check_password(new_password, tenant_user.password_hash):
        return {
            'success': False,
            'error': _('mobile.profile.new_password_same_as_current'),
        }

    new_hash = make_password(new_password)
    with schema_context(tenant_schema):
        TenantUser.all_objects.filter(pk=tenant_user.pk).update(
            password_hash=new_hash,
        )
        DriverPasswordResetOTP.objects.filter(pk=otp_record.pk).update(
            status=DriverPasswordResetOTP.Status.USED,
            used_at=timezone.now(),
        )
        DriverPasswordResetOTP.objects.filter(
            email=email_key,
            tenant_schema=tenant_schema,
            status=DriverPasswordResetOTP.Status.PENDING,
        ).update(status=DriverPasswordResetOTP.Status.EXPIRED)

    if access_jti:
        try:
            blacklist_token_jti(jti=access_jti, exp_ts=access_exp_ts)
            from superadmin.redis_helpers import revoke_tenant_session_by_jti

            revoke_tenant_session_by_jti(access_jti)
        except Exception as exc:
            logger.error('post password-change token revoke error: %s', exc)

    logger.info(
        'Password changed via mobile schema=%s user_id=%s',
        tenant_schema,
        user_id,
    )
    return {'success': True}


def get_driver_profile(
    *,
    user_id: str,
    tenant_schema: str,
    jwt_payload: dict | None = None,
    request=None,
) -> dict[str, Any]:
    """
    Build profile payload for mobile clients (serialized dict).

    Current truck assignment rule:
      ``TruckDriverAssignmentHistory`` rows with ``assigned_to IS NULL``,
      latest by ``assigned_from`` then ``created_at``.

    All ORM access and ``DriverProfileSerializer`` rendering run inside
    ``schema_context(tenant_schema)`` so FKs resolve in the correct tenant.

    Returns:
        {'success': True, 'profile': <nested dict>}
        {'success': False, 'error': ...}
    """
    from tenant_workspace.models import TruckDriverAssignmentHistory
    from mobile_api.serializers.driver_profile import DriverProfileSerializer

    jwt_email = (jwt_payload or {}).get('email')
    if not tenant_schema or not user_id:
        return {'success': False, 'error': _('mobile.auth.unauthorized')}

    with schema_context(tenant_schema):
        tenant_user = TenantUser.all_objects.filter(pk=user_id).first()
        if tenant_user is None:
            return {'success': False, 'error': _('mobile.auth.unauthorized')}
        if getattr(tenant_user, 'is_deleted', False):
            return {'success': False, 'error': _('mobile.auth.account_deleted')}
        if not _jwt_email_matches_user(tenant_user, jwt_email):
            return {'success': False, 'error': _('mobile.auth.unauthorized')}
        user_status = getattr(tenant_user, 'status', None)
        if user_status and str(user_status).lower() not in (
            'active',
            'Active',
        ):
            return {'success': False, 'error': _('mobile.auth.account_inactive')}

        driver = (
            DriverMaster.objects.select_related('nationality_country')
            .filter(user_account_id=tenant_user.pk)
            .first()
        )
        if driver is None:
            return {'success': False, 'error': _('mobile.auth.not_a_driver')}
        if str(driver.driver_status) != 'Active':
            return {'success': False, 'error': _('mobile.auth.driver_inactive')}

        assignment = (
            TruckDriverAssignmentHistory.objects.filter(
                driver=driver,
                assigned_to__isnull=True,
            )
            .select_related('truck', 'truck__truck_type')
            .order_by('-assigned_from', '-created_at')
            .first()
        )
        truck = assignment.truck if assignment else None
        truck_type = truck.truck_type if truck else None

        profile_ctx = {
            'driver': driver,
            'tenant_user': tenant_user,
            'current_truck': truck,
            'truck_type': truck_type,
            'assignment': assignment,
        }
        profile_data = DriverProfileSerializer(
            instance=profile_ctx,
            context={'request': request},
        ).data

    return {'success': True, 'profile': profile_data}


def _validate_profile_photo_upload(uploaded_file) -> None:
    """Raise ValidationError if upload violates Phase-1 image rules."""
    name = getattr(uploaded_file, 'name', '') or ''
    ext = os.path.splitext(name)[1].lower()
    if ext not in PROFILE_PHOTO_ALLOWED_EXTENSIONS:
        raise ValidationError(_('mobile.validation.invalid_image'))
    size = getattr(uploaded_file, 'size', None)
    if size is not None and size > PROFILE_PHOTO_MAX_SIZE_BYTES:
        size_mb = round(size / 1024 / 1024, 1)
        max_mb = PROFILE_PHOTO_MAX_SIZE_BYTES // (1024 * 1024)
        raise ValidationError(
            str(_('mobile.validation.image_too_large'))
            % {'max_mb': max_mb, 'size_mb': size_mb}
        )
    content_type = getattr(uploaded_file, 'content_type', '') or ''
    if content_type and not content_type.startswith('image/'):
        raise ValidationError(_('mobile.validation.invalid_image'))
    try:
        if hasattr(uploaded_file, 'seek'):
            uploaded_file.seek(0)
        get_image_dimensions(uploaded_file)
    except Exception:
        raise ValidationError(_('mobile.validation.invalid_image'))
    finally:
        if hasattr(uploaded_file, 'seek'):
            try:
                uploaded_file.seek(0)
            except Exception:
                pass


def update_driver_profile_photo(
    *,
    user_id: str,
    tenant_schema: str,
    uploaded_file,
    jwt_payload: dict | None = None,
    request=None,
) -> dict[str, Any]:
    """
    Phase 1: persist profile photo on ``DriverMaster.dl_image``.

    Later: swap to a dedicated ``profile_photo`` field with minimal changes here.

    Deletes the previous stored file when replacing (Django storage).
    """
    jwt_email = (jwt_payload or {}).get('email')
    ctx = _resolve_driver_context(
        user_id=user_id,
        tenant_schema=tenant_schema,
        jwt_email=jwt_email,
    )
    if not ctx['success']:
        return {'success': False, 'error': ctx['error']}

    driver = ctx['driver']

    try:
        _validate_profile_photo_upload(uploaded_file)
    except ValidationError as exc:
        # Single-message API style
        msg = exc.messages[0] if exc.messages else _('mobile.validation.failed')
        return {'success': False, 'error': msg}

    with schema_context(tenant_schema):
        from tenant_workspace.models import DriverMaster

        driver_db = DriverMaster.objects.filter(pk=driver.pk).first()
        if driver_db is None:
            return {'success': False, 'error': _('mobile.auth.not_a_driver')}

        if driver_db.dl_image:
            try:
                driver_db.dl_image.delete(save=False)
            except Exception as exc:
                logger.warning('Old dl_image delete failed pk=%s: %s', driver_db.pk, exc)

        driver_db.dl_image = uploaded_file
        driver_db.save(update_fields=['dl_image', 'updated_at'])

        driver_db.refresh_from_db()

        photo_url = safe_media_url(request, driver_db.dl_image)

    return {
        'success': True,
        'profile_photo_url': photo_url,
    }
