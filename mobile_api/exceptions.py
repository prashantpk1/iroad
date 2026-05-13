"""
mobile_api/exceptions.py

Global exception handler for Mobile API.

Converts ALL DRF exceptions into our standard envelope:
  { "status": 0/2, "message": "...", "data": {} }

Configured in REST_FRAMEWORK settings:
  'EXCEPTION_HANDLER': 'mobile_api.exceptions.mobile_exception_handler'

Auth errors return status=2.
All other errors return status=0.
"""
import logging
from rest_framework.views import exception_handler
from rest_framework.exceptions import (
    AuthenticationFailed,
    NotAuthenticated,
    PermissionDenied,
    ValidationError,
    NotFound,
    MethodNotAllowed,
    Throttled,
)
from rest_framework.response import Response
from rest_framework import status
from django.utils.translation import gettext as _

logger = logging.getLogger('mobile_api')


def mobile_exception_handler(exc, context):
    """
    Custom exception handler for Mobile API.

    All exceptions are converted to our envelope:
      { "status": 0|2, "message": "...", "data": {} }

    Auth exceptions → status=2, HTTP 401
    Permission exceptions → status=2, HTTP 403
    Validation exceptions → status=0, HTTP 400 with field errors in data
    Not found → status=0, HTTP 404
    Throttled → status=0, HTTP 429
    All others → status=0, HTTP 400 or 500
    """
    # Let DRF handle the exception first to get the response object
    response = exception_handler(exc, context)

    # ── Authentication failures → status=2 ───────────────────────
    if isinstance(exc, (AuthenticationFailed, NotAuthenticated)):
        # Do not forward raw DRF/JWT exception text (may leak library details).
        account_deleted_msg = str(_('mobile.auth.account_deleted'))
        is_account_deleted = False
        if isinstance(exc, AuthenticationFailed):
            codes = exc.get_codes()
            if codes == 'account_deleted':
                is_account_deleted = True
            elif isinstance(codes, list) and 'account_deleted' in codes:
                is_account_deleted = True
        if is_account_deleted:
            return Response(
                {
                    'status': 2,
                    'message': account_deleted_msg,
                    'data': {},
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )
        return Response(
            {
                'status': 2,
                'message': str(_('mobile.auth.unauthorized')),
                'data': {},
            },
            status=status.HTTP_401_UNAUTHORIZED,
        )

    # ── Permission denied → status=2 ─────────────────────────────
    if isinstance(exc, PermissionDenied):
        return Response(
            {
                'status': 2,
                'message': str(_('mobile.auth.forbidden')),
                'data': {},
            },
            status=status.HTTP_403_FORBIDDEN,
        )

    # ── Validation errors → status=0 with field errors ───────────
    if isinstance(exc, ValidationError):
        # Flatten field errors into data for mobile to display
        errors = {}
        if hasattr(exc, 'detail'):
            if isinstance(exc.detail, dict):
                for field, messages in exc.detail.items():
                    if isinstance(messages, list):
                        errors[field] = str(messages[0])
                    else:
                        errors[field] = str(messages)
            elif isinstance(exc.detail, list):
                errors['non_field_errors'] = str(exc.detail[0])
            else:
                errors['error'] = str(exc.detail)

        return Response(
            {
                'status': 0,
                'message': _('mobile.validation.failed'),
                'data': {'errors': errors},
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    # ── Not found → status=0 ─────────────────────────────────────
    if isinstance(exc, NotFound):
        return Response(
            {
                'status': 0,
                'message': str(_('mobile.error.not_found')),
                'data': {},
            },
            status=status.HTTP_404_NOT_FOUND,
        )

    # ── Method not allowed ────────────────────────────────────────
    if isinstance(exc, MethodNotAllowed):
        return Response(
            {
                'status': 0,
                'message': _('mobile.error.method_not_allowed'),
                'data': {},
            },
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    # ── Throttled (rate limit) ────────────────────────────────────
    if isinstance(exc, Throttled):
        wait = exc.wait
        message = (
            _('mobile.error.rate_limit')
            + (f' Try again in {int(wait)} seconds.' if wait else '')
        )
        return Response(
            {
                'status': 0,
                'message': message,
                'data': {},
            },
            status=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    # ── DRF handled it but not one of the above ───────────────────
    if response is not None:
        status_code = response.status_code
        if status_code >= 500:
            message = str(_('mobile.error.server_error'))
        else:
            message = str(_('mobile.error.generic'))
            if hasattr(exc, 'detail') and status_code < 500:
                detail = exc.detail
                if isinstance(detail, str) and detail.strip():
                    message = detail
                elif isinstance(detail, list) and detail:
                    message = str(detail[0])
                elif isinstance(detail, dict) and detail:
                    # Avoid dumping arbitrary dicts (may include internal keys).
                    message = str(_('mobile.error.generic'))
        return Response(
            {
                'status': 0,
                'message': message,
                'data': {},
            },
            status=status_code,
        )

    # ── Unhandled exception → log it ─────────────────────────────
    logger.exception(
        'Unhandled Mobile API exception: %s',
        str(exc),
        extra={
            'view': context.get('view'),
            'request': context.get('request'),
        },
    )
    return Response(
        {
            'status': 0,
            'message': str(_('mobile.error.server_error')),
            'data': {},
        },
        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )

