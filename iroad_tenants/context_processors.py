from django.utils import timezone
from django.conf import settings

from superadmin.models import SystemBanner


def tenant_system_banners(request):
    """
    Provide active/non-expired global banners to tenant-facing templates.
    """
    path = (getattr(request, 'path', '') or '').lower()
    if not path.startswith('/tenant/'):
        return {'tenant_system_banners': []}

    now = timezone.now()
    banners = SystemBanner.objects.filter(
        is_active=True,
        valid_from__lte=now,
    ).filter(
        valid_until__isnull=True,
    ) | SystemBanner.objects.filter(
        is_active=True,
        valid_from__lte=now,
        valid_until__gt=now,
    )
    banners = banners.order_by('-valid_from')
    return {'tenant_system_banners': list(banners[:5])}


def tenant_web_push_config(request):
    """Expose tenant web-push config to tenant portal templates."""
    path = (getattr(request, 'path', '') or '').lower()
    if not path.startswith('/tenant/'):
        return {'tenant_web_push_config': {}}

    cfg = {
        'apiKey': (getattr(settings, 'FIREBASE_WEB_API_KEY', '') or '').strip(),
        'authDomain': (getattr(settings, 'FIREBASE_WEB_AUTH_DOMAIN', '') or '').strip(),
        'projectId': (getattr(settings, 'FIREBASE_WEB_PROJECT_ID', '') or '').strip(),
        'storageBucket': (getattr(settings, 'FIREBASE_WEB_STORAGE_BUCKET', '') or '').strip(),
        'messagingSenderId': (getattr(settings, 'FIREBASE_WEB_MESSAGING_SENDER_ID', '') or '').strip(),
        'appId': (getattr(settings, 'FIREBASE_WEB_APP_ID', '') or '').strip(),
        'vapidKey': (getattr(settings, 'FCM_WEB_VAPID_KEY', '') or '').strip(),
    }
    return {'tenant_web_push_config': cfg}
