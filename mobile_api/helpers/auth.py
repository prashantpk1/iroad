"""
mobile_api/helpers/auth.py

JWT authentication helper for Mobile API.

Completely independent from superadmin JWT system.
Uses same PyJWT library but separate signing key,
separate payload structure, and separate TTL settings.

Token types:
  access  — short-lived (default 1 hour)
  refresh — long-lived (default 30 days)

Token payload:
  {
    'user_id': '<uuid>',
    'tenant_schema': '<schema_name>',
    'token_type': 'access' | 'refresh',
    'exp': <unix timestamp>,
    'iat': <unix timestamp>,
    'jti': '<uuid>',  # unique token ID for revocation
  }

Authorization header format:
  Authorization: Bearer <access_token>
"""
import uuid
import jwt
from datetime import datetime, timezone, timedelta
from django.conf import settings
from django.http import HttpRequest


# ─── Constants ───────────────────────────────────────────────────────────────

TOKEN_TYPE_ACCESS = 'access'
TOKEN_TYPE_REFRESH = 'refresh'
ALGORITHM = 'HS256'


# ─── Signing Key ─────────────────────────────────────────────────────────────

def _get_signing_key() -> str:
    """
    Get JWT signing key from settings.
    Falls back to Django SECRET_KEY if not configured.
    Never returns empty string.
    """
    key = getattr(settings, 'MOBILE_API_JWT_SIGNING_KEY', '').strip()
    if not key:
        key = settings.SECRET_KEY
    return key


def _get_redis_client():
    """Get Redis client if available, else None."""
    try:
        from superadmin.redis_helpers import get_redis_client
        return get_redis_client()
    except Exception:
        return None


def blacklist_token_jti(jti: str, exp_ts: int | None = None) -> bool:
    """
    Blacklist a JWT JTI until its expiry.
    Returns True when persisted, False on best-effort failure.
    """
    if not jti:
        return False
    client = _get_redis_client()
    if client is None:
        return False
    try:
        key = f'mobile:jwt:blacklist:{jti}'
        if exp_ts:
            now_ts = int(datetime.now(timezone.utc).timestamp())
            ttl = max(60, exp_ts - now_ts)
            client.setex(key, ttl, '1')
        else:
            client.setex(key, 3600, '1')
        return True
    except Exception:
        return False


def is_token_blacklisted(jti: str) -> bool:
    """Check if token jti is blacklisted."""
    if not jti:
        return False
    client = _get_redis_client()
    if client is None:
        return False
    try:
        return bool(client.get(f'mobile:jwt:blacklist:{jti}'))
    except Exception:
        return False


# ─── Token Generation ─────────────────────────────────────────────────────────

def generate_access_token(
    user_id: str,
    tenant_schema: str,
    extra_claims: dict | None = None,
) -> str:
    """
    Generate a short-lived access token.

    Args:
        user_id: str UUID of the authenticated user
        tenant_schema: str schema name of the tenant

    Returns:
        Encoded JWT string
    """
    ttl = getattr(
        settings,
        'MOBILE_API_ACCESS_TOKEN_TTL_SECONDS',
        3600,
    )
    now = datetime.now(timezone.utc)
    payload = {
        'user_id': str(user_id),
        'tenant_schema': tenant_schema,
        'token_type': TOKEN_TYPE_ACCESS,
        'iat': int(now.timestamp()),
        'exp': int((now + timedelta(seconds=ttl)).timestamp()),
        'jti': str(uuid.uuid4()),
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, _get_signing_key(), algorithm=ALGORITHM)


def generate_refresh_token(
    user_id: str,
    tenant_schema: str,
    extra_claims: dict | None = None,
) -> str:
    """
    Generate a long-lived refresh token.

    Args:
        user_id: str UUID of the authenticated user
        tenant_schema: str schema name of the tenant

    Returns:
        Encoded JWT string
    """
    ttl = getattr(
        settings,
        'MOBILE_API_REFRESH_TOKEN_TTL_SECONDS',
        2592000,
    )
    now = datetime.now(timezone.utc)
    payload = {
        'user_id': str(user_id),
        'tenant_schema': tenant_schema,
        'token_type': TOKEN_TYPE_REFRESH,
        'iat': int(now.timestamp()),
        'exp': int((now + timedelta(seconds=ttl)).timestamp()),
        'jti': str(uuid.uuid4()),
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, _get_signing_key(), algorithm=ALGORITHM)


