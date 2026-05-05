from django.utils import timezone

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
