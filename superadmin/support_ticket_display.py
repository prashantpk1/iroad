"""
Display helpers for ``SupportTicket`` rows stored in the public schema.
"""
import uuid

from django.db import connection
from django.utils.translation import gettext as _

from iroad_tenants.models import TenantRegistry
from tenant_workspace.models import TenantUser

from .models import AdminUser


def support_ticket_created_by_display_map(tickets):
    """
    Map each ticket's primary key to a human-readable "created by" label.

    ``SupportTicket.created_by`` may hold a tenant workspace ``TenantUser`` id,
    a ``TenantProfile`` id (session fallback), a control-panel ``AdminUser`` id,
    or a short non-UUID label.
    """
    items = list(tickets)
    if not items:
        return {}

    by_tenant_ids = {}
    for t in items:
        raw = (t.created_by or '').strip()
        if raw:
            by_tenant_ids.setdefault(str(t.tenant_id), set()).add(raw)

    tenant_user_labels = {}
    connection.set_schema_to_public()
    try:
        tenant_pks = list(by_tenant_ids.keys())
        regs = {
            str(r.tenant_profile_id): r
            for r in TenantRegistry.objects.filter(
                tenant_profile_id__in=tenant_pks,
            ).select_related('tenant_profile')
        }
        for tenant_pk, ids in by_tenant_ids.items():
            reg = regs.get(tenant_pk)
            if not reg:
                continue
            connection.set_tenant(reg)
            for tu in TenantUser.objects.filter(user_id__in=ids).only(
                'user_id', 'full_name', 'username', 'email',
            ):
                label = (
                    (tu.full_name or '').strip()
                    or (tu.username or '').strip()
                    or (tu.email or '').strip()
                )
                if label:
                    tenant_user_labels[(tenant_pk, str(tu.user_id))] = label
    finally:
        connection.set_schema_to_public()

    out = {}
    tenant_by = {}
    for t in items:
        tp = getattr(t, 'tenant', None)
        if tp is not None:
            tenant_by[str(t.tenant_id)] = tp

    need_admin = []
    for t in items:
        raw = (t.created_by or '').strip()
        tenant_pk = str(t.tenant_id)
        if not raw:
            out[t.pk] = '-'
            continue
        pair = (tenant_pk, raw)
        if pair in tenant_user_labels:
            out[t.pk] = tenant_user_labels[pair]
            continue
        if raw == tenant_pk:
            tp = tenant_by.get(tenant_pk)
            cn = (tp.company_name or '').strip() if tp else ''
            out[t.pk] = cn or _('Tenant portal')
            continue
        try:
            uuid.UUID(raw)
        except (ValueError, TypeError, AttributeError):
            out[t.pk] = raw
            continue
        need_admin.append((t.pk, raw))

    admin_map = {}
    admin_ids = {raw for _, raw in need_admin}
    if admin_ids:
        for a in AdminUser.objects.filter(pk__in=admin_ids).only(
            'id', 'first_name', 'last_name', 'email',
        ):
            name = f'{a.first_name} {a.last_name}'.strip()
            admin_map[str(a.pk)] = name or a.email

    for pk, raw in need_admin:
        out[pk] = admin_map.get(raw, _('Unknown user'))

    return out