def generate_token_pair(
    user_id: str,
    tenant_schema: str,
    extra_claims: dict | None = None,
) -> dict:
    """
    Generate both access and refresh tokens.

    Returns:
        dict with 'access_token' and 'refresh_token'
    """
    return {
        'access_token': generate_access_token(
            user_id,
            tenant_schema,
            extra_claims=extra_claims,
        ),
        'refresh_token': generate_refresh_token(
            user_id,
            tenant_schema,
            extra_claims=extra_claims,
        ),
    }


# ─── Token Verification ───────────────────────────────────────────────────────

def verify_token(token: str, expected_type: str = TOKEN_TYPE_ACCESS) -> dict | None:
    """
    Verify and decode a JWT token.

    Cryptographic and blacklist checks only. Callers that must enforce
    workspace account state (e.g. soft-deleted ``TenantUser``) should
    load the subject after verify — see ``MobileJWTAuthentication`` and
    ``authenticate_request`` / ``authenticate_refresh_request``.

    Args:
        token: JWT string
        expected_type: 'access' or 'refresh'

    Returns:
        Decoded payload dict if valid, None if invalid/expired
    """
    try:
        payload = jwt.decode(
            token,
            _get_signing_key(),
            algorithms=[ALGORITHM],
        )
        # Check token type
        if payload.get('token_type') != expected_type:
            return None
        # Check blacklist (logout revocation)
        if is_token_blacklisted(payload.get('jti', '')):
            return None
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


# ─── Request Auth Extraction ──────────────────────────────────────────────────

def get_token_from_request(request: HttpRequest) -> str | None:
    """
    Extract Bearer token from Authorization header.

    Returns:
        Token string or None if not present/invalid format
    """
    auth_header = request.headers.get('Authorization', '').strip()
    if auth_header.lower().startswith('bearer '):
        return auth_header.split(' ', 1)[1].strip()
    return None


def authenticate_request(request: HttpRequest) -> dict | None:
    """
    Full authentication flow for a mobile API request.

    Extracts Bearer token from Authorization header,
    verifies it as an access token, returns payload.

    Returns None if there is no token, the token is invalid, or the
    ``TenantUser`` row is missing or soft-deleted (same as DRF mobile auth).
    """
    token = get_token_from_request(request)
    if not token:
        return None
    payload = verify_token(token, expected_type=TOKEN_TYPE_ACCESS)
    if payload is None:
        return None
    user_id = str(payload.get('user_id') or '').strip()
    tenant_schema = str(payload.get('tenant_schema') or '').strip()
    if not user_id or not tenant_schema:
        return None
    try:
        from django_tenants.utils import schema_context
        from tenant_workspace.models import TenantUser

        with schema_context(tenant_schema):
            tu = TenantUser.all_objects.filter(pk=user_id).only('is_deleted').first()
    except Exception:
        return None
    if tu is None or getattr(tu, 'is_deleted', False):
        return None
    return payload


def authenticate_refresh_request(request: HttpRequest) -> dict | None:
    """
    Authentication flow for token refresh endpoint.

    Same as authenticate_request but expects a refresh token.
    Returns None when the token is invalid or the tenant user row is missing
    or soft-deleted (same client behaviour as an expired/invalid refresh).
    """
    token = get_token_from_request(request)
    if not token:
        return None
    payload = verify_token(token, expected_type=TOKEN_TYPE_REFRESH)
    if payload is None:
        return None
    user_id = str(payload.get('user_id') or '').strip()
    tenant_schema = str(payload.get('tenant_schema') or '').strip()
    if not user_id or not tenant_schema:
        return None
    try:
        from django_tenants.utils import schema_context
        from tenant_workspace.models import TenantUser

        with schema_context(tenant_schema):
            tu = TenantUser.all_objects.filter(pk=user_id).only('is_deleted').first()
    except Exception:
        return None
    if tu is None or getattr(tu, 'is_deleted', False):
        return None
    return payload

