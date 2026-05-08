import csv
import json
from decimal import Decimal
from urllib.parse import urlencode

import logging
import io
from django.apps import apps
from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import NoReverseMatch, resolve, reverse
from django.views import View
from django.contrib import messages
from django.contrib.auth.hashers import make_password
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.core.paginator import Paginator
from django.core.files.storage import default_storage
from django.db import IntegrityError, ProgrammingError, connection
from django.db.models.deletion import ProtectedError
from django.db import transaction as db_transaction
from django.db.models import Q, Sum
from django.utils.dateparse import parse_date, parse_datetime
from django.utils import timezone
from django_tenants.utils import schema_context
import os
import uuid
from superadmin.billing_helpers import (
    generate_invoice_pdf_bytes,
    calculate_pro_rata_credit,
    complete_order_payment_as_system,
    get_fx_snapshot,
    get_tax_code_for_tenant,
    refresh_order_projected_fields,
    resolve_upgrade_credit_basis_price,
    sync_or_create_order_payment_transaction,
    validate_downgrade_order,
)
from superadmin.models import TenantProfile, TenantSecuritySettings
from superadmin.models import (
    AccessLog,
    AdminUser,
    AuditLog,
    Country,
    Currency,
    OrderPlanLine,
    PaymentMethod,
    PlanPricingCycle,
    StandardInvoice,
    SubscriptionOrder,
    SubscriptionFAQ,
    SubscriptionPlan,
    SupportCategory,
    SupportTicket,
    TicketReply,
    PushDeviceToken,
)
from superadmin.redis_helpers import (
    get_all_active_tenant_sessions,
    get_tenant_session,
    refresh_tenant_session,
    revoke_tenant_session_key,
)
from superadmin.tenant_portal_auth import (
    clear_tenant_portal_cookie,
    get_tenant_portal_cookie_payload,
)
from superadmin.communication_helpers import send_named_notification_email
from tenant_workspace.models import (
    AutoNumberConfiguration,
    AutoNumberSequence,
    OrganizationProfile,
    TenantAddressMaster,
    TenantCargoCategory,
    TenantCargoMaster,
    TenantCargoMasterAttachment,
    TenantLocationMaster,
    TenantRouteMaster,
    TenantServiceItemMaster,
    TenantPriceList,
    TenantPriceListTripLine,
    TenantPriceListServiceLine,
    TenantClientAccount,
    TenantClientAccountSetting,
    TenantClientAttachment,
    TenantClientContact,
    TenantClientContract,
    TenantClientContractSetting,
    DriverAttachment,
    DriverMaster,
    DriverSettings,
    DriverTreasury,
    DriverTreasuryTransaction,
    TruckAttachment,
    TruckImage,
    TruckDriverAssignmentHistory,
    TruckSettings,
    TruckMaster,
    TruckTypeMaster,
    SalesInvoiceReport,
    SalesInvoiceReportBooking,
    SalesInvoiceReportSurcharge,
    SalesInvoiceReportShipment,
    TenantShipment,
    TenantShipmentDocument,
    TenantShipmentPodPage,
    TenantDocumentHandover,
    TenantDocumentHandoverLine,
    TenantTruckMovementLog,
    TenantOperationAction,
    TenantRole,
    TenantRolePermission,
    TenantUser,
)
from iroad_tenants.models import TenantPaymentCard, TenantRegistry
from iroad_tenants.forms_tenant_address import TenantAddressMasterForm
from iroad_tenants.forms_tenant_cargo import TenantCargoCategoryForm, TenantCargoMasterForm
from iroad_tenants.forms_tenant_location import TenantLocationMasterForm
from iroad_tenants.forms_tenant_route import TenantRouteMasterForm
from iroad_tenants.forms_truck_type import TruckTypeMasterForm
from iroad_tenants.forms_driver import DriverAttachmentForm, DriverSettingsForm
from iroad_tenants.forms_driver_master import DriverMasterForm
from iroad_tenants.forms_driver_treasury import (
    DriverTreasuryForm,
    DriverTreasuryTransactionForm,
)
from iroad_tenants.forms_truck_master import TruckMasterForm
from iroad_tenants.forms_truck import TruckSettingsForm
from iroad_tenants.forms_truck_attachment import TruckAttachmentForm
from iroad_tenants.forms_sales_invoice_report import (
    SalesInvoiceReportForm,
    SalesInvoiceReportBookingForm,
    SalesInvoiceReportSurchargeForm,
    SalesInvoiceReportShipmentForm,
)

logger = logging.getLogger(__name__)

ADDRESS_MASTER_AUTO_FORM_CODE = 'address-master'
ADDRESS_MASTER_AUTO_FORM_LABEL = 'Address Code'
ADDRESS_MASTER_REF_PREFIX = 'AD'

TRUCK_TYPE_AUTO_FORM_CODE = 'truck-type-master'
TRUCK_TYPE_AUTO_FORM_LABEL = 'Truck Type Code'
TRUCK_TYPE_REF_PREFIX = 'TRT'

TRUCK_MASTER_AUTO_FORM_CODE = 'truck-master'
TRUCK_MASTER_AUTO_FORM_LABEL = 'Truck Code'
TRUCK_MASTER_REF_PREFIX = 'TR'

DRIVER_MASTER_AUTO_FORM_CODE = 'driver-master'
DRIVER_MASTER_AUTO_FORM_LABEL = 'Driver Code'
DRIVER_MASTER_REF_PREFIX = 'DR'

TRUCK_ATTACHMENT_AUTO_FORM_CODE = 'truck-attachment'
TRUCK_ATTACHMENT_AUTO_FORM_LABEL = 'Truck Attachment No'
TRUCK_ATTACHMENT_REF_PREFIX = 'TRA'

DRIVER_ATTACHMENT_AUTO_FORM_CODE = 'driver-attachment'
DRIVER_ATTACHMENT_AUTO_FORM_LABEL = 'Driver Attachment No'
DRIVER_ATTACHMENT_REF_PREFIX = 'DRA'

CARGO_MASTER_AUTO_FORM_CODE = 'cargo-master'
CARGO_MASTER_AUTO_FORM_LABEL = 'Cargo code'
CARGO_MASTER_REF_PREFIX = 'CG'

CARGO_CATEGORY_AUTO_FORM_CODE = 'cargo-category'
CARGO_CATEGORY_AUTO_FORM_LABEL = 'Category code'
CARGO_CATEGORY_REF_PREFIX = 'CAT'

LOCATION_MASTER_AUTO_FORM_CODE = 'location-master'
LOCATION_MASTER_AUTO_FORM_LABEL = 'Location Master'
LOCATION_MASTER_REF_PREFIX = 'LC'

ROUTE_MASTER_AUTO_FORM_CODE = 'route-master'
ROUTE_MASTER_AUTO_FORM_LABEL = 'Route Master'
ROUTE_MASTER_REF_PREFIX = 'RT'

SERVICE_ITEM_MASTER_AUTO_FORM_CODE = 'service-item-master'
SERVICE_ITEM_MASTER_AUTO_FORM_LABEL = 'Service Item Master'
SERVICE_ITEM_MASTER_REF_PREFIX = 'SV'
PRICE_LIST_MASTER_AUTO_FORM_CODE = 'price-list-master'
PRICE_LIST_MASTER_AUTO_FORM_LABEL = 'Price List Master'
PRICE_LIST_MASTER_REF_PREFIX = 'PL'
SALES_INVOICE_REPORT_AUTO_FORM_CODE = 'sales-invoice-report'
SALES_INVOICE_REPORT_AUTO_FORM_LABEL = 'Sales Invoice Report'
SALES_INVOICE_REPORT_REF_PREFIX = 'SIR'
SHIPMENT_AUTO_FORM_CODE = 'shipment'
SHIPMENT_AUTO_FORM_LABEL = 'Shipment'
SHIPMENT_DOCUMENTS_AUTO_FORM_CODE = 'shipment-documents'
SHIPMENT_DOCUMENTS_AUTO_FORM_LABEL = 'Shipment Documents'
SHIPMENT_POD_AUTO_FORM_CODE = 'shipment-pod-analysis'
SHIPMENT_POD_AUTO_FORM_LABEL = 'Shipment POD'
DOCUMENT_HANDOVER_AUTO_FORM_CODE = 'document-handover'
DOCUMENT_HANDOVER_AUTO_FORM_LABEL = 'Document Handover'
TRUCK_MOVEMENT_LOG_AUTO_FORM_CODE = 'truck-movement-log'
TRUCK_MOVEMENT_LOG_AUTO_FORM_LABEL = 'Truck Movement Log'
OPERATION_ACTION_AUTO_FORM_CODE = 'operation-actions'
OPERATION_ACTION_AUTO_FORM_LABEL = 'Operation Actions'
SURCHARGE_SALES_AUTO_FORM_CODE = 'surcharge-sales-transaction'
SURCHARGE_SALES_AUTO_FORM_LABEL = 'Surcharge Sales Transaction'
SERVICE_ITEM_CATEGORY_OPTIONS = (
    'Service Category 1',
    'Service Category 2',
    'Service Category 3',
)

MAX_CARGO_ATTACHMENT_BYTES = 10 * 1024 * 1024

CLIENT_ATTACHMENT_AUTO_FORM_CODE = 'client-attachment'
CLIENT_ATTACHMENT_AUTO_FORM_LABEL = 'Client Attachment'
CLIENT_ATTACHMENT_REF_PREFIX = 'ATT'
MAX_CLIENT_ATTACHMENT_BYTES = 10 * 1024 * 1024
CLIENT_ATTACHMENT_ALLOWED_EXT = frozenset({
    '.pdf',
    '.doc',
    '.docx',
    '.xls',
    '.xlsx',
    '.jpg',
    '.jpeg',
    '.png',
    '.gif',
})

CLIENT_CONTRACT_AUTO_FORM_CODE = 'client-contract'
CLIENT_CONTRACT_AUTO_FORM_LABEL = 'Client Contract'
CLIENT_CONTRACT_REF_PREFIX = 'CNT'
MAX_CLIENT_CONTRACT_BYTES = 10 * 1024 * 1024
CLIENT_CONTRACT_ALLOWED_EXT = frozenset({
    '.pdf',
    '.doc',
    '.docx',
    '.jpg',
    '.jpeg',
    '.png',
    '.gif',
    '.webp',
})

MAX_SUPPORT_ATTACHMENT_BYTES = 10 * 1024 * 1024
SUPPORT_ATTACHMENT_ALLOWED_EXT = frozenset({
    '.pdf',
    '.png',
    '.jpg',
    '.jpeg',
    '.gif',
    '.webp',
    '.log',
})


def _validate_support_attachment_upload(upload, *, allow_empty=True):
    if not upload:
        return '' if allow_empty else 'Attachment is required.'
    try:
        size = int(upload.size)
    except (TypeError, ValueError):
        size = 0
    if size > MAX_SUPPORT_ATTACHMENT_BYTES:
        return 'Attachment must be 10MB or smaller.'
    name = (getattr(upload, 'name', None) or '').lower()
    ext = os.path.splitext(name)[1]
    if ext not in SUPPORT_ATTACHMENT_ALLOWED_EXT:
        return 'Attachment must be Image, PDF, or LOG file.'
    return ''


def _normalize_support_attachment_name(upload, row_id):
    """Rename support attachment file to <id>.<ext> before model save."""
    if not upload:
        return upload
    name = (getattr(upload, 'name', None) or '').lower()
    ext = os.path.splitext(name)[1]
    if not ext:
        ext = '.bin'
    upload.name = f'{row_id}{ext}'
    return upload


def _validate_client_attachment_upload(upload):
    if not upload:
        return 'Attachment file is required.'
    try:
        size = int(upload.size)
    except (TypeError, ValueError):
        size = 0
    if size > MAX_CLIENT_ATTACHMENT_BYTES:
        return 'File must be 10MB or smaller.'
    name = (getattr(upload, 'name', None) or '').lower()
    ext = os.path.splitext(name)[1]
    if ext not in CLIENT_ATTACHMENT_ALLOWED_EXT:
        return 'Unsupported file type. Use PDF, Office, or image formats.'
    return ''


def _validate_client_contract_upload(upload, *, allow_empty=False):
    if not upload:
        return '' if allow_empty else 'Contract attachment is required.'
    try:
        size = int(upload.size)
    except (TypeError, ValueError):
        size = 0
    if size > MAX_CLIENT_CONTRACT_BYTES:
        return 'File must be 10MB or smaller.'
    name = (getattr(upload, 'name', None) or '').lower()
    ext = os.path.splitext(name)[1]
    if ext not in CLIENT_CONTRACT_ALLOWED_EXT:
        return 'Unsupported file type. Use PDF, DOC, or image formats.'
    return ''


def _contract_status_for_dates(start_date, end_date):
    today = timezone.localdate()
    if end_date and end_date < today:
        return TenantClientContract.Status.EXPIRED
    if start_date and start_date > today:
        return TenantClientContract.Status.UPCOMING
    return TenantClientContract.Status.ACTIVE


def _redirect_client_contract_list(request):
    url = reverse('iroad_tenants:tenant_client_contract_list')
    return redirect(url)


def _get_singleton_client_contract_settings():
    """Single settings row for the active tenant schema (per-tenant DB)."""
    row = TenantClientContractSetting.objects.order_by('-updated_at').first()
    if row is None:
        row = TenantClientContractSetting.objects.create()
    return row


def _client_contract_settings_template_dict(settings_obj):
    return {
        'expired_contract_handling_mode': settings_obj.expired_contract_handling_mode,
        'grace_period_days': settings_obj.grace_period_days,
        'pre_expiry_notification_days': settings_obj.pre_expiry_notification_days,
        'post_expiry_notification_days': settings_obj.post_expiry_notification_days,
        'notification_frequency': settings_obj.notification_frequency,
        'notification_audience': settings_obj.notification_audience,
    }


def _validate_client_contract_period_against_settings(start_date, end_date, settings_obj):
    """Return field errors for contract period vs tenant Client Contract Settings."""
    errors = {}
    if not start_date or not end_date or end_date < start_date:
        return errors
    span_days = (end_date - start_date).days
    pre = int(settings_obj.pre_expiry_notification_days or 0)
    if pre > 0 and span_days < pre:
        errors['end_date'] = (
            f'Contract period must be at least {pre} day(s) so pre-expiry notifications '
            f'can apply (see Client Contract Settings).'
        )
    return errors


def _tenant_client_contracts_base_path():
    p = reverse('iroad_tenants:tenant_client_contract')
    return p if p.endswith('/') else f'{p}/'


def _tenant_client_contract_detail_path(contract_id):
    return f'{_tenant_client_contracts_base_path()}{contract_id}/detail/'


def _tenant_client_contract_edit_path(contract_id):
    return f'{_tenant_client_contracts_base_path()}{contract_id}/edit/'


def _tenant_client_contract_delete_path(contract_id):
    return f'{_tenant_client_contracts_base_path()}{contract_id}/delete/'


def _client_account_bootstrap_dict(account: TenantClientAccount, primary_contact=None):
    """JSON-serializable client header/overview for client-details.js bootstrap."""
    created = getattr(account, 'created_at', None)
    created_label = ''
    if created:
        created_label = timezone.localtime(created).strftime('%b %d, %Y, %I:%M %p')
    header_email = ''
    header_phone = ''
    if primary_contact is not None:
        header_email = (getattr(primary_contact, 'email', None) or '').strip()
        mob = (getattr(primary_contact, 'mobile_number', None) or '').strip()
        tel = (getattr(primary_contact, 'telephone_number', None) or '').strip()
        header_phone = mob or tel
    return {
        'account_no': account.account_no,
        'client_type': account.client_type,
        'status': account.status,
        'created_at': created_label,
        'name_arabic': account.name_arabic or '',
        'name_english': account.name_english or '',
        'display_name': account.display_name or '',
        'preferred_currency': account.preferred_currency or '',
        'billing_street_1': account.billing_street_1 or '',
        'billing_street_2': account.billing_street_2 or '',
        'billing_city': account.billing_city or '',
        'billing_region': account.billing_region or '',
        'postal_code': account.postal_code or '',
        'country': account.country or '',
        'commercial_registration_no': account.commercial_registration_no or '',
        'tax_registration_no': account.tax_registration_no or '',
        'national_id': account.national_id or '',
        'header_email': header_email,
        'header_phone': header_phone,
    }


def _resolve_tenant_favicon_url(request, tenant):
    """Return the default IR favicon for all tenant pages."""
    return (
        "https://ui-avatars.com/api/"
        "?name=IR&background=5051f9&color=fff&size=64&rounded=true&bold=true"
    )


def _tenant_context_from_session(request):
    auth_payload = get_tenant_portal_cookie_payload(request) or {}
    jwt_claims = auth_payload.get('jwt_claims') or {}
    tenant_id = auth_payload.get('tenant_id')
    tenant_jti = auth_payload.get('jti')
    tenant = None
    if tenant_id:
        tenant = TenantProfile.objects.filter(pk=tenant_id).first()
    if tenant is None:
        _clear_tenant_bootstrap_session(request)
        return None
    if tenant.account_status != 'Active':
        _clear_tenant_bootstrap_session(request)
        return None
    if not tenant_jti:
        _clear_tenant_bootstrap_session(request)
        return None

    # Refresh Redis-backed tenant session on every workspace request so that
    # tenant kill-switch/mass revoke takes effect immediately.
    sec = TenantSecuritySettings.objects.first()
    timeout_minutes = max(60, int(getattr(sec, 'tenant_web_timeout_hours', 12)) * 60)
    if not refresh_tenant_session(str(tenant.tenant_id), str(tenant_jti), timeout_minutes):
        _clear_tenant_bootstrap_session(request)
        return None
    
    # Default to tenant owner profile.
    if tenant.first_name or tenant.last_name:
        display_name = f"{tenant.first_name} {tenant.last_name}".strip()
    else:
        display_name = (tenant.company_name or 'Tenant User').strip()
    display_email = (tenant.primary_email or 'tenant@example.com').strip()
    display_role = 'Tenant Admin'
    permission_forms = set()
    is_tenant_admin = True

    # If this tenant session belongs to a tenant user, override display identity
    # and collect role permissions for menu-level visibility.
    session_data = get_tenant_session(str(tenant.tenant_id), str(tenant_jti)) or {}
    reference_id = str(session_data.get('reference_id') or '').strip()
    if reference_id and reference_id != str(tenant.tenant_id):
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is not None:
            try:
                tenant_user = TenantUser.objects.filter(pk=reference_id).first()
                if tenant_user:
                    display_name = (tenant_user.full_name or tenant_user.username or display_name).strip()
                    display_email = (tenant_user.email or display_email).strip()
                    display_role = (tenant_user.role_name or 'Tenant User').strip()
                    is_tenant_admin = False
                    role = TenantRole.objects.filter(
                        role_name_en__iexact=(tenant_user.role_name or '').strip()
                    ).first()
                    if role:
                        permission_forms = set(
                            TenantRolePermission.objects.filter(
                                role=role,
                                can_view=True,
                            ).values_list('form_name', flat=True)
                        )
            finally:
                connection.set_schema_to_public()

    return {
        'tenant': tenant,
        'display_name': display_name,
        'display_email': display_email,
        'display_role': display_role,
        'tenant_favicon_url': _resolve_tenant_favicon_url(request, tenant),
        'avatar_name': display_name.replace(' ', '+'),
        'is_tenant_admin': is_tenant_admin,
        'perm_forms': permission_forms,
        'can_view_cargo_master': 'Cargo Master' in permission_forms,
        'can_view_booking': 'Booking' in permission_forms,
        'can_view_shipment': 'Shipment' in permission_forms,
        'can_view_sales_invoicing': 'Sales Invoicing' in permission_forms,
        'jwt_claims': jwt_claims,
    }


def _tenant_redirect(request, route_name):
    base = reverse(route_name)
    return redirect(base)


def _clear_tenant_bootstrap_session(request):
    auth_payload = get_tenant_portal_cookie_payload(request) or {}
    tenant_id = auth_payload.get('tenant_id')
    jti = auth_payload.get('jti')
    if tenant_id and jti:
        revoke_tenant_session_key(str(tenant_id), str(jti))
    # Backward-compat cleanup for older session-based tenant bootstrap keys.
    for key in ('tenant_bootstrap_token', 'tenant_bootstrap_tenant_id', 'tenant_bootstrap_jti', 'tenant_bootstrap_expires_in'):
        request.session.pop(key, None)


def _activate_tenant_workspace_schema(request):
    """Switch DB connection to current tenant schema for tenant workspace ORM."""
    auth_payload = get_tenant_portal_cookie_payload(request) or {}
    tenant_id = auth_payload.get('tenant_id')
    if not tenant_id:
        return None

    connection.set_schema_to_public()
    registry = (
        TenantRegistry.objects.select_related('tenant_profile')
        .filter(tenant_profile_id=tenant_id)
        .first()
    )
    if registry is None:
        return None
    connection.set_tenant(registry)
    return registry


def _current_tenant_actor_id(request) -> str:
    """Best-effort actor id from tenant session for audit-style fields."""
    auth_payload = get_tenant_portal_cookie_payload(request) or {}
    tenant_id = str(auth_payload.get('tenant_id') or '').strip()
    jti = str(auth_payload.get('jti') or '').strip()
    if tenant_id and jti:
        session_data = get_tenant_session(tenant_id, jti) or {}
        reference_id = str(session_data.get('reference_id') or '').strip()
        if reference_id:
            return reference_id
    return tenant_id


class TenantDashboardView(View):
    """Tenant dashboard rendered from app template copy."""

    def get(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        return render(request, 'iroad_tenants/index.html', context)


class TenantSupportTicketListView(View):
    template_name = 'iroad_tenants/Support_Management/Support-ticket-master.html'

    def get(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        tenant = context['tenant']
        q = (request.GET.get('q') or '').strip()
        status = (request.GET.get('status') or '').strip()

        tickets_qs = SupportTicket.objects.filter(
            tenant=tenant,
        ).select_related(
            'category',
            'assigned_to',
        ).order_by('-created_at')

        if q:
            tickets_qs = tickets_qs.filter(
                Q(ticket_no__icontains=q) |
                Q(subject__icontains=q) |
                Q(category__name_en__icontains=q)
            )
        if status:
            tickets_qs = tickets_qs.filter(status=status)

        total_count = SupportTicket.objects.filter(tenant=tenant).count()
        open_count = SupportTicket.objects.filter(
            tenant=tenant,
            status='New',
        ).count()
        in_progress_count = SupportTicket.objects.filter(
            tenant=tenant,
            status='In_Progress',
        ).count()
        closed_count = SupportTicket.objects.filter(
            tenant=tenant,
            status='Closed',
        ).count()

        context.update({
            'tickets': tickets_qs,
            'search_q': q,
            'status_filter': status,
            'status_choices': SupportTicket.STATUS_CHOICES,
            'total_count': total_count,
            'open_count': open_count,
            'in_progress_count': in_progress_count,
            'closed_count': closed_count,
        })
        return render(request, self.template_name, context)


class TenantSupportTicketCreateView(View):
    template_name = 'iroad_tenants/Support_Management/Support-ticket-master-edit.html'

    def get(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        context.update({
            'categories': SupportCategory.objects.filter(
                is_active=True
            ).order_by('name_en'),
        })
        return render(request, self.template_name, context)

    def post(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        tenant = context['tenant']
        subject = (request.POST.get('subject') or '').strip()
        category_id = (request.POST.get('category_id') or '').strip()
        message_body = (request.POST.get('message_body') or '').strip()
        attachment = request.FILES.get('attachment')

        if not subject:
            messages.error(request, 'Subject is required.', extra_tags='tenant')
            return redirect('iroad_tenants:tenant_support_ticket_create')
        if not category_id:
            messages.error(request, 'Category is required.', extra_tags='tenant')
            return redirect('iroad_tenants:tenant_support_ticket_create')
        if not message_body:
            messages.error(request, 'Message is required.', extra_tags='tenant')
            return redirect('iroad_tenants:tenant_support_ticket_create')
        attachment_error = _validate_support_attachment_upload(attachment)
        if attachment_error:
            messages.error(request, attachment_error, extra_tags='tenant')
            return redirect('iroad_tenants:tenant_support_ticket_create')

        category = SupportCategory.objects.filter(
            category_id=category_id,
            is_active=True,
        ).first()
        if category is None:
            messages.error(request, 'Invalid category selected.', extra_tags='tenant')
            return redirect('iroad_tenants:tenant_support_ticket_create')

        actor_id = _current_tenant_actor_id(request) or str(tenant.tenant_id)
        ticket = SupportTicket.objects.create(
            ticket_no=SupportTicket.generate_ticket_no(),
            tenant=tenant,
            subject=subject,
            category=category,
            priority='Medium',
            status='New',
            created_by=actor_id,
            description=message_body,
        )

        first_reply = TicketReply(
            ticket=ticket,
            sender_type='Tenant_User',
            sender_id=actor_id,
            message_body=message_body,
            is_internal=False,
        )
        if attachment:
            first_reply.attachment = _normalize_support_attachment_name(
                attachment,
                first_reply.reply_id,
            )
        first_reply.save()

        messages.success(
            request,
            f'Support ticket {ticket.ticket_no} created successfully.',
            extra_tags='tenant',
        )
        return redirect('iroad_tenants:tenant_support_ticket_detail', ticket_id=ticket.ticket_id)


class TenantSupportTicketDetailView(View):
    template_name = 'iroad_tenants/Support_Management/Support-ticket-master-edit.html'

    def get(self, request, ticket_id):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        ticket = SupportTicket.objects.select_related(
            'category',
        ).filter(
            ticket_id=ticket_id,
            tenant=context['tenant'],
        ).first()
        if ticket is None:
            messages.error(request, 'Ticket not found.', extra_tags='tenant')
            return redirect('iroad_tenants:tenant_support_ticket_list')

        replies = ticket.replies.filter(
            is_internal=False,
        ).order_by('created_at')

        context.update({
            'ticket': ticket,
            'replies': replies,
            'is_ticket_detail': True,
        })
        return render(request, self.template_name, context)

    def post(self, request, ticket_id):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        ticket = SupportTicket.objects.filter(
            ticket_id=ticket_id,
            tenant=context['tenant'],
        ).first()
        if ticket is None:
            messages.error(request, 'Ticket not found.', extra_tags='tenant')
            return redirect('iroad_tenants:tenant_support_ticket_list')
        if ticket.status == 'Closed':
            messages.error(
                request,
                'Cannot reply to a closed ticket. Please create a new ticket.',
                extra_tags='tenant',
            )
            return redirect('iroad_tenants:tenant_support_ticket_detail', ticket_id=ticket.ticket_id)

        message_body = (request.POST.get('message_body') or '').strip()
        attachment = request.FILES.get('attachment')
        if not message_body:
            messages.error(request, 'Message is required.', extra_tags='tenant')
            return redirect('iroad_tenants:tenant_support_ticket_detail', ticket_id=ticket.ticket_id)
        attachment_error = _validate_support_attachment_upload(attachment)
        if attachment_error:
            messages.error(request, attachment_error, extra_tags='tenant')
            return redirect('iroad_tenants:tenant_support_ticket_detail', ticket_id=ticket.ticket_id)

        actor_id = _current_tenant_actor_id(request) or str(context['tenant'].tenant_id)
        reply = TicketReply(
            ticket=ticket,
            sender_type='Tenant_User',
            sender_id=actor_id,
            message_body=message_body,
            is_internal=False,
        )
        if attachment:
            reply.attachment = _normalize_support_attachment_name(
                attachment,
                reply.reply_id,
            )
        reply.save()
        ticket.status = 'In_Progress'
        ticket.save(update_fields=['status'])
        messages.success(request, 'Reply submitted successfully.', extra_tags='tenant')
        return redirect('iroad_tenants:tenant_support_ticket_detail', ticket_id=ticket.ticket_id)


def _tenant_cargo_master_access_guard(request, context):
    if context is None:
        response = redirect('login')
        clear_tenant_portal_cookie(response, request=request)
        return response
    if not context.get('is_tenant_admin') and not context.get('can_view_cargo_master'):
        messages.error(
            request,
            'You do not have permission to view Cargo Master.',
            extra_tags='tenant',
        )
        return _tenant_redirect(request, 'iroad_tenants:tenant_dashboard')
    return None


class TenantCargoMasterListView(View):
    """CG-001 cargo list with search, filters, pagination."""

    template_name = 'iroad_tenants/Master_Data/cargo_master/All-cargo-masters.html'

    def get(self, request):
        context = _tenant_context_from_session(request)
        denied = _tenant_cargo_master_access_guard(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        qs = TenantCargoMaster.objects.select_related('client_account', 'cargo_category')
        sq = request.GET.get('q', '').strip()
        cid = request.GET.get('client', '').strip()
        filter_client_id = ''

        status_raw = (request.GET.get('status') or '').strip().lower()
        if 'status' not in request.GET:
            qs = qs.filter(status=TenantCargoMaster.Status.ACTIVE)
            filter_status = ''
        elif not status_raw:
            qs = qs.filter(status=TenantCargoMaster.Status.ACTIVE)
            filter_status = 'active'
        elif status_raw == 'all':
            filter_status = 'all'
        elif status_raw == 'inactive':
            qs = qs.filter(status=TenantCargoMaster.Status.INACTIVE)
            filter_status = 'inactive'
        elif status_raw == 'active':
            qs = qs.filter(status=TenantCargoMaster.Status.ACTIVE)
            filter_status = 'active'
        else:
            qs = qs.filter(status=TenantCargoMaster.Status.ACTIVE)
            filter_status = 'active'

        if sq:
            qs = qs.filter(
                Q(display_name__icontains=sq)
                | Q(cargo_code__icontains=sq)
                | Q(client_sku_external_ref__icontains=sq)
                | Q(client_account__display_name__icontains=sq)
                | Q(cargo_category__name_english__icontains=sq)
                | Q(cargo_category__category_code__icontains=sq)
            )
        if cid:
            try:
                cid_uuid = uuid.UUID(cid)
                qs = qs.filter(client_account_id=cid_uuid)
                filter_client_id = str(cid_uuid)
            except ValueError:
                filter_client_id = ''

        sort_key_raw = (request.GET.get('sort') or 'truck_code').strip().lower()
        sort_dir_raw = (request.GET.get('dir') or 'asc').strip().lower()
        sort_map = {
            'truck_code': 'truck_code',
            'owner_name': 'owner_name',
            'default_driver_id': 'default_driver_id__driver_code',
            'sourcing_mode': 'sourcing_mode',
            'truck_type': 'truck_type__english_label',
            'plate_number': 'plate_number',
            'status': 'status',
        }
        sort_key = sort_map.get(sort_key_raw, 'truck_code')
        sort_dir = 'desc' if sort_dir_raw == 'desc' else 'asc'
        order_expr = f'-{sort_key}' if sort_dir == 'desc' else sort_key

        qs_ordered = qs.order_by(order_expr, '-created_at')
        stats = _cargo_master_list_stats(qs_ordered)
        paginator = Paginator(qs_ordered, 10)
        try:
            page_no = max(1, int(request.GET.get('page') or 1))
        except ValueError:
            page_no = 1
        page = paginator.get_page(page_no)

        total_count = paginator.count
        if total_count == 0:
            ps, pe = 0, 0
        else:
            ps = (page.number - 1) * paginator.per_page + 1
            pe = ps + len(page.object_list) - 1

        def _page_url(page_num):
            q = request.GET.copy()
            try:
                pn = int(page_num)
            except (TypeError, ValueError):
                pn = 1
            if pn > 1:
                q['page'] = str(pn)
            else:
                q.pop('page', None)
            return '?' + q.urlencode()

        pagination_page_links = [(n, _page_url(n)) for n in page.paginator.page_range]
        prev_url = _page_url(page.previous_page_number()) if page.has_previous() else None
        next_url = _page_url(page.next_page_number()) if page.has_next() else None

        clients = list(
            TenantClientAccount.objects.filter(
                status=TenantClientAccount.Status.ACTIVE,
            ).order_by('display_name')[:500]
        )

        context.update(
            {
                'cargos_page': page,
                'search_q': sq,
                'filter_status': filter_status,
                'filter_client_id': filter_client_id,
                'pagination_page_links': pagination_page_links,
                'pagination_prev_url': prev_url,
                'pagination_next_url': next_url,
                'stats': stats,
                'pagination_start': ps,
                'pagination_end': pe,
                'pagination_total': total_count,
                'client_filter_choices': clients,
                'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
            }
        )
        try:
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()


class TenantCargoMasterCreateView(View):
    template_name = 'iroad_tenants/Master_Data/cargo_master/Cargo-master.html'

    def get(self, request):
        context = _tenant_context_from_session(request)
        denied = _tenant_cargo_master_access_guard(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        try:
            if not TenantCargoCategory.objects.filter(
                status=TenantCargoCategory.Status.ACTIVE,
            ).exists():
                messages.warning(
                    request,
                    'Create at least one active Cargo Category before adding cargo.',
                    extra_tags='tenant',
                )
            preview = _preview_next_cargo_master_code()
            initial = {}
            cid = (request.GET.get('client') or '').strip()
            if cid:
                try:
                    initial['client_account'] = uuid.UUID(cid)
                except ValueError:
                    pass
            form = TenantCargoMasterForm(initial=initial)
            context.update(
                {
                    'form': form,
                    'preview_cargo_code': preview,
                    'is_edit': False,
                    'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()

    def post(self, request):
        context = _tenant_context_from_session(request)
        denied = _tenant_cargo_master_access_guard(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        redirect_resp = None
        try:
            form = TenantCargoMasterForm(request.POST, request.FILES)
            if not form.is_valid():
                preview = _preview_next_cargo_master_code()
                context.update(
                    {
                        'form': form,
                        'preview_cargo_code': preview,
                        'is_edit': False,
                        'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                    }
                )
                messages.error(request, 'Please fix the highlighted errors.', extra_tags='tenant')
                return render(request, self.template_name, context)

            try:
                with db_transaction.atomic():
                    code, seq = _next_auto_number_for_form(
                        CARGO_MASTER_AUTO_FORM_CODE,
                        CARGO_MASTER_AUTO_FORM_LABEL,
                        CARGO_MASTER_REF_PREFIX,
                    )
                    cargo = form.save(commit=False)
                    cargo.cargo_code = code
                    cargo.cargo_sequence = seq
                    cargo.full_clean()
                    cargo.save()
                    _save_cargo_master_attachments_from_request(request, cargo)
            except IntegrityError:
                logger.exception('Cargo Master create integrity violation')
                preview = _preview_next_cargo_master_code()
                form.add_error(
                    None,
                    ValidationError(
                        'Unable to allocate a unique cargo code. Please retry.',
                        code='cargo_integrity',
                    ),
                )
                context.update(
                    {
                        'form': form,
                        'preview_cargo_code': preview,
                        'is_edit': False,
                        'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                    }
                )
                messages.error(request, 'Could not save the cargo record.', extra_tags='tenant')
                return render(request, self.template_name, context)
            except ValidationError as ve:
                preview = _preview_next_cargo_master_code()
                if getattr(ve, 'error_dict', None):
                    for field_name, errs in ve.error_dict.items():
                        for err in errs:
                            form.add_error(field_name, err)
                else:
                    for msg in getattr(ve, 'messages', []) or [str(ve)]:
                        form.add_error(None, msg)
                context.update(
                    {
                        'form': form,
                        'preview_cargo_code': preview,
                        'is_edit': False,
                        'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                    }
                )
                messages.error(request, 'Could not save the cargo record.', extra_tags='tenant')
                return render(request, self.template_name, context)

            messages.success(
                request,
                f'Cargo {cargo.cargo_code} created successfully.',
                extra_tags='tenant',
            )
            redirect_resp = _tenant_redirect(request, 'iroad_tenants:tenant_cargo_master_list')
        finally:
            connection.set_schema_to_public()

        return redirect_resp


class TenantCargoMasterEditView(View):
    template_name = 'iroad_tenants/Master_Data/cargo_master/Cargo-master.html'

    def _load(self, cargo_id):
        return (
            TenantCargoMaster.objects.select_related('client_account', 'cargo_category')
            .filter(pk=cargo_id)
            .first()
        )

    def get(self, request, cargo_id):
        context = _tenant_context_from_session(request)
        denied = _tenant_cargo_master_access_guard(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        try:
            instance = self._load(cargo_id)
            if not instance:
                messages.error(request, 'Cargo record not found.', extra_tags='tenant')
                return _tenant_redirect(request, 'iroad_tenants:tenant_cargo_master_list')

            form = TenantCargoMasterForm(instance=instance)
            context.update(
                {
                    'form': form,
                    'is_edit': True,
                    'cargo_instance': instance,
                    'existing_attachments': list(instance.attachments.all()),
                    'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()

    def post(self, request, cargo_id):
        context = _tenant_context_from_session(request)
        denied = _tenant_cargo_master_access_guard(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        redirect_resp = None
        try:
            instance = self._load(cargo_id)
            if not instance:
                messages.error(request, 'Cargo record not found.', extra_tags='tenant')
                return _tenant_redirect(request, 'iroad_tenants:tenant_cargo_master_list')

            form = TenantCargoMasterForm(request.POST, request.FILES, instance=instance)
            if not form.is_valid():
                context.update(
                    {
                        'form': form,
                        'is_edit': True,
                        'cargo_instance': instance,
                        'existing_attachments': list(instance.attachments.all()),
                        'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                    }
                )
                messages.error(request, 'Please fix the highlighted errors.', extra_tags='tenant')
                return render(request, self.template_name, context)

            try:
                with db_transaction.atomic():
                    cargo = form.save(commit=False)
                    cargo.full_clean()
                    cargo.save()
                    if request.FILES.getlist('attachments'):
                        _save_cargo_master_attachments_from_request(request, cargo)
            except ValidationError as ve:
                if getattr(ve, 'error_dict', None):
                    for field_name, errs in ve.error_dict.items():
                        for err in errs:
                            form.add_error(field_name, err)
                else:
                    for msg in getattr(ve, 'messages', []) or [str(ve)]:
                        form.add_error(None, msg)
                context.update(
                    {
                        'form': form,
                        'is_edit': True,
                        'cargo_instance': instance,
                        'existing_attachments': list(instance.attachments.all()),
                        'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                    }
                )
                messages.error(request, 'Could not save the cargo record.', extra_tags='tenant')
                return render(request, self.template_name, context)

            messages.success(
                request,
                f'Cargo {cargo.cargo_code} updated successfully.',
                extra_tags='tenant',
            )
            redirect_resp = _tenant_redirect(request, 'iroad_tenants:tenant_cargo_master_list')
        finally:
            connection.set_schema_to_public()

        return redirect_resp


class TenantCargoMasterDetailView(View):
    template_name = 'iroad_tenants/Master_Data/cargo_master/Cargo-master.html'

    def get(self, request, cargo_id):
        context = _tenant_context_from_session(request)
        denied = _tenant_cargo_master_access_guard(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        try:
            cargo = (
                TenantCargoMaster.objects.select_related('client_account', 'cargo_category')
                .filter(pk=cargo_id)
                .first()
            )
            if not cargo:
                messages.error(request, 'Cargo record not found.', extra_tags='tenant')
                return _tenant_redirect(request, 'iroad_tenants:tenant_cargo_master_list')

            list_url = reverse('iroad_tenants:tenant_cargo_master_list')
            edit_url = reverse(
                'iroad_tenants:tenant_cargo_master_edit',
                kwargs={'cargo_id': cargo.cargo_id},
            )

            context.update(
                {
                    'form': TenantCargoMasterForm(instance=cargo),
                    'cargo_instance': cargo,
                    'existing_attachments': list(cargo.attachments.all()),
                    'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                    'back_to_list_url': list_url,
                    'edit_cargo_url': edit_url,
                    'is_edit': True,
                    'is_view': True,
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()


class TenantCargoMasterDeleteView(View):
    def post(self, request, cargo_id):
        context = _tenant_context_from_session(request)
        denied = _tenant_cargo_master_access_guard(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        try:
            cargo = TenantCargoMaster.objects.filter(pk=cargo_id).first()
            if not cargo:
                messages.error(request, 'Cargo record not found.', extra_tags='tenant')
                return _tenant_redirect(request, 'iroad_tenants:tenant_cargo_master_list')

            label = cargo.cargo_code
            cargo.delete()
            messages.success(request, f'Cargo {label} deleted.', extra_tags='tenant')
            return _tenant_redirect(request, 'iroad_tenants:tenant_cargo_master_list')
        finally:
            connection.set_schema_to_public()


class TenantCargoCategoryListView(View):
    template_name = 'iroad_tenants/Master_Data/cargo_master/Cargo-category-config-list.html'

    def get(self, request):
        context = _tenant_context_from_session(request)
        denied = _tenant_cargo_master_access_guard(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        try:
            scope = (request.GET.get('scope') or 'all').lower()
            if scope not in ('all', 'active', 'inactive'):
                scope = 'all'

            qs = TenantCargoCategory.objects.all().order_by('-created_at')
            if scope == 'active':
                qs = qs.filter(status=TenantCargoCategory.Status.ACTIVE)
            elif scope == 'inactive':
                qs = qs.filter(status=TenantCargoCategory.Status.INACTIVE)

            sq = request.GET.get('q', '').strip()
            if sq:
                qs = qs.filter(
                    Q(name_english__icontains=sq)
                    | Q(name_arabic__icontains=sq)
                    | Q(category_code__icontains=sq)
                )
            paginator = Paginator(qs, 10)
            try:
                page_no = max(1, int(request.GET.get('page') or 1))
            except ValueError:
                page_no = 1
            page = paginator.get_page(page_no)
            stats = {
                'total': TenantCargoCategory.objects.count(),
                'active': TenantCargoCategory.objects.filter(
                    status=TenantCargoCategory.Status.ACTIVE
                ).count(),
                'inactive': TenantCargoCategory.objects.filter(
                    status=TenantCargoCategory.Status.INACTIVE
                ).count(),
            }
            context.update(
                {
                    'categories_page': page,
                    'search_q': sq,
                    'category_scope': scope,
                    'category_stats': stats,
                    'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()


class TenantCargoCategoryCreateView(View):
    template_name = 'iroad_tenants/Master_Data/cargo_master/Cargo-category-config.html'

    def get(self, request):
        context = _tenant_context_from_session(request)
        denied = _tenant_cargo_master_access_guard(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        try:
            preview = _preview_next_cargo_category_code()
            form = TenantCargoCategoryForm()
            context.update(
                {
                    'form': form,
                    'preview_category_code': preview,
                    'is_edit': False,
                    'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()

    def post(self, request):
        context = _tenant_context_from_session(request)
        denied = _tenant_cargo_master_access_guard(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        redirect_resp = None
        try:
            form = TenantCargoCategoryForm(request.POST)
            if not form.is_valid():
                preview = _preview_next_cargo_category_code()
                context.update(
                    {
                        'form': form,
                        'preview_category_code': preview,
                        'is_edit': False,
                        'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                    }
                )
                messages.error(request, 'Please fix the highlighted errors.', extra_tags='tenant')
                return render(request, self.template_name, context)

            try:
                with db_transaction.atomic():
                    code, seq = _next_auto_number_for_form(
                        CARGO_CATEGORY_AUTO_FORM_CODE,
                        CARGO_CATEGORY_AUTO_FORM_LABEL,
                        CARGO_CATEGORY_REF_PREFIX,
                    )
                    cat = form.save(commit=False)
                    cat.category_code = code
                    cat.category_sequence = seq
                    cat.full_clean()
                    cat.save()
            except IntegrityError:
                preview = _preview_next_cargo_category_code()
                form.add_error(
                    None,
                    ValidationError(
                        'Unable to allocate a unique category code. Please retry.',
                        code='category_integrity',
                    ),
                )
                context.update(
                    {
                        'form': form,
                        'preview_category_code': preview,
                        'is_edit': False,
                        'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                    }
                )
                messages.error(request, 'Could not save the category.', extra_tags='tenant')
                return render(request, self.template_name, context)

            messages.success(
                request,
                f'Category {cat.category_code} created successfully.',
                extra_tags='tenant',
            )
            redirect_resp = _tenant_redirect(request, 'iroad_tenants:tenant_cargo_category_list')
        finally:
            connection.set_schema_to_public()

        return redirect_resp


def _redirect_cargo_category_list(request):
    return _tenant_redirect(request, 'iroad_tenants:tenant_cargo_category_list')


class TenantCargoCategoryDetailView(View):
    template_name = 'iroad_tenants/Master_Data/cargo_master/Cargo-category-config.html'

    def get(self, request, category_id):
        context = _tenant_context_from_session(request)
        denied = _tenant_cargo_master_access_guard(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        try:
            category = TenantCargoCategory.objects.filter(pk=category_id).first()
            if not category:
                messages.error(request, 'Category not found.', extra_tags='tenant')
                return _redirect_cargo_category_list(request)

            list_url = reverse('iroad_tenants:tenant_cargo_category_list')
            edit_url = reverse(
                'iroad_tenants:tenant_cargo_category_edit',
                kwargs={'category_id': category.category_id},
            )

            context.update(
                {
                    'form': TenantCargoCategoryForm(instance=category),
                    'category_instance': category,
                    'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                    'back_to_list_url': list_url,
                    'edit_category_url': edit_url,
                    'is_edit': True,
                    'is_view': True,
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()


class TenantCargoCategoryEditView(View):
    template_name = 'iroad_tenants/Master_Data/cargo_master/Cargo-category-config.html'

    def _load(self, category_id):
        return TenantCargoCategory.objects.filter(pk=category_id).first()

    def get(self, request, category_id):
        context = _tenant_context_from_session(request)
        denied = _tenant_cargo_master_access_guard(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        try:
            instance = self._load(category_id)
            if not instance:
                messages.error(request, 'Category not found.', extra_tags='tenant')
                return _redirect_cargo_category_list(request)

            form = TenantCargoCategoryForm(instance=instance)
            context.update(
                {
                    'form': form,
                    'is_edit': True,
                    'category_instance': instance,
                    'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()

    def post(self, request, category_id):
        context = _tenant_context_from_session(request)
        denied = _tenant_cargo_master_access_guard(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        redirect_resp = None
        try:
            instance = self._load(category_id)
            if not instance:
                messages.error(request, 'Category not found.', extra_tags='tenant')
                return _redirect_cargo_category_list(request)

            form = TenantCargoCategoryForm(request.POST, instance=instance)
            if not form.is_valid():
                context.update(
                    {
                        'form': form,
                        'is_edit': True,
                        'category_instance': instance,
                        'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                    }
                )
                messages.error(request, 'Please fix the highlighted errors.', extra_tags='tenant')
                return render(request, self.template_name, context)

            cat = form.save(commit=False)
            cat.full_clean()
            cat.save()
            messages.success(
                request,
                f'Category {cat.category_code} updated successfully.',
                extra_tags='tenant',
            )
            redirect_resp = _redirect_cargo_category_list(request)
        except ValidationError as ve:
            if getattr(ve, 'error_dict', None):
                for field_name, errs in ve.error_dict.items():
                    for err in errs:
                        form.add_error(field_name, err)
            else:
                for msg in getattr(ve, 'messages', []) or [str(ve)]:
                    form.add_error(None, msg)
            context.update(
                {
                    'form': form,
                    'is_edit': True,
                    'category_instance': instance,
                    'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                }
            )
            messages.error(request, 'Could not save the category.', extra_tags='tenant')
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()

        return redirect_resp


class TenantCargoCategoryDeleteView(View):
    def post(self, request, category_id):
        context = _tenant_context_from_session(request)
        denied = _tenant_cargo_master_access_guard(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        try:
            category = TenantCargoCategory.objects.filter(pk=category_id).first()
            if not category:
                messages.error(request, 'Category not found.', extra_tags='tenant')
                return _redirect_cargo_category_list(request)

            label = category.category_code
            try:
                category.delete()
            except ProtectedError:
                messages.error(
                    request,
                    'Cannot delete this category while cargo records reference it.',
                    extra_tags='tenant',
                )
                return _redirect_cargo_category_list(request)

            messages.success(request, f'Category {label} deleted.', extra_tags='tenant')
            return _redirect_cargo_category_list(request)
        finally:
            connection.set_schema_to_public()


class TenantSubscriptionPlanView(View):
    """Tenant subscription plan page with upgrade/downgrade/renewal actions."""

    _PLAN_ACTIONS = {'New_Subscription', 'Renewal', 'Upgrade', 'Downgrade'}

    def _resolve_payment_method(self, tenant, currency_code):
        has_default_card = TenantPaymentCard.objects.filter(
            tenant_profile=tenant,
            is_active=True,
            is_default=True,
        ).exists()
        if not has_default_card:
            return None
        # Card-based subscription flow uses online gateway only.
        return PaymentMethod.objects.filter(
            is_active=True,
            method_type='Online_Gateway',
            supported_currencies__contains=[currency_code],
        ).order_by('display_order').first()

    def _load_plan_context(self, tenant):
        current_plan = tenant.current_plan
        selected_currency = (
            SubscriptionOrder.objects.filter(tenant=tenant)
            .select_related('currency')
            .order_by('-created_at')
            .values_list('currency__currency_code', flat=True)
            .first()
        )
        if not selected_currency:
            selected_currency = (
                PlanPricingCycle.objects.select_related('currency')
                .filter(is_admin_only_cycle=False)
                .order_by('currency__currency_code')
                .values_list('currency__currency_code', flat=True)
                .first()
            )
        if not selected_currency:
            selected_currency = (
                Currency.objects.filter(is_active=True)
                .order_by('currency_code')
                .values_list('currency_code', flat=True)
                .first()
                or ''
            )

        eligible_plan_ids = PlanPricingCycle.objects.filter(
            is_admin_only_cycle=False,
            plan__is_active=True,
            plan__is_deleted=False,
        ).values_list('plan_id', flat=True).distinct()
        plans = list(
            SubscriptionPlan.objects.filter(
                is_active=True,
                is_deleted=False,
                plan_id__in=eligible_plan_ids,
            )
            .order_by('plan_name_en')
        )
        pricing_rows = (
            PlanPricingCycle.objects.select_related('currency')
            .filter(plan__in=plans, number_of_cycles__in=[1, 12], is_admin_only_cycle=False)
            .order_by('plan__plan_name_en', 'number_of_cycles', 'currency__currency_code')
        )
        pricing_map = {}
        for row in pricing_rows:
            key = (str(row.plan_id), row.currency_id)
            pricing_map.setdefault(key, {})[int(row.number_of_cycles)] = row

        current_monthly_price = None
        if current_plan and selected_currency:
            current_monthly = pricing_map.get(
                (str(current_plan.plan_id), selected_currency), {}
            ).get(1)
            if current_monthly:
                current_monthly_price = current_monthly.price

        plan_cards = []
        for plan in plans:
            prices_for_currency = pricing_map.get((str(plan.plan_id), selected_currency), {})
            monthly_row = prices_for_currency.get(1)
            yearly_row = prices_for_currency.get(12)
            if not monthly_row and not yearly_row:
                continue

            if monthly_row and yearly_row:
                default_cycle = 1
            elif monthly_row:
                default_cycle = 1
            else:
                default_cycle = 12
            default_row = monthly_row or yearly_row

            action_type = 'New_Subscription'
            action_label = 'Choose Plan'
            is_current = bool(current_plan and plan.plan_id == current_plan.plan_id)
            if is_current:
                action_type = 'Renewal'
                action_label = 'Renew Plan'
            elif current_plan and current_monthly_price is not None and monthly_row:
                if monthly_row.price >= current_monthly_price:
                    action_type = 'Upgrade'
                    action_label = 'Upgrade Plan'
                else:
                    action_type = 'Downgrade'
                    action_label = 'Downgrade Plan'

            plan_cards.append(
                {
                    'plan': plan,
                    'monthly_row': monthly_row,
                    'yearly_row': yearly_row,
                    'default_cycle': default_cycle,
                    'default_price': default_row.price if default_row else Decimal('0.00'),
                    'currency_code': selected_currency,
                    'is_current': is_current,
                    'action_type': action_type,
                    'action_label': action_label,
                }
            )

        has_yearly_option = any(card['yearly_row'] for card in plan_cards)
        faqs = list(
            SubscriptionFAQ.objects.filter(is_active=True)
            .order_by('display_order', 'created_at')
        )
        return {
            'plan_cards': plan_cards,
            'selected_currency': selected_currency,
            'has_yearly_option': has_yearly_option,
            'subscription_faqs': faqs,
        }

    def get(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        context.update(self._load_plan_context(context['tenant']))
        return render(request, 'iroad_tenants/Subscription_Manage/Subscription-plan.html', context)

    def post(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        tenant = context['tenant']
        action_type = (request.POST.get('action_type') or '').strip()
        plan_id = (request.POST.get('plan_id') or '').strip()
        selected_cycle_raw = (request.POST.get('selected_cycle') or '1').strip()
        selected_currency = (request.POST.get('currency_code') or '').strip()
        try:
            selected_cycle = int(selected_cycle_raw)
        except ValueError:
            selected_cycle = 1

        if action_type not in self._PLAN_ACTIONS:
            messages.error(request, 'Invalid subscription action.', extra_tags='tenant')
            return _tenant_redirect(request, 'iroad_tenants:tenant_subscription_plan')

        plan = SubscriptionPlan.objects.filter(
            plan_id=plan_id,
            is_active=True,
            is_deleted=False,
        ).first()
        if not plan:
            messages.error(request, 'Selected plan is not available.', extra_tags='tenant')
            return _tenant_redirect(request, 'iroad_tenants:tenant_subscription_plan')

        currency = Currency.objects.filter(
            currency_code=selected_currency,
            is_active=True,
        ).first()
        if not currency:
            currency = (
                SubscriptionOrder.objects.filter(tenant=tenant)
                .select_related('currency')
                .order_by('-created_at')
                .values_list('currency__currency_code', flat=True)
                .first()
            )
            currency = Currency.objects.filter(currency_code=currency, is_active=True).first()
        if not currency:
            messages.error(request, 'No active currency is configured.', extra_tags='tenant')
            return _tenant_redirect(request, 'iroad_tenants:tenant_subscription_plan')

        pricing_row = PlanPricingCycle.objects.filter(
            plan=plan,
            currency=currency,
            number_of_cycles=selected_cycle,
            is_admin_only_cycle=False,
        ).first()
        if not pricing_row:
            messages.error(
                request,
                'Pricing is not configured for this cycle/currency.',
                extra_tags='tenant',
            )
            return _tenant_redirect(request, 'iroad_tenants:tenant_subscription_plan')

        if action_type == 'Downgrade':
            error = validate_downgrade_order(tenant, plan)
            if error:
                messages.error(request, error, extra_tags='tenant')
                return _tenant_redirect(request, 'iroad_tenants:tenant_subscription_plan')

        tax = get_tax_code_for_tenant(tenant, client_ip=request.META.get('REMOTE_ADDR'))
        if tax is None:
            messages.error(
                request,
                'Tax settings are missing. Contact support.',
                extra_tags='tenant',
            )
            return _tenant_redirect(request, 'iroad_tenants:tenant_subscription_plan')

        fx = get_fx_snapshot(currency.currency_code, strict=True)
        if fx is None:
            messages.error(
                request,
                'Exchange rate is missing for selected currency.',
                extra_tags='tenant',
            )
            return _tenant_redirect(request, 'iroad_tenants:tenant_subscription_plan')

        payment_method = self._resolve_payment_method(tenant, currency.currency_code)
        if payment_method is None:
            messages.error(
                request,
                'Add a default payment card first. Offline bank transfer is not supported here.',
                extra_tags='tenant',
            )
            return _tenant_redirect(request, 'iroad_tenants:tenant_subscription_plan')

        tax_rate = tax.rate_percent or Decimal('0.00')
        plan_price = pricing_row.price
        pro_rata = Decimal('0.00')
        if action_type == 'Upgrade' and tenant.current_plan:
            old_price = resolve_upgrade_credit_basis_price(
                tenant.current_plan,
                currency.currency_code,
            )
            pro_rata = calculate_pro_rata_credit(tenant, old_price)
        line_total = (plan_price + pro_rata).quantize(Decimal('0.01'))
        sub_total = line_total
        tax_amount = (sub_total * tax_rate / Decimal('100')).quantize(Decimal('0.01'))
        grand_total = (sub_total + tax_amount).quantize(Decimal('0.01'))
        base_equiv = (grand_total * fx).quantize(Decimal('0.01'))

        with db_transaction.atomic():
            order = SubscriptionOrder.objects.create(
                tenant=tenant,
                order_classification=action_type,
                currency=currency,
                payment_method=payment_method,
                created_by=None,
                promo_code=None,
                tax_code=tax,
                sub_total=sub_total,
                discount_amount=Decimal('0.00'),
                tax_amount=tax_amount,
                grand_total=grand_total,
                exchange_rate_snapshot=fx,
                base_currency_equivalent=base_equiv,
                order_status='Pending_Payment',
            )
            OrderPlanLine.objects.create(
                order=order,
                plan=plan,
                number_of_cycles=selected_cycle,
                plan_price=plan_price,
                pro_rata_adjustment=pro_rata,
                line_total=line_total,
                plan_name_en_snapshot=plan.plan_name_en,
                plan_name_ar_snapshot=plan.plan_name_ar or '',
            )
            refresh_order_projected_fields(order)
            order.save(
                update_fields=[
                    'projected_plan',
                    'projected_expiry_date',
                    'projected_max_users',
                    'projected_max_internal_trucks',
                    'projected_max_external_trucks',
                    'projected_max_drivers',
                ]
            )
            sync_or_create_order_payment_transaction(order)

        if complete_order_payment_as_system(order, None):
            messages.success(
                request,
                f'{plan.plan_name_en} {action_type.replace("_", " ").lower()} completed successfully.',
                extra_tags='tenant',
            )
        else:
            messages.warning(
                request,
                'Order created, but payment capture did not complete.',
                extra_tags='tenant',
            )
        return _tenant_redirect(request, 'iroad_tenants:tenant_subscription_billing')


class TenantSubscriptionBillingView(View):
    """Tenant subscription billing page with live data."""

    @staticmethod
    def _parse_expiry(expiry_value):
        raw = (expiry_value or '').strip()
        if '/' not in raw:
            return None, None
        month_s, year_s = raw.split('/', 1)
        try:
            month = int(month_s)
            yy = int(year_s)
        except ValueError:
            return None, None
        if month < 1 or month > 12:
            return None, None
        year = 2000 + yy if yy < 100 else yy
        return month, year

    def get(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        tenant = context['tenant']
        current_plan = tenant.current_plan

        latest_plan_line = (
            OrderPlanLine.objects.select_related('order')
            .filter(order__tenant=tenant)
            .order_by('-order__created_at')
            .first()
        )
        current_cycle = 1
        if latest_plan_line and latest_plan_line.number_of_cycles in (1, 12):
            current_cycle = latest_plan_line.number_of_cycles

        active_currency = (
            SubscriptionOrder.objects.filter(tenant=tenant)
            .select_related('currency')
            .order_by('-created_at')
            .values_list('currency__currency_code', flat=True)
            .first()
        ) or 'SAR'

        current_price = Decimal('0.00')
        if current_plan:
            current_pricing = PlanPricingCycle.objects.filter(
                plan=current_plan,
                currency_id=active_currency,
                number_of_cycles=current_cycle,
            ).first()
            if current_pricing:
                current_price = current_pricing.price

        invoices = list(
            StandardInvoice.objects.filter(tenant=tenant)
            .select_related('currency')
            .order_by('-issue_date')[:20]
        )
        start_of_year = timezone.now().date().replace(month=1, day=1)
        total_spent_ytd = sum(
            (
                inv.grand_total
                for inv in invoices
                if inv.issue_date
                and inv.issue_date.date() >= start_of_year
                and inv.status in ('Issued', 'Paid')
            ),
            Decimal('0.00'),
        )

        next_payment_due = tenant.subscription_expiry_date
        cards = list(
            TenantPaymentCard.objects.filter(
                tenant_profile=tenant,
                is_active=True,
            ).order_by('-is_default', '-updated_at')
        )
        # Safety normalization: keep exactly one default card per tenant.
        if cards:
            default_cards = [c for c in cards if c.is_default]
            if len(default_cards) != 1:
                keeper = default_cards[0] if default_cards else cards[0]
                TenantPaymentCard.objects.filter(
                    tenant_profile=tenant,
                    is_active=True,
                ).update(is_default=False)
                keeper.is_default = True
                keeper.save(update_fields=['is_default', 'updated_at'])
                cards = list(
                    TenantPaymentCard.objects.filter(
                        tenant_profile=tenant,
                        is_active=True,
                    ).order_by('-is_default', '-updated_at')
                )
        default_card = next((c for c in cards if c.is_default), cards[0] if cards else None)

        context.update(
            {
                'current_plan': current_plan,
                'current_cycle': current_cycle,
                'current_cycle_label': 'Yearly Billing' if current_cycle == 12 else 'Monthly Billing',
                'current_price': current_price,
                'active_currency': active_currency,
                'next_payment_due': next_payment_due,
                'invoices': invoices,
                'total_spent_ytd': total_spent_ytd,
                'default_card': default_card,
                'payment_cards': cards,
            }
        )
        return render(request, 'iroad_tenants/Subscription_Manage/Subscription-billing.html', context)

    def post(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        tenant = context['tenant']
        action = (request.POST.get('action') or '').strip()
        if action == 'remove_card':
            target_card_id = (request.POST.get('card_id') or '').strip()
            target_card = TenantPaymentCard.objects.filter(
                tenant_profile=tenant,
                card_id=target_card_id,
                is_active=True,
            ).first()
            if not target_card:
                messages.error(request, 'Card to remove was not found.', extra_tags='tenant')
                return _tenant_redirect(request, 'iroad_tenants:tenant_subscription_billing')
            active_cards = list(
                TenantPaymentCard.objects.filter(
                    tenant_profile=tenant,
                    is_active=True,
                ).order_by('-is_default', '-updated_at')
            )
            if len(active_cards) <= 1:
                messages.error(request, 'At least one payment card is required.', extra_tags='tenant')
                return _tenant_redirect(request, 'iroad_tenants:tenant_subscription_billing')
            if target_card.is_default:
                messages.error(request, 'Current in-use card cannot be deleted.', extra_tags='tenant')
                return _tenant_redirect(request, 'iroad_tenants:tenant_subscription_billing')
            was_default = target_card.is_default
            target_card.is_active = False
            target_card.is_default = False
            target_card.save(update_fields=['is_active', 'is_default', 'updated_at'])
            if was_default:
                replacement = TenantPaymentCard.objects.filter(
                    tenant_profile=tenant,
                    is_active=True,
                ).order_by('-updated_at').first()
                if replacement:
                    replacement.is_default = True
                    replacement.save(update_fields=['is_default', 'updated_at'])
            messages.success(request, 'Payment card removed successfully.', extra_tags='tenant')
            return _tenant_redirect(request, 'iroad_tenants:tenant_subscription_billing')

        if action not in ('add_card', 'update_card'):
            messages.error(request, 'Invalid card action.', extra_tags='tenant')
            return _tenant_redirect(request, 'iroad_tenants:tenant_subscription_billing')

        cardholder_name = (request.POST.get('cardholderName') or '').strip()
        card_number = (request.POST.get('cardNumber') or '').strip().replace(' ', '')
        expiry = (request.POST.get('expiry') or '').strip()
        cvc = (request.POST.get('cvc') or '').strip()
        set_default = bool(request.POST.get('setAsDefault'))
        target_card_id = (request.POST.get('card_id') or '').strip()

        if not cardholder_name:
            messages.error(request, 'Card holder name is required.', extra_tags='tenant')
            return _tenant_redirect(request, 'iroad_tenants:tenant_subscription_billing')

        expiry_month, expiry_year = self._parse_expiry(expiry)
        if not expiry_month or not expiry_year:
            messages.error(request, 'Enter expiry in MM/YY format.', extra_tags='tenant')
            return _tenant_redirect(request, 'iroad_tenants:tenant_subscription_billing')

        if action == 'update_card':
            target_card = TenantPaymentCard.objects.filter(
                tenant_profile=tenant,
                card_id=target_card_id,
                is_active=True,
            ).first()
            if not target_card:
                messages.error(request, 'Card to update was not found.', extra_tags='tenant')
                return _tenant_redirect(request, 'iroad_tenants:tenant_subscription_billing')
            # Update modal shows masked card/CVC by default. If masked value is posted,
            # retain existing stored last4 and only validate when a full new number is entered.
            if '•' in card_number or card_number == '':
                card_last4 = target_card.last4
            else:
                if not card_number.isdigit() or len(card_number) != 16:
                    messages.error(request, 'Enter a valid 16-digit card number.', extra_tags='tenant')
                    return _tenant_redirect(request, 'iroad_tenants:tenant_subscription_billing')
                card_last4 = card_number[-4:]
            if not ('•' in cvc or cvc == '') and (not cvc.isdigit() or len(cvc) not in (3, 4)):
                messages.error(request, 'Enter a valid CVC.', extra_tags='tenant')
                return _tenant_redirect(request, 'iroad_tenants:tenant_subscription_billing')
        else:
            target_card = None
            if not card_number.isdigit() or len(card_number) != 16:
                messages.error(request, 'Enter a valid 16-digit card number.', extra_tags='tenant')
                return _tenant_redirect(request, 'iroad_tenants:tenant_subscription_billing')
            if not cvc.isdigit() or len(cvc) not in (3, 4):
                messages.error(request, 'Enter a valid CVC.', extra_tags='tenant')
                return _tenant_redirect(request, 'iroad_tenants:tenant_subscription_billing')
            card_last4 = card_number[-4:]

        if set_default:
            TenantPaymentCard.objects.filter(
                tenant_profile=tenant,
                is_active=True,
            ).update(is_default=False)
        if target_card is not None:
            target_card.cardholder_name = cardholder_name
            target_card.brand = 'VISA'
            target_card.last4 = card_last4
            target_card.expiry_month = expiry_month
            target_card.expiry_year = expiry_year
            target_card.is_default = set_default or target_card.is_default
            target_card.save(
                update_fields=[
                    'cardholder_name',
                    'brand',
                    'last4',
                    'expiry_month',
                    'expiry_year',
                    'is_default',
                    'updated_at',
                ]
            )
            messages.success(request, 'Payment card updated successfully.', extra_tags='tenant')
        else:
            TenantPaymentCard.objects.create(
                tenant_profile=tenant,
                cardholder_name=cardholder_name,
                brand='VISA',
                last4=card_last4,
                expiry_month=expiry_month,
                expiry_year=expiry_year,
                is_default=set_default or not TenantPaymentCard.objects.filter(
                    tenant_profile=tenant,
                    is_active=True,
                ).exists(),
                is_active=True,
            )
            messages.success(request, 'Payment card saved successfully.', extra_tags='tenant')
        return _tenant_redirect(request, 'iroad_tenants:tenant_subscription_billing')


class TenantInvoiceDownloadView(View):
    """Download a single invoice PDF for the logged-in tenant."""

    def get(self, request, invoice_id):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        tenant = context['tenant']
        invoice = (
            StandardInvoice.objects.select_related('tenant')
            .filter(invoice_id=invoice_id, tenant=tenant)
            .first()
        )
        if invoice is None:
            return HttpResponse('Invoice not found.', status=404)

        pdf_bytes = generate_invoice_pdf_bytes(invoice)
        if pdf_bytes:
            response = HttpResponse(pdf_bytes, content_type='application/pdf')
            response['Content-Disposition'] = (
                f'attachment; filename="{invoice.invoice_number}.pdf"'
            )
            return response

        fallback = (
            f'Invoice: {invoice.invoice_number}\n'
            f'Status: {invoice.status}\n'
            f'Amount: {invoice.currency_id} {invoice.grand_total}\n'
            f'Due Date: {invoice.due_date or ""}\n'
        )
        response = HttpResponse(fallback, content_type='text/plain')
        response['Content-Disposition'] = (
            f'attachment; filename="{invoice.invoice_number}.txt"'
        )
        return response


class TenantInvoiceExportAllView(View):
    """Export tenant invoice history as CSV."""

    def get(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        tenant = context['tenant']
        invoices = (
            StandardInvoice.objects.select_related('currency')
            .filter(tenant=tenant)
            .order_by('-issue_date')
        )

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                'Invoice Number',
                'Issue Date',
                'Due Date',
                'Plan',
                'Currency',
                'Sub Total',
                'Tax',
                'Discount',
                'Grand Total',
                'Status',
            ]
        )
        for inv in invoices:
            plan_name = ''
            first_line = inv.order.plan_lines.select_related('plan').first() if inv.order_id else None
            if first_line:
                plan_name = first_line.plan_name_en_snapshot or first_line.plan.plan_name_en
            writer.writerow(
                [
                    inv.invoice_number,
                    inv.issue_date.strftime('%Y-%m-%d') if inv.issue_date else '',
                    inv.due_date.strftime('%Y-%m-%d') if inv.due_date else '',
                    plan_name,
                    inv.currency_id,
                    f'{inv.sub_total}',
                    f'{inv.tax_amount}',
                    f'{inv.discount_amount}',
                    f'{inv.grand_total}',
                    inv.status,
                ]
            )

        response = HttpResponse(output.getvalue(), content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="tenant_invoices.csv"'
        return response


def _build_login_session_events_context(request):
    auth_payload = get_tenant_portal_cookie_payload(request) or {}
    tenant_id = str(auth_payload.get('tenant_id') or '').strip()

    events = []

    # Live active sessions from Redis (tenant-specific).
    for session in get_all_active_tenant_sessions():
        if str(session.get('tenant_id') or '') != tenant_id:
            continue
        started_at = session.get('started_at')
        if not started_at:
            continue
        started_dt = parse_datetime(str(started_at))
        if started_dt is None:
            continue
        if timezone.is_naive(started_dt):
            started_dt = timezone.make_aware(started_dt, timezone.get_current_timezone())
        events.append(
            {
                'timestamp': started_dt,
                'action': 'Session Active',
                'module': 'Authentication',
                'performed_by': session.get('reference_name') or session.get('reference_id') or 'Tenant User',
                'event_type': 'active_session',
            }
        )

    successful_logins = 0
    failed_attempts = 0

    # Tenant user login history and failed attempts from tenant workspace schema.
    tenant_registry = _activate_tenant_workspace_schema(request)
    if tenant_registry is not None:
        try:
            users = list(
                TenantUser.objects.values(
                    'full_name',
                    'email',
                    'last_login_at',
                    'login_attempts',
                )
            )
            for user in users:
                if user.get('last_login_at'):
                    successful_logins += 1
                    events.append(
                        {
                            'timestamp': user.get('last_login_at'),
                            'action': 'Login Success',
                            'module': 'Authentication',
                            'performed_by': user.get('full_name') or user.get('email') or 'Tenant User',
                            'event_type': 'login_success',
                        }
                    )
                failed_attempts += int(user.get('login_attempts') or 0)
                if int(user.get('login_attempts') or 0) > 0:
                    events.append(
                        {
                            'timestamp': timezone.now(),
                            'action': f'Failed Attempts ({int(user.get("login_attempts") or 0)})',
                            'module': 'Security',
                            'performed_by': user.get('full_name') or user.get('email') or 'Tenant User',
                            'event_type': 'failed_attempt',
                        }
                    )
        finally:
            connection.set_schema_to_public()

    events.sort(key=lambda row: row.get('timestamp') or timezone.now(), reverse=True)
    paginator = Paginator(events, 10)
    page_obj = paginator.get_page(request.GET.get('page'))

    active_sessions = sum(1 for event in events if event.get('event_type') == 'active_session')
    total_events = len(events)

    rows = []
    start_index = (page_obj.number - 1) * paginator.per_page
    for index, event in enumerate(page_obj.object_list, start=start_index + 1):
        rows.append(
            {
                'sl_no': index,
                'timestamp': event.get('timestamp'),
                'action': event.get('action'),
                'module': event.get('module'),
                'performed_by': event.get('performed_by'),
            }
        )

    return {
        'login_session_total_events': total_events,
        'login_session_successful_logins': successful_logins,
        'login_session_active_sessions': active_sessions,
        'login_session_failed_attempts': failed_attempts,
        'login_session_rows': rows,
        'login_session_page_obj': page_obj,
    }


def _build_role_permission_changes_context(request):
    rows = []
    total_changes = 0
    active_roles = 0
    permissions_updated = 0
    roles_deleted = 0
    page_obj = Paginator([], 10).get_page(1)

    tenant_registry = _activate_tenant_workspace_schema(request)
    if tenant_registry is not None:
        try:
            role_events = list(
                TenantRole.objects.values(
                    'updated_at',
                    'role_name_en',
                    'status',
                    'created_by_label',
                    'created_at',
                )
            )
            permission_events = list(
                TenantRolePermission.objects.select_related('role').values(
                    'updated_at',
                    'module_name',
                    'form_name',
                    'role__role_name_en',
                    'role__created_by_label',
                    'created_at',
                )
            )

            active_roles = TenantRole.objects.filter(
                status=TenantRole.Status.ACTIVE
            ).count()
            permissions_updated = TenantRolePermission.objects.count()
            # This card is labeled "Roles Deleted" in UI; use non-active roles
            # as the closest live indicator since hard deletes are not tracked.
            roles_deleted = TenantRole.objects.exclude(
                status=TenantRole.Status.ACTIVE
            ).count()
            total_changes = len(role_events) + len(permission_events)

            raw_events = []
            for event in role_events:
                created_at = event.get('created_at')
                updated_at = event.get('updated_at')
                action = 'Role Updated'
                if created_at and updated_at and created_at == updated_at:
                    action = 'Role Created'
                if event.get('status') == TenantRole.Status.INACTIVE:
                    action = 'Role Disabled'
                raw_events.append(
                    {
                        'timestamp': updated_at,
                        'action': action,
                        'module': 'Roles',
                        'performed_by': event.get('created_by_label') or 'System',
                    }
                )

            for event in permission_events:
                created_at = event.get('created_at')
                updated_at = event.get('updated_at')
                action = 'Permission Updated'
                if created_at and updated_at and created_at == updated_at:
                    action = 'Permission Added'
                raw_events.append(
                    {
                        'timestamp': updated_at,
                        'action': action,
                        'module': event.get('module_name') or 'Permissions',
                        'performed_by': (
                            event.get('role__created_by_label')
                            or event.get('role__role_name_en')
                            or 'System'
                        ),
                    }
                )

            raw_events.sort(
                key=lambda item: item.get('timestamp') or timezone.now(),
                reverse=True,
            )
            paginator = Paginator(raw_events, 10)
            page_obj = paginator.get_page(request.GET.get('page'))
            start_index = (page_obj.number - 1) * paginator.per_page
            for index, event in enumerate(page_obj.object_list, start=start_index + 1):
                rows.append(
                    {
                        'sl_no': index,
                        'timestamp': event.get('timestamp'),
                        'action': event.get('action'),
                        'module': event.get('module'),
                        'performed_by': event.get('performed_by'),
                    }
                )
        finally:
            connection.set_schema_to_public()

    return {
        'role_permission_total_changes': total_changes,
        'role_permission_active_roles': active_roles,
        'role_permission_permissions_updated': permissions_updated,
        'role_permission_roles_deleted': roles_deleted,
        'role_permission_rows': rows,
        'role_permission_page_obj': page_obj,
    }


def _build_critical_account_changes_context(request):
    auth_payload = get_tenant_portal_cookie_payload(request) or {}
    tenant_id = str(auth_payload.get('tenant_id') or '').strip()
    search_query = (request.GET.get('search') or '').strip()
    if not tenant_id:
        empty_page = Paginator([], 10).get_page(1)
        return {
            'critical_account_total_changes': 0,
            'critical_account_billing_updates': 0,
            'critical_account_security_updates': 0,
            'critical_account_critical_alerts': 0,
            'critical_account_rows': [],
            'critical_account_page_obj': empty_page,
            'critical_account_search_query': search_query,
        }

    tenant_actor_label = 'Tenant Admin'
    tenant_user_map = {}
    tenant_user_ids = []
    tenant_obj = TenantProfile.objects.filter(pk=tenant_id).first()
    if tenant_obj:
        tenant_actor_label = (
            tenant_obj.primary_email
            or tenant_obj.company_name
            or tenant_actor_label
        )
    session_data = get_tenant_session(
        tenant_id,
        str(auth_payload.get('jti') or '').strip(),
    ) or {}
    reference_id = str(session_data.get('reference_id') or '').strip()
    tenant_registry = _activate_tenant_workspace_schema(request)
    if tenant_registry is not None:
        try:
            for user in TenantUser.objects.values('user_id', 'full_name', 'email'):
                user_id = str(user.get('user_id') or '').strip()
                if not user_id:
                    continue
                tenant_user_ids.append(user_id)
                tenant_user_map[user_id] = (
                    (user.get('full_name') or '').strip()
                    or (user.get('email') or '').strip()
                )

            if reference_id and reference_id != tenant_id:
                tenant_user = TenantUser.objects.filter(pk=reference_id).first()
                if tenant_user:
                    tenant_actor_label = (
                        tenant_user.full_name
                        or tenant_user.email
                        or tenant_actor_label
                    )
        finally:
            connection.set_schema_to_public()

    security_terms = (
        Q(module_name__icontains='security')
        | Q(module_name__icontains='auth')
        | Q(module_name__icontains='session')
        | Q(module_name__icontains='login')
    )
    billing_terms = (
        Q(module_name__icontains='billing')
        | Q(module_name__icontains='invoice')
        | Q(module_name__icontains='payment')
        | Q(module_name__icontains='subscription')
    )
    module_scope_q = Q(
        Q(module_name__icontains='security')
        | Q(module_name__icontains='auth')
        | Q(module_name__icontains='session')
        | Q(module_name__icontains='login')
        | Q(module_name__icontains='billing')
        | Q(module_name__icontains='invoice')
        | Q(module_name__icontains='payment')
        | Q(module_name__icontains='subscription')
        | Q(module_name__icontains='tenant')
        | Q(module_name__icontains='crm')
        | Q(module_name__icontains='note')
    )
    tenant_scope_q = (
        Q(record_id=tenant_id)
        | Q(old_payload__contains={'tenant_id': tenant_id})
        | Q(new_payload__contains={'tenant_id': tenant_id})
        | Q(old_payload__contains={'tenant_profile': tenant_id})
        | Q(new_payload__contains={'tenant_profile': tenant_id})
        | Q(old_payload__contains={'tenant': tenant_id})
        | Q(new_payload__contains={'tenant': tenant_id})
    )
    if tenant_user_ids:
        tenant_scope_q |= Q(record_id__in=tenant_user_ids)

    critical_qs = AuditLog.objects.filter(module_scope_q & tenant_scope_q).select_related('admin')
    if search_query:
        critical_qs = critical_qs.filter(
            Q(action_type__icontains=search_query)
            | Q(module_name__icontains=search_query)
            | Q(record_id__icontains=search_query)
            | Q(admin__email__icontains=search_query)
            | Q(admin__first_name__icontains=search_query)
            | Q(admin__last_name__icontains=search_query)
        )
    critical_qs = critical_qs.order_by('-timestamp')
    paginator = Paginator(critical_qs, 10)
    page_obj = paginator.get_page(request.GET.get('page'))
    logs = list(page_obj.object_list)

    total_changes = critical_qs.count()
    billing_updates = critical_qs.filter(billing_terms).count()
    security_updates = critical_qs.filter(security_terms).count()
    critical_alerts = critical_qs.filter(
        Q(action_type='Delete') | Q(action_type='Status_Change')
    ).count()

    rows = []
    start_index = (page_obj.number - 1) * paginator.per_page
    for index, log in enumerate(logs, start=start_index + 1):
        module_name = (log.module_name or '').strip() or 'System'
        module_lower = module_name.lower()
        if any(key in module_lower for key in ('billing', 'invoice', 'payment', 'subscription')):
            normalized_module = 'Billing Settings'
        elif any(key in module_lower for key in ('security', 'auth', 'session', 'login')):
            normalized_module = 'Security Settings'
        else:
            normalized_module = 'Tenant Config'
        record_id = str(log.record_id or '').strip()
        performed_by_user = tenant_user_map.get(record_id, '')
        performed_by_admin = ''
        if log.admin_id and log.admin:
            performed_by_admin = (
                f'{(log.admin.first_name or "").strip()} {(log.admin.last_name or "").strip()}'.strip()
                or (log.admin.email or '').strip()
            )
        performed_by = (
            performed_by_user
            or performed_by_admin
            or tenant_actor_label
            or 'Tenant Admin'
        )
        rows.append(
            {
                'sl_no': index,
                'timestamp': log.timestamp,
                'action': f'{log.action_type} ({module_name})',
                'module': normalized_module,
                'performed_by': performed_by,
            }
        )

    return {
        'critical_account_total_changes': total_changes,
        'critical_account_billing_updates': billing_updates,
        'critical_account_security_updates': security_updates,
        'critical_account_critical_alerts': critical_alerts,
        'critical_account_rows': rows,
        'critical_account_page_obj': page_obj,
        'critical_account_search_query': search_query,
    }


class TenantRolePermissionChangesView(View):
    """Tenant audit page: role/permission changes (template-only for now)."""

    def get(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        context.update(_build_role_permission_changes_context(request))
        return render(
            request,
            'iroad_tenants/Audit_log/Role--permission-changes.html',
            context,
        )


class TenantCriticalAccountChangesView(View):
    """Tenant audit page: critical account changes (template-only for now)."""

    def get(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        context.update(_build_critical_account_changes_context(request))
        return render(
            request,
            'iroad_tenants/Audit_log/Critical-account-changes.html',
            context,
        )


class TenantLoginSessionEventsView(View):
    """Tenant audit page: login/session events (template-only for now)."""

    def get(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        context.update(_build_login_session_events_context(request))
        return render(
            request,
            'iroad_tenants/Audit_log/Login--session-events.html',
            context,
        )


class TenantSimplePageView(View):
    """Render tenant templates via GET only."""

    template_name = ''

    def get(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        return render(request, self.template_name, context)


class TenantServiceItemMasterListView(TenantSimplePageView):
    """Render service item master list page."""

    template_name = 'iroad_tenants/Master_Data/service_item_master/All-service-item-masters.html'

    def get(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        if not context.get('is_tenant_admin'):
            messages.error(request, 'You do not have permission to view Services Management.', extra_tags='tenant')
            return _tenant_redirect(request, 'iroad_tenants:tenant_dashboard')

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            service_items = list(
                TenantServiceItemMaster.objects.order_by('-created_at')
            )
            service_count = sum(
                1
                for item in service_items
                if item.service_type == TenantServiceItemMaster.ServiceType.SERVICE
            )
            trip_count = sum(
                1
                for item in service_items
                if item.service_type == TenantServiceItemMaster.ServiceType.TRIP
            )
            active_count = sum(
                1
                for item in service_items
                if item.status == TenantServiceItemMaster.Status.ACTIVE
            )
            context.update(
                {
                    'service_items': service_items,
                    'service_item_stats': {
                        'total': len(service_items),
                        'service_type': service_count,
                        'trip_type': trip_count,
                        'active': active_count,
                    },
                    'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()


class TenantServiceItemMasterCreateView(TenantSimplePageView):
    """Create a new service item using the service-item form layout."""

    template_name = 'iroad_tenants/Master_Data/service_item_master/Service-item-master.html'

    def get(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        if not context.get('is_tenant_admin'):
            messages.error(request, 'You do not have permission to manage Services Management.', extra_tags='tenant')
            return _tenant_redirect(request, 'iroad_tenants:tenant_dashboard')

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            active_routes = list(
                TenantRouteMaster.objects.filter(
                    status=TenantRouteMaster.Status.ACTIVE,
                )
                .select_related('origin_point', 'destination_point')
                .order_by('route_code')
            )
            context.update(
                {
                    'preview_service_item_code': _preview_next_service_item_code(),
                    'route_options': active_routes,
                    'service_item_category_options': SERVICE_ITEM_CATEGORY_OPTIONS,
                    'form_action_url': reverse('iroad_tenants:tenant_service_item_master_create'),
                    'page_title': 'Create Service Item',
                    'submit_label': 'Save Service Item',
                    'is_view_mode': False,
                    'is_edit_mode': False,
                    'form_data': {},
                    'field_errors': {},
                    'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()

    def post(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        if not context.get('is_tenant_admin'):
            messages.error(request, 'You do not have permission to manage Services.', extra_tags='tenant')
            return _tenant_redirect(request, 'iroad_tenants:tenant_dashboard')

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            form_data = {
                'service_type': (request.POST.get('service_type') or '').strip(),
                'status': (request.POST.get('status') or '').strip() or TenantServiceItemMaster.Status.ACTIVE,
                'english_name': (request.POST.get('english_name') or '').strip(),
                'arabic_name': (request.POST.get('arabic_name') or '').strip(),
                'category_name': (request.POST.get('category_name') or '').strip(),
                'route_id': (request.POST.get('route_id') or '').strip(),
                'sell_price': (request.POST.get('sell_price') or '').strip(),
                'outbound_sell_price': (request.POST.get('outbound_sell_price') or '').strip(),
                'inbound_sell_price': (request.POST.get('inbound_sell_price') or '').strip(),
            }
            field_errors = {}

            if form_data['service_type'] not in {
                TenantServiceItemMaster.ServiceType.SERVICE,
                TenantServiceItemMaster.ServiceType.TRIP,
            }:
                field_errors['service_type'] = 'Please select a valid service type.'
            if form_data['status'] not in {
                TenantServiceItemMaster.Status.ACTIVE,
                TenantServiceItemMaster.Status.INACTIVE,
            }:
                field_errors['status'] = 'Please select a valid status.'
            if not form_data['english_name']:
                field_errors['english_name'] = 'English name is required.'
            if not form_data['category_name']:
                field_errors['category_name'] = 'Category is required.'
            if (
                form_data['category_name']
                and form_data['category_name'] not in SERVICE_ITEM_CATEGORY_OPTIONS
            ):
                field_errors['category_name'] = 'Please select a valid category.'

            def _parse_decimal(raw_value, field_name, required=True):
                value = (raw_value or '').strip()
                if not value:
                    if required:
                        field_errors[field_name] = 'This field is required.'
                    return None
                try:
                    dec_value = Decimal(value)
                except Exception:
                    field_errors[field_name] = 'Enter a valid number.'
                    return None
                if dec_value < 0:
                    field_errors[field_name] = 'Must be 0 or greater.'
                    return None
                return dec_value

            sell_price_value = _parse_decimal(form_data['sell_price'], 'sell_price', required=True)
            outbound_value = None
            inbound_value = None
            route_obj = None

            if form_data['service_type'] == TenantServiceItemMaster.ServiceType.TRIP:
                if not form_data['route_id']:
                    field_errors['route_id'] = 'Route is required for Trip.'
                else:
                    route_obj = (
                        TenantRouteMaster.objects.filter(
                            route_id=form_data['route_id'],
                            status=TenantRouteMaster.Status.ACTIVE,
                        )
                        .select_related('origin_point', 'destination_point')
                        .first()
                    )
                    if route_obj is None:
                        field_errors['route_id'] = 'Please select an active route.'
                outbound_value = _parse_decimal(
                    form_data['outbound_sell_price'],
                    'outbound_sell_price',
                    required=True,
                )
                inbound_value = _parse_decimal(
                    form_data['inbound_sell_price'],
                    'inbound_sell_price',
                    required=True,
                )

            if field_errors:
                context.update(
                    {
                        'preview_service_item_code': _preview_next_service_item_code(),
                        'route_options': list(
                            TenantRouteMaster.objects.filter(
                                status=TenantRouteMaster.Status.ACTIVE,
                            )
                            .select_related('origin_point', 'destination_point')
                            .order_by('route_code')
                        ),
                        'service_item_category_options': SERVICE_ITEM_CATEGORY_OPTIONS,
                        'form_action_url': reverse('iroad_tenants:tenant_service_item_master_create'),
                        'page_title': 'Create Service Item',
                        'submit_label': 'Save Service Item',
                        'is_view_mode': False,
                        'is_edit_mode': False,
                        'form_data': form_data,
                        'field_errors': field_errors,
                        'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                    }
                )
                messages.error(request, 'Please fix the highlighted errors.', extra_tags='tenant')
                return render(request, self.template_name, context)

            service_code, service_seq = _next_auto_number_for_form(
                SERVICE_ITEM_MASTER_AUTO_FORM_CODE,
                SERVICE_ITEM_MASTER_AUTO_FORM_LABEL,
                SERVICE_ITEM_MASTER_REF_PREFIX,
            )
            item = TenantServiceItemMaster(
                service_code=service_code,
                service_sequence=service_seq,
                service_type=form_data['service_type'],
                status=form_data['status'],
                english_name=form_data['english_name'],
                arabic_name=form_data['arabic_name'],
                category_name=form_data['category_name'],
                route=route_obj,
                sell_price=sell_price_value or Decimal('0'),
                outbound_sell_price=outbound_value,
                inbound_sell_price=inbound_value,
            )
            item.full_clean()
            item.save()
            messages.success(request, f'{item.service_code} created successfully.', extra_tags='tenant')
            return _tenant_redirect(request, 'iroad_tenants:tenant_service_item_master_list')
        except ValidationError as exc:
            if hasattr(exc, 'message_dict'):
                mapped = {
                    'route': 'route_id',
                }
                for field, msgs in exc.message_dict.items():
                    key = mapped.get(field, field)
                    if msgs:
                        field_errors[key] = str(msgs[0])
            else:
                field_errors['non_field_errors'] = '; '.join(exc.messages) if exc.messages else str(exc)
            context.update(
                {
                    'preview_service_item_code': _preview_next_service_item_code(),
                    'route_options': list(
                        TenantRouteMaster.objects.filter(
                            status=TenantRouteMaster.Status.ACTIVE,
                        )
                        .select_related('origin_point', 'destination_point')
                        .order_by('route_code')
                    ),
                    'service_item_category_options': SERVICE_ITEM_CATEGORY_OPTIONS,
                    'form_action_url': reverse('iroad_tenants:tenant_service_item_master_create'),
                    'page_title': 'Create Service Item',
                    'submit_label': 'Save Service Item',
                    'is_view_mode': False,
                    'is_edit_mode': False,
                    'form_data': form_data,
                    'field_errors': field_errors,
                    'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                }
            )
            messages.error(request, 'Please fix the highlighted errors.', extra_tags='tenant')
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()


class TenantServiceItemMasterEditView(TenantSimplePageView):
    """Edit existing service item using the create-page layout."""

    template_name = 'iroad_tenants/Master_Data/service_item_master/Service-item-master.html'

    def get(self, request, service_item_id):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        if not context.get('is_tenant_admin'):
            messages.error(request, 'You do not have permission to manage Services Management.', extra_tags='tenant')
            return _tenant_redirect(request, 'iroad_tenants:tenant_dashboard')

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            item = TenantServiceItemMaster.objects.filter(service_item_id=service_item_id).first()
            if item is None:
                messages.error(request, 'Service item not found.', extra_tags='tenant')
                return _tenant_redirect(request, 'iroad_tenants:tenant_service_item_master_list')
            active_routes = list(
                TenantRouteMaster.objects.filter(
                    status=TenantRouteMaster.Status.ACTIVE,
                )
                .select_related('origin_point', 'destination_point')
                .order_by('route_code')
            )
            form_data = {
                'service_type': item.service_type,
                'status': item.status,
                'english_name': item.english_name,
                'arabic_name': item.arabic_name,
                'category_name': item.category_name,
                'route_id': str(item.route_id or ''),
                'sell_price': str(item.sell_price),
                'outbound_sell_price': '' if item.outbound_sell_price is None else str(item.outbound_sell_price),
                'inbound_sell_price': '' if item.inbound_sell_price is None else str(item.inbound_sell_price),
            }
            context.update(
                {
                    'preview_service_item_code': item.service_code,
                    'route_options': active_routes,
                    'service_item_category_options': SERVICE_ITEM_CATEGORY_OPTIONS,
                    'form_action_url': reverse('iroad_tenants:tenant_service_item_master_edit', kwargs={'service_item_id': item.service_item_id}),
                    'page_title': 'Edit Service Item',
                    'submit_label': 'Update Service Item',
                    'is_view_mode': False,
                    'is_edit_mode': True,
                    'form_data': form_data,
                    'field_errors': {},
                    'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()

    def post(self, request, service_item_id):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        if not context.get('is_tenant_admin'):
            messages.error(request, 'You do not have permission to manage Services.', extra_tags='tenant')
            return _tenant_redirect(request, 'iroad_tenants:tenant_dashboard')

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            item = TenantServiceItemMaster.objects.filter(service_item_id=service_item_id).first()
            if item is None:
                messages.error(request, 'Service item not found.', extra_tags='tenant')
                return _tenant_redirect(request, 'iroad_tenants:tenant_service_item_master_list')

            form_data = {
                'service_type': item.service_type,
                'status': (request.POST.get('status') or '').strip() or item.status,
                'english_name': (request.POST.get('english_name') or '').strip(),
                'arabic_name': (request.POST.get('arabic_name') or '').strip(),
                'category_name': (request.POST.get('category_name') or '').strip(),
                'route_id': (request.POST.get('route_id') or '').strip(),
                'sell_price': (request.POST.get('sell_price') or '').strip(),
                'outbound_sell_price': (request.POST.get('outbound_sell_price') or '').strip(),
                'inbound_sell_price': (request.POST.get('inbound_sell_price') or '').strip(),
            }
            field_errors = {}

            if form_data['status'] not in {
                TenantServiceItemMaster.Status.ACTIVE,
                TenantServiceItemMaster.Status.INACTIVE,
            }:
                field_errors['status'] = 'Please select a valid status.'
            if not form_data['english_name']:
                field_errors['english_name'] = 'English name is required.'
            if not form_data['category_name']:
                field_errors['category_name'] = 'Category is required.'
            if form_data['category_name'] and form_data['category_name'] not in SERVICE_ITEM_CATEGORY_OPTIONS:
                field_errors['category_name'] = 'Please select a valid category.'

            def _parse_decimal(raw_value, field_name, required=True):
                value = (raw_value or '').strip()
                if not value:
                    if required:
                        field_errors[field_name] = 'This field is required.'
                    return None
                try:
                    dec_value = Decimal(value)
                except Exception:
                    field_errors[field_name] = 'Enter a valid number.'
                    return None
                if dec_value < 0:
                    field_errors[field_name] = 'Must be 0 or greater.'
                    return None
                return dec_value

            sell_price_value = _parse_decimal(form_data['sell_price'], 'sell_price', required=True)
            outbound_value = None
            inbound_value = None
            route_obj = None
            if form_data['service_type'] == TenantServiceItemMaster.ServiceType.TRIP:
                if not form_data['route_id']:
                    field_errors['route_id'] = 'Route is required for Trip.'
                else:
                    route_obj = (
                        TenantRouteMaster.objects.filter(
                            route_id=form_data['route_id'],
                            status=TenantRouteMaster.Status.ACTIVE,
                        )
                        .select_related('origin_point', 'destination_point')
                        .first()
                    )
                    if route_obj is None:
                        field_errors['route_id'] = 'Please select an active route.'
                outbound_value = _parse_decimal(form_data['outbound_sell_price'], 'outbound_sell_price', required=True)
                inbound_value = _parse_decimal(form_data['inbound_sell_price'], 'inbound_sell_price', required=True)

            if field_errors:
                context.update(
                    {
                        'preview_service_item_code': item.service_code,
                        'route_options': list(
                            TenantRouteMaster.objects.filter(
                                status=TenantRouteMaster.Status.ACTIVE,
                            )
                            .select_related('origin_point', 'destination_point')
                            .order_by('route_code')
                        ),
                        'service_item_category_options': SERVICE_ITEM_CATEGORY_OPTIONS,
                        'form_action_url': reverse('iroad_tenants:tenant_service_item_master_edit', kwargs={'service_item_id': item.service_item_id}),
                        'page_title': 'Edit Service Item',
                        'submit_label': 'Update Service Item',
                        'is_view_mode': False,
                        'is_edit_mode': True,
                        'form_data': form_data,
                        'field_errors': field_errors,
                        'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                    }
                )
                messages.error(request, 'Please fix the highlighted errors.', extra_tags='tenant')
                return render(request, self.template_name, context)

            item.status = form_data['status']
            item.english_name = form_data['english_name']
            item.arabic_name = form_data['arabic_name']
            item.category_name = form_data['category_name']
            item.route = route_obj
            item.sell_price = sell_price_value or Decimal('0')
            item.outbound_sell_price = outbound_value
            item.inbound_sell_price = inbound_value
            item.full_clean()
            item.save()
            messages.success(request, f'{item.service_code} updated successfully.', extra_tags='tenant')
            return _tenant_redirect(request, 'iroad_tenants:tenant_service_item_master_list')
        except ValidationError as exc:
            if hasattr(exc, 'message_dict'):
                mapped = {'route': 'route_id'}
                for field, msgs in exc.message_dict.items():
                    key = mapped.get(field, field)
                    if msgs:
                        field_errors[key] = str(msgs[0])
            else:
                field_errors['non_field_errors'] = '; '.join(exc.messages) if exc.messages else str(exc)
            context.update(
                {
                    'preview_service_item_code': item.service_code if 'item' in locals() and item else '',
                    'route_options': list(
                        TenantRouteMaster.objects.filter(
                            status=TenantRouteMaster.Status.ACTIVE,
                        )
                        .select_related('origin_point', 'destination_point')
                        .order_by('route_code')
                    ),
                    'service_item_category_options': SERVICE_ITEM_CATEGORY_OPTIONS,
                    'form_action_url': reverse('iroad_tenants:tenant_service_item_master_edit', kwargs={'service_item_id': service_item_id}),
                    'page_title': 'Edit Service Item',
                    'submit_label': 'Update Service Item',
                    'is_view_mode': False,
                    'is_edit_mode': True,
                    'form_data': form_data if 'form_data' in locals() else {},
                    'field_errors': field_errors if 'field_errors' in locals() else {},
                    'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                }
            )
            messages.error(request, 'Please fix the highlighted errors.', extra_tags='tenant')
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()


class TenantServiceItemMasterDetailView(TenantSimplePageView):
    """View-only service item detail using the create-page layout."""

    template_name = 'iroad_tenants/Master_Data/service_item_master/Service-item-master.html'

    def get(self, request, service_item_id):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        if not context.get('is_tenant_admin'):
            messages.error(request, 'You do not have permission to view Services Management.', extra_tags='tenant')
            return _tenant_redirect(request, 'iroad_tenants:tenant_dashboard')
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            item = TenantServiceItemMaster.objects.filter(service_item_id=service_item_id).first()
            if item is None:
                messages.error(request, 'Service item not found.', extra_tags='tenant')
                return _tenant_redirect(request, 'iroad_tenants:tenant_service_item_master_list')
            context.update(
                {
                    'preview_service_item_code': item.service_code,
                    'route_options': list(
                        TenantRouteMaster.objects.filter(
                            status=TenantRouteMaster.Status.ACTIVE,
                        )
                        .select_related('origin_point', 'destination_point')
                        .order_by('route_code')
                    ),
                    'service_item_category_options': SERVICE_ITEM_CATEGORY_OPTIONS,
                    'form_action_url': '#',
                    'page_title': 'View Service Item',
                    'submit_label': '',
                    'is_view_mode': True,
                    'is_edit_mode': False,
                    'form_data': {
                        'service_type': item.service_type,
                        'status': item.status,
                        'english_name': item.english_name,
                        'arabic_name': item.arabic_name,
                        'category_name': item.category_name,
                        'route_id': str(item.route_id or ''),
                        'sell_price': str(item.sell_price),
                        'outbound_sell_price': '' if item.outbound_sell_price is None else str(item.outbound_sell_price),
                        'inbound_sell_price': '' if item.inbound_sell_price is None else str(item.inbound_sell_price),
                    },
                    'field_errors': {},
                    'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()


class TenantServiceItemMasterDeleteView(View):
    """Delete service item record."""

    def post(self, request, service_item_id):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        if not context.get('is_tenant_admin'):
            messages.error(request, 'You do not have permission to manage Services.', extra_tags='tenant')
            return _tenant_redirect(request, 'iroad_tenants:tenant_dashboard')
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            item = TenantServiceItemMaster.objects.filter(service_item_id=service_item_id).first()
            if item is None:
                messages.error(request, 'Service item not found.', extra_tags='tenant')
            else:
                code = item.service_code
                item.delete()
                messages.success(request, f'{code} deleted successfully.', extra_tags='tenant')
            return _tenant_redirect(request, 'iroad_tenants:tenant_service_item_master_list')
        except ProtectedError:
            messages.error(request, 'Cannot delete this service item because it is referenced by other records.', extra_tags='tenant')
            return _tenant_redirect(request, 'iroad_tenants:tenant_service_item_master_list')
        finally:
            connection.set_schema_to_public()


class TenantServiceItemSettingsView(TenantSimplePageView):
    """Render service item settings page."""

    template_name = 'iroad_tenants/Master_Data/service_item_master/Service-item-settings.html'


class TenantPriceListMasterListView(TenantSimplePageView):
    """Render price list master list page."""

    template_name = 'iroad_tenants/Master_Data/service_item_master/All-price-lists.html'

    def get(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        if not context.get('is_tenant_admin'):
            messages.error(request, 'You do not have permission to view Services Management.', extra_tags='tenant')
            return _tenant_redirect(request, 'iroad_tenants:tenant_dashboard')
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            today = timezone.localdate()
            expiring_threshold = today + timezone.timedelta(days=30)
            price_lists = list(
                TenantPriceList.objects.select_related('client_account')
                .prefetch_related('trip_lines', 'service_lines')
                .order_by('-created_at')
            )
            for item in price_lists:
                item.line_count = len(item.trip_lines.all()) + len(item.service_lines.all())
            context.update(
                {
                    'price_lists': price_lists,
                    'price_list_stats': {
                        'total': len(price_lists),
                        'active': sum(1 for item in price_lists if item.status == TenantPriceList.Status.ACTIVE),
                        'expiring_soon': sum(
                            1
                            for item in price_lists
                            if item.status == TenantPriceList.Status.ACTIVE
                            and item.effective_to
                            and today <= item.effective_to <= expiring_threshold
                        ),
                        'inactive': sum(1 for item in price_lists if item.status == TenantPriceList.Status.INACTIVE),
                    },
                    'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()


class TenantPriceListMasterCreateView(TenantSimplePageView):
    """Render price list master create page."""

    template_name = 'iroad_tenants/Master_Data/service_item_master/Price-list.html'

    def get(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        if not context.get('is_tenant_admin'):
            messages.error(request, 'You do not have permission to manage Services Management.', extra_tags='tenant')
            return _tenant_redirect(request, 'iroad_tenants:tenant_dashboard')
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            org = OrganizationProfile.objects.order_by('-updated_at').first()
            context.update(
                {
                    'preview_price_list_code': _preview_next_price_list_code(),
                    'active_clients': list(
                        TenantClientAccount.objects.filter(
                            status=TenantClientAccount.Status.ACTIVE,
                        ).order_by('account_no')
                    ),
                    'tenant_base_currency': (getattr(org, 'base_currency_code', '') or 'SAR'),
                    'active_trip_services': list(
                        TenantServiceItemMaster.objects.filter(
                            status=TenantServiceItemMaster.Status.ACTIVE,
                            service_type=TenantServiceItemMaster.ServiceType.TRIP,
                        ).order_by('service_code')
                    ),
                    'active_service_items': list(
                        TenantServiceItemMaster.objects.filter(
                            status=TenantServiceItemMaster.Status.ACTIVE,
                            service_type=TenantServiceItemMaster.ServiceType.SERVICE,
                        ).order_by('service_code')
                    ),
                    'form_data': {},
                    'field_errors': {},
                    'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()

    def post(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        if not context.get('is_tenant_admin'):
            messages.error(request, 'You do not have permission to manage Services Management.', extra_tags='tenant')
            return _tenant_redirect(request, 'iroad_tenants:tenant_dashboard')

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            form_data = {
                'price_list_name': (request.POST.get('price_list_name') or '').strip(),
                'client_account_id': (request.POST.get('client_account_id') or '').strip(),
                'status': (request.POST.get('status') or '').strip() or TenantPriceList.Status.DRAFT,
                'effective_from': (request.POST.get('effective_from') or '').strip(),
                'effective_to': (request.POST.get('effective_to') or '').strip(),
                'notes': (request.POST.get('notes') or '').strip(),
                'line_payload': (request.POST.get('line_payload') or '').strip(),
            }
            field_errors = {}
            if not form_data['price_list_name']:
                field_errors['price_list_name'] = 'Price list name is required.'
            if form_data['status'] not in {
                TenantPriceList.Status.DRAFT,
                TenantPriceList.Status.ACTIVE,
                TenantPriceList.Status.INACTIVE,
            }:
                field_errors['status'] = 'Please select a valid status.'

            client_obj = None
            if not form_data['client_account_id']:
                field_errors['client_account_id'] = 'Client account is required.'
            else:
                client_obj = TenantClientAccount.objects.filter(
                    account_id=form_data['client_account_id'],
                    status=TenantClientAccount.Status.ACTIVE,
                ).first()
                if client_obj is None:
                    field_errors['client_account_id'] = 'Please select an active client account.'

            effective_from = parse_date(form_data['effective_from']) if form_data['effective_from'] else None
            effective_to = parse_date(form_data['effective_to']) if form_data['effective_to'] else None
            if form_data['effective_from'] and effective_from is None:
                field_errors['effective_from'] = 'Enter a valid date.'
            if form_data['effective_to'] and effective_to is None:
                field_errors['effective_to'] = 'Enter a valid date.'
            if effective_from and effective_to and effective_from > effective_to:
                field_errors['effective_to'] = 'Effective To must be on or after Effective From.'

            org = OrganizationProfile.objects.order_by('-updated_at').first()
            tenant_base_currency = (getattr(org, 'base_currency_code', '') or 'SAR')

            if field_errors:
                context.update(
                    {
                        'preview_price_list_code': _preview_next_price_list_code(),
                        'active_clients': list(
                            TenantClientAccount.objects.filter(
                                status=TenantClientAccount.Status.ACTIVE,
                            ).order_by('account_no')
                        ),
                        'tenant_base_currency': tenant_base_currency,
                        'active_trip_services': list(
                            TenantServiceItemMaster.objects.filter(
                                status=TenantServiceItemMaster.Status.ACTIVE,
                                service_type=TenantServiceItemMaster.ServiceType.TRIP,
                            ).order_by('service_code')
                        ),
                        'active_service_items': list(
                            TenantServiceItemMaster.objects.filter(
                                status=TenantServiceItemMaster.Status.ACTIVE,
                                service_type=TenantServiceItemMaster.ServiceType.SERVICE,
                            ).order_by('service_code')
                        ),
                        'form_data': form_data,
                        'field_errors': field_errors,
                        'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                    }
                )
                messages.error(request, 'Please fix the highlighted errors.', extra_tags='tenant')
                return render(request, self.template_name, context)

            with db_transaction.atomic():
                parsed_lines = []
                if form_data['line_payload']:
                    try:
                        parsed_lines = json.loads(form_data['line_payload'])
                        if not isinstance(parsed_lines, list):
                            raise ValueError('Invalid line payload format.')
                    except Exception:
                        raise ValidationError({'line_payload': ['Invalid line data submitted.']})
                if not parsed_lines:
                    raise ValidationError({'line_payload': ['At least one price list line is required.']})

                price_list_code, price_list_sequence = _next_auto_number_for_form(
                    PRICE_LIST_MASTER_AUTO_FORM_CODE,
                    PRICE_LIST_MASTER_AUTO_FORM_LABEL,
                    PRICE_LIST_MASTER_REF_PREFIX,
                )
                price_list = TenantPriceList(
                    price_list_code=price_list_code,
                    price_list_sequence=price_list_sequence,
                    price_list_name=form_data['price_list_name'],
                    client_account=client_obj,
                    status=form_data['status'],
                    effective_from=effective_from,
                    effective_to=effective_to,
                    base_currency=tenant_base_currency,
                    notes=form_data['notes'],
                )
                price_list.full_clean()
                price_list.save()

                for idx, line in enumerate(parsed_lines, start=1):
                    line_type = (line.get('line_type') or '').strip()
                    notes = (line.get('notes') or '').strip()
                    if line_type == 'Trip':
                        trip_service_id = (line.get('service_item_id') or '').strip()
                        outbound_raw = str(line.get('outbound_sell_price') or '').strip()
                        inbound_raw = str(line.get('inbound_sell_price') or '').strip()
                        if not trip_service_id:
                            raise ValidationError({'line_payload': [f'Trip line {idx}: Trip service is required.']})
                        trip_service = TenantServiceItemMaster.objects.filter(
                            service_item_id=trip_service_id,
                            status=TenantServiceItemMaster.Status.ACTIVE,
                            service_type=TenantServiceItemMaster.ServiceType.TRIP,
                        ).first()
                        if trip_service is None:
                            raise ValidationError({'line_payload': [f'Trip line {idx}: Select an active Trip service item.']})
                        if outbound_raw == '' or inbound_raw == '':
                            raise ValidationError({'line_payload': [f'Trip line {idx}: Outbound and inbound sell prices are required.']})
                        try:
                            outbound_sell = Decimal(outbound_raw)
                            inbound_sell = Decimal(inbound_raw)
                        except Exception:
                            raise ValidationError({'line_payload': [f'Trip line {idx}: Enter valid numeric prices.']})
                        if outbound_sell < 0 or inbound_sell < 0:
                            raise ValidationError({'line_payload': [f'Trip line {idx}: Prices must be 0 or greater.']})

                        one_way = TenantPriceListTripLine(
                            price_list=price_list,
                            trip_service=trip_service,
                            trip_type=TenantPriceListTripLine.TripType.ONE_WAY,
                            sell_price_override=outbound_sell,
                            buy_price_override=outbound_sell,
                            notes=notes,
                        )
                        # TODO SS-001: enforce min margin / override reason policy here.
                        one_way.full_clean()
                        one_way.save()

                        round_line = TenantPriceListTripLine(
                            price_list=price_list,
                            trip_service=trip_service,
                            trip_type=TenantPriceListTripLine.TripType.ROUND,
                            sell_price_override=inbound_sell,
                            buy_price_override=inbound_sell,
                            notes=notes,
                        )
                        # TODO SS-001: enforce min margin / override reason policy here.
                        round_line.full_clean()
                        round_line.save()
                    elif line_type == 'Service':
                        service_item_id = (line.get('service_item_id') or '').strip()
                        sell_raw = str(line.get('sell_price') or '').strip()
                        if not service_item_id:
                            raise ValidationError({'line_payload': [f'Service line {idx}: Service item is required.']})
                        service_item = TenantServiceItemMaster.objects.filter(
                            service_item_id=service_item_id,
                            status=TenantServiceItemMaster.Status.ACTIVE,
                            service_type=TenantServiceItemMaster.ServiceType.SERVICE,
                        ).first()
                        if service_item is None:
                            raise ValidationError({'line_payload': [f'Service line {idx}: Select an active Service item.']})
                        if sell_raw == '':
                            raise ValidationError({'line_payload': [f'Service line {idx}: Sell price is required.']})
                        try:
                            sell_price = Decimal(sell_raw)
                        except Exception:
                            raise ValidationError({'line_payload': [f'Service line {idx}: Enter a valid sell price.']})
                        if sell_price < 0:
                            raise ValidationError({'line_payload': [f'Service line {idx}: Sell price must be 0 or greater.']})

                        service_line = TenantPriceListServiceLine(
                            price_list=price_list,
                            service_item=service_item,
                            sell_price_override=sell_price,
                            buy_price_override=sell_price,
                            notes=notes,
                        )
                        # TODO SS-001: enforce min margin / override reason policy here.
                        service_line.full_clean()
                        service_line.save()
                    else:
                        raise ValidationError({'line_payload': [f'Line {idx}: Invalid service type.']})

            messages.success(request, f'{price_list.price_list_code} created successfully.', extra_tags='tenant')
            return _tenant_redirect(request, 'iroad_tenants:tenant_price_list_master_list')
        except ValidationError as exc:
            if hasattr(exc, 'message_dict'):
                for field, msgs in exc.message_dict.items():
                    if msgs:
                        field_errors[field] = str(msgs[0])
            else:
                field_errors['non_field_errors'] = '; '.join(exc.messages) if exc.messages else str(exc)
            context.update(
                {
                    'preview_price_list_code': _preview_next_price_list_code(),
                    'active_clients': list(
                        TenantClientAccount.objects.filter(
                            status=TenantClientAccount.Status.ACTIVE,
                        ).order_by('account_no')
                    ),
                    'tenant_base_currency': (getattr(org, 'base_currency_code', '') or 'SAR') if 'org' in locals() else 'SAR',
                    'active_trip_services': list(
                        TenantServiceItemMaster.objects.filter(
                            status=TenantServiceItemMaster.Status.ACTIVE,
                            service_type=TenantServiceItemMaster.ServiceType.TRIP,
                        ).order_by('service_code')
                    ),
                    'active_service_items': list(
                        TenantServiceItemMaster.objects.filter(
                            status=TenantServiceItemMaster.Status.ACTIVE,
                            service_type=TenantServiceItemMaster.ServiceType.SERVICE,
                        ).order_by('service_code')
                    ),
                    'form_data': form_data if 'form_data' in locals() else {},
                    'field_errors': field_errors,
                    'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                }
            )
            messages.error(request, 'Please fix the highlighted errors.', extra_tags='tenant')
            return render(request, self.template_name, context)
        except IntegrityError:
            context.update(
                {
                    'preview_price_list_code': _preview_next_price_list_code(),
                    'active_clients': list(
                        TenantClientAccount.objects.filter(
                            status=TenantClientAccount.Status.ACTIVE,
                        ).order_by('account_no')
                    ),
                    'tenant_base_currency': (getattr(org, 'base_currency_code', '') or 'SAR') if 'org' in locals() else 'SAR',
                    'active_trip_services': list(
                        TenantServiceItemMaster.objects.filter(
                            status=TenantServiceItemMaster.Status.ACTIVE,
                            service_type=TenantServiceItemMaster.ServiceType.TRIP,
                        ).order_by('service_code')
                    ),
                    'active_service_items': list(
                        TenantServiceItemMaster.objects.filter(
                            status=TenantServiceItemMaster.Status.ACTIVE,
                            service_type=TenantServiceItemMaster.ServiceType.SERVICE,
                        ).order_by('service_code')
                    ),
                    'form_data': form_data if 'form_data' in locals() else {},
                    'field_errors': field_errors if 'field_errors' in locals() else {},
                    'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                }
            )
            messages.error(
                request,
                'Unable to save due to duplicate/constraint rules (for example: only one Active price list per client, or duplicate line combination).',
                extra_tags='tenant',
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()


class TenantPriceListMasterDetailView(TenantSimplePageView):
    """Read-only price list detail page."""

    template_name = 'iroad_tenants/Master_Data/service_item_master/Price-list.html'

    def get(self, request, price_list_id):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        if not context.get('is_tenant_admin'):
            messages.error(request, 'You do not have permission to view Services Management.', extra_tags='tenant')
            return _tenant_redirect(request, 'iroad_tenants:tenant_dashboard')
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            price_list = TenantPriceList.objects.select_related('client_account').filter(price_list_id=price_list_id).first()
            if price_list is None:
                messages.error(request, 'Price list not found.', extra_tags='tenant')
                return _tenant_redirect(request, 'iroad_tenants:tenant_price_list_master_list')
            context.update(
                {
                    'preview_price_list_code': price_list.price_list_code,
                    'active_clients': [],
                    'tenant_base_currency': price_list.base_currency or 'SAR',
                    'active_trip_services': [],
                    'active_service_items': [],
                    'form_data': {
                        'price_list_name': price_list.price_list_name,
                        'client_account_id': str(price_list.client_account_id or ''),
                        'status': price_list.status,
                        'effective_from': str(price_list.effective_from or ''),
                        'effective_to': str(price_list.effective_to or ''),
                        'notes': price_list.notes or '',
                    },
                    'field_errors': {},
                    'is_view_mode': True,
                    'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()


class TenantPriceListMasterEditView(TenantSimplePageView):
    """Edit price list header values."""

    template_name = 'iroad_tenants/Master_Data/service_item_master/Price-list.html'

    def get(self, request, price_list_id):
        return TenantPriceListMasterDetailView().get(request, price_list_id)

    def post(self, request, price_list_id):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        if not context.get('is_tenant_admin'):
            messages.error(request, 'You do not have permission to manage Services Management.', extra_tags='tenant')
            return _tenant_redirect(request, 'iroad_tenants:tenant_dashboard')
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            price_list = TenantPriceList.objects.filter(price_list_id=price_list_id).first()
            if price_list is None:
                messages.error(request, 'Price list not found.', extra_tags='tenant')
                return _tenant_redirect(request, 'iroad_tenants:tenant_price_list_master_list')
            status = (request.POST.get('status') or '').strip()
            if status in {TenantPriceList.Status.DRAFT, TenantPriceList.Status.ACTIVE, TenantPriceList.Status.INACTIVE}:
                price_list.status = status
            price_list.notes = (request.POST.get('notes') or '').strip()
            price_list.save(update_fields=['status', 'notes', 'updated_at'])
            messages.success(request, f'{price_list.price_list_code} updated successfully.', extra_tags='tenant')
            return _tenant_redirect(request, 'iroad_tenants:tenant_price_list_master_list')
        finally:
            connection.set_schema_to_public()


class TenantPriceListMasterDeleteView(View):
    """Soft delete by setting price list inactive."""

    def post(self, request, price_list_id):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        if not context.get('is_tenant_admin'):
            messages.error(request, 'You do not have permission to manage Services Management.', extra_tags='tenant')
            return _tenant_redirect(request, 'iroad_tenants:tenant_dashboard')
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            price_list = TenantPriceList.objects.filter(price_list_id=price_list_id).first()
            if price_list is None:
                messages.error(request, 'Price list not found.', extra_tags='tenant')
            else:
                price_list.status = TenantPriceList.Status.INACTIVE
                price_list.save(update_fields=['status', 'updated_at'])
                messages.success(request, f'{price_list.price_list_code} set to inactive.', extra_tags='tenant')
            return _tenant_redirect(request, 'iroad_tenants:tenant_price_list_master_list')
        finally:
            connection.set_schema_to_public()


class TenantSalesInvoiceReportListView(View):
    template_name = 'iroad_tenants/finance/sales_invoice_report/sales_invoice_report_list.html'

    def get(self, request):
        context = _tenant_context_from_session(request)
        denied = _tenant_sales_invoice_report_access(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            qs = SalesInvoiceReport.objects.select_related('client').all()
            search_q = (request.GET.get('q') or '').strip()
            status_filter = (request.GET.get('status') or '').strip()
            client_filter = (request.GET.get('client') or '').strip()
            date_from = parse_date((request.GET.get('date_from') or '').strip())
            date_to = parse_date((request.GET.get('date_to') or '').strip())

            if search_q:
                qs = qs.filter(
                    Q(report_no__icontains=search_q)
                    | Q(client__display_name__icontains=search_q)
                    | Q(client__account_no__icontains=search_q)
                    | Q(currency__icontains=search_q)
                )
            if status_filter in {
                SalesInvoiceReport.Status.DRAFT,
                SalesInvoiceReport.Status.VERIFIED,
                SalesInvoiceReport.Status.CONVERTED,
            }:
                qs = qs.filter(status=status_filter)
            if client_filter:
                try:
                    qs = qs.filter(client_id=uuid.UUID(client_filter))
                except ValueError:
                    pass
            if date_from:
                qs = qs.filter(report_date__gte=date_from)
            if date_to:
                qs = qs.filter(report_date__lte=date_to)

            qs = qs.order_by('-report_date', '-created_at')
            paginator = Paginator(qs, 10)
            try:
                page_no = max(1, int(request.GET.get('page') or 1))
            except ValueError:
                page_no = 1
            page = paginator.get_page(page_no)

            total_count = SalesInvoiceReport.objects.count()
            draft_count = SalesInvoiceReport.objects.filter(
                status=SalesInvoiceReport.Status.DRAFT
            ).count()
            verified_count = SalesInvoiceReport.objects.filter(
                status=SalesInvoiceReport.Status.VERIFIED
            ).count()
            converted_count = SalesInvoiceReport.objects.filter(
                status=SalesInvoiceReport.Status.CONVERTED
            ).count()
            total_freight = (
                SalesInvoiceReport.objects.aggregate(v=Sum('total_freight_amount')).get('v')
                or Decimal('0')
            )
            total_surcharge = (
                SalesInvoiceReport.objects.aggregate(v=Sum('total_surcharge_amount')).get('v')
                or Decimal('0')
            )

            context.update(
                {
                    'reports_page': page,
                    'search_q': search_q,
                    'status_filter': status_filter,
                    'client_filter': client_filter,
                    'date_from': request.GET.get('date_from') or '',
                    'date_to': request.GET.get('date_to') or '',
                    'client_choices': TenantClientAccount.objects.filter(
                        status=TenantClientAccount.Status.ACTIVE
                    ).order_by('display_name'),
                    'stats': {
                        'total_count': total_count,
                        'draft_count': draft_count,
                        'verified_count': verified_count,
                        'converted_count': converted_count,
                        'total_freight': total_freight,
                        'total_surcharge': total_surcharge,
                    },
                    'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()


class TenantSalesInvoiceReportCreateView(View):
    template_name = 'iroad_tenants/finance/sales_invoice_report/sales_invoice_report_form.html'

    def get(self, request):
        context = _tenant_context_from_session(request)
        denied = _tenant_sales_invoice_report_access(request, context)
        if denied:
            return denied
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            if (request.GET.get('action') or '').strip() == 'preview':
                client_id = (request.GET.get('client') or '').strip()
                date_from = parse_date((request.GET.get('booking_date_from') or '').strip())
                date_to = parse_date((request.GET.get('booking_date_to') or '').strip())
                currency = (request.GET.get('currency') or '').strip()
                try:
                    payload = _sales_invoice_report_preview_payload(
                        client_id=client_id,
                        booking_date_from=date_from,
                        booking_date_to=date_to,
                        currency=currency,
                    )
                except ValidationError as exc:
                    return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
                return JsonResponse(payload)

            bundle = _sales_invoice_report_forms_with_preview()
            context.update(
                {
                    **bundle,
                    'is_edit': False,
                    'page_title': 'Create Sales Invoice Report',
                    'lock_state': 'editable',
                    'booking_lines': [],
                    'surcharge_lines': [],
                    'shipment_lines': [],
                    'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()

    def post(self, request):
        context = _tenant_context_from_session(request)
        denied = _tenant_sales_invoice_report_access(request, context)
        if denied:
            return denied
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            bundle = _sales_invoice_report_forms_with_preview(data=request.POST)
            form = bundle['report_form']
            if not form.is_valid():
                context.update(
                    {
                        **bundle,
                        'is_edit': False,
                        'page_title': 'Create Sales Invoice Report',
                        'lock_state': 'editable',
                        'booking_lines': [],
                        'surcharge_lines': [],
                        'shipment_lines': [],
                        'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                    }
                )
                messages.error(request, 'Please fix the highlighted errors.', extra_tags='tenant')
                return render(request, self.template_name, context)

            obj = _create_sales_invoice_report_with_auto_number(
                form=form,
                created_by=context.get('display_name') or '',
            )
            messages.success(
                request,
                f'Sales Invoice Report {obj.report_no} created successfully.',
                extra_tags='tenant',
            )
            return _tenant_redirect(
                request,
                'iroad_tenants:sales_invoice_report_detail',
                report_id=obj.report_id,
            )
        finally:
            connection.set_schema_to_public()


class TenantSalesInvoiceReportDetailView(View):
    template_name = 'iroad_tenants/finance/sales_invoice_report/sales_invoice_report_detail.html'

    def get(self, request, report_id):
        context = _tenant_context_from_session(request)
        denied = _tenant_sales_invoice_report_access(request, context)
        if denied:
            return denied
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            report = (
                SalesInvoiceReport.objects.select_related('client')
                .filter(pk=report_id)
                .first()
            )
            if not report:
                messages.error(request, 'Sales Invoice Report not found.', extra_tags='tenant')
                return _tenant_redirect(request, 'iroad_tenants:sales_invoice_report_list')

            status_timeline = [
                {
                    'label': 'Draft',
                    'active': report.status in {
                        SalesInvoiceReport.Status.DRAFT,
                        SalesInvoiceReport.Status.VERIFIED,
                        SalesInvoiceReport.Status.CONVERTED,
                    },
                },
                {
                    'label': 'Verified',
                    'active': report.status in {
                        SalesInvoiceReport.Status.VERIFIED,
                        SalesInvoiceReport.Status.CONVERTED,
                    },
                },
                {
                    'label': 'Converted',
                    'active': report.status == SalesInvoiceReport.Status.CONVERTED,
                },
            ]

            context.update(
                {
                    'report': report,
                    'booking_lines': report.booking_lines.all().order_by('line_no'),
                    'surcharge_lines': report.surcharge_lines.all().order_by('line_no'),
                    'shipment_lines': report.shipment_lines.all().order_by('line_no'),
                    'status_timeline': status_timeline,
                    'lock_state': _sales_invoice_report_lock_state(report.status),
                    'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()


class TenantSalesInvoiceReportUpdateView(View):
    template_name = 'iroad_tenants/finance/sales_invoice_report/sales_invoice_report_form.html'

    def get(self, request, report_id):
        context = _tenant_context_from_session(request)
        denied = _tenant_sales_invoice_report_access(request, context)
        if denied:
            return denied
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            if (request.GET.get('action') or '').strip() == 'preview':
                client_id = (request.GET.get('client') or '').strip()
                date_from = parse_date((request.GET.get('booking_date_from') or '').strip())
                date_to = parse_date((request.GET.get('booking_date_to') or '').strip())
                currency = (request.GET.get('currency') or '').strip()
                try:
                    payload = _sales_invoice_report_preview_payload(
                        client_id=client_id,
                        booking_date_from=date_from,
                        booking_date_to=date_to,
                        currency=currency,
                    )
                except ValidationError as exc:
                    return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
                return JsonResponse(payload)

            report = SalesInvoiceReport.objects.filter(pk=report_id).first()
            if not report:
                messages.error(request, 'Sales Invoice Report not found.', extra_tags='tenant')
                return _tenant_redirect(request, 'iroad_tenants:sales_invoice_report_list')
            bundle = _sales_invoice_report_forms_with_preview(instance=report)
            context.update(
                {
                    **bundle,
                    'report': report,
                    'booking_lines': report.booking_lines.all().order_by('line_no'),
                    'surcharge_lines': report.surcharge_lines.all().order_by('line_no'),
                    'shipment_lines': report.shipment_lines.all().order_by('line_no'),
                    'is_edit': True,
                    'page_title': f'Edit {report.report_no}',
                    'lock_state': _sales_invoice_report_lock_state(report.status),
                    'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()

    def post(self, request, report_id):
        context = _tenant_context_from_session(request)
        denied = _tenant_sales_invoice_report_access(request, context)
        if denied:
            return denied
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            report = SalesInvoiceReport.objects.filter(pk=report_id).first()
            if not report:
                messages.error(request, 'Sales Invoice Report not found.', extra_tags='tenant')
                return _tenant_redirect(request, 'iroad_tenants:sales_invoice_report_list')
            if report.status == SalesInvoiceReport.Status.CONVERTED:
                messages.error(request, 'Converted reports cannot be edited.', extra_tags='tenant')
                return _tenant_redirect(
                    request,
                    'iroad_tenants:sales_invoice_report_detail',
                    report_id=report.report_id,
                )

            original = SalesInvoiceReport.objects.get(pk=report.pk)
            bundle = _sales_invoice_report_forms_with_preview(instance=report, data=request.POST)
            form = bundle['report_form']
            if not form.is_valid():
                context.update(
                    {
                        **bundle,
                        'report': report,
                        'booking_lines': report.booking_lines.all().order_by('line_no'),
                        'surcharge_lines': report.surcharge_lines.all().order_by('line_no'),
                        'shipment_lines': report.shipment_lines.all().order_by('line_no'),
                        'is_edit': True,
                        'page_title': f'Edit {report.report_no}',
                        'lock_state': _sales_invoice_report_lock_state(report.status),
                        'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                    }
                )
                messages.error(request, 'Please fix the highlighted errors.', extra_tags='tenant')
                return render(request, self.template_name, context)

            with db_transaction.atomic():
                obj = form.save(commit=False)
                if original.status == SalesInvoiceReport.Status.VERIFIED:
                    # Verified reports are partially locked: keep header scope immutable.
                    obj.client_id = original.client_id
                    obj.report_date = original.report_date
                    obj.booking_date_from = original.booking_date_from
                    obj.booking_date_to = original.booking_date_to
                    obj.currency = original.currency
                    obj.sales_invoice_ref = original.sales_invoice_ref
                obj.updated_by = (context.get('display_name') or '').strip()
                obj.full_clean()
                obj.save()

                # Verified state allows only limited edits (remarks/status); no child refresh.
                if original.status == SalesInvoiceReport.Status.DRAFT:
                    header_changed = any(
                        [
                            original.client_id != obj.client_id,
                            original.booking_date_from != obj.booking_date_from,
                            original.booking_date_to != obj.booking_date_to,
                            (original.currency or '') != (obj.currency or ''),
                        ]
                    )
                    if header_changed:
                        _refresh_sales_invoice_report_children(obj)

            messages.success(request, f'{obj.report_no} updated successfully.', extra_tags='tenant')
            return _tenant_redirect(
                request,
                'iroad_tenants:sales_invoice_report_detail',
                report_id=obj.report_id,
            )
        finally:
            connection.set_schema_to_public()


class TenantSalesInvoiceReportStatusUpdateView(View):
    def post(self, request, report_id):
        context = _tenant_context_from_session(request)
        denied = _tenant_sales_invoice_report_access(request, context)
        if denied:
            return denied
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            report = SalesInvoiceReport.objects.filter(pk=report_id).first()
            if not report:
                messages.error(request, 'Sales Invoice Report not found.', extra_tags='tenant')
                return _tenant_redirect(request, 'iroad_tenants:sales_invoice_report_list')
            next_status = (request.POST.get('status') or '').strip()
            if next_status not in {
                SalesInvoiceReport.Status.DRAFT,
                SalesInvoiceReport.Status.VERIFIED,
                SalesInvoiceReport.Status.CONVERTED,
            }:
                messages.error(request, 'Invalid status selected.', extra_tags='tenant')
                return _tenant_redirect(
                    request,
                    'iroad_tenants:sales_invoice_report_detail',
                    report_id=report.report_id,
                )
            if report.status == SalesInvoiceReport.Status.CONVERTED:
                messages.error(request, 'Converted report cannot change status.', extra_tags='tenant')
                return _tenant_redirect(
                    request,
                    'iroad_tenants:sales_invoice_report_detail',
                    report_id=report.report_id,
                )

            report.status = next_status
            if next_status == SalesInvoiceReport.Status.CONVERTED and not report.sales_invoice_ref:
                report.sales_invoice_ref = uuid.uuid4()
            report.updated_by = (context.get('display_name') or '').strip()
            report.save(update_fields=['status', 'sales_invoice_ref', 'updated_by', 'updated_at'])
            messages.success(request, f'Status updated to {next_status}.', extra_tags='tenant')
            return _tenant_redirect(
                request,
                'iroad_tenants:sales_invoice_report_detail',
                report_id=report.report_id,
            )
        finally:
            connection.set_schema_to_public()


class TenantOperationBookingCreateView(TenantSimplePageView):
    """Render booking create page."""

    template_name = 'iroad_tenants/Operation_management/Booking/Booking.html'

    def get(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        if not context.get('is_tenant_admin') and not context.get('can_view_booking'):
            messages.error(request, 'You do not have permission to view Booking.', extra_tags='tenant')
            return _tenant_redirect(request, 'iroad_tenants:tenant_dashboard')
        return render(request, self.template_name, context)


class TenantOperationBookingListView(TenantSimplePageView):
    """Render booking list page."""

    template_name = 'iroad_tenants/Operation_management/Booking/Booking-list.html'

    def get(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        if not context.get('is_tenant_admin') and not context.get('can_view_booking'):
            messages.error(request, 'You do not have permission to view Booking.', extra_tags='tenant')
            return _tenant_redirect(request, 'iroad_tenants:tenant_dashboard')
        return render(request, self.template_name, context)


class TenantOperationShipmentListView(TenantSimplePageView):
    template_name = 'iroad_tenants/Operation_management/Shipment/Shipment-list.html'

    def get(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        if not context.get('is_tenant_admin') and not context.get('can_view_shipment'):
            messages.error(request, 'You do not have permission to view Shipment.', extra_tags='tenant')
            return _tenant_redirect(request, 'iroad_tenants:tenant_dashboard')
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            context.update(
                {
                    'shipment_records': list(TenantShipment.objects.order_by('-created_at')[:100]),
                    'tenant_schema_name': tenant_registry.schema_name,
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()


class TenantOperationShipmentCreateView(TenantSimplePageView):
    template_name = 'iroad_tenants/Operation_management/Shipment/Shipment.html'

    def get(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        if not context.get('is_tenant_admin') and not context.get('can_view_shipment'):
            messages.error(request, 'You do not have permission to view Shipment.', extra_tags='tenant')
            return _tenant_redirect(request, 'iroad_tenants:tenant_dashboard')
        context['shipment_creation_date'] = timezone.now().date()
        return render(request, self.template_name, context)

    def post(self, request):
        messages.info(request, 'Shipment create/save is available in the latest flow.', extra_tags='tenant')
        return _tenant_redirect(request, 'iroad_tenants:tenant_operation_shipment_list')


class TenantOperationShipmentDocumentsListView(TenantSimplePageView):
    template_name = 'iroad_tenants/Operation_management/Shipment_Document/Shipment-documents-list.html'

    def get(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            context.update(
                {
                    'shipment_documents_page': Paginator(
                        list(TenantShipmentDocument.objects.order_by('-created_at')),
                        10,
                    ).get_page(request.GET.get('page') or 1),
                    'tenant_schema_name': tenant_registry.schema_name,
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()


class TenantOperationShipmentDocumentsCreateView(TenantSimplePageView):
    template_name = 'iroad_tenants/Operation_management/Shipment_Document/Shipment-documents.html'

    def get(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        return render(request, self.template_name, context)

    def post(self, request):
        messages.info(request, 'Shipment document save is available in the latest flow.', extra_tags='tenant')
        return _tenant_redirect(request, 'iroad_tenants:tenant_operation_shipment_documents_list')


class TenantOperationShipmentPodListView(TenantSimplePageView):
    template_name = 'iroad_tenants/Operation_management/Shipment_POD/Shipment-POD-analysis-list.html'

    def get(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            context.update(
                {
                    'pod_rows': list(TenantShipmentDocument.objects.order_by('-created_at')[:200]),
                    'tenant_schema_name': tenant_registry.schema_name,
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()


class TenantOperationShipmentPodCreateView(TenantSimplePageView):
    template_name = 'iroad_tenants/Operation_management/Shipment_POD/Digital-pod-transaction.html'

    def get(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        return render(request, self.template_name, context)

    def post(self, request):
        messages.info(request, 'POD save is available in the latest flow.', extra_tags='tenant')
        return _tenant_redirect(request, 'iroad_tenants:tenant_operation_shipment_pod_list')


class TenantOperationShipmentPodDetailView(TenantSimplePageView):
    template_name = 'iroad_tenants/Operation_management/Shipment_POD/Shipment-POD-analysis-details.html'

    def get(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        record_no = (request.GET.get('record_no') or '').strip()
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            context.update(
                {
                    'pod_record': TenantShipmentDocument.objects.filter(record_no=record_no).first(),
                    'tenant_schema_name': tenant_registry.schema_name,
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()


class TenantOperationShipmentPodDeleteView(TenantSimplePageView):
    def post(self, request):
        record_no = (request.POST.get('record_no') or '').strip()
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            if record_no:
                doc = TenantShipmentDocument.objects.filter(record_no=record_no).first()
                if doc:
                    TenantShipmentPodPage.objects.filter(document=doc).delete()
            messages.success(request, 'POD record deleted.', extra_tags='tenant')
            return _tenant_redirect(request, 'iroad_tenants:tenant_operation_shipment_pod_list')
        finally:
            connection.set_schema_to_public()


class TenantOperationDocumentHandoverListView(TenantSimplePageView):
    template_name = 'iroad_tenants/Operation_management/Document_handover/Document-handover-list.html'

    def get(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            context.update(
                {
                    'document_handover_rows': list(TenantDocumentHandover.objects.order_by('-created_at')[:100]),
                    'tenant_schema_name': tenant_registry.schema_name,
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()


class TenantOperationDocumentHandoverCreateView(TenantSimplePageView):
    template_name = 'iroad_tenants/Operation_management/Document_handover/Document-handover.html'

    def get(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        return render(request, self.template_name, context)

    def post(self, request):
        messages.info(request, 'Document handover save is available in the latest flow.', extra_tags='tenant')
        return _tenant_redirect(request, 'iroad_tenants:tenant_operation_document_handover_list')


class TenantOperationTruckMovementLogListView(TenantSimplePageView):
    template_name = 'iroad_tenants/Operation_management/Truck_movement_log/Truck-movement-log-list.html'

    def get(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            context.update(
                {
                    'truck_movement_rows': list(TenantTruckMovementLog.objects.order_by('-created_at')[:100]),
                    'tenant_schema_name': tenant_registry.schema_name,
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()


class TenantOperationTruckMovementLogCreateView(TenantSimplePageView):
    template_name = 'iroad_tenants/Operation_management/Truck_movement_log/Truck-movement-log.html'

    def get(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        return render(request, self.template_name, context)

    def post(self, request):
        messages.info(request, 'Truck movement log save is available in the latest flow.', extra_tags='tenant')
        return _tenant_redirect(request, 'iroad_tenants:tenant_operation_truck_movement_log_list')


class TenantOperationActionsListView(TenantSimplePageView):
    template_name = 'iroad_tenants/Operation_management/Operation_Action/Operation-actions-list.html'

    def get(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            context.update(
                {
                    'operation_action_rows': list(TenantOperationAction.objects.order_by('action_code')[:200]),
                    'tenant_schema_name': tenant_registry.schema_name,
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()


class TenantOperationActionsCreateView(TenantSimplePageView):
    template_name = 'iroad_tenants/Operation_management/Operation_Action/Operation-actions.html'

    def get(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        return render(request, self.template_name, context)

    def post(self, request):
        messages.info(request, 'Operation action save is available in the latest flow.', extra_tags='tenant')
        return _tenant_redirect(request, 'iroad_tenants:tenant_operation_actions_list')


class TenantOperationSurchargeSalesListView(TenantSimplePageView):
    template_name = 'iroad_tenants/Operation_management/Surcharge/Surcharge-sales-transaction.html'

    def get(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        return render(request, self.template_name, context)


class TenantOperationSurchargeSalesCreateView(TenantSimplePageView):
    template_name = 'iroad_tenants/Operation_management/Surcharge/Surcharge-sales-transaction-create.html'

    def get(self, request, mode=None):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        context['surcharge_mode'] = (mode or 'create').strip().lower()
        return render(request, self.template_name, context)

    def post(self, request, mode=None):
        messages.info(request, 'Surcharge transaction save is available in the latest flow.', extra_tags='tenant')
        return _tenant_redirect(request, 'iroad_tenants:tenant_operation_surcharge_sales_list')


class TenantWebPushTokenUpsertView(View):
    """Register/update browser push token for the logged-in tenant portal user."""

    def post(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            return JsonResponse({'ok': False, 'error': 'unauthorized'}, status=401)
        try:
            payload = json.loads(request.body or '{}')
        except json.JSONDecodeError:
            return JsonResponse({'ok': False, 'error': 'invalid_json'}, status=400)

        token = str(payload.get('device_token') or '').strip()
        if not token:
            return JsonResponse({'ok': False, 'error': 'device_token_required'}, status=400)

        auth_payload = get_tenant_portal_cookie_payload(request) or {}
        tenant_id = str(auth_payload.get('tenant_id') or '').strip()
        tenant_jti = str(auth_payload.get('jti') or '').strip()
        session_data = get_tenant_session(tenant_id, tenant_jti) if (tenant_id and tenant_jti) else {}
        session_data = session_data or {}

        reference_id = str(
            session_data.get('reference_id') or getattr(context.get('tenant'), 'tenant_id', '')
        ).strip()
        user_domain = str(session_data.get('user_domain') or 'Tenant_User').strip()
        if user_domain not in ('Tenant_User', 'Driver', 'Admin'):
            user_domain = 'Tenant_User'

        obj, _created = PushDeviceToken.objects.update_or_create(
            device_token=token,
            defaults={
                'tenant': context['tenant'],
                'user_domain': user_domain,
                'reference_id': reference_id,
                'is_active': True,
            },
        )
        return JsonResponse(
            {
                'ok': True,
                'token_id': str(obj.token_id),
                'user_domain': obj.user_domain,
                'reference_id': obj.reference_id,
                'is_active': obj.is_active,
            },
            status=200,
        )


def _get_singleton_client_account_settings():
    """Single settings row for the active tenant schema (isolated per-tenant DB).

    Prefer the most recently updated row so we never read stale flags if duplicate
    rows ever existed; settings POST success path consolidates to one row.
    """
    row = TenantClientAccountSetting.objects.order_by('-updated_at').first()
    if row is None:
        row = TenantClientAccountSetting.objects.create()
    return row


def _client_account_settings_template_dict(settings_obj):
    return {
        'require_national_id_individual': settings_obj.require_national_id_individual,
        'require_commercial_registration_business': (
            settings_obj.require_commercial_registration_business
        ),
        'require_tax_vat_registration_business': settings_obj.require_tax_vat_registration_business,
        'default_client_status': settings_obj.default_client_status,
        'default_client_type': settings_obj.default_client_type,
        'default_preferred_currency': settings_obj.default_preferred_currency or '',
    }


def _apply_client_account_defaults_from_settings(form_data, settings_obj):
    """Pre-fill new-client form from tenant-scoped Client Account Settings."""
    if settings_obj.default_client_status:
        form_data['status'] = settings_obj.default_client_status
    if settings_obj.default_client_type:
        form_data['client_type'] = settings_obj.default_client_type
    default_cur = (settings_obj.default_preferred_currency or '').strip()
    if default_cur:
        form_data['preferred_currency'] = default_cur


def _validate_client_account_document_rules(form_data, settings_obj, form_errors):
    """Enforce National ID / CR / VAT rules from the current tenant's settings."""
    from tenant_workspace.client_account_document_rules import (
        collect_client_account_document_rule_errors,
    )

    form_errors.update(
        collect_client_account_document_rule_errors(
            client_type=form_data.get('client_type'),
            national_id=form_data.get('national_id'),
            commercial_registration_no=form_data.get('commercial_registration_no'),
            tax_registration_no=form_data.get('tax_registration_no'),
            require_national_id_individual=bool(settings_obj.require_national_id_individual),
            require_commercial_registration_business=bool(
                settings_obj.require_commercial_registration_business
            ),
            require_tax_vat_registration_business=bool(
                settings_obj.require_tax_vat_registration_business
            ),
        )
    )


def _merge_validation_error_into_form_errors(exc, form_errors):
    """Map Django ``ValidationError`` (e.g. from ``TenantClientAccount.save``) to form field errors."""
    if hasattr(exc, 'message_dict') and exc.message_dict:
        for field, msgs in exc.message_dict.items():
            if isinstance(msgs, (list, tuple)) and msgs:
                form_errors[field] = str(msgs[0])
            elif msgs:
                form_errors[field] = str(msgs)
            else:
                form_errors[field] = str(exc)
    elif getattr(exc, 'messages', None):
        joined = '; '.join(str(m) for m in exc.messages)
        form_errors.setdefault('__all__', joined)


class TenantClientAccountView(View):
    """List client accounts from the current tenant workspace schema."""

    template_name = 'iroad_tenants/Clients_Management/Client-account.html'

    def get(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            client_accounts = list(
                TenantClientAccount.objects.all().order_by('-created_at', '-account_no')
            )
            context.update(
                {
                    'client_accounts': client_accounts,
                    'client_accounts_count': len(client_accounts),
                    'tenant_schema_name': tenant_registry.schema_name,
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()


class TenantClientAccountSettingsView(View):
    """Load/save client CRM defaults in the current tenant workspace schema only."""

    template_name = 'iroad_tenants/Clients_Management/Client-account-setting.html'

    def get(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            settings_obj = _get_singleton_client_account_settings()
            context.update(
                {
                    'settings_data': _client_account_settings_template_dict(settings_obj),
                    'settings_errors': {},
                    'currency_options': _active_currency_options(),
                    'tenant_schema_name': tenant_registry.schema_name,
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()

    def post(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            settings_data = {
                'require_national_id_individual': bool(
                    request.POST.get('require_national_id_individual')
                ),
                'require_commercial_registration_business': bool(
                    request.POST.get('require_commercial_registration_business')
                ),
                'require_tax_vat_registration_business': bool(
                    request.POST.get('require_tax_vat_registration_business')
                ),
                'default_client_status': (request.POST.get('default_client_status') or '').strip()
                or TenantClientAccount.Status.ACTIVE,
                'default_client_type': (request.POST.get('default_client_type') or '').strip()
                or TenantClientAccount.ClientType.INDIVIDUAL,
                'default_preferred_currency': (request.POST.get('default_preferred_currency') or '').strip(),
            }
            settings_errors = {}
            if settings_data['default_client_status'] not in {
                TenantClientAccount.Status.ACTIVE,
                TenantClientAccount.Status.INACTIVE,
            }:
                settings_errors['default_client_status'] = 'Invalid default status.'
            if settings_data['default_client_type'] not in {
                TenantClientAccount.ClientType.INDIVIDUAL,
                TenantClientAccount.ClientType.BUSINESS,
            }:
                settings_errors['default_client_type'] = 'Invalid default client type.'
            if settings_data['default_preferred_currency']:
                valid_codes = {
                    c['currency_code'] for c in _active_currency_options()
                }
                if settings_data['default_preferred_currency'] not in valid_codes:
                    settings_errors['default_preferred_currency'] = (
                        'Choose an active currency from the list, or leave the default currency empty.'
                    )

            if settings_errors:
                context.update(
                    {
                        'settings_data': settings_data,
                        'settings_errors': settings_errors,
                        'currency_options': _active_currency_options(),
                        'tenant_schema_name': tenant_registry.schema_name,
                    }
                )
                messages.error(
                    request,
                    'Please fix the highlighted errors.',
                    extra_tags='tenant',
                )
                return render(request, self.template_name, context)

            keeper = TenantClientAccountSetting.objects.order_by('-updated_at').first()
            if keeper is None:
                keeper = TenantClientAccountSetting.objects.create()
            else:
                TenantClientAccountSetting.objects.exclude(pk=keeper.pk).delete()
            settings_obj = keeper
            settings_obj.require_national_id_individual = settings_data[
                'require_national_id_individual'
            ]
            settings_obj.require_commercial_registration_business = settings_data[
                'require_commercial_registration_business'
            ]
            settings_obj.require_tax_vat_registration_business = settings_data[
                'require_tax_vat_registration_business'
            ]
            settings_obj.default_client_status = settings_data['default_client_status']
            settings_obj.default_client_type = settings_data['default_client_type']
            settings_obj.default_preferred_currency = settings_data['default_preferred_currency']
            settings_obj.save()
            messages.success(
                request,
                'Client account settings saved for this workspace.',
                extra_tags='tenant',
            )
            return _tenant_redirect(request, 'iroad_tenants:tenant_client_account_settings')
        finally:
            connection.set_schema_to_public()


class TenantClientAccountCreateView(View):
    template_name = 'iroad_tenants/Clients_Management/Client-account-new.html'

    CLIENT_FORM_CODE = 'client-account'
    CLIENT_FORM_LABEL = 'Client Account'
    CLIENT_REF_PREFIX = 'CA'

    def _base_form_data(self):
        return {
            'account_no': '',
            'created_at': timezone.localtime().strftime('%b %d, %Y, %I:%M %p'),
            'client_type': TenantClientAccount.ClientType.INDIVIDUAL,
            'status': TenantClientAccount.Status.ACTIVE,
            'name_arabic': '',
            'name_english': '',
            'display_name': '',
            'preferred_currency': '',
            'billing_street_1': '',
            'billing_street_2': '',
            'billing_city': '',
            'billing_region': '',
            'postal_code': '',
            'country': '',
            'credit_limit_amount': '',
            'limit_currency_code': 'SAR',
            'payment_term_days': '',
            'national_id': '',
            'commercial_registration_no': '',
            'tax_registration_no': '',
        }

    def _collect_form_data(self, request):
        data = self._base_form_data()
        for key in data.keys():
            if key in {'status', 'client_type'}:
                data[key] = (request.POST.get(key) or '').strip() or data[key]
            else:
                data[key] = (request.POST.get(key) or '').strip()
        data['limit_currency_code'] = data['limit_currency_code'] or 'SAR'
        return data

    def _build_preview_account_no(self):
        config, _ = AutoNumberConfiguration.objects.get_or_create(
            form_code=self.CLIENT_FORM_CODE,
            defaults={
                'form_label': self.CLIENT_FORM_LABEL,
                'number_of_digits': 4,
                'sequence_format': AutoNumberConfiguration.SequenceFormat.NUMERIC,
                'is_unique': True,
            },
        )
        sequence = AutoNumberSequence.objects.filter(form_code=self.CLIENT_FORM_CODE).first()
        next_number = int(sequence.next_number if sequence else 1)
        return _render_tenant_ref_no(next_number, config, prefix=self.CLIENT_REF_PREFIX)

    def get(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            settings_obj = _get_singleton_client_account_settings()
            form_data = self._base_form_data()
            form_data['account_no'] = self._build_preview_account_no()
            _apply_client_account_defaults_from_settings(form_data, settings_obj)
            context.update(
                {
                    'form_data': form_data,
                    'form_errors': {},
                    'tenant_schema_name': tenant_registry.schema_name,
                    'currency_options': _active_currency_options(),
                    'settings_data': _client_account_settings_template_dict(settings_obj),
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()

    def post(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        form_data = self._collect_form_data(request)
        form_errors = {}
        try:
            settings_obj = _get_singleton_client_account_settings()
            if form_data['client_type'] not in {
                TenantClientAccount.ClientType.INDIVIDUAL,
                TenantClientAccount.ClientType.BUSINESS,
            }:
                form_errors['client_type'] = 'Invalid client type selected.'
            if form_data['status'] not in {
                TenantClientAccount.Status.ACTIVE,
                TenantClientAccount.Status.INACTIVE,
            }:
                form_errors['status'] = 'Invalid status selected.'
            if not form_data['name_english']:
                form_errors['name_english'] = 'Name (English) is required.'
            if not form_data['display_name']:
                form_errors['display_name'] = 'Display Name is required.'
            if not form_data['preferred_currency']:
                form_errors['preferred_currency'] = 'Preferred Currency is required.'
            if not form_data['billing_street_1']:
                form_errors['billing_street_1'] = 'Billing Street 1 is required.'
            if not form_data['billing_city']:
                form_errors['billing_city'] = 'Billing City is required.'
            if not form_data['country']:
                form_errors['country'] = 'Country is required.'
            if not form_errors.get('client_type'):
                _validate_client_account_document_rules(form_data, settings_obj, form_errors)

            credit_limit_raw = form_data['credit_limit_amount'] or '0'
            payment_term_raw = form_data['payment_term_days'] or '0'
            try:
                credit_limit_amount = Decimal(credit_limit_raw)
                if credit_limit_amount < 0:
                    raise ValueError
            except Exception:
                form_errors['credit_limit_amount'] = 'Credit Limit Amount must be 0 or greater.'
                credit_limit_amount = Decimal('0')
            try:
                payment_term_days = int(payment_term_raw)
                if payment_term_days < 0:
                    raise ValueError
            except Exception:
                form_errors['payment_term_days'] = 'Payment Term (Days) must be 0 or greater.'
                payment_term_days = 0

            if form_errors:
                form_data['account_no'] = self._build_preview_account_no()
                context.update(
                    {
                        'form_data': form_data,
                        'form_errors': form_errors,
                        'tenant_schema_name': tenant_registry.schema_name,
                        'currency_options': _active_currency_options(),
                        'settings_data': _client_account_settings_template_dict(settings_obj),
                    }
                )
                messages.error(request, 'Please fix the highlighted errors.', extra_tags='tenant')
                return render(request, self.template_name, context)

            account_no, account_sequence = _next_auto_number_for_form(
                form_code=self.CLIENT_FORM_CODE,
                form_label=self.CLIENT_FORM_LABEL,
                prefix=self.CLIENT_REF_PREFIX,
            )
            try:
                TenantClientAccount.objects.create(
                    account_no=account_no,
                    account_sequence=account_sequence,
                    client_type=form_data['client_type'],
                    status=form_data['status'],
                    name_arabic=form_data['name_arabic'],
                    name_english=form_data['name_english'],
                    display_name=form_data['display_name'],
                    preferred_currency=form_data['preferred_currency'],
                    billing_street_1=form_data['billing_street_1'],
                    billing_street_2=form_data['billing_street_2'],
                    billing_city=form_data['billing_city'],
                    billing_region=form_data['billing_region'],
                    postal_code=form_data['postal_code'],
                    country=form_data['country'],
                    credit_limit_amount=credit_limit_amount,
                    limit_currency_code=form_data['limit_currency_code'] or 'SAR',
                    payment_term_days=payment_term_days,
                    national_id=form_data['national_id'],
                    commercial_registration_no=form_data['commercial_registration_no'],
                    tax_registration_no=form_data['tax_registration_no'],
                    created_by_label=(context.get('display_name') or '').strip(),
                )
            except ValidationError as exc:
                _merge_validation_error_into_form_errors(exc, form_errors)
                form_data['account_no'] = self._build_preview_account_no()
                context.update(
                    {
                        'form_data': form_data,
                        'form_errors': form_errors,
                        'tenant_schema_name': tenant_registry.schema_name,
                        'currency_options': _active_currency_options(),
                        'settings_data': _client_account_settings_template_dict(settings_obj),
                    }
                )
                messages.error(request, 'Please fix the highlighted errors.', extra_tags='tenant')
                return render(request, self.template_name, context)
            messages.success(
                request,
                f'Client account {account_no} created successfully.',
                extra_tags='tenant',
            )
            return _tenant_redirect(request, 'iroad_tenants:tenant_client_account')
        finally:
            connection.set_schema_to_public()


class TenantClientAccountEditView(TenantClientAccountCreateView):
    """Edit an existing client account using the same form as create."""

    def _form_data_from_account(self, account):
        return {
            'account_no': account.account_no,
            'created_at': timezone.localtime(account.created_at).strftime('%b %d, %Y, %I:%M %p'),
            'client_type': account.client_type,
            'status': account.status,
            'name_arabic': account.name_arabic or '',
            'name_english': account.name_english or '',
            'display_name': account.display_name or '',
            'preferred_currency': account.preferred_currency or '',
            'billing_street_1': account.billing_street_1 or '',
            'billing_street_2': account.billing_street_2 or '',
            'billing_city': account.billing_city or '',
            'billing_region': account.billing_region or '',
            'postal_code': account.postal_code or '',
            'country': account.country or '',
            'credit_limit_amount': str(account.credit_limit_amount),
            'limit_currency_code': account.limit_currency_code or 'SAR',
            'payment_term_days': str(account.payment_term_days),
            'national_id': account.national_id or '',
            'commercial_registration_no': account.commercial_registration_no or '',
            'tax_registration_no': account.tax_registration_no or '',
        }

    def get(self, request, account_no):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            account = TenantClientAccount.objects.filter(account_no=(account_no or '').strip()).first()
            if account is None:
                messages.error(request, 'Client account not found.', extra_tags='tenant')
                return _tenant_redirect(request, 'iroad_tenants:tenant_client_account')
            settings_obj = _get_singleton_client_account_settings()
            form_data = self._form_data_from_account(account)
            context.update(
                {
                    'form_data': form_data,
                    'form_errors': {},
                    'tenant_schema_name': tenant_registry.schema_name,
                    'is_edit_mode': True,
                    'editing_account_no': account.account_no,
                    'currency_options': _active_currency_options(),
                    'settings_data': _client_account_settings_template_dict(settings_obj),
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()

    def post(self, request, account_no):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            account = TenantClientAccount.objects.filter(account_no=(account_no or '').strip()).first()
            if account is None:
                messages.error(request, 'Client account not found.', extra_tags='tenant')
                return _tenant_redirect(request, 'iroad_tenants:tenant_client_account')
            form_data = self._collect_form_data(request)
            form_data['account_no'] = account.account_no
            form_errors = {}
            settings_obj = _get_singleton_client_account_settings()
            if form_data['client_type'] not in {
                TenantClientAccount.ClientType.INDIVIDUAL,
                TenantClientAccount.ClientType.BUSINESS,
            }:
                form_errors['client_type'] = 'Invalid client type selected.'
            if form_data['status'] not in {
                TenantClientAccount.Status.ACTIVE,
                TenantClientAccount.Status.INACTIVE,
            }:
                form_errors['status'] = 'Invalid status selected.'
            if not form_data['name_english']:
                form_errors['name_english'] = 'Name (English) is required.'
            if not form_data['display_name']:
                form_errors['display_name'] = 'Display Name is required.'
            if not form_data['preferred_currency']:
                form_errors['preferred_currency'] = 'Preferred Currency is required.'
            if not form_data['billing_street_1']:
                form_errors['billing_street_1'] = 'Billing Street 1 is required.'
            if not form_data['billing_city']:
                form_errors['billing_city'] = 'Billing City is required.'
            if not form_data['country']:
                form_errors['country'] = 'Country is required.'
            if not form_errors.get('client_type'):
                _validate_client_account_document_rules(form_data, settings_obj, form_errors)

            credit_limit_raw = form_data['credit_limit_amount'] or '0'
            payment_term_raw = form_data['payment_term_days'] or '0'
            try:
                credit_limit_amount = Decimal(credit_limit_raw)
                if credit_limit_amount < 0:
                    raise ValueError
            except Exception:
                form_errors['credit_limit_amount'] = 'Credit Limit Amount must be 0 or greater.'
                credit_limit_amount = Decimal('0')
            try:
                payment_term_days = int(payment_term_raw)
                if payment_term_days < 0:
                    raise ValueError
            except Exception:
                form_errors['payment_term_days'] = 'Payment Term (Days) must be 0 or greater.'
                payment_term_days = 0

            if form_errors:
                context.update(
                    {
                        'form_data': form_data,
                        'form_errors': form_errors,
                        'tenant_schema_name': tenant_registry.schema_name,
                        'is_edit_mode': True,
                        'editing_account_no': account.account_no,
                        'currency_options': _active_currency_options(),
                        'settings_data': _client_account_settings_template_dict(settings_obj),
                    }
                )
                messages.error(request, 'Please fix the highlighted errors.', extra_tags='tenant')
                return render(request, self.template_name, context)

            account.client_type = form_data['client_type']
            account.status = form_data['status']
            account.name_arabic = form_data['name_arabic']
            account.name_english = form_data['name_english']
            account.display_name = form_data['display_name']
            account.preferred_currency = form_data['preferred_currency']
            account.billing_street_1 = form_data['billing_street_1']
            account.billing_street_2 = form_data['billing_street_2']
            account.billing_city = form_data['billing_city']
            account.billing_region = form_data['billing_region']
            account.postal_code = form_data['postal_code']
            account.country = form_data['country']
            account.credit_limit_amount = credit_limit_amount
            account.limit_currency_code = form_data['limit_currency_code'] or 'SAR'
            account.payment_term_days = payment_term_days
            account.national_id = form_data['national_id']
            account.commercial_registration_no = form_data['commercial_registration_no']
            account.tax_registration_no = form_data['tax_registration_no']
            try:
                account.save()
            except ValidationError as exc:
                _merge_validation_error_into_form_errors(exc, form_errors)
                context.update(
                    {
                        'form_data': form_data,
                        'form_errors': form_errors,
                        'tenant_schema_name': tenant_registry.schema_name,
                        'is_edit_mode': True,
                        'editing_account_no': account.account_no,
                        'currency_options': _active_currency_options(),
                        'settings_data': _client_account_settings_template_dict(settings_obj),
                    }
                )
                messages.error(request, 'Please fix the highlighted errors.', extra_tags='tenant')
                return render(request, self.template_name, context)
            messages.success(
                request,
                f'Client account {account.account_no} updated successfully.',
                extra_tags='tenant',
            )
            return _tenant_redirect(request, 'iroad_tenants:tenant_client_account')
        finally:
            connection.set_schema_to_public()


class TenantClientAccountToggleStatusView(View):
    def post(self, request, account_no):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            account = TenantClientAccount.objects.filter(account_no=(account_no or '').strip()).first()
            if account is None:
                messages.error(request, 'Client account not found.', extra_tags='tenant')
                return _tenant_redirect(request, 'iroad_tenants:tenant_client_account')
            account.status = (
                TenantClientAccount.Status.INACTIVE
                if account.status == TenantClientAccount.Status.ACTIVE
                else TenantClientAccount.Status.ACTIVE
            )
            account.save(update_fields=['status', 'updated_at'])
            messages.success(request, f'Status changed to {account.status}.', extra_tags='tenant')
            if (request.POST.get('return_to') or '').strip() == 'details':
                q = {'id': account.account_no}
                return redirect(
                    f"{reverse('iroad_tenants:tenant_client_details')}?{urlencode(q)}"
                )
            return _tenant_redirect(request, 'iroad_tenants:tenant_client_account')
        finally:
            connection.set_schema_to_public()


class TenantClientAccountDeleteView(View):
    def post(self, request, account_no):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            account = TenantClientAccount.objects.filter(account_no=(account_no or '').strip()).first()
            if account is None:
                messages.error(request, 'Client account not found.', extra_tags='tenant')
                return _tenant_redirect(request, 'iroad_tenants:tenant_client_account')
            label = account.account_no
            try:
                account.delete()
            except ProtectedError:
                messages.error(
                    request,
                    'This client cannot be deleted while addresses or other records still reference it.',
                    extra_tags='tenant',
                )
                return _tenant_redirect(request, 'iroad_tenants:tenant_client_account')
            messages.success(request, f'Client account {label} deleted.', extra_tags='tenant')
            return _tenant_redirect(request, 'iroad_tenants:tenant_client_account')
        finally:
            connection.set_schema_to_public()


class TenantClientSalesReportView(View):
    """Per-account sales report placeholder until reporting is implemented."""

    template_name = 'iroad_tenants/Clients_Management/Client-sales-report.html'

    def get(self, request, account_no):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            account = TenantClientAccount.objects.filter(account_no=(account_no or '').strip()).first()
            if account is None:
                messages.error(request, 'Client account not found.', extra_tags='tenant')
                return _tenant_redirect(request, 'iroad_tenants:tenant_client_account')
            context.update(
                {
                    'client_account': account,
                    'tenant_schema_name': tenant_registry.schema_name,
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()


class TenantClientAttachmentsView(View):
    """Create client attachment (POST) bound to ``TenantClientAccount``."""

    template_name = 'iroad_tenants/Clients_Management/Client-attachments.html'

    def _account_queryset(self):
        return TenantClientAccount.objects.all().order_by('account_no')

    def _default_form_data(self):
        return {
            'attachment_date': timezone.localdate().isoformat(),
            'is_expiry_applicable': 'false',
            'expiry_date': '',
            'file_notes': '',
            'client_account': '',
        }

    def get(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            form_data = self._default_form_data()
            pre = (request.GET.get('account') or request.GET.get('client') or '').strip()
            try:
                match = resolve(request.path_info)
            except Exception:
                match = None
            # Bare ``/attachments/`` (e.g. old bookmarks): send users to the list; keep ``/create/`` and ``?account=`` for the form.
            if (
                match
                and match.url_name == 'tenant_client_attachments'
                and not pre
            ):
                list_url = reverse('iroad_tenants:tenant_client_attachments_list')
                return redirect(list_url)
            if pre:
                form_data['client_account'] = pre
            context.update(
                {
                    'form_data': form_data,
                    'form_errors': {},
                    'client_account_options': list(self._account_queryset()),
                    'tenant_schema_name': tenant_registry.schema_name,
                    'attachment_form_action': reverse(
                        'iroad_tenants:tenant_client_attachments_create',
                    ),
                    'is_edit_mode': False,
                    'editing_attachment': None,
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()

    def post(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        form_data = self._default_form_data()
        form_data['client_account'] = (request.POST.get('client_account') or '').strip()
        form_data['attachment_date'] = (request.POST.get('attachment_date') or '').strip()
        form_data['is_expiry_applicable'] = (request.POST.get('is_expiry_applicable') or '').strip()
        form_data['expiry_date'] = (request.POST.get('expiry_date') or '').strip()
        form_data['file_notes'] = (request.POST.get('file_notes') or '').strip()

        form_errors = {}
        account = None
        if not form_data['client_account']:
            form_errors['client_account'] = 'Select a client account.'
        else:
            account = TenantClientAccount.objects.filter(
                account_no=form_data['client_account'],
            ).first()
            if account is None:
                form_errors['client_account'] = 'Client account not found.'

        ad = parse_date(form_data['attachment_date']) if form_data['attachment_date'] else None
        if not ad:
            form_errors['attachment_date'] = 'Enter a valid attachment date.'

        is_expiry = form_data['is_expiry_applicable'] == 'true'
        ex = None
        if is_expiry:
            ex = parse_date(form_data['expiry_date']) if form_data['expiry_date'] else None
            if not ex:
                form_errors['expiry_date'] = 'Expiry date is required when expiry applies.'
        else:
            form_data['expiry_date'] = ''

        upload = request.FILES.get('attachment_file')
        upload_err = _validate_client_attachment_upload(upload)
        if upload_err:
            form_errors['attachment_file'] = upload_err

        if form_errors:
            context.update(
                {
                    'form_data': form_data,
                    'form_errors': form_errors,
                    'client_account_options': list(self._account_queryset()),
                    'tenant_schema_name': tenant_registry.schema_name,
                    'attachment_form_action': reverse(
                        'iroad_tenants:tenant_client_attachments_create',
                    ),
                    'is_edit_mode': False,
                    'editing_attachment': None,
                }
            )
            messages.error(request, 'Please fix the highlighted errors.', extra_tags='tenant')
            try:
                return render(request, self.template_name, context)
            finally:
                connection.set_schema_to_public()

        try:
            with db_transaction.atomic():
                attachment_no, attachment_sequence = _next_auto_number_for_form(
                    form_code=CLIENT_ATTACHMENT_AUTO_FORM_CODE,
                    form_label=CLIENT_ATTACHMENT_AUTO_FORM_LABEL,
                    prefix=CLIENT_ATTACHMENT_REF_PREFIX,
                )
                TenantClientAttachment.objects.create(
                    attachment_no=attachment_no,
                    attachment_sequence=attachment_sequence,
                    attachment_date=ad,
                    is_expiry_applicable=is_expiry,
                    expiry_date=ex,
                    attachment_file=upload,
                    file_notes=form_data['file_notes'],
                    created_by_label=(context.get('display_name') or '').strip(),
                    client_account=account,
                )
        except Exception:
            logger.exception('Tenant client attachment create failed')
            context.update(
                {
                    'form_data': form_data,
                    'form_errors': {'attachment_file': 'Could not save the file. Try again.'},
                    'client_account_options': list(self._account_queryset()),
                    'tenant_schema_name': tenant_registry.schema_name,
                    'attachment_form_action': reverse(
                        'iroad_tenants:tenant_client_attachments_create',
                    ),
                    'is_edit_mode': False,
                    'editing_attachment': None,
                }
            )
            messages.error(request, 'Upload failed.', extra_tags='tenant')
            try:
                return render(request, self.template_name, context)
            finally:
                connection.set_schema_to_public()

        messages.success(
            request,
            f'Attachment {attachment_no} uploaded for {account.account_no}.',
            extra_tags='tenant',
        )
        connection.set_schema_to_public()
        list_url = reverse('iroad_tenants:tenant_client_attachments_list')
        return redirect(list_url)


def _redirect_client_attachment_list(request):
    url = reverse('iroad_tenants:tenant_client_attachments_list')
    return redirect(url)


def _tenant_client_attachments_base_path():
    """Path prefix for attachment URLs under ``tenant_client_attachments``."""
    p = reverse('iroad_tenants:tenant_client_attachments')
    return p if p.endswith('/') else f'{p}/'


def _tenant_client_attachment_detail_path(attachment_id):
    return f'{_tenant_client_attachments_base_path()}{attachment_id}/detail/'


def _redirect_client_contact_list(request):
    url = reverse('iroad_tenants:tenant_client_contacts_list')
    return redirect(url)


def _tenant_client_contacts_base_path():
    """Path prefix for contact CRUD URLs, ending with ``/`` (from ``tenant_client_contacts``)."""
    p = reverse('iroad_tenants:tenant_client_contacts')
    return p if p.endswith('/') else f'{p}/'


def _tenant_client_contact_edit_path(contact_id):
    return f'{_tenant_client_contacts_base_path()}{contact_id}/edit/'


def _tenant_client_contact_delete_path(contact_id):
    return f'{_tenant_client_contacts_base_path()}{contact_id}/delete/'


def _tenant_client_contact_detail_path(contact_id):
    return f'{_tenant_client_contacts_base_path()}{contact_id}/detail/'


class TenantClientAttachmentEditView(View):
    """Edit metadata / optionally replace file for an existing tenant client attachment."""

    template_name = 'iroad_tenants/Clients_Management/Client-attachments.html'

    def _account_queryset(self):
        return TenantClientAccount.objects.all().order_by('account_no')

    def _default_form_data(self):
        return {
            'attachment_date': timezone.localdate().isoformat(),
            'is_expiry_applicable': 'false',
            'expiry_date': '',
            'file_notes': '',
            'client_account': '',
        }

    def get(self, request, attachment_id):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            att = (
                TenantClientAttachment.objects.select_related('client_account')
                .filter(pk=attachment_id)
                .first()
            )
            if att is None:
                messages.error(request, 'Attachment not found.', extra_tags='tenant')
                return _redirect_client_attachment_list(request)
            form_data = self._default_form_data()
            form_data['client_account'] = att.client_account.account_no
            form_data['attachment_date'] = att.attachment_date.isoformat()
            form_data['is_expiry_applicable'] = 'true' if att.is_expiry_applicable else 'false'
            form_data['expiry_date'] = att.expiry_date.isoformat() if att.expiry_date else ''
            form_data['file_notes'] = att.file_notes or ''
            context.update(
                {
                    'form_data': form_data,
                    'form_errors': {},
                    'client_account_options': list(self._account_queryset()),
                    'tenant_schema_name': tenant_registry.schema_name,
                    'attachment_form_action': reverse(
                        'iroad_tenants:tenant_client_attachment_edit',
                        kwargs={'attachment_id': att.attachment_id},
                    ),
                    'is_edit_mode': True,
                    'editing_attachment': att,
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()

    def post(self, request, attachment_id):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            att = (
                TenantClientAttachment.objects.select_related('client_account')
                .filter(pk=attachment_id)
                .first()
            )
            if att is None:
                messages.error(request, 'Attachment not found.', extra_tags='tenant')
                return _redirect_client_attachment_list(request)

            form_data = self._default_form_data()
            form_data['client_account'] = (request.POST.get('client_account') or '').strip()
            form_data['attachment_date'] = (request.POST.get('attachment_date') or '').strip()
            form_data['is_expiry_applicable'] = (
                request.POST.get('is_expiry_applicable') or ''
            ).strip()
            form_data['expiry_date'] = (request.POST.get('expiry_date') or '').strip()
            form_data['file_notes'] = (request.POST.get('file_notes') or '').strip()

            form_errors = {}
            account = None
            if not form_data['client_account']:
                form_errors['client_account'] = 'Select a client account.'
            else:
                account = TenantClientAccount.objects.filter(
                    account_no=form_data['client_account'],
                ).first()
                if account is None:
                    form_errors['client_account'] = 'Client account not found.'

            ad = parse_date(form_data['attachment_date']) if form_data['attachment_date'] else None
            if not ad:
                form_errors['attachment_date'] = 'Enter a valid attachment date.'

            is_expiry = form_data['is_expiry_applicable'] == 'true'
            ex = None
            if is_expiry:
                ex = parse_date(form_data['expiry_date']) if form_data['expiry_date'] else None
                if not ex:
                    form_errors['expiry_date'] = 'Expiry date is required when expiry applies.'
            else:
                form_data['expiry_date'] = ''

            upload = request.FILES.get('attachment_file')
            if upload:
                upload_err = _validate_client_attachment_upload(upload)
                if upload_err:
                    form_errors['attachment_file'] = upload_err

            _edit_ctx = {
                'form_data': form_data,
                'client_account_options': list(self._account_queryset()),
                'tenant_schema_name': tenant_registry.schema_name,
                'attachment_form_action': reverse(
                    'iroad_tenants:tenant_client_attachment_edit',
                    kwargs={'attachment_id': att.attachment_id},
                ),
                'is_edit_mode': True,
                'editing_attachment': att,
            }

            if form_errors:
                context.update({**_edit_ctx, 'form_errors': form_errors})
                messages.error(request, 'Please fix the highlighted errors.', extra_tags='tenant')
                return render(request, self.template_name, context)

            try:
                with db_transaction.atomic():
                    att.client_account = account
                    att.attachment_date = ad
                    att.is_expiry_applicable = is_expiry
                    att.expiry_date = ex
                    att.file_notes = form_data['file_notes']
                    if upload:
                        if att.attachment_file:
                            att.attachment_file.delete(save=False)
                        att.attachment_file = upload
                    att.save()
            except Exception:
                logger.exception('Tenant client attachment update failed')
                context.update(
                    {
                        **_edit_ctx,
                        'form_errors': {
                            'attachment_file': 'Could not save the file. Try again.',
                        },
                    }
                )
                messages.error(request, 'Update failed.', extra_tags='tenant')
                return render(request, self.template_name, context)

            messages.success(
                request,
                f'Attachment {att.attachment_no} updated.',
                extra_tags='tenant',
            )
            return _redirect_client_attachment_list(request)
        finally:
            connection.set_schema_to_public()


class TenantClientAttachmentDeleteView(View):
    def post(self, request, attachment_id):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            att = TenantClientAttachment.objects.filter(pk=attachment_id).first()
            if att is None:
                messages.error(request, 'Attachment not found.', extra_tags='tenant')
                return _redirect_client_attachment_list(request)
            label = att.attachment_no
            if att.attachment_file:
                att.attachment_file.delete(save=False)
            att.delete()
            messages.success(request, f'Attachment {label} deleted.', extra_tags='tenant')
            return _redirect_client_attachment_list(request)
        finally:
            connection.set_schema_to_public()


class TenantClientAttachmentDetailView(View):
    """Read-only full-page attachment detail."""

    template_name = 'iroad_tenants/Clients_Management/Client-attachment-detail.html'

    def get(self, request, attachment_id):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            attachment = (
                TenantClientAttachment.objects.select_related('client_account')
                .filter(pk=attachment_id)
                .first()
            )
            if attachment is None:
                messages.error(request, 'Attachment not found.', extra_tags='tenant')
                return _redirect_client_attachment_list(request)
            list_url = reverse('iroad_tenants:tenant_client_attachments_list')
            try:
                edit_url = reverse(
                    'iroad_tenants:tenant_client_attachment_edit',
                    kwargs={'attachment_id': attachment.attachment_id},
                )
            except NoReverseMatch:
                edit_url = f'{_tenant_client_attachments_base_path()}{attachment.attachment_id}/edit/'
            file_url = ''
            try:
                if attachment.attachment_file:
                    file_url = attachment.attachment_file.url
            except Exception:
                file_url = ''
            context.update(
                {
                    'attachment': attachment,
                    'tenant_schema_name': tenant_registry.schema_name,
                    'back_to_list_url': list_url,
                    'edit_attachment_url': edit_url,
                    'attachment_file_url': file_url,
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()


class TenantClientAttachmentsListView(View):
    """List all tenant client attachments with summary stats."""

    template_name = 'iroad_tenants/Clients_Management/Client-attachments-list.html'

    def get(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            att = TenantClientAttachment
            qs = (
                TenantClientAttachment.objects.select_related('client_account')
                .order_by('-created_at')
                .all()
            )
            rows = list(qs)
            base = _tenant_client_attachments_base_path()
            for row in rows:
                try:
                    detail_u = reverse(
                        'iroad_tenants:tenant_client_attachment_detail',
                        kwargs={'attachment_id': row.attachment_id},
                    )
                except NoReverseMatch:
                    detail_u = _tenant_client_attachment_detail_path(row.attachment_id)
                row.list_detail_url = detail_u
            attachment_stats = {
                'total': len(rows),
                'valid': sum(1 for r in rows if r.computed_status == att.Status.VALID),
                'does_not_expire': sum(
                    1 for r in rows if r.computed_status == att.Status.DOES_NOT_EXPIRE
                ),
                'expired': sum(1 for r in rows if r.computed_status == att.Status.EXPIRED),
            }
            context.update(
                {
                    'client_attachments': rows,
                    'client_attachments_count': len(rows),
                    'attachment_stats': attachment_stats,
                    'tenant_schema_name': tenant_registry.schema_name,
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()


class TenantClientContactsView(View):
    """Create client contact (GET form / POST save), optional ``?account=`` pre-select."""

    template_name = 'iroad_tenants/Clients_Management/Client-contacts.html'

    def _account_queryset(self):
        return TenantClientAccount.objects.all().order_by('account_no')

    def _default_form_data(self):
        return {
            'client_account': '',
            'name': '',
            'email': '',
            'mobile_number': '',
            'telephone_number': '',
            'extension': '',
            'position': '',
            'is_primary': '',
        }


    def get(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            form_data = self._default_form_data()
            pre = (request.GET.get('account') or request.GET.get('client') or '').strip()
            try:
                match = resolve(request.path_info)
            except Exception:
                match = None
            # Bare ``/contacts/`` (e.g. old bookmarks): send users to the list; keep ``/create/`` and ``?account=`` for the form.
            if (
                match
                and match.url_name == 'tenant_client_contacts'
                and not pre
            ):
                list_url = reverse('iroad_tenants:tenant_client_contacts_list')
                return redirect(list_url)
            if pre:
                form_data['client_account'] = pre
            context.update(
                {
                    'form_data': form_data,
                    'form_errors': {},
                    'client_account_options': list(self._account_queryset()),
                    'tenant_schema_name': tenant_registry.schema_name,
                    'contact_form_action': reverse(
                        'iroad_tenants:tenant_client_contacts_create',
                    ),
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()

    def post(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        form_data = self._default_form_data()
        form_data['client_account'] = (request.POST.get('client_account') or '').strip()
        form_data['name'] = (request.POST.get('name') or '').strip()
        form_data['email'] = (request.POST.get('email') or '').strip()
        form_data['mobile_number'] = (request.POST.get('mobile_number') or '').strip()
        form_data['telephone_number'] = (request.POST.get('telephone_number') or '').strip()
        form_data['extension'] = (request.POST.get('extension') or '').strip()
        form_data['position'] = (request.POST.get('position') or '').strip()
        form_data['is_primary'] = (
            'true' if (request.POST.get('is_primary') in ('true', 'on', '1')) else ''
        )

        form_errors = {}
        account = None
        if not form_data['client_account']:
            form_errors['client_account'] = 'Select a client account.'
        else:
            account = TenantClientAccount.objects.filter(
                account_no=form_data['client_account'],
            ).first()
            if account is None:
                form_errors['client_account'] = 'Client account not found.'

        if not form_data['name']:
            form_errors['name'] = 'Enter the contact name.'

        if form_data['email']:
            try:
                validate_email(form_data['email'])
            except ValidationError:
                form_errors['email'] = 'Enter a valid email address.'

        if form_errors:
            context.update(
                {
                    'form_data': form_data,
                    'form_errors': form_errors,
                    'client_account_options': list(self._account_queryset()),
                    'tenant_schema_name': tenant_registry.schema_name,
                    'contact_form_action': reverse(
                        'iroad_tenants:tenant_client_contacts_create',
                    ),
                }
            )
            messages.error(request, 'Please fix the highlighted errors.', extra_tags='tenant')
            try:
                return render(request, self.template_name, context)
            finally:
                connection.set_schema_to_public()

        is_primary = bool(form_data['is_primary'])
        try:
            with db_transaction.atomic():
                if is_primary:
                    TenantClientContact.objects.filter(
                        client_account=account,
                        is_primary=True,
                    ).update(is_primary=False)
                TenantClientContact.objects.create(
                    name=form_data['name'],
                    email=form_data['email'],
                    mobile_number=form_data['mobile_number'],
                    telephone_number=form_data['telephone_number'],
                    extension=form_data['extension'],
                    position=form_data['position'],
                    is_primary=is_primary,
                    created_by_label=(context.get('display_name') or '').strip(),
                    client_account=account,
                )
        except Exception:
            logger.exception('Tenant client contact create failed')
            context.update(
                {
                    'form_data': form_data,
                    'form_errors': {'__all__': 'Could not save the contact. Try again.'},
                    'client_account_options': list(self._account_queryset()),
                    'tenant_schema_name': tenant_registry.schema_name,
                    'contact_form_action': reverse(
                        'iroad_tenants:tenant_client_contacts_create',
                    ),
                }
            )
            messages.error(request, 'Save failed.', extra_tags='tenant')
            try:
                return render(request, self.template_name, context)
            finally:
                connection.set_schema_to_public()

        messages.success(
            request,
            f'Contact {form_data["name"]} added for {account.account_no}.',
            extra_tags='tenant',
        )
        connection.set_schema_to_public()
        list_url = reverse('iroad_tenants:tenant_client_contacts_list')
        return redirect(list_url)


class TenantClientContactEditView(View):
    """Edit an existing tenant client contact."""

    template_name = 'iroad_tenants/Clients_Management/Client-contacts.html'

    def _account_queryset(self):
        return TenantClientAccount.objects.all().order_by('account_no')

    def _default_form_data(self):
        return {
            'client_account': '',
            'name': '',
            'email': '',
            'mobile_number': '',
            'telephone_number': '',
            'extension': '',
            'position': '',
            'is_primary': '',
        }


    def get(self, request, contact_id):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            contact = (
                TenantClientContact.objects.select_related('client_account')
                .filter(pk=contact_id)
                .first()
            )
            if contact is None:
                messages.error(request, 'Contact not found.', extra_tags='tenant')
                return _redirect_client_contact_list(request)
            form_data = self._default_form_data()
            form_data['client_account'] = contact.client_account.account_no
            form_data['name'] = contact.name
            form_data['email'] = contact.email or ''
            form_data['mobile_number'] = contact.mobile_number or ''
            form_data['telephone_number'] = contact.telephone_number or ''
            form_data['extension'] = contact.extension or ''
            form_data['position'] = contact.position or ''
            form_data['is_primary'] = 'true' if contact.is_primary else ''
            context.update(
                {
                    'form_data': form_data,
                    'form_errors': {},
                    'client_account_options': list(self._account_queryset()),
                    'tenant_schema_name': tenant_registry.schema_name,
                    'contact_form_action': _tenant_client_contact_edit_path(
                        contact.contact_id,
                    ),
                    'is_edit_mode': True,
                    'editing_contact': contact,
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()

    def post(self, request, contact_id):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            contact = (
                TenantClientContact.objects.select_related('client_account')
                .filter(pk=contact_id)
                .first()
            )
            if contact is None:
                messages.error(request, 'Contact not found.', extra_tags='tenant')
                return _redirect_client_contact_list(request)

            form_data = self._default_form_data()
            form_data['client_account'] = (request.POST.get('client_account') or '').strip()
            form_data['name'] = (request.POST.get('name') or '').strip()
            form_data['email'] = (request.POST.get('email') or '').strip()
            form_data['mobile_number'] = (request.POST.get('mobile_number') or '').strip()
            form_data['telephone_number'] = (request.POST.get('telephone_number') or '').strip()
            form_data['extension'] = (request.POST.get('extension') or '').strip()
            form_data['position'] = (request.POST.get('position') or '').strip()
            form_data['is_primary'] = (
                'true' if (request.POST.get('is_primary') in ('true', 'on', '1')) else ''
            )

            form_errors = {}
            account = None
            if not form_data['client_account']:
                form_errors['client_account'] = 'Select a client account.'
            else:
                account = TenantClientAccount.objects.filter(
                    account_no=form_data['client_account'],
                ).first()
                if account is None:
                    form_errors['client_account'] = 'Client account not found.'

            if not form_data['name']:
                form_errors['name'] = 'Enter the contact name.'

            if form_data['email']:
                try:
                    validate_email(form_data['email'])
                except ValidationError:
                    form_errors['email'] = 'Enter a valid email address.'

            _edit_ctx = {
                'form_data': form_data,
                'client_account_options': list(self._account_queryset()),
                'tenant_schema_name': tenant_registry.schema_name,
                'contact_form_action': _tenant_client_contact_edit_path(
                    contact.contact_id,
                ),
                'is_edit_mode': True,
                'editing_contact': contact,
            }

            if form_errors:
                context.update({**_edit_ctx, 'form_errors': form_errors})
                messages.error(request, 'Please fix the highlighted errors.', extra_tags='tenant')
                return render(request, self.template_name, context)

            is_primary = bool(form_data['is_primary'])
            try:
                with db_transaction.atomic():
                    if is_primary:
                        TenantClientContact.objects.filter(
                            client_account=account,
                            is_primary=True,
                        ).exclude(pk=contact.contact_id).update(is_primary=False)
                    contact.client_account = account
                    contact.name = form_data['name']
                    contact.email = form_data['email']
                    contact.mobile_number = form_data['mobile_number']
                    contact.telephone_number = form_data['telephone_number']
                    contact.extension = form_data['extension']
                    contact.position = form_data['position']
                    contact.is_primary = is_primary
                    contact.save()
            except Exception:
                logger.exception('Tenant client contact update failed')
                context.update(
                    {
                        **_edit_ctx,
                        'form_errors': {'__all__': 'Could not save the contact. Try again.'},
                    }
                )
                messages.error(request, 'Update failed.', extra_tags='tenant')
                return render(request, self.template_name, context)

            messages.success(
                request,
                f'Contact {form_data["name"]} updated.',
                extra_tags='tenant',
            )
            return _redirect_client_contact_list(request)
        finally:
            connection.set_schema_to_public()


class TenantClientContactDeleteView(View):
    def post(self, request, contact_id):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            contact = TenantClientContact.objects.filter(pk=contact_id).first()
            if contact is None:
                messages.error(request, 'Contact not found.', extra_tags='tenant')
                return _redirect_client_contact_list(request)
            label = contact.name
            contact.delete()
            messages.success(request, f'Contact {label} deleted.', extra_tags='tenant')
            return _redirect_client_contact_list(request)
        finally:
            connection.set_schema_to_public()


class TenantClientContactDetailView(View):
    """Read-only full-page contact detail (same UX pattern as Users Administration view mode)."""

    template_name = 'iroad_tenants/Clients_Management/Client-contact-detail.html'

    def get(self, request, contact_id):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            contact = (
                TenantClientContact.objects.select_related('client_account')
                .filter(pk=contact_id)
                .first()
            )
            if contact is None:
                messages.error(request, 'Contact not found.', extra_tags='tenant')
                return _redirect_client_contact_list(request)
            list_url = reverse('iroad_tenants:tenant_client_contacts_list')
            try:
                edit_url = reverse(
                    'iroad_tenants:tenant_client_contact_edit',
                    kwargs={'contact_id': contact.contact_id},
                )
            except NoReverseMatch:
                edit_url = _tenant_client_contact_edit_path(contact.contact_id)
            context.update(
                {
                    'contact': contact,
                    'tenant_schema_name': tenant_registry.schema_name,
                    'back_to_list_url': list_url,
                    'edit_contact_url': edit_url,
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()


class TenantClientContactsListView(View):
    template_name = 'iroad_tenants/Clients_Management/Client-contacts-list.html'

    def get(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            contacts = list(
                TenantClientContact.objects.select_related('client_account').order_by(
                    '-created_at'
                )
            )
            contact_stats = {
                'total': len(contacts),
                'primary': sum(1 for c in contacts if c.is_primary),
                'secondary': sum(1 for c in contacts if not c.is_primary),
                'client_accounts': TenantClientAccount.objects.count(),
            }
            base = _tenant_client_contacts_base_path()
            for contact in contacts:
                try:
                    edit_u = reverse(
                        'iroad_tenants:tenant_client_contact_edit',
                        kwargs={'contact_id': contact.contact_id},
                    )
                except NoReverseMatch:
                    edit_u = f'{base}{contact.contact_id}/edit/'
                contact.list_edit_url = edit_u
                try:
                    del_u = reverse(
                        'iroad_tenants:tenant_client_contact_delete',
                        kwargs={'contact_id': contact.contact_id},
                    )
                except NoReverseMatch:
                    del_u = f'{base}{contact.contact_id}/delete/'
                contact.list_delete_action_url = del_u
                try:
                    detail_u = reverse(
                        'iroad_tenants:tenant_client_contact_detail',
                        kwargs={'contact_id': contact.contact_id},
                    )
                except NoReverseMatch:
                    detail_u = _tenant_client_contact_detail_path(contact.contact_id)
                contact.list_detail_url = detail_u
            context.update(
                {
                    'client_contacts': contacts,
                    'client_contacts_count': len(contacts),
                    'contact_stats': contact_stats,
                    'tenant_schema_name': tenant_registry.schema_name,
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()


class TenantClientContractView(View):
    template_name = 'iroad_tenants/Clients_Management/Client-contract.html'

    def _account_queryset(self):
        return TenantClientAccount.objects.all().order_by('account_no')

    def _default_form_data(self):
        return {
            'contract_no': '',
            'client_account': '',
            'start_date': '',
            'end_date': '',
            'notes': '',
        }

    def _build_create_context(self, request, tenant_registry, form_data, form_errors):
        form_data = dict(form_data)
        if not form_data.get('contract_no'):
            form_data['contract_no'] = _preview_next_contract_code()
        settings_row = _get_singleton_client_contract_settings()
        return {
            'form_data': form_data,
            'form_errors': form_errors,
            'client_account_options': list(self._account_queryset()),
            'tenant_schema_name': tenant_registry.schema_name,
            'contract_form_action': reverse(
                'iroad_tenants:tenant_client_contract_create',
            ),
            'contract_settings': _client_contract_settings_template_dict(settings_row),
        }

    def get(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            form_data = self._default_form_data()
            pre = (request.GET.get('account') or request.GET.get('client') or '').strip()
            try:
                match = resolve(request.path_info)
            except Exception:
                match = None
            # Bare ``/contracts/`` (e.g. old bookmarks): send users to the list; keep ``/create/`` and ``?account=`` for the form.
            if (
                match
                and match.url_name == 'tenant_client_contract'
                and not pre
            ):
                list_url = reverse('iroad_tenants:tenant_client_contract_list')
                return redirect(list_url)
            if pre:
                form_data['client_account'] = pre
            context.update(self._build_create_context(request, tenant_registry, form_data, {}))
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()

    def post(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            form_data = self._default_form_data()
            form_data['client_account'] = (request.POST.get('client_account') or '').strip()
            form_data['start_date'] = (request.POST.get('start_date') or '').strip()
            form_data['end_date'] = (request.POST.get('end_date') or '').strip()
            form_data['notes'] = (request.POST.get('notes') or '').strip()

            form_errors = {}
            account = None
            if not form_data['client_account']:
                form_errors['client_account'] = 'Select a client account.'
            else:
                account = TenantClientAccount.objects.filter(
                    account_no=form_data['client_account'],
                ).first()
                if account is None:
                    form_errors['client_account'] = 'Client account not found.'
                elif hasattr(account, 'contract'):
                    form_errors['client_account'] = 'This client already has a contract.'

            start_date = parse_date(form_data['start_date']) if form_data['start_date'] else None
            end_date = parse_date(form_data['end_date']) if form_data['end_date'] else None
            if not start_date:
                form_errors['start_date'] = 'Enter a valid start date.'
            if not end_date:
                form_errors['end_date'] = 'Enter a valid end date.'
            if start_date and end_date and end_date < start_date:
                form_errors['end_date'] = 'End date must be on or after start date.'

            if start_date and end_date and end_date >= start_date:
                settings_row = _get_singleton_client_contract_settings()
                for key, msg in _validate_client_contract_period_against_settings(
                    start_date, end_date, settings_row
                ).items():
                    form_errors.setdefault(key, msg)

            upload = request.FILES.get('contract_attachment')
            upload_err = _validate_client_contract_upload(upload, allow_empty=False)
            if upload_err:
                form_errors['contract_attachment'] = upload_err

            if form_errors:
                context.update(
                    self._build_create_context(request, tenant_registry, form_data, form_errors)
                )
                messages.error(request, 'Please fix the highlighted errors.', extra_tags='tenant')
                return render(request, self.template_name, context)

            with db_transaction.atomic():
                contract_no, contract_sequence = _next_auto_number_for_form(
                    form_code=CLIENT_CONTRACT_AUTO_FORM_CODE,
                    form_label=CLIENT_CONTRACT_AUTO_FORM_LABEL,
                    prefix=CLIENT_CONTRACT_REF_PREFIX,
                )
                TenantClientContract.objects.create(
                    contract_no=contract_no,
                    contract_sequence=contract_sequence,
                    start_date=start_date,
                    end_date=end_date,
                    status=_contract_status_for_dates(start_date, end_date),
                    notes=form_data['notes'],
                    contract_attachment=upload,
                    created_by_label=(context.get('display_name') or '').strip(),
                    client_account=account,
                )
            messages.success(
                request,
                f'Contract {contract_no} created for {account.account_no}.',
                extra_tags='tenant',
            )
            list_url = reverse('iroad_tenants:tenant_client_contract_list')
            return redirect(list_url)
        except Exception:
            logger.exception('Tenant client contract create failed')
            context.update(
                self._build_create_context(
                    request,
                    tenant_registry,
                    form_data,
                    {'contract_attachment': 'Could not save the contract. Try again.'},
                )
            )
            messages.error(request, 'Contract save failed.', extra_tags='tenant')
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()


class TenantClientContractDetailView(View):
    """Read-only full-page client contract detail."""

    template_name = 'iroad_tenants/Clients_Management/Client-contract-detail.html'

    def get(self, request, contract_id):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            contract = (
                TenantClientContract.objects.select_related('client_account')
                .filter(pk=contract_id)
                .first()
            )
            if contract is None:
                messages.error(request, 'Contract not found.', extra_tags='tenant')
                return _redirect_client_contract_list(request)
            list_url = reverse('iroad_tenants:tenant_client_contract_list')
            try:
                edit_url = reverse(
                    'iroad_tenants:tenant_client_contract_edit',
                    kwargs={'contract_id': contract.contract_id},
                )
            except NoReverseMatch:
                edit_url = _tenant_client_contract_edit_path(contract.contract_id)
            file_url = ''
            try:
                if contract.contract_attachment:
                    file_url = contract.contract_attachment.url
            except Exception:
                file_url = ''
            context.update(
                {
                    'contract': contract,
                    'tenant_schema_name': tenant_registry.schema_name,
                    'back_to_list_url': list_url,
                    'edit_contract_url': edit_url,
                    'contract_file_url': file_url,
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()


class TenantClientContractEditView(View):
    """Edit an existing tenant client contract (same form as create)."""

    template_name = 'iroad_tenants/Clients_Management/Client-contract.html'

    def _account_queryset(self):
        return TenantClientAccount.objects.all().order_by('account_no')

    def _edit_context(
        self,
        request,
        tenant_registry,
        contract,
        form_data,
        form_errors,
    ):
        list_url = reverse('iroad_tenants:tenant_client_contract_list')
        settings_row = _get_singleton_client_contract_settings()
        return {
            'form_data': form_data,
            'form_errors': form_errors,
            'client_account_options': list(self._account_queryset()),
            'tenant_schema_name': tenant_registry.schema_name,
            'back_to_list_url': list_url,
            'contract_form_action': reverse(
                'iroad_tenants:tenant_client_contract_edit',
                kwargs={'contract_id': contract.contract_id},
            ),
            'is_edit_mode': True,
            'editing_contract': contract,
            'contract_settings': _client_contract_settings_template_dict(settings_row),
        }

    def get(self, request, contract_id):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            contract = (
                TenantClientContract.objects.select_related('client_account')
                .filter(pk=contract_id)
                .first()
            )
            if contract is None:
                messages.error(request, 'Contract not found.', extra_tags='tenant')
                return _redirect_client_contract_list(request)
            form_data = {
                'contract_no': contract.contract_no,
                'client_account': contract.client_account.account_no,
                'start_date': contract.start_date.isoformat(),
                'end_date': contract.end_date.isoformat(),
                'notes': contract.notes or '',
            }
            context.update(
                self._edit_context(request, tenant_registry, contract, form_data, {})
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()

    def post(self, request, contract_id):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            contract = (
                TenantClientContract.objects.select_related('client_account')
                .filter(pk=contract_id)
                .first()
            )
            if contract is None:
                messages.error(request, 'Contract not found.', extra_tags='tenant')
                return _redirect_client_contract_list(request)

            form_data = {
                'contract_no': contract.contract_no,
                'client_account': contract.client_account.account_no,
                'start_date': (request.POST.get('start_date') or '').strip(),
                'end_date': (request.POST.get('end_date') or '').strip(),
                'notes': (request.POST.get('notes') or '').strip(),
            }

            form_errors = {}
            start_date = parse_date(form_data['start_date']) if form_data['start_date'] else None
            end_date = parse_date(form_data['end_date']) if form_data['end_date'] else None
            if not start_date:
                form_errors['start_date'] = 'Enter a valid start date.'
            if not end_date:
                form_errors['end_date'] = 'Enter a valid end date.'
            if start_date and end_date and end_date < start_date:
                form_errors['end_date'] = 'End date must be on or after start date.'

            if start_date and end_date and end_date >= start_date:
                settings_row = _get_singleton_client_contract_settings()
                for key, msg in _validate_client_contract_period_against_settings(
                    start_date, end_date, settings_row
                ).items():
                    form_errors.setdefault(key, msg)

            upload = request.FILES.get('contract_attachment')
            allow_empty = contract.has_contract_file
            upload_err = _validate_client_contract_upload(upload, allow_empty=allow_empty)
            if upload_err:
                form_errors['contract_attachment'] = upload_err

            if form_errors:
                context.update(
                    self._edit_context(request, tenant_registry, contract, form_data, form_errors)
                )
                messages.error(request, 'Please fix the highlighted errors.', extra_tags='tenant')
                return render(request, self.template_name, context)

            try:
                with db_transaction.atomic():
                    contract.start_date = start_date
                    contract.end_date = end_date
                    contract.notes = form_data['notes']
                    contract.status = _contract_status_for_dates(start_date, end_date)
                    if upload:
                        if contract.contract_attachment:
                            contract.contract_attachment.delete(save=False)
                        contract.contract_attachment = upload
                    contract.save()
            except Exception:
                logger.exception('Tenant client contract update failed')
                context.update(
                    self._edit_context(
                        request,
                        tenant_registry,
                        contract,
                        form_data,
                        {'contract_attachment': 'Could not save the contract. Try again.'},
                    )
                )
                messages.error(request, 'Update failed.', extra_tags='tenant')
                return render(request, self.template_name, context)

            messages.success(
                request,
                f'Contract {contract.contract_no} updated.',
                extra_tags='tenant',
            )
            return _redirect_client_contract_list(request)
        finally:
            connection.set_schema_to_public()


class TenantClientContractDeleteView(View):
    def post(self, request, contract_id):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            contract = TenantClientContract.objects.filter(pk=contract_id).first()
            if contract is None:
                messages.error(request, 'Contract not found.', extra_tags='tenant')
                return _redirect_client_contract_list(request)
            label = contract.contract_no
            if contract.contract_attachment:
                contract.contract_attachment.delete(save=False)
            contract.delete()
            messages.success(request, f'Contract {label} deleted.', extra_tags='tenant')
            return _redirect_client_contract_list(request)
        finally:
            connection.set_schema_to_public()


class TenantClientContractListView(View):
    template_name = 'iroad_tenants/Clients_Management/Client-contract-list.html'

    def get(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            rows = list(
                TenantClientContract.objects.select_related('client_account').order_by(
                    '-created_at'
                )
            )
            base = _tenant_client_contracts_base_path()
            for row in rows:
                cid = row.contract_id
                try:
                    detail_u = reverse(
                        'iroad_tenants:tenant_client_contract_detail',
                        kwargs={'contract_id': cid},
                    )
                except NoReverseMatch:
                    detail_u = _tenant_client_contract_detail_path(cid)
                row.list_detail_url = detail_u
                try:
                    edit_u = reverse(
                        'iroad_tenants:tenant_client_contract_edit',
                        kwargs={'contract_id': cid},
                    )
                except NoReverseMatch:
                    edit_u = _tenant_client_contract_edit_path(cid)
                row.list_edit_url = edit_u
                try:
                    del_u = reverse(
                        'iroad_tenants:tenant_client_contract_delete',
                        kwargs={'contract_id': cid},
                    )
                except NoReverseMatch:
                    del_u = _tenant_client_contract_delete_path(cid)
                row.list_delete_action_url = del_u
            today = timezone.localdate()
            soon_cutoff = today + timezone.timedelta(days=30)
            contract_stats = {
                'total': len(rows),
                'active': sum(1 for r in rows if r.status == TenantClientContract.Status.ACTIVE),
                'upcoming': sum(
                    1 for r in rows if r.status == TenantClientContract.Status.UPCOMING
                ),
                'expired': sum(1 for r in rows if r.status == TenantClientContract.Status.EXPIRED),
                'expiring_soon': sum(
                    1
                    for r in rows
                    if r.end_date and today <= r.end_date <= soon_cutoff and r.status != 'Expired'
                ),
            }
            context.update(
                {
                    'client_contracts': rows,
                    'client_contracts_count': len(rows),
                    'contract_stats': contract_stats,
                    'tenant_schema_name': tenant_registry.schema_name,
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()


class TenantClientContractSettingsView(View):
    """Load/save client contract notification rules in the current tenant workspace schema."""

    template_name = 'iroad_tenants/Clients_Management/Client-contract-settings.html'

    def get(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            settings_obj = _get_singleton_client_contract_settings()
            context.update(
                {
                    'settings_data': _client_contract_settings_template_dict(settings_obj),
                    'settings_errors': {},
                    'tenant_schema_name': tenant_registry.schema_name,
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()

    def post(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            mode = (request.POST.get('expired_contract_handling_mode') or '').strip()
            grace_raw = (request.POST.get('grace_period_days') or '').strip()
            pre_raw = (request.POST.get('pre_expiry_notification_days') or '').strip()
            post_raw = (request.POST.get('post_expiry_notification_days') or '').strip()
            freq = (request.POST.get('notification_frequency') or '').strip()
            audience = (request.POST.get('notification_audience') or '').strip()

            settings_data = {
                'expired_contract_handling_mode': mode,
                'grace_period_days': grace_raw,
                'pre_expiry_notification_days': pre_raw,
                'post_expiry_notification_days': post_raw,
                'notification_frequency': freq,
                'notification_audience': audience,
            }
            settings_errors = {}

            valid_modes = {c[0] for c in TenantClientContractSetting.ExpiredHandling.choices}
            if mode not in valid_modes:
                settings_errors['expired_contract_handling_mode'] = 'Select a valid handling mode.'

            valid_freq = {c[0] for c in TenantClientContractSetting.NotificationFrequency.choices}
            if freq not in valid_freq:
                settings_errors['notification_frequency'] = 'Select a valid frequency.'

            valid_audience = {c[0] for c in TenantClientContractSetting.NotificationAudience.choices}
            if audience not in valid_audience:
                settings_errors['notification_audience'] = 'Select a valid audience.'

            def _parse_days(raw, field_key, *, required=False, allow_zero=True):
                if raw == '':
                    if required:
                        settings_errors[field_key] = 'Enter a number of days.'
                    return None
                try:
                    v = int(raw)
                except (TypeError, ValueError):
                    settings_errors[field_key] = 'Enter a whole number of days.'
                    return None
                if v < 0:
                    settings_errors[field_key] = 'Days cannot be negative.'
                    return None
                if not allow_zero and v == 0:
                    settings_errors[field_key] = 'Enter a value greater than zero.'
                    return None
                if v > 3660:
                    settings_errors[field_key] = 'Use a value of 3660 days or less.'
                    return None
                return v

            grace_val = _parse_days(grace_raw, 'grace_period_days')
            if pre_raw == '':
                pre_val = 0
            else:
                pre_val = _parse_days(pre_raw, 'pre_expiry_notification_days', allow_zero=True)
            if post_raw == '':
                post_val = 0
            else:
                post_val = _parse_days(post_raw, 'post_expiry_notification_days', allow_zero=True)

            if (
                mode == TenantClientContractSetting.ExpiredHandling.DEACTIVATE_AFTER_GRACE
                and 'grace_period_days' not in settings_errors
            ):
                if grace_val is None and grace_raw == '':
                    settings_errors['grace_period_days'] = (
                        'Enter a grace period when using Deactivate After Grace.'
                    )
                elif grace_val is not None and grace_val < 1:
                    settings_errors['grace_period_days'] = 'Grace period must be at least 1 day.'

            if settings_errors:
                context.update(
                    {
                        'settings_data': settings_data,
                        'settings_errors': settings_errors,
                        'tenant_schema_name': tenant_registry.schema_name,
                    }
                )
                messages.error(
                    request,
                    'Please fix the highlighted errors.',
                    extra_tags='tenant',
                )
                return render(request, self.template_name, context)

            keeper = TenantClientContractSetting.objects.order_by('-updated_at').first()
            prev_grace = keeper.grace_period_days if keeper is not None else 30
            if keeper is None:
                keeper = TenantClientContractSetting.objects.create()
            else:
                TenantClientContractSetting.objects.exclude(pk=keeper.pk).delete()
            settings_obj = keeper
            settings_obj.expired_contract_handling_mode = mode
            # Grace input is disabled unless mode is Deactivate After Grace; omitted POST keeps prev_grace.
            settings_obj.grace_period_days = grace_val if grace_val is not None else prev_grace
            settings_obj.pre_expiry_notification_days = pre_val if pre_val is not None else 0
            settings_obj.post_expiry_notification_days = post_val if post_val is not None else 0
            settings_obj.notification_frequency = freq
            settings_obj.notification_audience = audience
            settings_obj.save()
            messages.success(
                request,
                'Client contract settings saved for this workspace.',
                extra_tags='tenant',
            )
            return _tenant_redirect(request, 'iroad_tenants:tenant_client_contract_settings')
        finally:
            connection.set_schema_to_public()


class TenantClientDetailsView(View):
    """Client overview; loads account + attachments from tenant schema when ``?id=`` matches."""

    template_name = 'iroad_tenants/Clients_Management/client-details.html'

    def get(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            account_no = (request.GET.get('id') or '').strip()
            client_account = None
            client_attachments = []
            client_addresses = []
            client_contacts = []
            primary_contact = None
            client_account_bootstrap = None
            if account_no:
                client_account = TenantClientAccount.objects.filter(
                    account_no=account_no,
                ).first()
                if client_account:
                    client_contacts = list(
                        TenantClientContact.objects.filter(
                            client_account=client_account,
                        ).order_by('-is_primary', '-created_at')
                    )
                    primary_contact = next(
                        (c for c in client_contacts if c.is_primary),
                        None,
                    )
                    client_account_bootstrap = _client_account_bootstrap_dict(
                        client_account,
                        primary_contact,
                    )
                    client_attachments = list(
                        TenantClientAttachment.objects.filter(
                            client_account=client_account,
                        ).order_by('-attachment_date', '-created_at')
                    )
                    client_addresses = list(
                        TenantAddressMaster.objects.filter(
                            client_account=client_account,
                        )
                        .select_related('country', 'client_account')
                        .order_by('-created_at')
                    )
                    for addr in client_addresses:
                        try:
                            edit_u = reverse(
                                'iroad_tenants:tenant_address_master_edit',
                                kwargs={'address_id': addr.address_id},
                            )
                        except NoReverseMatch:
                            edit_u = (
                                f'/master-data/addresses/{addr.address_id}/edit/'
                            )
                        addr.list_edit_url = edit_u
            client_cargo_masters = []
            client_contract = None
            if client_account:
                client_cargo_masters = list(
                    TenantCargoMaster.objects.filter(client_account=client_account)
                    .select_related('cargo_category')
                    .order_by('-created_at')
                )
                for c in client_cargo_masters:
                    try:
                        edit_u = reverse(
                            'iroad_tenants:tenant_cargo_master_edit',
                            kwargs={'cargo_id': c.cargo_id},
                        )
                    except NoReverseMatch:
                        edit_u = f'/master-data/cargo/{c.cargo_id}/edit/'
                    c.list_edit_url = edit_u
                    try:
                        detail_u = reverse(
                            'iroad_tenants:tenant_cargo_master_detail',
                            kwargs={'cargo_id': c.cargo_id},
                        )
                    except NoReverseMatch:
                        detail_u = f'/master-data/cargo/{c.cargo_id}/'
                    c.list_detail_url = detail_u
                    try:
                        del_u = reverse(
                            'iroad_tenants:tenant_cargo_master_delete',
                            kwargs={'cargo_id': c.cargo_id},
                        )
                    except NoReverseMatch:
                        del_u = f'/master-data/cargo/{c.cargo_id}/delete/'
                    c.list_delete_action_url = del_u
                client_contract = (
                    TenantClientContract.objects.filter(client_account=client_account)
                    .select_related('client_account')
                    .first()
                )
                if client_contract:
                    cid = client_contract.contract_id
                    try:
                        detail_u = reverse(
                            'iroad_tenants:tenant_client_contract_detail',
                            kwargs={'contract_id': cid},
                        )
                    except NoReverseMatch:
                        detail_u = _tenant_client_contract_detail_path(cid)
                    client_contract.list_detail_url = detail_u
                    try:
                        edit_u = reverse(
                            'iroad_tenants:tenant_client_contract_edit',
                            kwargs={'contract_id': cid},
                        )
                    except NoReverseMatch:
                        edit_u = _tenant_client_contract_edit_path(cid)
                    client_contract.list_edit_url = edit_u
                    try:
                        del_u = reverse(
                            'iroad_tenants:tenant_client_contract_delete',
                            kwargs={'contract_id': cid},
                        )
                    except NoReverseMatch:
                        del_u = _tenant_client_contract_delete_path(cid)
                    client_contract.list_delete_action_url = del_u
            client_lookup_failed = bool(account_no) and client_account is None
            context.update(
                {
                    'tenant_schema_name': tenant_registry.schema_name,
                    'client_account': client_account,
                    'client_attachments': client_attachments,
                    'client_contacts': client_contacts,
                    'primary_contact': primary_contact if client_account else None,
                    'client_account_bootstrap': client_account_bootstrap,
                    'client_lookup_failed': client_lookup_failed,
                    'client_contract': client_contract,
                    'client_addresses': client_addresses,
                    'client_cargo_masters': client_cargo_masters,
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()


def _tenant_address_master_access(request, context):
    if context is None:
        response = redirect('login')
        clear_tenant_portal_cookie(response, request=request)
        return response
    if not context.get('is_tenant_admin'):
        messages.error(
            request,
            'You do not have access to Address Master.',
            extra_tags='tenant',
        )
        return _tenant_redirect(request, 'iroad_tenants:tenant_dashboard')
    return None


def _tenant_truck_type_master_access(request, context):
    if context is None:
        response = redirect('login')
        clear_tenant_portal_cookie(response, request=request)
        return response
    if not context.get('is_tenant_admin'):
        messages.error(
            request,
            'You do not have access to Truck Type Master.',
            extra_tags='tenant',
        )
        return _tenant_redirect(request, 'iroad_tenants:tenant_dashboard')
    return None


def _tenant_truck_master_access(request, context):
    """Same gate as Address Master — tenant admins only."""
    if context is None:
        response = redirect('login')
        clear_tenant_portal_cookie(response, request=request)
        return response
    if not context.get('is_tenant_admin'):
        messages.error(
            request,
            'You do not have access to Truck Master.',
            extra_tags='tenant',
        )
        return _tenant_redirect(request, 'iroad_tenants:tenant_dashboard')
    return None


def _tenant_driver_master_access(request, context):
    """Same pattern as _tenant_truck_master_access."""
    if context is None:
        response = redirect('login')
        clear_tenant_portal_cookie(response, request=request)
        return response
    if not context.get('is_tenant_admin'):
        messages.error(
            request,
            'You do not have access to Driver Master.',
            extra_tags='tenant',
        )
        return _tenant_redirect(request, 'iroad_tenants:tenant_dashboard')
    return None


def _tenant_sales_invoice_report_access(request, context):
    if context is None:
        response = redirect('login')
        clear_tenant_portal_cookie(response, request=request)
        return response
    if not (context.get('is_tenant_admin') or context.get('can_view_sales_invoicing')):
        messages.error(
            request,
            'You do not have access to Sales Invoice Report.',
            extra_tags='tenant',
        )
        return _tenant_redirect(request, 'iroad_tenants:tenant_dashboard')
    return None


def _active_truck_master_count_for_type(truck_type_pk):
    """Active ``TruckMaster`` rows using this truck type (0 if model/FK not present yet)."""
    from django.apps import apps
    from django.core.exceptions import FieldDoesNotExist

    try:
        TM = apps.get_model('tenant_workspace', 'TruckMaster')
    except LookupError:
        return 0
    try:
        TM._meta.get_field('truck_type')
    except FieldDoesNotExist:
        return 0
    qs = TM.objects.filter(truck_type_id=truck_type_pk)
    status_cls = getattr(TM, 'Status', None)
    if status_cls is not None:
        qs = qs.filter(status=status_cls.ACTIVE)
    else:
        qs = qs.filter(status='Active')
    return qs.count()


def _format_digits_display(value: str) -> str:
    """Group digits for list display (+966 50 123 4567 style, simplified)."""
    d = ''.join(ch for ch in (value or '') if ch.isdigit())
    if not d:
        return '—'
    if len(d) <= 12:
        return ' '.join(d[i : i + 3] for i in range(0, len(d), 3))
    return d


def _hydrate_address_master_list_rows(addresses_page):
    """Annotate pagination rows for list UI (Country master + display strings)."""
    rows = list(addresses_page.object_list)
    codes = {getattr(r, 'country_id', None) for r in rows}
    codes.discard(None)
    cmap = {}
    if codes:
        with schema_context('public'):
            for c in Country.objects.filter(pk__in=codes):
                cmap[c.country_code] = {
                    'label': f'{c.country_code} — {c.name_en}',
                    'code': c.country_code,
                    'name_en': (c.name_en or '').strip(),
                }

    for row in rows:
        cid = getattr(row, 'country_id', None)
        if cid and cid in cmap:
            info = cmap[cid]
            city = (row.city or '').strip()
            name_en = info['name_en']
            setattr(row, 'country_display_label', info['label'])
            setattr(row, 'country_code_short', info['code'])
            setattr(
                row,
                'city_country_cell',
                f'{city} / {name_en}' if city else f'— / {name_en}',
            )
        else:
            setattr(row, 'country_display_label', '—')
            setattr(row, 'country_code_short', '—')
            setattr(row, 'city_country_cell', '—')
        setattr(row, 'phone_display_cell', _format_digits_display(row.mobile_no_1))


def _hydrate_truck_master_list_rows(trucks_page):
    """Registration country display for Truck Master list (FK stores ``country_code``)."""
    rows = list(trucks_page.object_list)
    codes = set()
    for r in rows:
        cc = getattr(r, 'registration_country_id', None)
        if cc:
            codes.add(str(cc).strip().upper())
    labels = {}
    if codes:
        with schema_context('public'):
            for c in Country.objects.filter(country_code__in=list(codes)):
                labels[c.country_code.upper()] = f'{c.country_code} — {c.name_en}'
    for row in rows:
        cc = getattr(row, 'registration_country_id', None)
        ccu = str(cc).strip().upper() if cc else ''
        setattr(row, 'registration_country_cell', labels.get(ccu, ccu if ccu else '—'))


def _address_master_list_stats(filtered_qs):
    addr = TenantAddressMaster
    return {
        'total': filtered_qs.count(),
        'pickup_only': filtered_qs.filter(
            address_category=addr.AddressCategory.PICKUP_ADDRESS
        ).count(),
        'delivery_only': filtered_qs.filter(
            address_category=addr.AddressCategory.DELIVERY_ADDRESS
        ).count(),
        'both': filtered_qs.filter(address_category=addr.AddressCategory.BOTH).count(),
    }


class TenantAddressMasterListView(View):
    """AD-001 list with search/filter and deactivate (inactive) via POST."""

    template_name = 'iroad_tenants/Master_Data/address_master_list.html'

    def get(self, request):
        context = _tenant_context_from_session(request)
        denied = _tenant_address_master_access(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        qs = TenantAddressMaster.objects.select_related('client_account')
        sq = request.GET.get('q', '').strip()
        cid = request.GET.get('client', '').strip()
        filter_client_id = ''

        # AD-001: default list = Active only; All / Inactive only when explicitly requested.
        status_raw = (request.GET.get('status') or '').strip().lower()
        if 'status' not in request.GET:
            qs = qs.filter(status=TenantAddressMaster.Status.ACTIVE)
            filter_status = ''
        elif not status_raw:
            qs = qs.filter(status=TenantAddressMaster.Status.ACTIVE)
            filter_status = 'active'
        elif status_raw == 'all':
            filter_status = 'all'
        elif status_raw == 'inactive':
            qs = qs.filter(status=TenantAddressMaster.Status.INACTIVE)
            filter_status = 'inactive'
        elif status_raw == 'active':
            qs = qs.filter(status=TenantAddressMaster.Status.ACTIVE)
            filter_status = 'active'
        else:
            qs = qs.filter(status=TenantAddressMaster.Status.ACTIVE)
            filter_status = 'active'

        if sq:
            qs = qs.filter(
                Q(display_name__icontains=sq)
                | Q(address_code__icontains=sq)
                | Q(city__icontains=sq)
                | Q(client_account__display_name__icontains=sq)
            )
        if cid:
            try:
                cid_uuid = uuid.UUID(cid)
                qs = qs.filter(client_account_id=cid_uuid)
                filter_client_id = str(cid_uuid)
            except ValueError:
                filter_client_id = ''

        sort_key_raw = (request.GET.get('sort') or 'truck_code').strip().lower()
        sort_dir_raw = (request.GET.get('dir') or 'asc').strip().lower()
        sort_map = {
            'truck_code': 'truck_code',
            'owner_name': 'owner_name',
            'default_driver_id': 'default_driver_id',
            'sourcing_mode': 'sourcing_mode',
            'truck_type': 'truck_type__english_label',
            'plate_number': 'plate_number',
            'status': 'status',
        }
        sort_key = sort_map.get(sort_key_raw, 'truck_code')
        sort_dir = 'desc' if sort_dir_raw == 'desc' else 'asc'
        order_expr = f'-{sort_key}' if sort_dir == 'desc' else sort_key

        qs_ordered = qs.order_by(order_expr, '-created_at')
        stats = _address_master_list_stats(qs_ordered)
        paginator = Paginator(qs_ordered, 10)
        try:
            page_no = max(1, int(request.GET.get('page') or 1))
        except ValueError:
            page_no = 1
        page = paginator.get_page(page_no)
        _hydrate_address_master_list_rows(page)

        total_count = paginator.count
        if total_count == 0:
            ps, pe = 0, 0
        else:
            ps = (page.number - 1) * paginator.per_page + 1
            pe = ps + len(page.object_list) - 1

        def _page_url(page_num):
            q = request.GET.copy()
            q.pop('stype', None)
            try:
                pn = int(page_num)
            except (TypeError, ValueError):
                pn = 1
            if pn > 1:
                q['page'] = str(pn)
            else:
                q.pop('page', None)
            return '?' + q.urlencode()

        def _sort_url(col_key):
            q = request.GET.copy()
            q.pop('stype', None)
            current_key = sort_key_raw if sort_key_raw in sort_map else 'truck_code'
            current_dir = sort_dir
            if col_key == current_key:
                next_dir = 'desc' if current_dir == 'asc' else 'asc'
            else:
                next_dir = 'asc'
            q['sort'] = col_key
            q['dir'] = next_dir
            q.pop('page', None)
            return '?' + q.urlencode()

        def _sort_url(col_key):
            q = request.GET.copy()
            q.pop('stype', None)
            current_key = sort_key_raw if sort_key_raw in sort_map else 'truck_code'
            current_dir = sort_dir
            if col_key == current_key:
                next_dir = 'desc' if current_dir == 'asc' else 'asc'
            else:
                next_dir = 'asc'
            q['sort'] = col_key
            q['dir'] = next_dir
            q.pop('page', None)
            return '?' + q.urlencode()

        pagination_page_links = [(n, _page_url(n)) for n in page.paginator.page_range]
        prev_url = _page_url(page.previous_page_number()) if page.has_previous() else None
        next_url = _page_url(page.next_page_number()) if page.has_next() else None

        clients = list(
            TenantClientAccount.objects.filter(
                status=TenantClientAccount.Status.ACTIVE,
            ).order_by('display_name')[:500]
        )

        context.update(
            {
                'addresses_page': page,
                'search_q': sq,
                'filter_status': filter_status,
                'filter_client_id': filter_client_id,
                'pagination_page_links': pagination_page_links,
                'pagination_prev_url': prev_url,
                'pagination_next_url': next_url,
                'stats': stats,
                'pagination_start': ps,
                'pagination_end': pe,
                'pagination_total': total_count,
                'client_filter_choices': clients,
                'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
            }
        )
        try:
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()

    def post(self, request):
        """Set status Active / Inactivate (PCS: keep row, no delete)."""

        context = _tenant_context_from_session(request)
        denied = _tenant_address_master_access(request, context)
        if denied:
            return denied

        if request.POST.get('action') != 'set_status':
            return self.get(request)

        address_id = (request.POST.get('address_id') or '').strip()
        new_status = (request.POST.get('new_status') or '').strip()
        if new_status not in (
            TenantAddressMaster.Status.ACTIVE,
            TenantAddressMaster.Status.INACTIVE,
        ):
            messages.error(request, 'Invalid status.', extra_tags='tenant')
            rq = (request.POST.get('redirect_query') or '').strip()
            base = reverse('iroad_tenants:tenant_address_master')
            if rq:
                return redirect(f'{base}?{rq}')
            return _tenant_redirect(request, 'iroad_tenants:tenant_address_master')

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        try:
            addr = TenantAddressMaster.objects.filter(pk=address_id).first()
            if not addr:
                messages.error(request, 'Address not found.', extra_tags='tenant')
            else:
                addr.status = new_status
                addr.save(update_fields=['status', 'updated_at'])
                messages.success(request, f'Address set to {new_status.lower()}.', extra_tags='tenant')
        finally:
            connection.set_schema_to_public()

        rq = (request.POST.get('redirect_query') or '').strip()
        base = reverse('iroad_tenants:tenant_address_master')
        if rq:
            return redirect(f'{base}?{rq}')
        return _tenant_redirect(request, 'iroad_tenants:tenant_address_master')


class TenantAddressMasterCreateView(View):

    template_name = 'iroad_tenants/Master_Data/address_master_form.html'

    def get(self, request):
        context = _tenant_context_from_session(request)
        denied = _tenant_address_master_access(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        try:
            preview = _preview_next_address_master_code()

            initial = {}
            cid = (request.GET.get('client') or '').strip()
            if cid:
                try:
                    initial['client_account'] = uuid.UUID(cid)
                except ValueError:
                    pass

            form = TenantAddressMasterForm(
                initial=initial,
            )
            context.update(
                {
                    'form': form,
                    'preview_address_code': preview,
                    'is_edit': False,
                    'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()

    def post(self, request):
        context = _tenant_context_from_session(request)
        denied = _tenant_address_master_access(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        redirect_resp = None
        try:
            logger.info(
                'Address Master create POST: keys=%s',
                sorted(request.POST.keys()),
            )
            form = TenantAddressMasterForm(
                request.POST,
            )

            if not form.is_valid():
                logger.warning(
                    'Address Master create validation failed errors=%s',
                    form.errors.as_json(),
                )
                preview = _preview_next_address_master_code()
                context.update(
                    {
                        'form': form,
                        'preview_address_code': preview,
                        'is_edit': False,
                        'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                    }
                )
                messages.error(request, 'Please fix the highlighted errors.', extra_tags='tenant')
                return render(request, self.template_name, context)

            try:
                with db_transaction.atomic():
                    addr_code, addr_seq = _next_auto_number_for_form(
                        ADDRESS_MASTER_AUTO_FORM_CODE,
                        ADDRESS_MASTER_AUTO_FORM_LABEL,
                        ADDRESS_MASTER_REF_PREFIX,
                    )
                    addr = form.save(commit=False)
                    addr.address_code = addr_code
                    addr.address_sequence = addr_seq
                    addr.save()
            except IntegrityError:
                logger.exception('Address Master create integrity violation')
                preview = _preview_next_address_master_code()
                form.add_error(
                    None,
                    ValidationError(
                        'Unable to allocate a unique address code. Please retry.',
                        code='address_integrity',
                    ),
                )
                context.update(
                    {
                        'form': form,
                        'preview_address_code': preview,
                        'is_edit': False,
                        'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                    }
                )
                messages.error(request, 'Could not save the address.', extra_tags='tenant')
                return render(request, self.template_name, context)
            except ValidationError as ve:
                logger.warning('Address Master create raised ValidationError: %s', ve)
                preview = _preview_next_address_master_code()
                if getattr(ve, 'error_dict', None):
                    for field_name, errs in ve.error_dict.items():
                        for err in errs:
                            form.add_error(field_name, err)
                else:
                    for msg in getattr(ve, 'messages', []) or [str(ve)]:
                        form.add_error(None, msg)
                context.update(
                    {
                        'form': form,
                        'preview_address_code': preview,
                        'is_edit': False,
                        'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                    }
                )
                messages.error(request, 'Could not save the address.', extra_tags='tenant')
                return render(request, self.template_name, context)
            except Exception:
                logger.exception('Address Master create save failed')
                preview = _preview_next_address_master_code()
                form.add_error(
                    None,
                    ValidationError(
                        'Saving failed unexpectedly. Try again or contact support.',
                        code='address_save_failed',
                    ),
                )
                context.update(
                    {
                        'form': form,
                        'preview_address_code': preview,
                        'is_edit': False,
                        'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                    }
                )
                messages.error(request, 'Could not save the address.', extra_tags='tenant')
                return render(request, self.template_name, context)

            messages.success(
                request,
                f'Address {addr.address_code} created successfully.',
                extra_tags='tenant',
            )

            redirect_resp = _tenant_redirect(request, 'iroad_tenants:tenant_address_master')
        finally:
            connection.set_schema_to_public()

        return redirect_resp


class TenantAddressMasterEditView(View):

    template_name = 'iroad_tenants/Master_Data/address_master_form.html'

    def _load(self, address_id):
        return TenantAddressMaster.objects.select_related('client_account').filter(
            pk=address_id
        ).first()

    def get(self, request, address_id):
        context = _tenant_context_from_session(request)
        denied = _tenant_address_master_access(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        try:
            instance = self._load(address_id)
            if not instance:
                messages.error(request, 'Address not found.', extra_tags='tenant')
                return _tenant_redirect(request, 'iroad_tenants:tenant_address_master')

            form = TenantAddressMasterForm(
                instance=instance,
            )

            context.update(
                {
                    'form': form,
                    'is_edit': True,
                    'address_record': instance,
                    'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()

    def post(self, request, address_id):
        context = _tenant_context_from_session(request)
        denied = _tenant_address_master_access(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        redirect_resp = None
        try:
            instance = self._load(address_id)
            if not instance:
                messages.error(request, 'Address not found.', extra_tags='tenant')
                return _tenant_redirect(request, 'iroad_tenants:tenant_address_master')

            logger.info(
                'Address Master edit POST address_id=%s keys=%s',
                address_id,
                sorted(request.POST.keys()),
            )
            form = TenantAddressMasterForm(
                request.POST,
                instance=instance,
            )

            if not form.is_valid():
                logger.warning(
                    'Address Master edit validation failed address_id=%s errors=%s',
                    address_id,
                    form.errors.as_json(),
                )
                context.update(
                    {
                        'form': form,
                        'is_edit': True,
                        'address_record': instance,
                        'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                    }
                )
                messages.error(request, 'Please fix the highlighted errors.', extra_tags='tenant')
                return render(request, self.template_name, context)

            try:
                with db_transaction.atomic():
                    form.save()
            except IntegrityError:
                logger.exception('Address Master edit integrity violation address_id=%s', address_id)
                form.add_error(
                    None,
                    ValidationError(
                        'Conflict while saving this address.',
                        code='address_integrity',
                    ),
                )
                context.update(
                    {
                        'form': form,
                        'is_edit': True,
                        'address_record': instance,
                        'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                    }
                )
                messages.error(request, 'Could not save changes.', extra_tags='tenant')
                return render(request, self.template_name, context)
            except ValidationError as ve:
                logger.warning('Address Master edit ValidationError address_id=%s detail=%s', address_id, ve)
                if getattr(ve, 'error_dict', None):
                    for field_name, errs in ve.error_dict.items():
                        for err in errs:
                            form.add_error(field_name, err)
                else:
                    for msg in getattr(ve, 'messages', []) or [str(ve)]:
                        form.add_error(None, msg)
                context.update(
                    {
                        'form': form,
                        'is_edit': True,
                        'address_record': instance,
                        'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                    }
                )
                messages.error(request, 'Could not save changes.', extra_tags='tenant')
                return render(request, self.template_name, context)
            except Exception:
                logger.exception('Address Master edit save failed address_id=%s', address_id)
                form.add_error(
                    None,
                    ValidationError(
                        'Saving failed unexpectedly. Try again or contact support.',
                        code='address_save_failed',
                    ),
                )
                context.update(
                    {
                        'form': form,
                        'is_edit': True,
                        'address_record': instance,
                        'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                    }
                )
                messages.error(request, 'Could not save changes.', extra_tags='tenant')
                return render(request, self.template_name, context)

            messages.success(request, 'Address updated successfully.', extra_tags='tenant')
            redirect_resp = _tenant_redirect(request, 'iroad_tenants:tenant_address_master')
        finally:
            connection.set_schema_to_public()

        return redirect_resp


class TenantAddressMasterDetailView(View):
    """AD-001 detail page (read-only), accessed from list action menu."""

    template_name = 'iroad_tenants/Master_Data/address_master_detail.html'

    def get(self, request, address_id):
        context = _tenant_context_from_session(request)
        denied = _tenant_address_master_access(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        try:
            address = (
                TenantAddressMaster.objects.select_related('client_account')
                .filter(pk=address_id)
                .first()
            )
            if not address:
                messages.error(request, 'Address not found.', extra_tags='tenant')
                return _tenant_redirect(request, 'iroad_tenants:tenant_address_master')

            country_display = '—'
            country_code = (getattr(address, 'country_id', '') or '').strip()
            if country_code:
                with schema_context('public'):
                    country = Country.objects.filter(country_code=country_code).first()
                    if country:
                        country_display = f'{country.name_en} ({country.country_code})'

            context.update(
                {
                    'address': address,
                    'country_display': country_display,
                    'city_country_display': f'{(address.city or "").strip()} / {country_display}'
                    if (address.city or '').strip()
                    else f'— / {country_display}',
                    'mobile_no_1_display': _format_digits_display(address.mobile_no_1),
                    'mobile_no_2_display': _format_digits_display(address.mobile_no_2),
                    'phone_no_display': _format_digits_display(address.phone_no),
                    'whatsapp_no_display': _format_digits_display(address.whatsapp_no),
                    'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()


class TruckTypeMasterListView(View):
    """TRT-001 list with search, status filter, pagination, and status toggle via POST."""

    template_name = 'iroad_tenants/fleet/truck_type/truck_type_list.html'

    def get(self, request):
        context = _tenant_context_from_session(request)
        denied = _tenant_truck_type_master_access(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        tt = TruckTypeMaster
        qs = tt.objects.all()
        sq = request.GET.get('q', '').strip()

        status_raw = (request.GET.get('status') or '').strip().lower()
        if 'status' not in request.GET:
            qs = qs.filter(status=tt.Status.ACTIVE)
            filter_status = ''
        elif not status_raw:
            qs = qs.filter(status=tt.Status.ACTIVE)
            filter_status = 'active'
        elif status_raw == 'all':
            filter_status = 'all'
        elif status_raw == 'inactive':
            qs = qs.filter(status=tt.Status.INACTIVE)
            filter_status = 'inactive'
        elif status_raw == 'active':
            qs = qs.filter(status=tt.Status.ACTIVE)
            filter_status = 'active'
        else:
            qs = qs.filter(status=tt.Status.ACTIVE)
            filter_status = 'active'

        if sq:
            qs = qs.filter(
                Q(english_label__icontains=sq) | Q(arabic_label__icontains=sq)
            )

        sort_key_raw = (request.GET.get('sort') or 'english_label').strip().lower()
        sort_dir_raw = (request.GET.get('dir') or 'asc').strip().lower()
        sort_map = {
            'truck_type_code': 'truck_type_code',
            'english_label': 'english_label',
            'arabic_label': 'arabic_label',
            'status': 'status',
        }
        sort_key = sort_map.get(sort_key_raw, 'english_label')
        sort_dir = 'desc' if sort_dir_raw == 'desc' else 'asc'
        order_expr = f'-{sort_key}' if sort_dir == 'desc' else sort_key

        qs_ordered = qs.order_by(order_expr)
        stats = {
            'total_count': tt.objects.count(),
            'active_count': tt.objects.filter(status=tt.Status.ACTIVE).count(),
            'inactive_count': tt.objects.filter(status=tt.Status.INACTIVE).count(),
        }
        paginator = Paginator(qs_ordered, 10)
        try:
            page_no = max(1, int(request.GET.get('page') or 1))
        except ValueError:
            page_no = 1
        page = paginator.get_page(page_no)

        total_count = paginator.count
        if total_count == 0:
            ps, pe = 0, 0
        else:
            ps = (page.number - 1) * paginator.per_page + 1
            pe = ps + len(page.object_list) - 1

        def _page_url(page_num):
            q = request.GET.copy()
            q.pop('stype', None)
            try:
                pn = int(page_num)
            except (TypeError, ValueError):
                pn = 1
            if pn > 1:
                q['page'] = str(pn)
            else:
                q.pop('page', None)
            return '?' + q.urlencode()

        def _sort_url(col_key):
            q = request.GET.copy()
            q.pop('stype', None)
            current_key = sort_key
            current_dir = sort_dir
            if col_key == current_key:
                next_dir = 'desc' if current_dir == 'asc' else 'asc'
            else:
                next_dir = 'asc'
            q['sort'] = col_key
            q['dir'] = next_dir
            q.pop('page', None)
            return '?' + q.urlencode()

        pagination_page_links = [(n, _page_url(n)) for n in page.paginator.page_range]
        prev_url = _page_url(page.previous_page_number()) if page.has_previous() else None
        next_url = _page_url(page.next_page_number()) if page.has_next() else None

        context.update(
            {
                'truck_types_page': page,
                'search_q': sq,
                'filter_status': filter_status,
                'stats': stats,
                'pagination_page_links': pagination_page_links,
                'pagination_prev_url': prev_url,
                'pagination_next_url': next_url,
                'pagination_start': ps,
                'pagination_end': pe,
                'pagination_total': total_count,
                'sort_key': sort_key,
                'sort_dir': sort_dir,
                'sort_url_truck_type_code': _sort_url('truck_type_code'),
                'sort_url_english_label': _sort_url('english_label'),
                'sort_url_arabic_label': _sort_url('arabic_label'),
                'sort_url_status': _sort_url('status'),
                'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
            }
        )
        try:
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()

    def post(self, request):
        context = _tenant_context_from_session(request)
        denied = _tenant_truck_type_master_access(request, context)
        if denied:
            return denied

        if request.POST.get('action') != 'set_status':
            return self.get(request)

        truck_type_id = (request.POST.get('truck_type_id') or '').strip()
        new_status = (request.POST.get('new_status') or '').strip()
        if new_status not in (TruckTypeMaster.Status.ACTIVE, TruckTypeMaster.Status.INACTIVE):
            messages.error(request, 'Invalid status.', extra_tags='tenant')
            rq = (request.POST.get('redirect_query') or '').strip()
            base = reverse('iroad_tenants:truck_type_master')
            if rq:
                return redirect(f'{base}?{rq}')
            return _tenant_redirect(request, 'iroad_tenants:truck_type_master')

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        try:
            row = TruckTypeMaster.objects.filter(pk=truck_type_id).first()
            if not row:
                messages.error(request, 'Truck type not found.', extra_tags='tenant')
            elif new_status == TruckTypeMaster.Status.INACTIVE:
                in_use = _active_truck_master_count_for_type(row.truck_type_id)
                if in_use:
                    messages.error(
                        request,
                        f'Cannot deactivate — {in_use} trucks are using this type',
                        extra_tags='tenant',
                    )
                else:
                    row.status = new_status
                    row.save(update_fields=['status', 'updated_at'])
                    messages.success(
                        request,
                        f'Truck type set to {new_status.lower()}.',
                        extra_tags='tenant',
                    )
            else:
                row.status = new_status
                row.save(update_fields=['status', 'updated_at'])
                messages.success(
                    request,
                    f'Truck type set to {new_status.lower()}.',
                    extra_tags='tenant',
                )
        finally:
            connection.set_schema_to_public()

        rq = (request.POST.get('redirect_query') or '').strip()
        base = reverse('iroad_tenants:truck_type_master')
        if rq:
            return redirect(f'{base}?{rq}')
        return _tenant_redirect(request, 'iroad_tenants:truck_type_master')


class TruckTypeMasterCreateView(View):
    template_name = 'iroad_tenants/fleet/truck_type/truck_type_form.html'

    def get(self, request):
        context = _tenant_context_from_session(request)
        denied = _tenant_truck_type_master_access(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        try:
            preview = _preview_next_auto_number_for_form(
                form_code='truck-type-master',
                form_label='Truck Type Master',
                prefix='TRT',
            )
            form = TruckTypeMasterForm()
            context.update(
                {
                    'form': form,
                    'preview_truck_type_code': preview,
                    'is_edit': False,
                    'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()

    def post(self, request):
        context = _tenant_context_from_session(request)
        denied = _tenant_truck_type_master_access(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        redirect_resp = None
        try:
            form = TruckTypeMasterForm(request.POST)
            if not form.is_valid():
                preview = _preview_next_auto_number_for_form(
                    form_code='truck-type-master',
                    form_label='Truck Type Master',
                    prefix='TRT',
                )
                context.update(
                    {
                        'form': form,
                        'preview_truck_type_code': preview,
                        'is_edit': False,
                        'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                    }
                )
                messages.error(request, 'Please fix the highlighted errors.', extra_tags='tenant')
                return render(request, self.template_name, context)

            try:
                with db_transaction.atomic():
                    code, seq = _next_auto_number_for_form(
                        form_code='truck-type-master',
                        form_label='Truck Type Master',
                        prefix='TRT',
                    )
                    obj = form.save(commit=False)
                    obj.truck_type_code = code
                    obj.truck_type_sequence = seq
                    obj.full_clean()
                    obj.save()
            except IntegrityError:
                preview = _preview_next_auto_number_for_form(
                    form_code='truck-type-master',
                    form_label='Truck Type Master',
                    prefix='TRT',
                )
                form.add_error(
                    None,
                    ValidationError(
                        'Unable to allocate a unique truck type code. Please retry.',
                        code='truck_type_integrity',
                    ),
                )
                context.update(
                    {
                        'form': form,
                        'preview_truck_type_code': preview,
                        'is_edit': False,
                        'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                    }
                )
                messages.error(request, 'Could not save the truck type.', extra_tags='tenant')
                return render(request, self.template_name, context)
            except ValidationError as ve:
                preview = _preview_next_auto_number_for_form(
                    form_code='truck-type-master',
                    form_label='Truck Type Master',
                    prefix='TRT',
                )
                if getattr(ve, 'error_dict', None):
                    for field_name, errs in ve.error_dict.items():
                        for err in errs:
                            form.add_error(field_name, err)
                else:
                    for msg in getattr(ve, 'messages', []) or [str(ve)]:
                        form.add_error(None, msg)
                context.update(
                    {
                        'form': form,
                        'preview_truck_type_code': preview,
                        'is_edit': False,
                        'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                    }
                )
                messages.error(request, 'Could not save the truck type.', extra_tags='tenant')
                return render(request, self.template_name, context)

            messages.success(
                request,
                f'Truck type {obj.truck_type_code} created successfully.',
                extra_tags='tenant',
            )
            redirect_resp = _tenant_redirect(request, 'iroad_tenants:truck_type_master')
        finally:
            connection.set_schema_to_public()

        return redirect_resp


class TruckTypeMasterEditView(View):
    template_name = 'iroad_tenants/fleet/truck_type/truck_type_form.html'

    def _load(self, truck_type_id):
        return TruckTypeMaster.objects.filter(pk=truck_type_id).first()

    def get(self, request, truck_type_id):
        context = _tenant_context_from_session(request)
        denied = _tenant_truck_type_master_access(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        try:
            instance = self._load(truck_type_id)
            if not instance:
                messages.error(request, 'Truck type not found.', extra_tags='tenant')
                return _tenant_redirect(request, 'iroad_tenants:truck_type_master')

            form = TruckTypeMasterForm(instance=instance)
            context.update(
                {
                    'form': form,
                    'is_edit': True,
                    'truck_type_record': instance,
                    'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()

    def post(self, request, truck_type_id):
        context = _tenant_context_from_session(request)
        denied = _tenant_truck_type_master_access(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        redirect_resp = None
        try:
            instance = self._load(truck_type_id)
            if not instance:
                messages.error(request, 'Truck type not found.', extra_tags='tenant')
                return _tenant_redirect(request, 'iroad_tenants:truck_type_master')

            immutable_code = instance.truck_type_code
            form = TruckTypeMasterForm(request.POST, instance=instance)
            if not form.is_valid():
                context.update(
                    {
                        'form': form,
                        'is_edit': True,
                        'truck_type_record': instance,
                        'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                    }
                )
                messages.error(request, 'Please fix the highlighted errors.', extra_tags='tenant')
                return render(request, self.template_name, context)

            if form.cleaned_data.get('status') == TruckTypeMaster.Status.INACTIVE:
                in_use = _active_truck_master_count_for_type(instance.truck_type_id)
                if in_use:
                    form.add_error(
                        'status',
                        ValidationError(
                            f'Cannot deactivate — {in_use} trucks are using this type'
                        ),
                    )
                    context.update(
                        {
                            'form': form,
                            'is_edit': True,
                            'truck_type_record': instance,
                            'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                        }
                    )
                    messages.error(
                        request,
                        f'Cannot deactivate — {in_use} trucks are using this type',
                        extra_tags='tenant',
                    )
                    return render(request, self.template_name, context)

            try:
                with db_transaction.atomic():
                    obj = form.save(commit=False)
                    obj.truck_type_code = immutable_code
                    obj.full_clean()
                    obj.save()
            except IntegrityError:
                form.add_error(
                    None,
                    ValidationError(
                        'Conflict while saving this truck type.',
                        code='truck_type_integrity',
                    ),
                )
                context.update(
                    {
                        'form': form,
                        'is_edit': True,
                        'truck_type_record': instance,
                        'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                    }
                )
                messages.error(request, 'Could not save changes.', extra_tags='tenant')
                return render(request, self.template_name, context)
            except ValidationError as ve:
                if getattr(ve, 'error_dict', None):
                    for field_name, errs in ve.error_dict.items():
                        for err in errs:
                            form.add_error(field_name, err)
                else:
                    for msg in getattr(ve, 'messages', []) or [str(ve)]:
                        form.add_error(None, msg)
                context.update(
                    {
                        'form': form,
                        'is_edit': True,
                        'truck_type_record': instance,
                        'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                    }
                )
                messages.error(request, 'Could not save changes.', extra_tags='tenant')
                return render(request, self.template_name, context)

            messages.success(request, 'Truck type updated successfully.', extra_tags='tenant')
            redirect_resp = _tenant_redirect(request, 'iroad_tenants:truck_type_master')
        finally:
            connection.set_schema_to_public()

        return redirect_resp


class TruckMasterListView(View):
    """TR-001 list: search, status / sourcing / type filters, stats, deactivate via POST."""

    template_name = 'iroad_tenants/fleet/truck_master/truck_master_list.html'

    def get(self, request):
        context = _tenant_context_from_session(request)
        denied = _tenant_truck_master_access(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        tm = TruckMaster
        qs = tm.objects.select_related('truck_type')

        sq = (request.GET.get('q') or '').strip()

        status_raw = (request.GET.get('status') or '').strip().lower()
        if 'status' not in request.GET:
            qs = qs.filter(status=tm.Status.ACTIVE)
            filter_status = ''
        elif not status_raw:
            qs = qs.filter(status=tm.Status.ACTIVE)
            filter_status = 'active'
        elif status_raw == 'all':
            filter_status = 'all'
        elif status_raw == 'inactive':
            qs = qs.filter(status=tm.Status.INACTIVE)
            filter_status = 'inactive'
        elif status_raw == 'active':
            qs = qs.filter(status=tm.Status.ACTIVE)
            filter_status = 'active'
        else:
            qs = qs.filter(status=tm.Status.ACTIVE)
            filter_status = 'active'

        sourcing_raw = (request.GET.get('sourcing_mode') or 'all').strip().lower()
        if sourcing_raw == 'in_source':
            qs = qs.filter(sourcing_mode=tm.SourcingMode.IN_SOURCE)
            filter_sourcing_mode = 'in_source'
        elif sourcing_raw == 'out_source':
            qs = qs.filter(sourcing_mode=tm.SourcingMode.OUT_SOURCE)
            filter_sourcing_mode = 'out_source'
        else:
            filter_sourcing_mode = 'all'

        filter_truck_type_id = ''
        tt_param = (request.GET.get('truck_type') or '').strip()
        if tt_param:
            try:
                tt_uuid = uuid.UUID(tt_param)
                qs = qs.filter(truck_type_id=tt_uuid)
                filter_truck_type_id = str(tt_uuid)
            except ValueError:
                filter_truck_type_id = ''

        if sq:
            qs = qs.filter(
                Q(truck_code__icontains=sq)
                | Q(plate_number__icontains=sq)
                | Q(owner_name__icontains=sq)
            )

        sort_key_raw = (request.GET.get('sort') or 'truck_code').strip().lower()
        sort_dir_raw = (request.GET.get('dir') or 'asc').strip().lower()
        sort_map = {
            'truck_code': 'truck_code',
            'owner_name': 'owner_name',
            'default_driver_id': 'default_driver_id',
            'sourcing_mode': 'sourcing_mode',
            'truck_type': 'truck_type__english_label',
            'plate_number': 'plate_number',
            'status': 'status',
        }
        sort_key = sort_map.get(sort_key_raw, 'truck_code')
        sort_dir = 'desc' if sort_dir_raw == 'desc' else 'asc'
        order_expr = f'-{sort_key}' if sort_dir == 'desc' else sort_key

        qs_ordered = qs.order_by(order_expr, '-created_at')
        stats = {
            'total_count': tm.objects.count(),
            'active_count': tm.objects.filter(status=tm.Status.ACTIVE).count(),
            'inactive_count': tm.objects.filter(status=tm.Status.INACTIVE).count(),
            'in_source_count': tm.objects.filter(
                sourcing_mode=tm.SourcingMode.IN_SOURCE
            ).count(),
            'out_source_count': tm.objects.filter(
                sourcing_mode=tm.SourcingMode.OUT_SOURCE
            ).count(),
        }

        paginator = Paginator(qs_ordered, 10)
        try:
            page_no = max(1, int(request.GET.get('page') or 1))
        except ValueError:
            page_no = 1
        page = paginator.get_page(page_no)
        _hydrate_truck_master_list_rows(page)

        total_count = paginator.count
        if total_count == 0:
            ps, pe = 0, 0
        else:
            ps = (page.number - 1) * paginator.per_page + 1
            pe = ps + len(page.object_list) - 1

        def _page_url(page_num):
            q = request.GET.copy()
            q.pop('stype', None)
            try:
                pn = int(page_num)
            except (TypeError, ValueError):
                pn = 1
            if pn > 1:
                q['page'] = str(pn)
            else:
                q.pop('page', None)
            return '?' + q.urlencode()

        def _sort_url(col_key):
            q = request.GET.copy()
            q.pop('stype', None)
            current_key = sort_key_raw if sort_key_raw in sort_map else 'truck_code'
            current_dir = sort_dir
            if col_key == current_key:
                next_dir = 'desc' if current_dir == 'asc' else 'asc'
            else:
                next_dir = 'asc'
            q['sort'] = col_key
            q['dir'] = next_dir
            q.pop('page', None)
            return '?' + q.urlencode()

        pagination_page_links = [(n, _page_url(n)) for n in page.paginator.page_range]
        prev_url = _page_url(page.previous_page_number()) if page.has_previous() else None
        next_url = _page_url(page.next_page_number()) if page.has_next() else None

        truck_type_choices = list(
            TruckTypeMaster.objects.order_by('english_label')[:500]
        )

        context.update(
            {
                'trucks_page': page,
                'search_q': sq,
                'filter_status': filter_status,
                'filter_sourcing_mode': filter_sourcing_mode,
                'filter_truck_type_id': filter_truck_type_id,
                'truck_type_filter_choices': truck_type_choices,
                'stats': stats,
                'pagination_page_links': pagination_page_links,
                'pagination_prev_url': prev_url,
                'pagination_next_url': next_url,
                'pagination_start': ps,
                'pagination_end': pe,
                'pagination_total': total_count,
                'sort_key': sort_key_raw if sort_key_raw in sort_map else 'truck_code',
                'sort_dir': sort_dir,
                'sort_url_truck_code': _sort_url('truck_code'),
                'sort_url_owner_name': _sort_url('owner_name'),
                'sort_url_default_driver_id': _sort_url('default_driver_id'),
                'sort_url_sourcing_mode': _sort_url('sourcing_mode'),
                'sort_url_truck_type': _sort_url('truck_type'),
                'sort_url_plate_number': _sort_url('plate_number'),
                'sort_url_status': _sort_url('status'),
                'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
            }
        )
        try:
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()

    def post(self, request):
        context = _tenant_context_from_session(request)
        denied = _tenant_truck_master_access(request, context)
        if denied:
            return denied

        if request.POST.get('action') != 'set_status':
            return self.get(request)

        truck_pk = (request.POST.get('truck_id') or '').strip()
        new_status = (request.POST.get('new_status') or '').strip()
        if new_status not in (TruckMaster.Status.ACTIVE, TruckMaster.Status.INACTIVE):
            messages.error(request, 'Invalid status.', extra_tags='tenant')
            rq = (request.POST.get('redirect_query') or '').strip()
            base = reverse('iroad_tenants:truck_master')
            if rq:
                return redirect(f'{base}?{rq}')
            return _tenant_redirect(request, 'iroad_tenants:truck_master')

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        try:
            row = TruckMaster.objects.filter(pk=truck_pk).first()
            if not row:
                messages.error(request, 'Truck not found.', extra_tags='tenant')
            else:
                # TODO: Check active bookings when Booking module is built
                # For now: allow deactivate freely
                row.status = new_status
                row.save(update_fields=['status', 'updated_at'])
                messages.success(
                    request,
                    f'Truck set to {new_status.lower()}.',
                    extra_tags='tenant',
                )
        finally:
            connection.set_schema_to_public()

        rq = (request.POST.get('redirect_query') or '').strip()
        base = reverse('iroad_tenants:truck_master')
        if rq:
            return redirect(f'{base}?{rq}')
        return _tenant_redirect(request, 'iroad_tenants:truck_master')


def _sync_truck_driver_assignment_history(truck, previous_driver_id=None):
    """Maintain truck-driver assignment history when default driver changes."""
    now = timezone.now()
    current_driver_id = getattr(truck, 'default_driver_id_id', None)

    # No change: keep current history as-is.
    if previous_driver_id == current_driver_id:
        return

    # Close any open assignments for this truck.
    TruckDriverAssignmentHistory.objects.filter(
        truck=truck,
        assigned_to__isnull=True,
    ).update(assigned_to=now, updated_at=now)

    # Open a new current assignment if a driver is selected.
    if current_driver_id:
        TruckDriverAssignmentHistory.objects.create(
            truck=truck,
            driver_id=current_driver_id,
            assigned_from=now,
            assigned_to=None,
        )


def _settings_effective_truck_status(default_status_value):
    if default_status_value == TruckSettings.DefaultStatus.ACTIVE:
        return TruckMaster.Status.ACTIVE
    return TruckMaster.Status.INACTIVE


class TruckMasterCreateView(View):
    template_name = 'iroad_tenants/fleet/truck_master/truck_master_form.html'

    def get(self, request):
        context = _tenant_context_from_session(request)
        denied = _tenant_truck_master_access(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        try:
            settings = TruckSettings.get_or_create_singleton()
            code_preview = _preview_next_auto_number_for_form(
                form_code='truck-master',
                form_label='Truck Master',
                prefix='TR',
            )
            form = TruckMasterForm(
                initial={
                    'status': _settings_effective_truck_status(
                        settings.default_truck_status
                    )
                }
            )
            context.update(
                {
                    'form': form,
                    'code_preview': code_preview,
                    'settings_default_status_effective': _settings_effective_truck_status(
                        settings.default_truck_status
                    ),
                    'settings_driver_assignment_required': bool(settings.driver_assignment_required),
                    'page_title': 'Add Truck',
                    'is_edit': False,
                    'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()

    def post(self, request):
        context = _tenant_context_from_session(request)
        denied = _tenant_truck_master_access(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        redirect_resp = None
        try:
            settings = TruckSettings.get_or_create_singleton()
            form = TruckMasterForm(request.POST, request.FILES)
            if not form.is_valid():
                code_preview = _preview_next_auto_number_for_form(
                    form_code='truck-master',
                    form_label='Truck Master',
                    prefix='TR',
                )
                context.update(
                    {
                        'form': form,
                        'code_preview': code_preview,
                        'settings_default_status_effective': _settings_effective_truck_status(
                            settings.default_truck_status
                        ),
                        'settings_driver_assignment_required': bool(settings.driver_assignment_required),
                        'page_title': 'Add Truck',
                        'is_edit': False,
                        'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                    }
                )
                messages.error(request, 'Please fix the highlighted errors.', extra_tags='tenant')
                return render(request, self.template_name, context)

            if settings.driver_assignment_required and not form.cleaned_data.get(
                'default_driver_id'
            ):
                form.add_error(
                    'default_driver_id',
                    'Truck Settings requires assigning a default driver.',
                )
                code_preview = _preview_next_auto_number_for_form(
                    form_code='truck-master',
                    form_label='Truck Master',
                    prefix='TR',
                )
                context.update(
                    {
                        'form': form,
                        'code_preview': code_preview,
                        'settings_default_status_effective': _settings_effective_truck_status(
                            settings.default_truck_status
                        ),
                        'settings_driver_assignment_required': bool(settings.driver_assignment_required),
                        'page_title': 'Add Truck',
                        'is_edit': False,
                        'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                    }
                )
                messages.error(request, 'Please fix the highlighted errors.', extra_tags='tenant')
                return render(request, self.template_name, context)

            try:
                with db_transaction.atomic():
                    code, seq = _next_auto_number_for_form(
                        form_code='truck-master',
                        form_label='Truck Master',
                        prefix='TR',
                    )
                    obj = form.save(commit=False)
                    obj.truck_code = code
                    obj.truck_sequence = seq
                    obj.status = _settings_effective_truck_status(
                        settings.default_truck_status
                    )
                    obj.full_clean()
                    obj.save()
                    _sync_truck_driver_assignment_history(obj, previous_driver_id=None)
                    truck_image_files = request.FILES.getlist('truck_images')
                    for img_file in truck_image_files:
                        if getattr(img_file, 'name', '').strip():
                            TruckImage.objects.create(truck=obj, image=img_file)
            except IntegrityError:
                logger.exception('Truck Master create integrity violation')
                code_preview = _preview_next_auto_number_for_form(
                    form_code='truck-master',
                    form_label='Truck Master',
                    prefix='TR',
                )
                form.add_error(
                    None,
                    ValidationError(
                        'Unable to allocate a unique truck code or save this truck. Please retry.',
                        code='truck_master_integrity',
                    ),
                )
                context.update(
                    {
                        'form': form,
                        'code_preview': code_preview,
                        'settings_default_status_effective': _settings_effective_truck_status(
                            settings.default_truck_status
                        ),
                        'settings_driver_assignment_required': bool(settings.driver_assignment_required),
                        'page_title': 'Add Truck',
                        'is_edit': False,
                        'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                    }
                )
                messages.error(request, 'Could not save the truck.', extra_tags='tenant')
                return render(request, self.template_name, context)
            except ValidationError as ve:
                code_preview = _preview_next_auto_number_for_form(
                    form_code='truck-master',
                    form_label='Truck Master',
                    prefix='TR',
                )
                if getattr(ve, 'error_dict', None):
                    for field_name, errs in ve.error_dict.items():
                        for err in errs:
                            form.add_error(field_name, err)
                else:
                    for msg in getattr(ve, 'messages', []) or [str(ve)]:
                        form.add_error(None, msg)
                context.update(
                    {
                        'form': form,
                        'code_preview': code_preview,
                        'settings_default_status_effective': _settings_effective_truck_status(
                            settings.default_truck_status
                        ),
                        'settings_driver_assignment_required': bool(settings.driver_assignment_required),
                        'page_title': 'Add Truck',
                        'is_edit': False,
                        'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                    }
                )
                messages.error(request, 'Could not save the truck.', extra_tags='tenant')
                return render(request, self.template_name, context)

            messages.success(
                request,
                f'Truck {obj.truck_code} created successfully.',
                extra_tags='tenant',
            )
            redirect_resp = _tenant_redirect(request, 'iroad_tenants:truck_master')
        finally:
            connection.set_schema_to_public()

        return redirect_resp


class TruckMasterEditView(View):
    template_name = 'iroad_tenants/fleet/truck_master/truck_master_form.html'

    def _load(self, truck_id):
        return (
            TruckMaster.objects.select_related('truck_type')
            .prefetch_related('truck_images')
            .filter(pk=truck_id)
            .first()
        )

    def get(self, request, truck_id):
        context = _tenant_context_from_session(request)
        denied = _tenant_truck_master_access(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        try:
            truck = self._load(truck_id)
            if not truck:
                messages.error(request, 'Truck not found.', extra_tags='tenant')
                return _tenant_redirect(request, 'iroad_tenants:truck_master')

            settings = TruckSettings.get_or_create_singleton()
            form = TruckMasterForm(instance=truck)
            context.update(
                {
                    'form': form,
                    'truck': truck,
                    'settings_default_status_effective': _settings_effective_truck_status(
                        settings.default_truck_status
                    ),
                    'settings_driver_assignment_required': bool(settings.driver_assignment_required),
                    'page_title': 'Edit Truck',
                    'is_edit': True,
                    'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()

    def post(self, request, truck_id):
        context = _tenant_context_from_session(request)
        denied = _tenant_truck_master_access(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        redirect_resp = None
        try:
            truck = self._load(truck_id)
            if not truck:
                messages.error(request, 'Truck not found.', extra_tags='tenant')
                return _tenant_redirect(request, 'iroad_tenants:truck_master')

            immutable_code = truck.truck_code
            previous_driver_id = truck.default_driver_id_id
            settings = TruckSettings.get_or_create_singleton()
            form = TruckMasterForm(request.POST, request.FILES, instance=truck)
            if not form.is_valid():
                context.update(
                    {
                        'form': form,
                        'truck': truck,
                        'settings_default_status_effective': _settings_effective_truck_status(
                            settings.default_truck_status
                        ),
                        'settings_driver_assignment_required': bool(settings.driver_assignment_required),
                        'page_title': 'Edit Truck',
                        'is_edit': True,
                        'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                    }
                )
                messages.error(request, 'Please fix the highlighted errors.', extra_tags='tenant')
                return render(request, self.template_name, context)

            if settings.driver_assignment_required and not form.cleaned_data.get(
                'default_driver_id'
            ):
                form.add_error(
                    'default_driver_id',
                    'Truck Settings requires assigning a default driver.',
                )
                context.update(
                    {
                        'form': form,
                        'truck': truck,
                        'settings_default_status_effective': _settings_effective_truck_status(
                            settings.default_truck_status
                        ),
                        'settings_driver_assignment_required': bool(settings.driver_assignment_required),
                        'page_title': 'Edit Truck',
                        'is_edit': True,
                        'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                    }
                )
                messages.error(request, 'Please fix the highlighted errors.', extra_tags='tenant')
                return render(request, self.template_name, context)

            try:
                with db_transaction.atomic():
                    obj = form.save(commit=False)
                    obj.truck_code = immutable_code
                    obj.full_clean()
                    obj.save()
                    _sync_truck_driver_assignment_history(
                        obj,
                        previous_driver_id=previous_driver_id,
                    )

                    delete_uuids = []
                    for raw in request.POST.getlist('delete_image_ids'):
                        raw = (raw or '').strip()
                        if not raw:
                            continue
                        try:
                            delete_uuids.append(uuid.UUID(raw))
                        except ValueError:
                            continue
                    if delete_uuids:
                        images_to_delete = TruckImage.objects.filter(
                            image_id__in=delete_uuids,
                            truck=obj,
                        )
                        for img in images_to_delete:
                            if img.image:
                                try:
                                    fp = img.image.path
                                    if os.path.isfile(fp):
                                        os.remove(fp)
                                except (NotImplementedError, OSError, ValueError):
                                    pass
                                img.image.delete(save=False)
                            img.delete()

                    truck_image_files = request.FILES.getlist('truck_images')
                    for img_file in truck_image_files:
                        if getattr(img_file, 'name', '').strip():
                            TruckImage.objects.create(truck=obj, image=img_file)
            except IntegrityError:
                form.add_error(
                    None,
                    ValidationError(
                        'Conflict while saving this truck.',
                        code='truck_master_integrity',
                    ),
                )
                context.update(
                    {
                        'form': form,
                        'truck': truck,
                        'settings_default_status_effective': _settings_effective_truck_status(
                            settings.default_truck_status
                        ),
                        'settings_driver_assignment_required': bool(settings.driver_assignment_required),
                        'page_title': 'Edit Truck',
                        'is_edit': True,
                        'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                    }
                )
                messages.error(request, 'Could not save changes.', extra_tags='tenant')
                return render(request, self.template_name, context)
            except ValidationError as ve:
                if getattr(ve, 'error_dict', None):
                    for field_name, errs in ve.error_dict.items():
                        for err in errs:
                            form.add_error(field_name, err)
                else:
                    for msg in getattr(ve, 'messages', []) or [str(ve)]:
                        form.add_error(None, msg)
                context.update(
                    {
                        'form': form,
                        'truck': truck,
                        'settings_default_status_effective': _settings_effective_truck_status(
                            settings.default_truck_status
                        ),
                        'settings_driver_assignment_required': bool(settings.driver_assignment_required),
                        'page_title': 'Edit Truck',
                        'is_edit': True,
                        'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                    }
                )
                messages.error(request, 'Could not save changes.', extra_tags='tenant')
                return render(request, self.template_name, context)

            messages.success(request, 'Truck updated successfully.', extra_tags='tenant')
            redirect_resp = _tenant_redirect(request, 'iroad_tenants:truck_master')
        finally:
            connection.set_schema_to_public()

        return redirect_resp


class TruckMasterDetailView(View):
    template_name = 'iroad_tenants/fleet/truck_master/truck_master_detail.html'

    def get(self, request, truck_id):
        context = _tenant_context_from_session(request)
        denied = _tenant_truck_master_access(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        try:
            truck = (
                TruckMaster.objects.select_related('truck_type', 'default_driver_id')
                .prefetch_related('truck_images')
                .filter(pk=truck_id)
                .first()
            )
            if not truck:
                messages.error(request, 'Truck not found.', extra_tags='tenant')
                return _tenant_redirect(request, 'iroad_tenants:truck_master')

            doc_att_qs = truck.attachments.filter(is_deleted=False).order_by('-attachment_date', '-created_at')
            document_attachment_count = doc_att_qs.count()
            document_attachments_preview = list(doc_att_qs[:3])
            assignments_qs = truck.driver_assignments.select_related('driver').order_by(
                '-assigned_from', '-created_at'
            )
            assigned_drivers_rows = [
                {
                    'driver': row.driver,
                    'assigned_from': row.assigned_from,
                    'assigned_to': row.assigned_to,
                    'status': row.assignment_status,
                }
                for row in assignments_qs
            ]
            reg_country_display = '—'
            rc = getattr(truck, 'registration_country_id', None)
            if rc:
                with schema_context('public'):
                    cobj = Country.objects.filter(country_code=rc).first()
                    if cobj:
                        reg_country_display = f'{cobj.country_code} — {cobj.name_en}'

            context.update(
                {
                    'truck': truck,
                    'truck_images': list(truck.truck_images.all()),
                    'document_attachment_count': document_attachment_count,
                    'document_attachments_preview': document_attachments_preview,
                    'assigned_drivers_rows': assigned_drivers_rows,
                    'reg_country_display': reg_country_display,
                    'page_title': truck.truck_code,
                    'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()


class DriverMasterListView(View):
    """Driver Master list: search, status/source filters, stats, paginate."""

    template_name = 'iroad_tenants/drivers/driver_master/driver_list.html'

    def get(self, request):
        context = _tenant_context_from_session(request)
        denied = _tenant_driver_master_access(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        dm = DriverMaster
        qs = dm.objects.select_related('nationality_country')

        sq = (request.GET.get('q') or '').strip()

        status_raw = (request.GET.get('status') or '').strip().lower()
        if 'status' not in request.GET:
            qs = qs.filter(driver_status=dm.Status.ACTIVE)
            filter_status = ''
        elif not status_raw:
            qs = qs.filter(driver_status=dm.Status.ACTIVE)
            filter_status = 'active'
        elif status_raw == 'all':
            filter_status = 'all'
        elif status_raw == 'inactive':
            qs = qs.filter(driver_status=dm.Status.INACTIVE)
            filter_status = 'inactive'
        elif status_raw == 'active':
            qs = qs.filter(driver_status=dm.Status.ACTIVE)
            filter_status = 'active'
        else:
            qs = qs.filter(driver_status=dm.Status.ACTIVE)
            filter_status = 'active'

        source_raw = (request.GET.get('driver_source') or 'all').strip().lower()
        if source_raw == 'in_source':
            qs = qs.filter(driver_source=dm.DriverSource.IN_SOURCE)
            filter_driver_source = 'in_source'
        elif source_raw == 'out_source':
            qs = qs.filter(driver_source=dm.DriverSource.OUT_SOURCE)
            filter_driver_source = 'out_source'
        else:
            filter_driver_source = 'all'

        if sq:
            qs = qs.filter(
                Q(driver_code__icontains=sq)
                | Q(arabic_name__icontains=sq)
                | Q(english_name__icontains=sq)
                | Q(mobile_number__icontains=sq)
            )

        sort_key_raw = (request.GET.get('sort') or 'driver_code').strip().lower()
        sort_dir_raw = (request.GET.get('dir') or 'asc').strip().lower()
        sort_map = {
            'driver_code': 'driver_code',
            'arabic_name': 'arabic_name',
            'english_name': 'english_name',
            'mobile_number': 'mobile_number',
            'driver_source': 'driver_source',
            'driver_status': 'driver_status',
        }
        sort_key = sort_map.get(sort_key_raw, 'driver_code')
        sort_dir = 'desc' if sort_dir_raw == 'desc' else 'asc'
        order_expr = f'-{sort_key}' if sort_dir == 'desc' else sort_key

        qs_ordered = qs.order_by(order_expr, '-created_at')
        stats = {
            'total_count': dm.objects.count(),
            'active_count': dm.objects.filter(
                driver_status=dm.Status.ACTIVE
            ).count(),
            'inactive_count': dm.objects.filter(
                driver_status=dm.Status.INACTIVE
            ).count(),
            'in_source_count': dm.objects.filter(
                driver_source=dm.DriverSource.IN_SOURCE
            ).count(),
            'out_source_count': dm.objects.filter(
                driver_source=dm.DriverSource.OUT_SOURCE
            ).count(),
        }

        paginator = Paginator(qs_ordered, 10)
        try:
            page_no = max(1, int(request.GET.get('page') or 1))
        except ValueError:
            page_no = 1
        page = paginator.get_page(page_no)

        total_count = paginator.count
        if total_count == 0:
            ps, pe = 0, 0
        else:
            ps = (page.number - 1) * paginator.per_page + 1
            pe = ps + len(page.object_list) - 1

        def _page_url(page_num):
            q = request.GET.copy()
            q.pop('stype', None)
            try:
                pn = int(page_num)
            except (TypeError, ValueError):
                pn = 1
            if pn > 1:
                q['page'] = str(pn)
            else:
                q.pop('page', None)
            return '?' + q.urlencode()

        def _sort_url(col_key):
            q = request.GET.copy()
            q.pop('stype', None)
            current_key = sort_key_raw if sort_key_raw in sort_map else 'driver_code'
            current_dir = sort_dir
            if col_key == current_key:
                next_dir = 'desc' if current_dir == 'asc' else 'asc'
            else:
                next_dir = 'asc'
            q['sort'] = col_key
            q['dir'] = next_dir
            q.pop('page', None)
            return '?' + q.urlencode()

        pagination_page_links = [(n, _page_url(n)) for n in page.paginator.page_range]
        prev_url = _page_url(page.previous_page_number()) if page.has_previous() else None
        next_url = _page_url(page.next_page_number()) if page.has_next() else None

        context.update(
            {
                'drivers_page': page,
                'search_q': sq,
                'filter_status': filter_status,
                'filter_driver_source': filter_driver_source,
                'stats': stats,
                'pagination_page_links': pagination_page_links,
                'pagination_prev_url': prev_url,
                'pagination_next_url': next_url,
                'pagination_start': ps,
                'pagination_end': pe,
                'pagination_total': total_count,
                'sort_key': sort_key_raw if sort_key_raw in sort_map else 'driver_code',
                'sort_dir': sort_dir,
                'sort_url_driver_code': _sort_url('driver_code'),
                'sort_url_arabic_name': _sort_url('arabic_name'),
                'sort_url_english_name': _sort_url('english_name'),
                'sort_url_mobile_number': _sort_url('mobile_number'),
                'sort_url_driver_source': _sort_url('driver_source'),
                'sort_url_driver_status': _sort_url('driver_status'),
                'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
            }
        )
        try:
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()

    def post(self, request):
        context = _tenant_context_from_session(request)
        denied = _tenant_driver_master_access(request, context)
        if denied:
            return denied

        if request.POST.get('action') != 'set_status':
            return self.get(request)

        driver_pk = (request.POST.get('driver_id') or '').strip()
        new_status = (request.POST.get('new_status') or '').strip()
        if new_status not in (DriverMaster.Status.ACTIVE, DriverMaster.Status.INACTIVE):
            messages.error(request, 'Invalid status.', extra_tags='tenant')
            rq = (request.POST.get('redirect_query') or '').strip()
            base = reverse('iroad_tenants:driver_master')
            if rq:
                return redirect(f'{base}?{rq}')
            return _tenant_redirect(request, 'iroad_tenants:driver_master')

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        try:
            row = DriverMaster.objects.filter(pk=driver_pk).first()
            if not row:
                messages.error(request, 'Driver not found.', extra_tags='tenant')
            else:
                settings = DriverSettings.get_or_create_singleton()
                if new_status == DriverMaster.Status.ACTIVE:
                    if (
                        settings.document_upload_mandatory
                        and not _driver_has_any_core_document(row)
                    ):
                        messages.error(
                            request,
                            'Cannot activate: Driver Settings requires at least one core document upload.',
                            extra_tags='tenant',
                        )
                        rq = (request.POST.get('redirect_query') or '').strip()
                        base = reverse('iroad_tenants:driver_master')
                        if rq:
                            return redirect(f'{base}?{rq}')
                        return _tenant_redirect(request, 'iroad_tenants:driver_master')
                # TODO: Check active bookings when built
                row.driver_status = new_status
                row.save(update_fields=['driver_status', 'updated_at'])
                messages.success(
                    request,
                    f'Driver set to {new_status.lower()}.',
                    extra_tags='tenant',
                )
        finally:
            connection.set_schema_to_public()

        rq = (request.POST.get('redirect_query') or '').strip()
        base = reverse('iroad_tenants:driver_master')
        if rq:
            return redirect(f'{base}?{rq}')
        return _tenant_redirect(request, 'iroad_tenants:driver_master')


def _assign_default_truck_for_driver(driver, selected_truck):
    """Assign/unassign driver's default truck and keep truck-side history in sync."""
    current_trucks = list(TruckMaster.objects.filter(default_driver_id=driver))

    if selected_truck is None:
        for truck in current_trucks:
            prev_driver_id = truck.default_driver_id_id
            truck.default_driver_id = None
            truck.save(update_fields=['default_driver_id', 'updated_at'])
            _sync_truck_driver_assignment_history(
                truck,
                previous_driver_id=prev_driver_id,
            )
        return

    selected_truck_id = selected_truck.pk
    for truck in current_trucks:
        if truck.pk == selected_truck_id:
            continue
        prev_driver_id = truck.default_driver_id_id
        truck.default_driver_id = None
        truck.save(update_fields=['default_driver_id', 'updated_at'])
        _sync_truck_driver_assignment_history(
            truck,
            previous_driver_id=prev_driver_id,
        )

    if selected_truck.default_driver_id_id != driver.pk:
        prev_driver_id = selected_truck.default_driver_id_id
        selected_truck.default_driver_id = driver
        selected_truck.save(update_fields=['default_driver_id', 'updated_at'])
        _sync_truck_driver_assignment_history(
            selected_truck,
            previous_driver_id=prev_driver_id,
        )


def _settings_effective_driver_status(default_status_value):
    """
    Map DriverSettings default status to DriverMaster status domain.
    DriverMaster supports Active/Inactive only.
    """
    if default_status_value == DriverSettings.DefaultStatus.ACTIVE:
        return DriverMaster.Status.ACTIVE
    return DriverMaster.Status.INACTIVE


def _driver_has_any_core_document(driver_obj):
    """True if at least one core identity document file exists on driver."""
    return any(
        bool(getattr(driver_obj, fname, None))
        for fname in ('id_image', 'passport_image', 'dl_image', 'card_image')
    )


class DriverMasterCreateView(View):
    template_name = 'iroad_tenants/drivers/driver_master/driver_form.html'

    def get(self, request):
        context = _tenant_context_from_session(request)
        denied = _tenant_driver_master_access(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        try:
            settings = DriverSettings.get_or_create_singleton()
            code_preview = _preview_next_auto_number_for_form(
                form_code='driver-master',
                form_label='Driver Master',
                prefix='DR',
            )
            form = DriverMasterForm(
                initial={
                    'driver_status': _settings_effective_driver_status(
                        settings.default_driver_status
                    )
                }
            )
            form.fields['default_truck_id'].initial = None
            context.update(
                {
                    'form': form,
                    'code_preview': code_preview,
                    'assigned_truck_date': None,
                    'settings_default_status_effective': _settings_effective_driver_status(
                        settings.default_driver_status
                    ),
                    'settings_driver_assignment_required': bool(settings.driver_assignment_required),
                    'settings_document_upload_mandatory': bool(settings.document_upload_mandatory),
                    'page_title': 'Create Driver Profile',
                    'is_edit': False,
                    'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()

    def post(self, request):
        context = _tenant_context_from_session(request)
        denied = _tenant_driver_master_access(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        redirect_resp = None
        try:
            settings = DriverSettings.get_or_create_singleton()
            form = DriverMasterForm(request.POST, request.FILES)
            if not form.is_valid():
                code_preview = _preview_next_auto_number_for_form(
                    form_code='driver-master',
                    form_label='Driver Master',
                    prefix='DR',
                )
                context.update(
                    {
                        'form': form,
                        'code_preview': code_preview,
                        'assigned_truck_date': None,
                        'settings_default_status_effective': _settings_effective_driver_status(
                            settings.default_driver_status
                        ),
                        'settings_driver_assignment_required': bool(settings.driver_assignment_required),
                        'settings_document_upload_mandatory': bool(settings.document_upload_mandatory),
                        'page_title': 'Create Driver Profile',
                        'is_edit': False,
                        'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                    }
                )
                messages.error(request, 'Please fix the highlighted errors.', extra_tags='tenant')
                return render(request, self.template_name, context)

            try:
                with db_transaction.atomic():
                    selected_truck = form.cleaned_data.get('default_truck_id')
                    if settings.driver_assignment_required and not selected_truck:
                        form.add_error(
                            'default_truck_id',
                            'Driver Settings requires assigning a truck.',
                        )
                        raise ValidationError('Driver assignment is required by settings.')

                    code, seq = _next_auto_number_for_form(
                        form_code='driver-master',
                        form_label='Driver Master',
                        prefix='DR',
                    )
                    obj = form.save(commit=False)
                    obj.driver_code = code
                    obj.driver_sequence = seq
                    for fname in ('id_image', 'passport_image', 'dl_image', 'card_image'):
                        upl = request.FILES.get(fname)
                        if upl:
                            setattr(obj, fname, upl)

                    # Enforce default status from Driver Settings for new driver creation.
                    obj.driver_status = _settings_effective_driver_status(
                        settings.default_driver_status
                    )

                    if (
                        settings.document_upload_mandatory
                        and obj.driver_status == DriverMaster.Status.ACTIVE
                        and not _driver_has_any_core_document(obj)
                    ):
                        form.add_error(
                            None,
                            'Driver Settings requires document upload before activating driver.',
                        )
                        form.add_error(
                            'driver_status',
                            'Status is forced to Active by settings; upload at least one core document.',
                        )
                        raise ValidationError('Mandatory document rule failed.')

                    obj.full_clean()
                    obj.save()
                    _assign_default_truck_for_driver(
                        driver=obj,
                        selected_truck=selected_truck,
                    )
            except IntegrityError:
                logger.exception('Driver Master create integrity violation')
                code_preview = _preview_next_auto_number_for_form(
                    form_code='driver-master',
                    form_label='Driver Master',
                    prefix='DR',
                )
                form.add_error(
                    None,
                    ValidationError(
                        'Unable to allocate a unique driver code or save this driver. Please retry.',
                        code='driver_master_integrity',
                    ),
                )
                context.update(
                    {
                        'form': form,
                        'code_preview': code_preview,
                        'assigned_truck_date': None,
                        'settings_default_status_effective': _settings_effective_driver_status(
                            settings.default_driver_status
                        ),
                        'settings_driver_assignment_required': bool(settings.driver_assignment_required),
                        'settings_document_upload_mandatory': bool(settings.document_upload_mandatory),
                        'page_title': 'Create Driver Profile',
                        'is_edit': False,
                        'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                    }
                )
                messages.error(request, 'Could not save the driver.', extra_tags='tenant')
                return render(request, self.template_name, context)
            except ValidationError as ve:
                code_preview = _preview_next_auto_number_for_form(
                    form_code='driver-master',
                    form_label='Driver Master',
                    prefix='DR',
                )
                if getattr(ve, 'error_dict', None):
                    for field_name, errs in ve.error_dict.items():
                        for err in errs:
                            form.add_error(field_name, err)
                else:
                    for msg in getattr(ve, 'messages', []) or [str(ve)]:
                        form.add_error(None, msg)
                context.update(
                    {
                        'form': form,
                        'code_preview': code_preview,
                        'assigned_truck_date': None,
                        'settings_default_status_effective': _settings_effective_driver_status(
                            settings.default_driver_status
                        ),
                        'settings_driver_assignment_required': bool(settings.driver_assignment_required),
                        'settings_document_upload_mandatory': bool(settings.document_upload_mandatory),
                        'page_title': 'Create Driver Profile',
                        'is_edit': False,
                        'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                    }
                )
                messages.error(request, 'Could not save the driver.', extra_tags='tenant')
                return render(request, self.template_name, context)

            messages.success(
                request,
                f'Driver profile {obj.driver_code} created successfully.',
                extra_tags='tenant',
            )
            redirect_resp = _tenant_redirect(request, 'iroad_tenants:driver_master')
        finally:
            connection.set_schema_to_public()

        return redirect_resp


class DriverMasterEditView(View):
    template_name = 'iroad_tenants/drivers/driver_master/driver_form.html'

    def _load(self, driver_id):
        return (
            DriverMaster.objects.select_related('nationality_country')
            .filter(pk=driver_id)
            .first()
        )

    def get(self, request, driver_id):
        context = _tenant_context_from_session(request)
        denied = _tenant_driver_master_access(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        try:
            driver = self._load(driver_id)
            if not driver:
                messages.error(request, 'Driver not found.', extra_tags='tenant')
                return _tenant_redirect(request, 'iroad_tenants:driver_master')

            settings = DriverSettings.get_or_create_singleton()
            form = DriverMasterForm(instance=driver)
            current_truck = (
                TruckMaster.objects.filter(default_driver_id=driver)
                .order_by('-updated_at')
                .first()
            )
            if current_truck:
                form.fields['default_truck_id'].queryset = (
                    form.fields['default_truck_id'].queryset
                    | TruckMaster.objects.filter(pk=current_truck.pk)
                ).distinct().order_by('truck_code')
                form.fields['default_truck_id'].initial = current_truck.pk
            context.update(
                {
                    'form': form,
                    'driver': driver,
                    'assigned_truck_date': current_truck.updated_at if current_truck else None,
                    'settings_default_status_effective': _settings_effective_driver_status(
                        settings.default_driver_status
                    ),
                    'settings_driver_assignment_required': bool(settings.driver_assignment_required),
                    'settings_document_upload_mandatory': bool(settings.document_upload_mandatory),
                    'page_title': 'Edit Driver',
                    'is_edit': True,
                    'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()

    def post(self, request, driver_id):
        context = _tenant_context_from_session(request)
        denied = _tenant_driver_master_access(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        redirect_resp = None
        try:
            driver = self._load(driver_id)
            if not driver:
                messages.error(request, 'Driver not found.', extra_tags='tenant')
                return _tenant_redirect(request, 'iroad_tenants:driver_master')

            settings = DriverSettings.get_or_create_singleton()
            immutable_code = driver.driver_code
            form = DriverMasterForm(request.POST, request.FILES, instance=driver)
            current_truck = (
                TruckMaster.objects.filter(default_driver_id=driver)
                .order_by('-updated_at')
                .first()
            )
            if current_truck:
                form.fields['default_truck_id'].queryset = (
                    form.fields['default_truck_id'].queryset
                    | TruckMaster.objects.filter(pk=current_truck.pk)
                ).distinct().order_by('truck_code')
            if not form.is_valid():
                context.update(
                    {
                        'form': form,
                        'driver': driver,
                        'assigned_truck_date': current_truck.updated_at if current_truck else None,
                        'settings_default_status_effective': _settings_effective_driver_status(
                            settings.default_driver_status
                        ),
                        'settings_driver_assignment_required': bool(settings.driver_assignment_required),
                        'settings_document_upload_mandatory': bool(settings.document_upload_mandatory),
                        'page_title': 'Edit Driver',
                        'is_edit': True,
                        'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                    }
                )
                messages.error(request, 'Please fix the highlighted errors.', extra_tags='tenant')
                return render(request, self.template_name, context)

            try:
                with db_transaction.atomic():
                    selected_truck = form.cleaned_data.get('default_truck_id')
                    if settings.driver_assignment_required and not selected_truck:
                        form.add_error(
                            'default_truck_id',
                            'Driver Settings requires assigning a truck.',
                        )
                        raise ValidationError('Driver assignment is required by settings.')

                    obj = form.save(commit=False)
                    obj.driver_code = immutable_code
                    for fname in ('id_image', 'passport_image', 'dl_image', 'card_image'):
                        upl = request.FILES.get(fname)
                        if upl:
                            setattr(obj, fname, upl)
                    if (
                        settings.document_upload_mandatory
                        and obj.driver_status == DriverMaster.Status.ACTIVE
                        and not _driver_has_any_core_document(obj)
                    ):
                        form.add_error(
                            None,
                            'Driver Settings requires document upload before activating driver.',
                        )
                        form.add_error(
                            'driver_status',
                            'Active status is blocked until at least one core document is uploaded.',
                        )
                        raise ValidationError('Mandatory document rule failed.')
                    obj.full_clean()
                    obj.save()
                    _assign_default_truck_for_driver(
                        driver=obj,
                        selected_truck=selected_truck,
                    )
            except IntegrityError:
                form.add_error(
                    None,
                    ValidationError(
                        'Conflict while saving this driver.',
                        code='driver_master_integrity',
                    ),
                )
                context.update(
                    {
                        'form': form,
                        'driver': driver,
                        'assigned_truck_date': current_truck.updated_at if current_truck else None,
                        'settings_default_status_effective': _settings_effective_driver_status(
                            settings.default_driver_status
                        ),
                        'settings_driver_assignment_required': bool(settings.driver_assignment_required),
                        'settings_document_upload_mandatory': bool(settings.document_upload_mandatory),
                        'page_title': 'Edit Driver',
                        'is_edit': True,
                        'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                    }
                )
                messages.error(request, 'Could not save changes.', extra_tags='tenant')
                return render(request, self.template_name, context)
            except ValidationError as ve:
                if getattr(ve, 'error_dict', None):
                    for field_name, errs in ve.error_dict.items():
                        for err in errs:
                            form.add_error(field_name, err)
                else:
                    for msg in getattr(ve, 'messages', []) or [str(ve)]:
                        form.add_error(None, msg)
                context.update(
                    {
                        'form': form,
                        'driver': driver,
                        'assigned_truck_date': current_truck.updated_at if current_truck else None,
                        'settings_default_status_effective': _settings_effective_driver_status(
                            settings.default_driver_status
                        ),
                        'settings_driver_assignment_required': bool(settings.driver_assignment_required),
                        'settings_document_upload_mandatory': bool(settings.document_upload_mandatory),
                        'page_title': 'Edit Driver',
                        'is_edit': True,
                        'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                    }
                )
                messages.error(request, 'Could not save changes.', extra_tags='tenant')
                return render(request, self.template_name, context)

            messages.success(request, 'Driver profile updated successfully.', extra_tags='tenant')
            redirect_resp = _tenant_redirect(request, 'iroad_tenants:driver_master')
        finally:
            connection.set_schema_to_public()

        return redirect_resp


class DriverMasterDetailView(View):
    template_name = 'iroad_tenants/drivers/driver_master/driver_detail.html'

    def get(self, request, driver_id):
        context = _tenant_context_from_session(request)
        denied = _tenant_driver_master_access(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        try:
            driver = (
                DriverMaster.objects.select_related('nationality_country')
                .filter(pk=driver_id)
                .first()
            )
            if not driver:
                messages.error(request, 'Driver not found.', extra_tags='tenant')
                return _tenant_redirect(request, 'iroad_tenants:driver_master')

            attachments_count = driver.attachments.count()
            attachments_preview = list(
                driver.attachments.order_by(
                    '-attachment_date', '-created_at'
                )[:3]
            )
            assigned_trucks_qs = (
                TruckDriverAssignmentHistory.objects.select_related(
                    'truck',
                    'truck__truck_type',
                )
                .filter(driver=driver)
                .order_by('-assigned_from', '-created_at')
            )
            assigned_trucks_rows = [
                {
                    'truck': row.truck,
                    'assigned_from': row.assigned_from,
                    'assigned_to': row.assigned_to,
                    'status': row.assignment_status,
                }
                for row in assigned_trucks_qs
            ]
            context.update(
                {
                    'driver': driver,
                    'attachments_count': attachments_count,
                    'attachments_preview': attachments_preview,
                    'assigned_trucks_rows': assigned_trucks_rows,
                    'id_document_status': driver.id_status,
                    'passport_document_status': driver.passport_status,
                    'dl_document_status': driver.dl_status,
                    'card_document_status': driver.card_status,
                    'page_title': driver.driver_code,
                    'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()


DRIVER_TREASURY_AUTO_FORM_CODE = 'driver-treasury'
DRIVER_TREASURY_AUTO_FORM_LABEL = 'Driver Treasury'
DRIVER_TREASURY_REF_PREFIX = 'DTR'

DRIVER_TXN_AUTO_FORM_CODE = 'driver-treasury-transaction'
DRIVER_TXN_AUTO_FORM_LABEL = 'Driver Treasury Transactions'
DRIVER_TXN_REF_PREFIX = 'DTT'


def _tenant_driver_treasury_access(request, context):
    if context is None:
        response = redirect('login')
        clear_tenant_portal_cookie(
            response, request=request
        )
        return response
    if not context.get('is_tenant_admin'):
        messages.error(
            request,
            'You do not have access to '
            'Driver Treasury.',
            extra_tags='tenant',
        )
        return _tenant_redirect(
            request,
            'iroad_tenants:tenant_dashboard',
        )
    return None


class DriverTreasuryListView(View):
    template_name = 'iroad_tenants/drivers/driver_treasury/treasury_list.html'

    def get(self, request):
        context = _tenant_context_from_session(request)
        denied = _tenant_driver_treasury_access(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        try:
            qs = DriverTreasury.objects.select_related('driver')
            search_q = (request.GET.get('q') or '').strip()
            if search_q:
                qs = qs.filter(
                    Q(treasury_code__icontains=search_q)
                    | Q(driver__arabic_name__icontains=search_q)
                    | Q(driver__driver_code__icontains=search_q)
                )

            status_raw = (request.GET.get('status') or '').strip().lower()
            if 'status' not in request.GET:
                qs = qs.filter(status=DriverTreasury.Status.ACTIVE)
                filter_status = ''
            elif not status_raw:
                qs = qs.filter(status=DriverTreasury.Status.ACTIVE)
                filter_status = 'active'
            elif status_raw == 'all':
                filter_status = 'all'
            elif status_raw == 'inactive':
                qs = qs.filter(status=DriverTreasury.Status.INACTIVE)
                filter_status = 'inactive'
            elif status_raw == 'active':
                qs = qs.filter(status=DriverTreasury.Status.ACTIVE)
                filter_status = 'active'
            else:
                qs = qs.filter(status=DriverTreasury.Status.ACTIVE)
                filter_status = 'active'

            sort_key_raw = (request.GET.get('sort') or 'created_at').strip().lower()
            sort_dir_raw = (request.GET.get('dir') or 'desc').strip().lower()
            sort_map = {
                'treasury_code': 'treasury_code',
                'driver_name': 'driver__arabic_name',
                'status': 'status',
                'current_balance': 'current_balance',
                'created_at': 'created_at',
            }
            sort_key = sort_map.get(sort_key_raw, 'created_at')
            sort_dir = 'desc' if sort_dir_raw == 'desc' else 'asc'
            order_expr = f'-{sort_key}' if sort_dir == 'desc' else sort_key
            qs = qs.order_by(order_expr, '-created_at')

            stats = {
                'total_count': DriverTreasury.objects.count(),
                'active_count': DriverTreasury.objects.filter(
                    status=DriverTreasury.Status.ACTIVE
                ).count(),
                'inactive_count': DriverTreasury.objects.filter(
                    status=DriverTreasury.Status.INACTIVE
                ).count(),
                'total_balance': (
                    DriverTreasury.objects.filter(
                        status=DriverTreasury.Status.ACTIVE
                    ).aggregate(total=Sum('current_balance')).get('total')
                    or Decimal('0.00')
                ),
            }

            paginator = Paginator(qs, 10)
            try:
                page_no = max(1, int(request.GET.get('page') or 1))
            except ValueError:
                page_no = 1
            treasuries_page = paginator.get_page(page_no)

            total_count = paginator.count
            if total_count == 0:
                ps, pe = 0, 0
            else:
                ps = (treasuries_page.number - 1) * paginator.per_page + 1
                pe = ps + len(treasuries_page.object_list) - 1

            def _page_url(page_num):
                q = request.GET.copy()
                try:
                    pn = int(page_num)
                except (TypeError, ValueError):
                    pn = 1
                if pn > 1:
                    q['page'] = str(pn)
                else:
                    q.pop('page', None)
                return '?' + q.urlencode()

            def _sort_url(col_key):
                q = request.GET.copy()
                current_key = sort_key_raw if sort_key_raw in sort_map else 'created_at'
                current_dir = sort_dir
                if col_key == current_key:
                    next_dir = 'desc' if current_dir == 'asc' else 'asc'
                else:
                    next_dir = 'asc'
                q['sort'] = col_key
                q['dir'] = next_dir
                q.pop('page', None)
                return '?' + q.urlencode()

            pagination_page_links = [
                (n, _page_url(n)) for n in treasuries_page.paginator.page_range
            ]
            prev_url = (
                _page_url(treasuries_page.previous_page_number())
                if treasuries_page.has_previous()
                else None
            )
            next_url = (
                _page_url(treasuries_page.next_page_number())
                if treasuries_page.has_next()
                else None
            )

            context.update(
                {
                    'treasuries_page': treasuries_page,
                    'stats': stats,
                    'filter_status': filter_status,
                    'search_q': search_q,
                    'pagination_page_links': pagination_page_links,
                    'pagination_prev_url': prev_url,
                    'pagination_next_url': next_url,
                    'pagination_start': ps,
                    'pagination_end': pe,
                    'pagination_total': total_count,
                    'sort_key': sort_key_raw if sort_key_raw in sort_map else 'created_at',
                    'sort_dir': sort_dir,
                    'sort_url_treasury_code': _sort_url('treasury_code'),
                    'sort_url_driver_name': _sort_url('driver_name'),
                    'sort_url_status': _sort_url('status'),
                    'sort_url_current_balance': _sort_url('current_balance'),
                    'sort_url_created_at': _sort_url('created_at'),
                    'page_title': 'Driver Treasury',
                    'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()

    def post(self, request):
        context = _tenant_context_from_session(request)
        denied = _tenant_driver_treasury_access(request, context)
        if denied:
            return denied

        if request.POST.get('action') != 'set_status':
            return self.get(request)

        treasury_id = (request.POST.get('treasury_id') or '').strip()
        new_status = (request.POST.get('new_status') or '').strip()
        if new_status not in (
            DriverTreasury.Status.ACTIVE,
            DriverTreasury.Status.INACTIVE,
        ):
            messages.error(request, 'Invalid status.', extra_tags='tenant')
            rq = (request.POST.get('redirect_query') or '').strip()
            base = reverse('iroad_tenants:driver_treasury_list')
            if rq:
                return redirect(f'{base}?{rq}')
            return _tenant_redirect(request, 'iroad_tenants:driver_treasury_list')

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        try:
            treasury = DriverTreasury.objects.filter(pk=treasury_id).first()
            if not treasury:
                messages.error(request, 'Treasury not found.', extra_tags='tenant')
            else:
                treasury.status = new_status
                treasury.save(update_fields=['status', 'updated_at'])
                messages.success(
                    request,
                    f'Treasury set to {new_status.lower()}.',
                    extra_tags='tenant',
                )
        finally:
            connection.set_schema_to_public()

        rq = (request.POST.get('redirect_query') or '').strip()
        base = reverse('iroad_tenants:driver_treasury_list')
        if rq:
            return redirect(f'{base}?{rq}')
        return _tenant_redirect(request, 'iroad_tenants:driver_treasury_list')


class DriverTreasuryCreateView(View):
    template_name = 'iroad_tenants/drivers/driver_treasury/treasury_form.html'

    @staticmethod
    def _driver_balance_map():
        balances = (
            DriverTreasury.objects.values('driver_id')
            .annotate(total=Sum('current_balance'))
            .order_by()
        )
        return {
            str(row['driver_id']): f"{(row['total'] or Decimal('0.00')):.2f}"
            for row in balances
        }

    def get(self, request):
        context = _tenant_context_from_session(request)
        denied = _tenant_driver_treasury_access(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        try:
            form = DriverTreasuryForm()
            context.update(
                {
                    'form': form,
                    'code_preview': _preview_next_auto_number_for_form(
                        form_code=DRIVER_TREASURY_AUTO_FORM_CODE,
                        form_label=DRIVER_TREASURY_AUTO_FORM_LABEL,
                        prefix=DRIVER_TREASURY_REF_PREFIX,
                    ),
                    'driver_balance_map': self._driver_balance_map(),
                    'page_title': 'Add Driver Treasury',
                    'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()

    def post(self, request):
        context = _tenant_context_from_session(request)
        denied = _tenant_driver_treasury_access(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        try:
            form = DriverTreasuryForm(request.POST)
            if not form.is_valid():
                context.update(
                    {
                        'form': form,
                        'code_preview': _preview_next_auto_number_for_form(
                            form_code=DRIVER_TREASURY_AUTO_FORM_CODE,
                            form_label=DRIVER_TREASURY_AUTO_FORM_LABEL,
                            prefix=DRIVER_TREASURY_REF_PREFIX,
                        ),
                        'driver_balance_map': self._driver_balance_map(),
                        'page_title': 'Add Driver Treasury',
                        'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                    }
                )
                messages.error(request, 'Please fix the highlighted errors.', extra_tags='tenant')
                return render(request, self.template_name, context)

            with db_transaction.atomic():
                code, seq = _next_auto_number_for_form(
                    form_code=DRIVER_TREASURY_AUTO_FORM_CODE,
                    form_label=DRIVER_TREASURY_AUTO_FORM_LABEL,
                    prefix=DRIVER_TREASURY_REF_PREFIX,
                )
                obj = form.save(commit=False)
                obj.treasury_code = code
                obj.treasury_sequence = seq
                obj.current_balance = Decimal('0.00')
                obj.save()

            messages.success(
                request,
                f'Treasury {obj.treasury_code} created.',
                extra_tags='tenant',
            )
            return _tenant_redirect(request, 'iroad_tenants:driver_treasury_list')
        finally:
            connection.set_schema_to_public()


class DriverTreasuryEditView(View):
    template_name = 'iroad_tenants/drivers/driver_treasury/treasury_form.html'

    def _load(self, treasury_id):
        return DriverTreasury.objects.select_related('driver').filter(
            pk=treasury_id
        ).first()

    def get(self, request, treasury_id):
        context = _tenant_context_from_session(request)
        denied = _tenant_driver_treasury_access(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        try:
            treasury = self._load(treasury_id)
            if not treasury:
                messages.error(request, 'Treasury not found.', extra_tags='tenant')
                return _tenant_redirect(request, 'iroad_tenants:driver_treasury_list')

            form = DriverTreasuryForm(instance=treasury)
            context.update(
                {
                    'form': form,
                    'treasury': treasury,
                    'is_edit': True,
                    'page_title': 'Edit Driver Treasury',
                    'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()

    def post(self, request, treasury_id):
        context = _tenant_context_from_session(request)
        denied = _tenant_driver_treasury_access(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        try:
            treasury = self._load(treasury_id)
            if not treasury:
                messages.error(request, 'Treasury not found.', extra_tags='tenant')
                return _tenant_redirect(request, 'iroad_tenants:driver_treasury_list')

            immutable_code = treasury.treasury_code
            immutable_balance = treasury.current_balance
            form = DriverTreasuryForm(request.POST, instance=treasury)
            if not form.is_valid():
                context.update(
                    {
                        'form': form,
                        'treasury': treasury,
                        'is_edit': True,
                        'page_title': 'Edit Driver Treasury',
                        'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                    }
                )
                messages.error(request, 'Please fix the highlighted errors.', extra_tags='tenant')
                return render(request, self.template_name, context)

            obj = form.save(commit=False)
            obj.treasury_code = immutable_code
            obj.current_balance = immutable_balance
            obj.save()
            messages.success(request, 'Driver Treasury updated.', extra_tags='tenant')
            return _tenant_redirect(request, 'iroad_tenants:driver_treasury_list')
        finally:
            connection.set_schema_to_public()


class DriverTreasuryDetailView(View):
    template_name = 'iroad_tenants/drivers/driver_treasury/treasury_detail.html'

    def get(self, request, treasury_id):
        context = _tenant_context_from_session(request)
        denied = _tenant_driver_treasury_access(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        try:
            treasury = DriverTreasury.objects.select_related('driver').filter(
                pk=treasury_id
            ).first()
            if not treasury:
                messages.error(request, 'Treasury not found.', extra_tags='tenant')
                return _tenant_redirect(request, 'iroad_tenants:driver_treasury_list')

            recent_transactions = list(
                treasury.transactions.select_related('driver_treasury')
                .order_by('-transaction_date', '-created_at')[:10]
            )
            context.update(
                {
                    'treasury': treasury,
                    'recent_transactions': recent_transactions,
                    'page_title': 'Treasury Detail',
                    'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()


class DriverTreasuryTransactionListView(View):
    template_name = 'iroad_tenants/drivers/driver_treasury/transaction_list.html'

    def get(self, request):
        context = _tenant_context_from_session(request)
        denied = _tenant_driver_treasury_access(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        try:
            qs = DriverTreasuryTransaction.objects.select_related(
                'driver_treasury',
                'driver_treasury__driver',
            )
            search_q = (request.GET.get('q') or '').strip()
            if search_q:
                qs = qs.filter(
                    Q(transaction_no__icontains=search_q)
                    | Q(driver_treasury__treasury_code__icontains=search_q)
                    | Q(driver_treasury__driver__arabic_name__icontains=search_q)
                )

            type_filter = (request.GET.get('transaction_type') or 'all').strip()
            if type_filter in (
                DriverTreasuryTransaction.TransactionType.CREDIT,
                DriverTreasuryTransaction.TransactionType.DEBIT,
            ):
                qs = qs.filter(transaction_type=type_filter)
            else:
                type_filter = 'all'

            category_filter = (request.GET.get('transaction_category') or 'all').strip()
            if category_filter in (
                DriverTreasuryTransaction.TransactionCategory.CLIENT_COLLECTION,
                DriverTreasuryTransaction.TransactionCategory.CUSTODY_COLLECTION,
            ):
                qs = qs.filter(transaction_category=category_filter)
            else:
                category_filter = 'all'

            sort_key_raw = (request.GET.get('sort') or 'transaction_date').strip().lower()
            sort_dir_raw = (request.GET.get('dir') or 'desc').strip().lower()
            sort_map = {
                'transaction_no': 'transaction_no',
                'transaction_date': 'transaction_date',
                'treasury_code': 'driver_treasury__treasury_code',
                'driver_name': 'driver_treasury__driver__arabic_name',
                'transaction_type': 'transaction_type',
                'transaction_category': 'transaction_category',
                'amount': 'amount',
            }
            sort_key = sort_map.get(sort_key_raw, 'transaction_date')
            sort_dir = 'desc' if sort_dir_raw == 'desc' else 'asc'
            order_expr = f'-{sort_key}' if sort_dir == 'desc' else sort_key
            qs = qs.order_by(order_expr, '-created_at')
            stats = {
                'total_count': DriverTreasuryTransaction.objects.count(),
                'credit_count': DriverTreasuryTransaction.objects.filter(
                    transaction_type=DriverTreasuryTransaction.TransactionType.CREDIT
                ).count(),
                'debit_count': DriverTreasuryTransaction.objects.filter(
                    transaction_type=DriverTreasuryTransaction.TransactionType.DEBIT
                ).count(),
                'total_credit_amount': (
                    DriverTreasuryTransaction.objects.filter(
                        transaction_type=DriverTreasuryTransaction.TransactionType.CREDIT
                    ).aggregate(total=Sum('amount')).get('total')
                    or Decimal('0.00')
                ),
                'total_debit_amount': (
                    DriverTreasuryTransaction.objects.filter(
                        transaction_type=DriverTreasuryTransaction.TransactionType.DEBIT
                    ).aggregate(total=Sum('amount')).get('total')
                    or Decimal('0.00')
                ),
            }

            paginator = Paginator(qs, 10)
            try:
                page_no = max(1, int(request.GET.get('page') or 1))
            except ValueError:
                page_no = 1
            transactions_page = paginator.get_page(page_no)

            total_count = paginator.count
            if total_count == 0:
                ps, pe = 0, 0
            else:
                ps = (transactions_page.number - 1) * paginator.per_page + 1
                pe = ps + len(transactions_page.object_list) - 1

            def _page_url(page_num):
                q = request.GET.copy()
                try:
                    pn = int(page_num)
                except (TypeError, ValueError):
                    pn = 1
                if pn > 1:
                    q['page'] = str(pn)
                else:
                    q.pop('page', None)
                return '?' + q.urlencode()

            def _sort_url(col_key):
                q = request.GET.copy()
                current_key = sort_key_raw if sort_key_raw in sort_map else 'transaction_date'
                current_dir = sort_dir
                if col_key == current_key:
                    next_dir = 'desc' if current_dir == 'asc' else 'asc'
                else:
                    next_dir = 'asc'
                q['sort'] = col_key
                q['dir'] = next_dir
                q.pop('page', None)
                return '?' + q.urlencode()

            pagination_page_links = [
                (n, _page_url(n)) for n in transactions_page.paginator.page_range
            ]
            prev_url = (
                _page_url(transactions_page.previous_page_number())
                if transactions_page.has_previous()
                else None
            )
            next_url = (
                _page_url(transactions_page.next_page_number())
                if transactions_page.has_next()
                else None
            )

            context.update(
                {
                    'transactions_page': transactions_page,
                    'stats': stats,
                    'search_q': search_q,
                    'filter_transaction_type': type_filter,
                    'filter_transaction_category': category_filter,
                    'pagination_page_links': pagination_page_links,
                    'pagination_prev_url': prev_url,
                    'pagination_next_url': next_url,
                    'pagination_start': ps,
                    'pagination_end': pe,
                    'pagination_total': total_count,
                    'sort_key': sort_key_raw if sort_key_raw in sort_map else 'transaction_date',
                    'sort_dir': sort_dir,
                    'sort_url_transaction_no': _sort_url('transaction_no'),
                    'sort_url_transaction_date': _sort_url('transaction_date'),
                    'sort_url_treasury_code': _sort_url('treasury_code'),
                    'sort_url_driver_name': _sort_url('driver_name'),
                    'sort_url_transaction_type': _sort_url('transaction_type'),
                    'sort_url_transaction_category': _sort_url('transaction_category'),
                    'sort_url_amount': _sort_url('amount'),
                    'page_title': 'Driver Treasury Transactions',
                    'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()

    def post(self, request):
        context = _tenant_context_from_session(request)
        denied = _tenant_driver_treasury_access(request, context)
        if denied:
            return denied

        if request.POST.get('action') != 'delete':
            return self.get(request)

        transaction_id = (request.POST.get('transaction_id') or '').strip()
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        try:
            trx = DriverTreasuryTransaction.objects.filter(pk=transaction_id).first()
            if not trx:
                messages.error(request, 'Transaction not found.', extra_tags='tenant')
            else:
                trx.delete()
                messages.success(request, 'Transaction deleted.', extra_tags='tenant')
        finally:
            connection.set_schema_to_public()

        rq = (request.POST.get('redirect_query') or '').strip()
        base = reverse('iroad_tenants:driver_transaction_list')
        if rq:
            return redirect(f'{base}?{rq}')
        return _tenant_redirect(request, 'iroad_tenants:driver_transaction_list')


class DriverTreasuryTransactionCreateView(View):
    template_name = 'iroad_tenants/drivers/driver_treasury/transaction_form.html'

    def get(self, request):
        context = _tenant_context_from_session(request)
        denied = _tenant_driver_treasury_access(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        try:
            initial = {}
            treasury_id = (request.GET.get('treasury_id') or '').strip()
            if treasury_id:
                initial['driver_treasury'] = treasury_id
            form = DriverTreasuryTransactionForm(initial=initial)
            context.update(
                {
                    'form': form,
                    'code_preview': _preview_next_auto_number_for_form(
                        form_code=DRIVER_TXN_AUTO_FORM_CODE,
                        form_label=DRIVER_TXN_AUTO_FORM_LABEL,
                        prefix=DRIVER_TXN_REF_PREFIX,
                    ),
                    'page_title': 'Add Transaction',
                    'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()

    def post(self, request):
        context = _tenant_context_from_session(request)
        denied = _tenant_driver_treasury_access(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        try:
            form = DriverTreasuryTransactionForm(request.POST)
            if not form.is_valid():
                context.update(
                    {
                        'form': form,
                        'code_preview': _preview_next_auto_number_for_form(
                            form_code=DRIVER_TXN_AUTO_FORM_CODE,
                            form_label=DRIVER_TXN_AUTO_FORM_LABEL,
                            prefix=DRIVER_TXN_REF_PREFIX,
                        ),
                        'page_title': 'Add Transaction',
                        'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                    }
                )
                messages.error(request, 'Please fix the highlighted errors.', extra_tags='tenant')
                return render(request, self.template_name, context)

            with db_transaction.atomic():
                code, seq = _next_auto_number_for_form(
                    form_code=DRIVER_TXN_AUTO_FORM_CODE,
                    form_label=DRIVER_TXN_AUTO_FORM_LABEL,
                    prefix=DRIVER_TXN_REF_PREFIX,
                )
                obj = form.save(commit=False)
                obj.transaction_no = code
                obj.transaction_sequence = seq
                obj.save()

            messages.success(
                request,
                f'Transaction {obj.transaction_no} saved.',
                extra_tags='tenant',
            )
            return _tenant_redirect(request, 'iroad_tenants:driver_transaction_list')
        finally:
            connection.set_schema_to_public()


class DriverTreasuryTransactionEditView(View):
    template_name = 'iroad_tenants/drivers/driver_treasury/transaction_form.html'

    def _load(self, transaction_id):
        return DriverTreasuryTransaction.objects.select_related(
            'driver_treasury',
            'driver_treasury__driver',
        ).filter(pk=transaction_id).first()

    def get(self, request, transaction_id):
        context = _tenant_context_from_session(request)
        denied = _tenant_driver_treasury_access(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        try:
            transaction = self._load(transaction_id)
            if not transaction:
                messages.error(request, 'Transaction not found.', extra_tags='tenant')
                return _tenant_redirect(request, 'iroad_tenants:driver_transaction_list')

            form = DriverTreasuryTransactionForm(instance=transaction)
            context.update(
                {
                    'form': form,
                    'transaction': transaction,
                    'is_edit': True,
                    'page_title': 'Edit Transaction',
                    'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()

    def post(self, request, transaction_id):
        context = _tenant_context_from_session(request)
        denied = _tenant_driver_treasury_access(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        try:
            transaction = self._load(transaction_id)
            if not transaction:
                messages.error(request, 'Transaction not found.', extra_tags='tenant')
                return _tenant_redirect(request, 'iroad_tenants:driver_transaction_list')

            immutable_no = transaction.transaction_no
            immutable_seq = transaction.transaction_sequence
            form = DriverTreasuryTransactionForm(request.POST, instance=transaction)
            if not form.is_valid():
                context.update(
                    {
                        'form': form,
                        'transaction': transaction,
                        'is_edit': True,
                        'page_title': 'Edit Transaction',
                        'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                    }
                )
                messages.error(request, 'Please fix the highlighted errors.', extra_tags='tenant')
                return render(request, self.template_name, context)

            obj = form.save(commit=False)
            obj.transaction_no = immutable_no
            obj.transaction_sequence = immutable_seq
            obj.save()
            messages.success(request, 'Transaction updated.', extra_tags='tenant')
            return _tenant_redirect(request, 'iroad_tenants:driver_transaction_list')
        finally:
            connection.set_schema_to_public()


class DriverTreasuryTransactionDetailView(View):
    template_name = 'iroad_tenants/drivers/driver_treasury/transaction_detail.html'

    def get(self, request, transaction_id):
        context = _tenant_context_from_session(request)
        denied = _tenant_driver_treasury_access(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        try:
            transaction = DriverTreasuryTransaction.objects.select_related(
                'driver_treasury',
                'driver_treasury__driver',
            ).filter(pk=transaction_id).first()
            if not transaction:
                messages.error(request, 'Transaction not found.', extra_tags='tenant')
                return _tenant_redirect(request, 'iroad_tenants:driver_transaction_list')
            context.update(
                {
                    'transaction': transaction,
                    'page_title': 'Transaction Detail',
                    'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()


class DriverAttachmentAllListView(View):
    """All driver attachments across drivers (tenant sidebar pattern)."""

    template_name = 'iroad_tenants/drivers/driver_attachments/attachment_all_list.html'

    def get(self, request):
        context = _tenant_context_from_session(request)
        denied = _tenant_driver_master_access(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        da = DriverAttachment
        status_param = (request.GET.get('status') or '').strip()
        allowed = {
            '',
            da.Status.VALID,
            da.Status.EXPIRED,
            da.Status.DOES_NOT_EXPIRE,
        }
        filter_status = status_param if status_param in allowed else ''
        sq = (request.GET.get('q') or '').strip()

        allowed_sort = frozenset(
            {'attachment_no', 'driver', 'attachment_date', 'expiry_date', 'file', 'status'},
        )
        raw_sort = (request.GET.get('sort') or '').strip()
        raw_dir = (request.GET.get('dir') or '').strip().lower()
        if raw_dir not in ('asc', 'desc'):
            raw_dir = ''
        if not raw_sort and not raw_dir:
            sort_field = 'attachment_date'
            sort_dir = 'desc'
        else:
            sort_field = raw_sort if raw_sort in allowed_sort else 'attachment_date'
            sort_dir = raw_dir if raw_dir in ('asc', 'desc') else 'desc'
        sort_reverse = sort_dir == 'desc'

        try:
            qs = da.objects.select_related('driver')
            if sq:
                qs = qs.filter(
                    Q(driver__driver_code__icontains=sq)
                    | Q(driver__arabic_name__icontains=sq),
                )

            all_attachments = list(qs)
            today = timezone.localdate()
            soon_cutoff = today + timezone.timedelta(days=30)
            active_count = 0
            expiring_soon_count = 0
            expired_count = 0
            for a in all_attachments:
                st = a.status
                if st == da.Status.EXPIRED:
                    expired_count += 1
                elif st == da.Status.DOES_NOT_EXPIRE:
                    active_count += 1
                elif st == da.Status.VALID:
                    ed = a.expiry_date
                    if (
                        ed is not None
                        and today <= ed <= soon_cutoff
                    ):
                        expiring_soon_count += 1
                    else:
                        active_count += 1
            stats = {
                'total': len(all_attachments),
                'active_count': active_count,
                'expiring_soon_count': expiring_soon_count,
                'expired_count': expired_count,
            }

            filtered = all_attachments
            if filter_status:
                filtered = [a for a in all_attachments if a.status == filter_status]

            def _sort_file_key(att):
                if att.attachment_file and att.attachment_file.name:
                    return os.path.basename(att.attachment_file.name).lower()
                return ''

            def _status_rank(att):
                st = att.status
                if st == da.Status.EXPIRED:
                    return 0
                if st == da.Status.VALID:
                    return 1
                if st == da.Status.DOES_NOT_EXPIRE:
                    return 2
                return 3

            dated = [
                a
                for a in filtered
                if a.is_expiry_applicable and a.expiry_date
            ]
            undated = [
                a
                for a in filtered
                if not (a.is_expiry_applicable and a.expiry_date)
            ]

            if sort_field == 'attachment_no':
                filtered = sorted(
                    filtered,
                    key=lambda a: (a.attachment_no or '').lower(),
                    reverse=sort_reverse,
                )
            elif sort_field == 'driver':
                filtered = sorted(
                    filtered,
                    key=lambda a: (a.driver.driver_code or '').lower(),
                    reverse=sort_reverse,
                )
            elif sort_field == 'attachment_date':
                filtered = sorted(
                    filtered,
                    key=lambda a: a.attachment_date,
                    reverse=sort_reverse,
                )
            elif sort_field == 'expiry_date':
                dated.sort(
                    key=lambda a: a.expiry_date,
                    reverse=sort_reverse,
                )
                filtered = dated + undated
            elif sort_field == 'file':
                filtered = sorted(
                    filtered,
                    key=_sort_file_key,
                    reverse=sort_reverse,
                )
            elif sort_field == 'status':
                filtered = sorted(
                    filtered,
                    key=_status_rank,
                    reverse=sort_reverse,
                )
            else:
                filtered = sorted(
                    filtered,
                    key=lambda a: a.attachment_date,
                    reverse=True,
                )

            paginator = Paginator(filtered, 10)
            try:
                page_no = max(1, int(request.GET.get('page') or 1))
            except ValueError:
                page_no = 1
            page = paginator.get_page(page_no)

            for att in page.object_list:
                if att.attachment_file:
                    att.list_file_name = os.path.basename(att.attachment_file.name)
                else:
                    att.list_file_name = ''

            total_count = paginator.count
            if total_count == 0:
                ps, pe = 0, 0
            else:
                ps = (page.number - 1) * paginator.per_page + 1
                pe = ps + len(page.object_list) - 1

            def _page_url(page_num):
                q = request.GET.copy()
                q.pop('stype', None)
                try:
                    pn = int(page_num)
                except (TypeError, ValueError):
                    pn = 1
                if pn > 1:
                    q['page'] = str(pn)
                else:
                    q.pop('page', None)
                return '?' + q.urlencode()

            def _sort_url(target_field):
                q = request.GET.copy()
                q.pop('page', None)
                q['sort'] = target_field
                if sort_field == target_field:
                    q['dir'] = 'asc' if sort_dir == 'desc' else 'desc'
                else:
                    q['dir'] = 'asc'
                return '?' + q.urlencode()

            sort_urls = {f: _sort_url(f) for f in allowed_sort}

            pagination_page_links = [(n, _page_url(n)) for n in page.paginator.page_range]
            prev_url = _page_url(page.previous_page_number()) if page.has_previous() else None
            next_url = _page_url(page.next_page_number()) if page.has_next() else None

            context.update(
                {
                    'attachments_page': page,
                    'stats': stats,
                    'filter_status': filter_status,
                    'search_q': sq,
                    'pagination_page_links': pagination_page_links,
                    'pagination_prev_url': prev_url,
                    'pagination_next_url': next_url,
                    'pagination_start': ps,
                    'pagination_end': pe,
                    'pagination_total': total_count,
                    'status_choices': [
                        da.Status.VALID,
                        da.Status.EXPIRED,
                        da.Status.DOES_NOT_EXPIRE,
                    ],
                    'page_title': 'Driver Attachments List',
                    'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                    'sort_field': sort_field,
                    'sort_dir': sort_dir,
                    'sort_urls': sort_urls,
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()


class DriverAttachmentListView(View):
    """Attachments for one driver (scoped list)."""

    template_name = 'iroad_tenants/drivers/driver_attachments/attachment_list.html'

    def get(self, request, driver_id):
        # This per-driver list page should not appear in the UI.
        # Keep the URL for backward compatibility, but redirect to the add form
        # with the driver pre-selected (Fleet-style flow).
        return redirect('iroad_tenants:driver_attachment_create', driver_id=driver_id)


class DriverAttachmentCreateView(View):
    template_name = 'iroad_tenants/drivers/driver_attachments/attachment_form.html'

    def get(self, request, driver_id=None):
        context = _tenant_context_from_session(request)
        denied = _tenant_driver_master_access(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        try:
            driver = None
            show_driver_selector = not bool(driver_id)
            drivers_for_select = []
            if driver_id:
                driver = DriverMaster.objects.filter(pk=driver_id).first()
                if not driver:
                    messages.error(request, 'Driver not found.', extra_tags='tenant')
                    return _tenant_redirect(request, 'iroad_tenants:driver_master')
            else:
                drivers_for_select = list(
                    DriverMaster.objects.filter(
                        driver_status=DriverMaster.Status.ACTIVE,
                    ).order_by('driver_code')
                )

            form = DriverAttachmentForm()
            context.update(
                {
                    'form': form,
                    'driver': driver,
                    'page_title': 'Add Attachment',
                    'attachment_no_preview': _preview_next_auto_number_for_form(
                        form_code='driver-attachment',
                        form_label='Driver Attachments',
                        prefix='DRA',
                    ),
                    'show_driver_selector': show_driver_selector,
                    'drivers_for_select': drivers_for_select,
                    'selected_driver_id': '',
                    'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()

    def post(self, request, driver_id=None):
        context = _tenant_context_from_session(request)
        denied = _tenant_driver_master_access(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        try:
            driver = None
            show_driver_selector = not bool(driver_id)
            drivers_for_select = []
            selected_driver_id = ''
            driver_error = ''
            if driver_id:
                driver = DriverMaster.objects.filter(pk=driver_id).first()
                if not driver:
                    messages.error(request, 'Driver not found.', extra_tags='tenant')
                    return _tenant_redirect(request, 'iroad_tenants:driver_master')
            else:
                selected_driver_id = (request.POST.get('driver_id') or '').strip()
                drivers_for_select = list(
                    DriverMaster.objects.filter(
                        driver_status=DriverMaster.Status.ACTIVE,
                    ).order_by('driver_code')
                )
                if selected_driver_id:
                    driver = DriverMaster.objects.filter(
                        pk=selected_driver_id,
                        driver_status=DriverMaster.Status.ACTIVE,
                    ).first()
                if driver is None:
                    driver_error = 'Please select an active driver.'

            form = DriverAttachmentForm(request.POST, request.FILES)
            if driver_error or not form.is_valid():
                context.update(
                    {
                        'form': form,
                        'driver': driver,
                        'page_title': 'Add Attachment',
                        'attachment_no_preview': _preview_next_auto_number_for_form(
                            form_code='driver-attachment',
                            form_label='Driver Attachments',
                            prefix='DRA',
                        ),
                        'show_driver_selector': show_driver_selector,
                        'drivers_for_select': drivers_for_select,
                        'selected_driver_id': selected_driver_id,
                        'driver_error': driver_error,
                        'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                    }
                )
                messages.error(request, 'Please fix the highlighted errors.', extra_tags='tenant')
                return render(request, self.template_name, context)

            with db_transaction.atomic():
                attachment_no, attachment_seq = _next_auto_number_for_form(
                    form_code='driver-attachment',
                    form_label='Driver Attachments',
                    prefix='DRA',
                )
                obj = form.save(commit=False)
                obj.driver = driver
                obj.attachment_no = attachment_no
                obj.attachment_sequence = attachment_seq
                obj.full_clean()
                obj.save()
            messages.success(request, 'Attachment saved.', extra_tags='tenant')
            return redirect('iroad_tenants:driver_attachment_all_list')
        finally:
            connection.set_schema_to_public()


class DriverAttachmentEditView(View):
    template_name = 'iroad_tenants/drivers/driver_attachments/attachment_form.html'

    def get(self, request, driver_id, attachment_id):
        context = _tenant_context_from_session(request)
        denied = _tenant_driver_master_access(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        try:
            driver = DriverMaster.objects.filter(pk=driver_id).first()
            if not driver:
                messages.error(request, 'Driver not found.', extra_tags='tenant')
                return _tenant_redirect(request, 'iroad_tenants:driver_master')

            attachment = DriverAttachment.objects.filter(
                pk=attachment_id,
                driver=driver,
            ).first()
            if not attachment:
                messages.error(request, 'Attachment not found.', extra_tags='tenant')
                return redirect('iroad_tenants:driver_attachment_all_list')

            form = DriverAttachmentForm(instance=attachment)
            context.update(
                {
                    'form': form,
                    'driver': driver,
                    'attachment': attachment,
                    'page_title': 'Edit Attachment',
                    'is_edit': True,
                    'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()

    def post(self, request, driver_id, attachment_id):
        context = _tenant_context_from_session(request)
        denied = _tenant_driver_master_access(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        try:
            driver = DriverMaster.objects.filter(pk=driver_id).first()
            if not driver:
                messages.error(request, 'Driver not found.', extra_tags='tenant')
                return _tenant_redirect(request, 'iroad_tenants:driver_master')

            attachment = DriverAttachment.objects.filter(
                pk=attachment_id,
                driver=driver,
            ).first()
            if not attachment:
                messages.error(request, 'Attachment not found.', extra_tags='tenant')
                return redirect('iroad_tenants:driver_attachment_all_list')

            old_file_name = (
                attachment.attachment_file.name
                if attachment and attachment.attachment_file
                else ''
            )

            form = DriverAttachmentForm(
                request.POST,
                request.FILES,
                instance=attachment,
            )
            if not form.is_valid():
                context.update(
                    {
                        'form': form,
                        'driver': driver,
                        'attachment': attachment,
                        'page_title': 'Edit Attachment',
                        'is_edit': True,
                        'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                    }
                )
                messages.error(
                    request,
                    'Please fix the highlighted errors.',
                    extra_tags='tenant',
                )
                return render(request, self.template_name, context)

            obj = form.save(commit=False)
            obj.driver = driver
            obj.save()
            new_file_uploaded = bool(request.FILES.get('attachment_file'))
            new_file_name = obj.attachment_file.name if obj.attachment_file else ''
            if (
                new_file_uploaded
                and old_file_name
                and old_file_name != new_file_name
                and default_storage.exists(old_file_name)
            ):
                default_storage.delete(old_file_name)
            messages.success(
                request,
                'Attachment updated successfully.',
                extra_tags='tenant',
            )
            return redirect('iroad_tenants:driver_attachment_all_list')
        finally:
            connection.set_schema_to_public()


class DriverAttachmentDetailView(View):
    template_name = 'iroad_tenants/drivers/driver_attachments/attachment_detail.html'

    def get(self, request, driver_id, attachment_id):
        context = _tenant_context_from_session(request)
        denied = _tenant_driver_master_access(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        try:
            driver = DriverMaster.objects.filter(pk=driver_id).first()
            if not driver:
                messages.error(request, 'Driver not found.', extra_tags='tenant')
                return _tenant_redirect(request, 'iroad_tenants:driver_master')

            attachment = DriverAttachment.objects.filter(
                pk=attachment_id,
                driver=driver,
            ).first()
            if not attachment:
                messages.error(request, 'Attachment not found.', extra_tags='tenant')
                return redirect('iroad_tenants:driver_attachment_all_list')

            context.update(
                {
                    'driver': driver,
                    'attachment': attachment,
                    'page_title': 'Attachment Detail',
                    'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()


class DriverAttachmentDeleteView(View):
    def post(self, request, driver_id, attachment_id):
        context = _tenant_context_from_session(request)
        denied = _tenant_driver_master_access(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        try:
            driver = DriverMaster.objects.filter(pk=driver_id).first()
            if not driver:
                messages.error(request, 'Driver not found.', extra_tags='tenant')
                return _tenant_redirect(request, 'iroad_tenants:driver_master')

            att = DriverAttachment.objects.filter(
                pk=attachment_id,
                driver_id=driver.driver_id,
            ).first()
            if att is None:
                messages.error(request, 'Attachment not found.', extra_tags='tenant')
            else:
                if att.attachment_file:
                    att.attachment_file.delete(save=False)
                att.delete()
                messages.success(request, 'Attachment deleted.', extra_tags='tenant')

            return redirect('iroad_tenants:driver_attachment_all_list')
        finally:
            connection.set_schema_to_public()


class DriverAttachmentDriverSelectView(View):
    """
    Intermediate page: pick an active driver, then Add Attachment for that driver.
    """

    template_name = (
        'iroad_tenants/drivers/driver_attachments/attachment_driver_select.html'
    )

    def get(self, request):
        context = _tenant_context_from_session(request)
        denied = _tenant_driver_master_access(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        try:
            sq = (request.GET.get('q') or '').strip()
            drivers = DriverMaster.objects.filter(
                driver_status=DriverMaster.Status.ACTIVE,
            ).order_by('driver_code')

            if sq:
                drivers = drivers.filter(
                    Q(driver_code__icontains=sq)
                    | Q(arabic_name__icontains=sq),
                )

            paginator = Paginator(drivers, 10)
            try:
                page_no = max(1, int(request.GET.get('page') or 1))
            except ValueError:
                page_no = 1
            page = paginator.get_page(page_no)

            context.update(
                {
                    'drivers_page': page,
                    'search_q': sq,
                    'page_title': 'Select Driver — Add Attachment',
                    'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()


class DriverSettingsView(View):
    template_name = 'iroad_tenants/drivers/driver_settings/driver_settings.html'

    def get(self, request):
        context = _tenant_context_from_session(request)
        denied = _tenant_driver_master_access(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        try:
            settings = DriverSettings.get_or_create_singleton()
            form = DriverSettingsForm(instance=settings)
            context.update(
                {
                    'form': form,
                    'settings': settings,
                    'page_title': 'Driver Settings',
                    'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()

    def post(self, request):
        context = _tenant_context_from_session(request)
        denied = _tenant_driver_master_access(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        try:
            settings = DriverSettings.get_or_create_singleton()
            form = DriverSettingsForm(request.POST, instance=settings)
            if form.is_valid():
                form.save()
                messages.success(request, 'Settings saved.', extra_tags='tenant')
                return redirect('iroad_tenants:driver_settings')

            messages.error(request, 'Please fix the highlighted errors.', extra_tags='tenant')
            context.update(
                {
                    'form': form,
                    'settings': settings,
                    'page_title': 'Driver Settings',
                    'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()


class TruckSettingsView(View):
    template_name = 'iroad_tenants/fleet/truck_settings/truck_settings.html'

    def get(self, request):
        context = _tenant_context_from_session(request)
        denied = _tenant_truck_master_access(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        try:
            settings = TruckSettings.get_or_create_singleton()
            form = TruckSettingsForm(instance=settings)
            context.update(
                {
                    'form': form,
                    'settings': settings,
                    'page_title': 'Truck Settings',
                    'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()

    def post(self, request):
        context = _tenant_context_from_session(request)
        denied = _tenant_truck_master_access(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        try:
            settings = TruckSettings.get_or_create_singleton()
            form = TruckSettingsForm(request.POST, instance=settings)
            if form.is_valid():
                form.save()
                messages.success(request, 'Settings saved.', extra_tags='tenant')
                return redirect('iroad_tenants:truck_settings')

            messages.error(request, 'Please fix the highlighted errors.', extra_tags='tenant')
            context.update(
                {
                    'form': form,
                    'settings': settings,
                    'page_title': 'Truck Settings',
                    'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()


class TruckAttachmentListView(View):
    """TR-ATT-001 list for one truck; status filter uses derived ``TruckAttachment.status``."""

    template_name = 'iroad_tenants/fleet/truck_attachments/attachment_list.html'

    def get(self, request, truck_id):
        return redirect('iroad_tenants:truck_attachment_all_list')


class TruckAttachmentCreateView(View):
    template_name = 'iroad_tenants/fleet/truck_attachments/attachment_form.html'

    def get(self, request, truck_id=None):
        context = _tenant_context_from_session(request)
        denied = _tenant_truck_master_access(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        try:
            truck = None
            show_truck_selector = not bool(truck_id)
            trucks_for_select = []
            if truck_id:
                truck = TruckMaster.objects.filter(pk=truck_id).first()
                if not truck:
                    messages.error(request, 'Truck not found.', extra_tags='tenant')
                    return _tenant_redirect(request, 'iroad_tenants:truck_master')
            else:
                trucks_for_select = list(
                    TruckMaster.objects.filter(
                        status=TruckMaster.Status.ACTIVE
                    ).order_by('truck_code')
                )

            form = TruckAttachmentForm()
            context.update(
                {
                    'form': form,
                    'truck': truck,
                    'page_title': 'Add Attachment',
                    'attachment_no_preview': _preview_next_auto_number_for_form(
                        form_code='truck-attachment',
                        form_label='Truck Attachments',
                        prefix='TRA',
                    ),
                    'show_truck_selector': show_truck_selector,
                    'trucks_for_select': trucks_for_select,
                    'selected_truck_id': '',
                    'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()

    def post(self, request, truck_id=None):
        context = _tenant_context_from_session(request)
        denied = _tenant_truck_master_access(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        try:
            truck = None
            show_truck_selector = not bool(truck_id)
            trucks_for_select = []
            selected_truck_id = ''
            truck_error = ''
            if truck_id:
                truck = TruckMaster.objects.filter(pk=truck_id).first()
                if not truck:
                    messages.error(request, 'Truck not found.', extra_tags='tenant')
                    return _tenant_redirect(request, 'iroad_tenants:truck_master')
            else:
                selected_truck_id = (request.POST.get('truck_id') or '').strip()
                trucks_for_select = list(
                    TruckMaster.objects.filter(
                        status=TruckMaster.Status.ACTIVE
                    ).order_by('truck_code')
                )
                if selected_truck_id:
                    truck = TruckMaster.objects.filter(
                        pk=selected_truck_id,
                        status=TruckMaster.Status.ACTIVE,
                    ).first()
                if truck is None:
                    truck_error = 'Please select an active truck.'

            form = TruckAttachmentForm(request.POST, request.FILES)
            if truck_error or not form.is_valid():
                context.update(
                    {
                        'form': form,
                        'truck': truck,
                        'page_title': 'Add Attachment',
                        'attachment_no_preview': _preview_next_auto_number_for_form(
                            form_code='truck-attachment',
                            form_label='Truck Attachments',
                            prefix='TRA',
                        ),
                        'show_truck_selector': show_truck_selector,
                        'trucks_for_select': trucks_for_select,
                        'selected_truck_id': selected_truck_id,
                        'truck_error': truck_error,
                        'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                    }
                )
                messages.error(request, 'Please fix the highlighted errors.', extra_tags='tenant')
                return render(request, self.template_name, context)

            with db_transaction.atomic():
                attachment_no, attachment_seq = _next_auto_number_for_form(
                    form_code='truck-attachment',
                    form_label='Truck Attachments',
                    prefix='TRA',
                )
                obj = form.save(commit=False)
                obj.truck = truck
                obj.attachment_no = attachment_no
                obj.attachment_sequence = attachment_seq
                obj.full_clean()
                obj.save()
            messages.success(request, 'Attachment saved.', extra_tags='tenant')
            return redirect('iroad_tenants:truck_attachment_all_list')
        finally:
            connection.set_schema_to_public()


class TruckAttachmentEditView(View):
    template_name = 'iroad_tenants/fleet/truck_attachments/attachment_form.html'

    def get(self, request, truck_id, attachment_id):
        context = _tenant_context_from_session(request)
        denied = _tenant_truck_master_access(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        try:
            truck = TruckMaster.objects.filter(pk=truck_id).first()
            if not truck:
                messages.error(request, 'Truck not found.', extra_tags='tenant')
                return _tenant_redirect(request, 'iroad_tenants:truck_master')

            attachment = TruckAttachment.objects.filter(
                pk=attachment_id,
                truck=truck,
                is_deleted=False,
            ).first()
            if not attachment:
                messages.error(request, 'Attachment not found.', extra_tags='tenant')
                return redirect('iroad_tenants:truck_attachment_all_list')

            form = TruckAttachmentForm(instance=attachment)
            context.update(
                {
                    'form': form,
                    'truck': truck,
                    'attachment': attachment,
                    'page_title': 'Edit Attachment',
                    'is_edit': True,
                    'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()

    def post(self, request, truck_id, attachment_id):
        context = _tenant_context_from_session(request)
        denied = _tenant_truck_master_access(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        try:
            truck = TruckMaster.objects.filter(pk=truck_id).first()
            if not truck:
                messages.error(request, 'Truck not found.', extra_tags='tenant')
                return _tenant_redirect(request, 'iroad_tenants:truck_master')

            attachment = TruckAttachment.objects.filter(
                pk=attachment_id,
                truck=truck,
                is_deleted=False,
            ).first()
            if not attachment:
                messages.error(request, 'Attachment not found.', extra_tags='tenant')
                return redirect('iroad_tenants:truck_attachment_all_list')

            old_file_name = (
                attachment.attachment_file.name
                if attachment and attachment.attachment_file
                else ''
            )

            form = TruckAttachmentForm(
                request.POST,
                request.FILES,
                instance=attachment,
            )
            if not form.is_valid():
                context.update(
                    {
                        'form': form,
                        'truck': truck,
                        'attachment': attachment,
                        'page_title': 'Edit Attachment',
                        'is_edit': True,
                        'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                    }
                )
                messages.error(
                    request,
                    'Please fix the highlighted errors.',
                    extra_tags='tenant',
                )
                return render(request, self.template_name, context)

            obj = form.save(commit=False)
            obj.truck = truck
            obj.save()
            new_file_uploaded = bool(request.FILES.get('attachment_file'))
            new_file_name = obj.attachment_file.name if obj.attachment_file else ''
            if (
                new_file_uploaded
                and old_file_name
                and old_file_name != new_file_name
                and default_storage.exists(old_file_name)
            ):
                default_storage.delete(old_file_name)
            messages.success(
                request,
                'Attachment updated successfully.',
                extra_tags='tenant',
            )
            return redirect('iroad_tenants:truck_attachment_all_list')
        finally:
            connection.set_schema_to_public()


class TruckAttachmentDetailView(View):
    template_name = 'iroad_tenants/fleet/truck_attachments/attachment_detail.html'

    def get(self, request, truck_id, attachment_id):
        context = _tenant_context_from_session(request)
        denied = _tenant_truck_master_access(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        try:
            truck = TruckMaster.objects.filter(pk=truck_id).first()
            if not truck:
                messages.error(request, 'Truck not found.', extra_tags='tenant')
                return _tenant_redirect(request, 'iroad_tenants:truck_master')

            attachment = TruckAttachment.objects.filter(
                pk=attachment_id,
                truck=truck,
                is_deleted=False,
            ).first()
            if not attachment:
                messages.error(request, 'Attachment not found.', extra_tags='tenant')
                return redirect('iroad_tenants:truck_attachment_all_list')

            context.update(
                {
                    'truck': truck,
                    'attachment': attachment,
                    'page_title': 'Attachment Detail',
                    'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()


class TruckAttachmentTruckSelectView(View):
    """
    Intermediate page shown when user clicks Add Attachment from all-attachments list.
    User selects a truck and then goes to truck_attachment_create for that truck.
    """

    template_name = 'iroad_tenants/fleet/truck_attachments/attachment_truck_select.html'

    def get(self, request):
        context = _tenant_context_from_session(request)
        denied = _tenant_truck_master_access(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        try:
            sq = (request.GET.get('q') or '').strip()
            trucks = TruckMaster.objects.filter(
                status=TruckMaster.Status.ACTIVE
            ).order_by('truck_code')

            if sq:
                trucks = trucks.filter(
                    Q(truck_code__icontains=sq)
                    | Q(plate_number__icontains=sq)
                    | Q(owner_name__icontains=sq)
                )

            paginator = Paginator(trucks, 10)
            try:
                page_no = max(1, int(request.GET.get('page') or 1))
            except ValueError:
                page_no = 1
            page = paginator.get_page(page_no)

            context.update(
                {
                    'trucks_page': page,
                    'search_q': sq,
                    'page_title': 'Select Truck — Add Attachment',
                    'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()


class TruckAttachmentDeleteView(View):
    def post(self, request, truck_id, attachment_id):
        context = _tenant_context_from_session(request)
        denied = _tenant_truck_master_access(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        try:
            truck = TruckMaster.objects.filter(pk=truck_id).first()
            if not truck:
                messages.error(request, 'Truck not found.', extra_tags='tenant')
                return _tenant_redirect(request, 'iroad_tenants:truck_master')

            att = TruckAttachment.objects.filter(
                pk=attachment_id,
                truck_id=truck.truck_id,
                is_deleted=False,
            ).first()
            if att is None:
                messages.error(request, 'Attachment not found.', extra_tags='tenant')
            else:
                if att.attachment_file:
                    att.attachment_file.delete(save=False)
                att.attachment_file = ''
                att.is_deleted = True
                att.is_deleted_by = _current_tenant_actor_id(request)
                att.save(update_fields=['attachment_file', 'is_deleted', 'is_deleted_by', 'updated_at'])
                messages.success(request, 'Attachment deleted.', extra_tags='tenant')

            return redirect('iroad_tenants:truck_attachment_all_list')
        finally:
            connection.set_schema_to_public()


class TruckAttachmentAllListView(View):
    """Standalone list of ALL truck attachments across trucks (sidebar)."""

    template_name = 'iroad_tenants/fleet/truck_attachments/attachment_all_list.html'

    def get(self, request):
        context = _tenant_context_from_session(request)
        denied = _tenant_truck_master_access(request, context)
        if denied:
            return denied

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        ta = TruckAttachment
        status_param = (request.GET.get('status') or '').strip()
        allowed = {
            '',
            ta.Status.VALID,
            ta.Status.EXPIRED,
            ta.Status.DOES_NOT_EXPIRE,
        }
        filter_status = status_param if status_param in allowed else ''
        sq = (request.GET.get('q') or '').strip()

        allowed_sort = frozenset(
            {
                'attachment_no',
                'truck',
                'attachment_date',
                'expiry_date',
                'file',
                'status',
            },
        )
        raw_sort = (request.GET.get('sort') or '').strip()
        raw_dir = (request.GET.get('dir') or '').strip().lower()
        if raw_dir not in ('asc', 'desc'):
            raw_dir = ''
        if not raw_sort and not raw_dir:
            sort_field = 'attachment_date'
            sort_dir = 'desc'
        else:
            sort_field = raw_sort if raw_sort in allowed_sort else 'attachment_date'
            sort_dir = raw_dir if raw_dir in ('asc', 'desc') else 'desc'
        sort_reverse = sort_dir == 'desc'

        try:
            qs = ta.objects.filter(is_deleted=False).select_related(
                'truck',
                'truck__truck_type',
            )
            if sq:
                qs = qs.filter(
                    Q(truck__truck_code__icontains=sq)
                    | Q(truck__plate_number__icontains=sq),
                )

            all_attachments = list(qs)
            today = timezone.localdate()
            soon_cutoff = today + timezone.timedelta(days=30)
            active_count = 0
            expiring_soon_count = 0
            expired_count = 0
            for a in all_attachments:
                st = a.status
                if st == ta.Status.EXPIRED:
                    expired_count += 1
                elif st == ta.Status.DOES_NOT_EXPIRE:
                    active_count += 1
                elif st == ta.Status.VALID:
                    ed = a.expiry_date
                    if (
                        ed is not None
                        and today <= ed <= soon_cutoff
                    ):
                        expiring_soon_count += 1
                    else:
                        active_count += 1
            stats = {
                'total': len(all_attachments),
                'active_count': active_count,
                'expiring_soon_count': expiring_soon_count,
                'expired_count': expired_count,
            }

            filtered = all_attachments
            if filter_status:
                filtered = [a for a in all_attachments if a.status == filter_status]

            def _sort_file_key(att):
                if att.attachment_file and att.attachment_file.name:
                    return os.path.basename(att.attachment_file.name).lower()
                return ''

            def _status_rank(att):
                st = att.status
                if st == ta.Status.EXPIRED:
                    return 0
                if st == ta.Status.VALID:
                    return 1
                if st == ta.Status.DOES_NOT_EXPIRE:
                    return 2
                return 3

            dated = [
                a
                for a in filtered
                if a.is_expiry_applicable and a.expiry_date
            ]
            undated = [
                a
                for a in filtered
                if not (a.is_expiry_applicable and a.expiry_date)
            ]

            if sort_field == 'attachment_no':
                filtered = sorted(
                    filtered,
                    key=lambda a: (
                        not bool((a.attachment_no or '').strip()),
                        (a.attachment_no or '').lower(),
                    ),
                    reverse=sort_reverse,
                )
            elif sort_field == 'truck':
                filtered = sorted(
                    filtered,
                    key=lambda a: (a.truck.truck_code or '').lower(),
                    reverse=sort_reverse,
                )
            elif sort_field == 'attachment_date':
                filtered = sorted(
                    filtered,
                    key=lambda a: a.attachment_date,
                    reverse=sort_reverse,
                )
            elif sort_field == 'expiry_date':
                dated.sort(
                    key=lambda a: a.expiry_date,
                    reverse=sort_reverse,
                )
                filtered = dated + undated
            elif sort_field == 'file':
                filtered = sorted(
                    filtered,
                    key=_sort_file_key,
                    reverse=sort_reverse,
                )
            elif sort_field == 'status':
                filtered = sorted(
                    filtered,
                    key=_status_rank,
                    reverse=sort_reverse,
                )
            else:
                filtered = sorted(
                    filtered,
                    key=lambda a: a.attachment_date,
                    reverse=True,
                )

            paginator = Paginator(filtered, 10)
            try:
                page_no = max(1, int(request.GET.get('page') or 1))
            except ValueError:
                page_no = 1
            page = paginator.get_page(page_no)

            for att in page.object_list:
                if att.attachment_file:
                    att.list_file_name = os.path.basename(att.attachment_file.name)
                else:
                    att.list_file_name = ''

            total_count = paginator.count
            if total_count == 0:
                ps, pe = 0, 0
            else:
                ps = (page.number - 1) * paginator.per_page + 1
                pe = ps + len(page.object_list) - 1

            def _page_url(page_num):
                q = request.GET.copy()
                q.pop('stype', None)
                try:
                    pn = int(page_num)
                except (TypeError, ValueError):
                    pn = 1
                if pn > 1:
                    q['page'] = str(pn)
                else:
                    q.pop('page', None)
                return '?' + q.urlencode()

            def _sort_url(target_field):
                q = request.GET.copy()
                q.pop('page', None)
                q['sort'] = target_field
                if sort_field == target_field:
                    q['dir'] = 'asc' if sort_dir == 'desc' else 'desc'
                else:
                    q['dir'] = 'asc'
                return '?' + q.urlencode()

            sort_urls = {f: _sort_url(f) for f in allowed_sort}

            pagination_page_links = [(n, _page_url(n)) for n in page.paginator.page_range]
            prev_url = _page_url(page.previous_page_number()) if page.has_previous() else None
            next_url = _page_url(page.next_page_number()) if page.has_next() else None

            context.update(
                {
                    'attachments_page': page,
                    'stats': stats,
                    'filter_status': filter_status,
                    'search_q': sq,
                    'pagination_page_links': pagination_page_links,
                    'pagination_prev_url': prev_url,
                    'pagination_next_url': next_url,
                    'pagination_start': ps,
                    'pagination_end': pe,
                    'pagination_total': total_count,
                    'status_choices': [
                        ta.Status.VALID,
                        ta.Status.EXPIRED,
                        ta.Status.DOES_NOT_EXPIRE,
                    ],
                    'page_title': 'Truck Attachments List',
                    'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                    'sort_field': sort_field,
                    'sort_dir': sort_dir,
                    'sort_urls': sort_urls,
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()


class TenantAddressLocationOptionsView(View):
    """AJAX options for Address Master location dropdowns."""

    def get(self, request):
        context = _tenant_context_from_session(request)
        denied = _tenant_address_master_access(request, context)
        if denied:
            return JsonResponse({'ok': False, 'error': 'forbidden'}, status=403)

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            return JsonResponse({'ok': False, 'error': 'unauthorized'}, status=401)

        country_id = (request.GET.get('country') or '').strip()
        province = (request.GET.get('province') or '').strip()
        try:
            if not country_id:
                return JsonResponse({'ok': True, 'provinces': [], 'cities': []})
            qs = TenantLocationMaster.active_serviceable_objects.filter(country_id=country_id)
            provinces = list(
                qs.exclude(province='')
                .values_list('province', flat=True)
                .distinct()
                .order_by('province')
            )
            cities = []
            if province:
                cities = list(
                    qs.filter(province=province)
                    .values_list('display_label', flat=True)
                    .distinct()
                    .order_by('display_label')
                )
            return JsonResponse(
                {
                    'ok': True,
                    'provinces': provinces,
                    'cities': cities,
                }
            )
        finally:
            connection.set_schema_to_public()


class TenantMyAccountView(View):
    """Tenant self account summary page."""

    def get(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        return render(request, 'iroad_tenants/my_account.html', context)

    def post(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        tenant = context['tenant']
        
        # Personal Info (Only name and password are now editable)
        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        
        # Password Change
        password = request.POST.get('password', '')

        # Password Validation
        if password:
            if len(password) < 8:
                messages.error(request, "Password must be at least 8 characters.", extra_tags='tenant')
                return render(request, 'iroad_tenants/my_account.html', context)

        try:
            # Update Names
            tenant.first_name = first_name
            tenant.last_name = last_name
            
            # Update Password if provided
            if password:
                tenant.portal_bootstrap_password_hash = make_password(password)
            
            tenant.save()
            
            messages.success(request, "Profile updated successfully.", extra_tags='tenant')
            # Refresh context to show new values
            context = _tenant_context_from_session(request)
        except Exception as e:
            messages.error(request, f"Error updating profile: {str(e)}", extra_tags='tenant')

        return render(request, 'iroad_tenants/my_account.html', context)


class TenantAutoNumberConfigurationView(View):
    """Tenant auto number configuration page."""

    ORGANIZATION_FORM_CODE = 'organization-profile'
    ORGANIZATION_FORM_LABEL = 'Organization Profile'
    USERS_FORM_CODE = 'users-administration'
    USERS_FORM_LABEL = 'Users Administration'
    CLIENT_ACCOUNT_FORM_CODE = 'client-account'
    CLIENT_ACCOUNT_FORM_LABEL = 'Client Account'
    CLIENT_CONTRACT_FORM_CODE = CLIENT_CONTRACT_AUTO_FORM_CODE
    CLIENT_CONTRACT_FORM_LABEL = CLIENT_CONTRACT_AUTO_FORM_LABEL
    ALLOWED_SEQUENCE_FORMATS = {'numeric', 'alpha', 'alphanumeric'}

    FORM_LABELS = {
        ORGANIZATION_FORM_CODE: ORGANIZATION_FORM_LABEL,
        USERS_FORM_CODE: USERS_FORM_LABEL,
        CLIENT_ACCOUNT_FORM_CODE: CLIENT_ACCOUNT_FORM_LABEL,
        CLIENT_CONTRACT_FORM_CODE: CLIENT_CONTRACT_FORM_LABEL,
        ADDRESS_MASTER_AUTO_FORM_CODE: ADDRESS_MASTER_AUTO_FORM_LABEL,
        # Fleet + Driver modules (configurable from Auto Number Configuration).
        TRUCK_MASTER_AUTO_FORM_CODE: 'Truck Master',
        TRUCK_TYPE_AUTO_FORM_CODE: 'Truck Type Master',
        TRUCK_ATTACHMENT_AUTO_FORM_CODE: 'Truck Attachments',
        DRIVER_MASTER_AUTO_FORM_CODE: 'Driver Master',
        DRIVER_ATTACHMENT_AUTO_FORM_CODE: 'Driver Attachments',
        DRIVER_TREASURY_AUTO_FORM_CODE: 'Driver Treasury',
        DRIVER_TXN_AUTO_FORM_CODE: 'Driver Treasury Transactions',
        CARGO_MASTER_AUTO_FORM_CODE: CARGO_MASTER_AUTO_FORM_LABEL,
        CARGO_CATEGORY_AUTO_FORM_CODE: CARGO_CATEGORY_AUTO_FORM_LABEL,
        LOCATION_MASTER_AUTO_FORM_CODE: LOCATION_MASTER_AUTO_FORM_LABEL,
        ROUTE_MASTER_AUTO_FORM_CODE: ROUTE_MASTER_AUTO_FORM_LABEL,
        SERVICE_ITEM_MASTER_AUTO_FORM_CODE: SERVICE_ITEM_MASTER_AUTO_FORM_LABEL,
        PRICE_LIST_MASTER_AUTO_FORM_CODE: PRICE_LIST_MASTER_AUTO_FORM_LABEL,
        SALES_INVOICE_REPORT_AUTO_FORM_CODE: SALES_INVOICE_REPORT_AUTO_FORM_LABEL,
    }

    FORM_PREFIXES = {
        ORGANIZATION_FORM_CODE: 'ORG',
        USERS_FORM_CODE: 'USR',
        CLIENT_ACCOUNT_FORM_CODE: 'CA',
        CLIENT_CONTRACT_FORM_CODE: CLIENT_CONTRACT_REF_PREFIX,
        ADDRESS_MASTER_AUTO_FORM_CODE: ADDRESS_MASTER_REF_PREFIX,
        TRUCK_MASTER_AUTO_FORM_CODE: TRUCK_MASTER_REF_PREFIX,
        TRUCK_TYPE_AUTO_FORM_CODE: TRUCK_TYPE_REF_PREFIX,
        TRUCK_ATTACHMENT_AUTO_FORM_CODE: TRUCK_ATTACHMENT_REF_PREFIX,
        DRIVER_MASTER_AUTO_FORM_CODE: DRIVER_MASTER_REF_PREFIX,
        DRIVER_ATTACHMENT_AUTO_FORM_CODE: DRIVER_ATTACHMENT_REF_PREFIX,
        DRIVER_TREASURY_AUTO_FORM_CODE: 'DTR',
        DRIVER_TXN_AUTO_FORM_CODE: DRIVER_TXN_REF_PREFIX,
        CARGO_MASTER_AUTO_FORM_CODE: CARGO_MASTER_REF_PREFIX,
        CARGO_CATEGORY_AUTO_FORM_CODE: CARGO_CATEGORY_REF_PREFIX,
        LOCATION_MASTER_AUTO_FORM_CODE: LOCATION_MASTER_REF_PREFIX,
        ROUTE_MASTER_AUTO_FORM_CODE: ROUTE_MASTER_REF_PREFIX,
        SERVICE_ITEM_MASTER_AUTO_FORM_CODE: SERVICE_ITEM_MASTER_REF_PREFIX,
        PRICE_LIST_MASTER_AUTO_FORM_CODE: PRICE_LIST_MASTER_REF_PREFIX,
        SALES_INVOICE_REPORT_AUTO_FORM_CODE: SALES_INVOICE_REPORT_REF_PREFIX,
    }

    @staticmethod
    def _normalize_form_code(raw):
        """Match URL/post variants (e.g. Cargo-master, cargo_master) to FORM_LABELS keys."""
        if raw is None:
            return ''
        s = str(raw).strip()
        if not s:
            return ''
        return s.replace('_', '-').lower()

    def _auto_number_list_url(self, request, form_code):
        q = {'form_code': form_code}
        return f"{reverse('iroad_tenants:tenant_auto_number_configuration')}?{urlencode(q)}"

    def _load_config(self, form_code):
        config, _ = AutoNumberConfiguration.objects.get_or_create(
            form_code=form_code,
            defaults={
                'form_label': self.FORM_LABELS.get(form_code, form_code),
                'number_of_digits': 4,
                'sequence_format': AutoNumberConfiguration.SequenceFormat.NUMERIC,
                'is_unique': True,
            },
        )
        return config

    def get(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            raw_form = request.GET.get('form_code')
            if raw_form is None or not str(raw_form).strip():
                requested_form_code = self.ORGANIZATION_FORM_CODE
            else:
                requested_form_code = self._normalize_form_code(raw_form)
                if requested_form_code not in self.FORM_LABELS:
                    requested_form_code = self.ORGANIZATION_FORM_CODE
            config = self._load_config(requested_form_code)
            sequence = AutoNumberSequence.objects.filter(form_code=requested_form_code).first()
            base_next_number = sequence.next_number if sequence else 1
        finally:
            connection.set_schema_to_public()

        context.update(
            {
                'auto_number_config': config,
                'auto_number_form_code': requested_form_code,
                'auto_number_form_label': self.FORM_LABELS.get(requested_form_code, requested_form_code),
                'auto_number_prefix': self.FORM_PREFIXES.get(requested_form_code, ''),
                'base_next_number': base_next_number,
                'auto_number_enabled_form_codes': list(self.FORM_LABELS.keys()),
                'tenant_schema_name': tenant_registry.schema_name,
            }
        )
        return render(
            request,
            'iroad_tenants/configuration/Auto-number-configuration.html',
            context,
        )

    def post(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        redirect_url = self._auto_number_list_url(request, self.ORGANIZATION_FORM_CODE)
        try:
            selected_form = self._normalize_form_code(request.POST.get('form_code'))
            if selected_form not in self.FORM_LABELS:
                messages.error(
                    request,
                    'Invalid auto number form selected.',
                    extra_tags='tenant',
                )
            else:
                try:
                    config = self._load_config(selected_form)
                    digits_raw = (request.POST.get('number_of_digits') or '').strip()
                    sequence_format = (request.POST.get('sequence_format') or '').strip().lower()

                    if not digits_raw.isdigit() or not (1 <= int(digits_raw) <= 10):
                        raise ValueError('Number of digits must be between 1 and 10.')
                    if sequence_format not in self.ALLOWED_SEQUENCE_FORMATS:
                        raise ValueError('Invalid sequence format selected.')

                    config.number_of_digits = int(digits_raw)
                    config.sequence_format = sequence_format
                    config.is_unique = request.POST.get('is_unique') == 'on'
                    config.form_label = self.FORM_LABELS[selected_form]
                    config.save(update_fields=[
                        'number_of_digits',
                        'sequence_format',
                        'is_unique',
                        'form_label',
                        'updated_at',
                    ])
                    messages.success(
                        request,
                        f'Auto number configuration saved for {self.FORM_LABELS[selected_form]}.',
                        extra_tags='tenant',
                    )
                except ValueError as exc:
                    messages.error(request, str(exc), extra_tags='tenant')
                redirect_url = self._auto_number_list_url(request, selected_form)
        finally:
            connection.set_schema_to_public()

        return redirect(redirect_url)


class TenantLogoutView(View):
    """Clear tenant session and redirect to login."""

    def get(self, request):
        self._clear_tenant_session(request)
        response = redirect('login')
        clear_tenant_portal_cookie(response, request=request)
        return response

    def post(self, request):
        self._clear_tenant_session(request)
        response = redirect('login')
        clear_tenant_portal_cookie(response, request=request)
        return response

    @staticmethod
    def _clear_tenant_session(request):
        _clear_tenant_bootstrap_session(request)


class TenantOrganizationProfileView(View):
    """View organization profile."""

    def get(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            profile = _get_or_create_organization_profile(context['tenant'])
            _sync_tenant_ref_if_config_changed(profile)
            owner_label = _owner_user_label(profile.owner_user_id)
            context.update({
                'org': profile,
                'owner_label': owner_label,
                'org_status_label': _organization_status_from_tenant(context['tenant']),
                'logo_display_name': _logo_display_name(profile),
                'tenant_schema_name': tenant_registry.schema_name,
            })
        finally:
            connection.set_schema_to_public()
        return render(request, 'iroad_tenants/Administration/Organization-profile.html', context)


class TenantOrganizationProfileEditView(View):
    """Edit organization profile."""

    def get(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            profile = _get_or_create_organization_profile(context['tenant'])
            _sync_tenant_ref_if_config_changed(profile)
            context.update(_organization_form_context(profile))
            context['org_status_label'] = _organization_status_from_tenant(context['tenant'])
            context['tenant_schema_name'] = tenant_registry.schema_name
        finally:
            connection.set_schema_to_public()
        return render(request, 'iroad_tenants/Administration/Organization-profile-view.html', context)

    def post(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            profile = _get_or_create_organization_profile(context['tenant'])
            _sync_tenant_ref_if_config_changed(profile)
            _apply_organization_profile_post(request, profile)
            profile.save()
            messages.success(request, 'Organization profile updated successfully.', extra_tags='tenant')
            return _tenant_redirect(request, 'iroad_tenants:tenant_organization_profile')
        except ValueError as exc:
            messages.error(request, str(exc), extra_tags='tenant')
            context.update(_organization_form_context(profile))
            context['org_status_label'] = _organization_status_from_tenant(context['tenant'])
            context['tenant_schema_name'] = tenant_registry.schema_name
            return render(request, 'iroad_tenants/Administration/Organization-profile-view.html', context)
        finally:
            connection.set_schema_to_public()


class TenantLocationMasterListView(View):
    template_name = 'iroad_tenants/Master_Data/location_master/Location-master-list.html'

    def get(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        if not context.get('is_tenant_admin'):
            messages.error(
                request,
                'You do not have permission to view Route Management.',
                extra_tags='tenant',
            )
            return _tenant_redirect(request, 'iroad_tenants:tenant_dashboard')
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        try:
            qs = TenantLocationMaster.objects.select_related('country')
            search_q = (request.GET.get('q') or '').strip()
            if search_q:
                qs = qs.filter(
                    Q(location_code__icontains=search_q)
                    | Q(display_label__icontains=search_q)
                    | Q(location_name_english__icontains=search_q)
                    | Q(location_name_arabic__icontains=search_q)
                    | Q(province__icontains=search_q)
                    | Q(country__name_en__icontains=search_q)
                )

            status_filter = (request.GET.get('status') or 'all').strip().lower()
            if status_filter == 'active':
                qs = qs.filter(status=TenantLocationMaster.Status.ACTIVE)
            elif status_filter == 'inactive':
                qs = qs.filter(status=TenantLocationMaster.Status.INACTIVE)
            else:
                status_filter = 'all'

            serviceable_filter = (request.GET.get('serviceable') or 'all').strip().lower()
            if serviceable_filter == 'yes':
                qs = qs.filter(is_serviceable=True)
            elif serviceable_filter == 'no':
                qs = qs.filter(is_serviceable=False)
            else:
                serviceable_filter = 'all'

            qs = qs.order_by('-created_at')
            stats = {
                'total': TenantLocationMaster.objects.count(),
                'serviceable': TenantLocationMaster.objects.filter(is_serviceable=True).count(),
                'non_serviceable': TenantLocationMaster.objects.filter(is_serviceable=False).count(),
                'inactive': TenantLocationMaster.objects.filter(
                    status=TenantLocationMaster.Status.INACTIVE
                ).count(),
            }
            paginator = Paginator(qs, 10)
            try:
                page_no = max(1, int(request.GET.get('page') or 1))
            except ValueError:
                page_no = 1
            page = paginator.get_page(page_no)
            context.update(
                {
                    'locations_page': page,
                    'search_q': search_q,
                    'filter_status': status_filter,
                    'filter_serviceable': serviceable_filter,
                    'location_stats': stats,
                    'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                }
            )
            return render(request, self.template_name, context)
        except ProgrammingError:
            # Tenant schema is missing the latest migration table(s).
            messages.error(
                request,
                'Location Master is not initialized for this tenant yet. '
                'Please run tenant migrations and reload.',
                extra_tags='tenant',
            )
            context.update(
                {
                    'locations': [],
                    'location_stats': {
                        'total': 0,
                        'serviceable': 0,
                        'non_serviceable': 0,
                        'inactive': 0,
                    },
                    'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()


class TenantLocationMasterCreateView(View):
    template_name = 'iroad_tenants/Master_Data/location_master/Location-master.html'

    def get(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        if not context.get('is_tenant_admin'):
            messages.error(
                request,
                'You do not have permission to view Route Management.',
                extra_tags='tenant',
            )
            return _tenant_redirect(request, 'iroad_tenants:tenant_dashboard')
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            context.update(
                {
                    'form': TenantLocationMasterForm(allow_inactive_status=False),
                    'preview_location_code': _preview_next_location_master_code(),
                    'is_edit': False,
                    'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()

    def post(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        if not context.get('is_tenant_admin'):
            messages.error(
                request,
                'You do not have permission to view Route Management.',
                extra_tags='tenant',
            )
            return _tenant_redirect(request, 'iroad_tenants:tenant_dashboard')
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        try:
            form = TenantLocationMasterForm(request.POST, allow_inactive_status=False)
            if not form.is_valid():
                context.update(
                    {
                        'form': form,
                        'preview_location_code': _preview_next_location_master_code(),
                        'is_edit': False,
                        'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                    }
                )
                return render(request, self.template_name, context)

            try:
                location_code, location_sequence = _next_auto_number_for_form(
                    LOCATION_MASTER_AUTO_FORM_CODE,
                    LOCATION_MASTER_AUTO_FORM_LABEL,
                    LOCATION_MASTER_REF_PREFIX,
                )
                location = form.save(commit=False)
                location.location_code = location_code
                location.location_sequence = location_sequence
                location.full_clean()
                location.save()
            except IntegrityError:
                form.add_error(
                    'display_label',
                    'A location with this country/province/display label already exists.',
                )
                context.update(
                    {
                        'form': form,
                        'preview_location_code': _preview_next_location_master_code(),
                        'is_edit': False,
                        'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                    }
                )
                return render(request, self.template_name, context)

            messages.success(
                request,
                f'{location.location_code} created successfully.',
                extra_tags='tenant',
            )
            return _tenant_redirect(request, 'iroad_tenants:tenant_location_master_list')
        finally:
            connection.set_schema_to_public()


class TenantRouteMasterListView(View):
    template_name = 'iroad_tenants/Master_Data/location_master/Route-master-list.html'

    def get(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        if not context.get('is_tenant_admin'):
            messages.error(request, 'You do not have permission to view Route Management.', extra_tags='tenant')
            return _tenant_redirect(request, 'iroad_tenants:tenant_dashboard')

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            qs = TenantRouteMaster.objects.select_related('origin_point', 'destination_point')
            search_q = (request.GET.get('q') or '').strip()
            route_scope = (request.GET.get('scope') or 'all').strip().lower()
            if search_q:
                qs = qs.filter(
                    Q(route_code__icontains=search_q)
                    | Q(route_label__icontains=search_q)
                    | Q(origin_point__display_label__icontains=search_q)
                    | Q(destination_point__display_label__icontains=search_q)
                )
            if route_scope == 'domestic':
                qs = qs.filter(route_type=TenantRouteMaster.RouteType.DOMESTIC)
            elif route_scope == 'international':
                qs = qs.filter(route_type=TenantRouteMaster.RouteType.INTERNATIONAL)

            routes_page = Paginator(qs.order_by('-created_at'), 25).get_page(
                request.GET.get('page') or 1
            )
            stats_qs = TenantRouteMaster.objects.all()
            route_stats = {
                'total': stats_qs.count(),
                'active': stats_qs.filter(status=TenantRouteMaster.Status.ACTIVE).count(),
                'with_customs': stats_qs.filter(has_customs=True).count(),
                'with_toll_gates': stats_qs.filter(has_toll_gates=True).count(),
            }
            context.update(
                {
                    'routes_page': routes_page,
                    'search_q': search_q,
                    'route_scope': route_scope,
                    'route_stats': route_stats,
                    'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()


class TenantLocationMasterDeleteView(View):
    def post(self, request, location_id):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        if not context.get('is_tenant_admin'):
            messages.error(
                request,
                'You do not have permission to modify Route Management.',
                extra_tags='tenant',
            )
            return _tenant_redirect(request, 'iroad_tenants:tenant_dashboard')

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        try:
            location = TenantLocationMaster.objects.filter(pk=location_id).first()
            if not location:
                messages.error(request, 'Location not found.', extra_tags='tenant')
                return _tenant_redirect(request, 'iroad_tenants:tenant_location_master_list')

            code = location.location_code
            try:
                location.delete()
            except ProtectedError:
                messages.error(
                    request,
                    'This location cannot be deleted because it is referenced by other records.',
                    extra_tags='tenant',
                )
                return _tenant_redirect(request, 'iroad_tenants:tenant_location_master_list')

            messages.success(request, f'{code} deleted successfully.', extra_tags='tenant')
            return _tenant_redirect(request, 'iroad_tenants:tenant_location_master_list')
        finally:
            connection.set_schema_to_public()


class TenantLocationMasterDetailView(View):
    template_name = 'iroad_tenants/Master_Data/location_master/Location-master.html'

    def get(self, request, location_id):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        if not context.get('is_tenant_admin'):
            messages.error(
                request,
                'You do not have permission to view Route Management.',
                extra_tags='tenant',
            )
            return _tenant_redirect(request, 'iroad_tenants:tenant_dashboard')

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            location = TenantLocationMaster.objects.select_related('country').filter(
                pk=location_id
            ).first()
            if not location:
                messages.error(request, 'Location not found.', extra_tags='tenant')
                return _tenant_redirect(request, 'iroad_tenants:tenant_location_master_list')
            context.update(
                {
                    'form': TenantLocationMasterForm(
                        instance=location,
                        allow_inactive_status=True,
                    ),
                    'location': location,
                    'preview_location_code': location.location_code,
                    'is_edit': True,
                    'is_view': True,
                    'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()


class TenantLocationMasterEditView(View):
    template_name = 'iroad_tenants/Master_Data/location_master/Location-master.html'

    def _get_location(self, location_id):
        return TenantLocationMaster.objects.select_related('country').filter(pk=location_id).first()

    def get(self, request, location_id):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        if not context.get('is_tenant_admin'):
            messages.error(
                request,
                'You do not have permission to view Route Management.',
                extra_tags='tenant',
            )
            return _tenant_redirect(request, 'iroad_tenants:tenant_dashboard')

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            location = self._get_location(location_id)
            if not location:
                messages.error(request, 'Location not found.', extra_tags='tenant')
                return _tenant_redirect(request, 'iroad_tenants:tenant_location_master_list')
            context.update(
                {
                    'form': TenantLocationMasterForm(
                        instance=location,
                        allow_inactive_status=True,
                    ),
                    'location': location,
                    'preview_location_code': location.location_code,
                    'is_edit': True,
                    'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()

    def post(self, request, location_id):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        if not context.get('is_tenant_admin'):
            messages.error(
                request,
                'You do not have permission to view Route Management.',
                extra_tags='tenant',
            )
            return _tenant_redirect(request, 'iroad_tenants:tenant_dashboard')

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            location = self._get_location(location_id)
            if not location:
                messages.error(request, 'Location not found.', extra_tags='tenant')
                return _tenant_redirect(request, 'iroad_tenants:tenant_location_master_list')

            form = TenantLocationMasterForm(
                request.POST,
                instance=location,
                allow_inactive_status=True,
            )
            if not form.is_valid():
                context.update(
                    {
                        'form': form,
                        'location': location,
                        'preview_location_code': location.location_code,
                        'is_edit': True,
                        'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                    }
                )
                return render(request, self.template_name, context)

            location = form.save(commit=False)
            location.full_clean()
            location.save()
            messages.success(
                request,
                f'{location.location_code} updated successfully.',
                extra_tags='tenant',
            )
            return _tenant_redirect(request, 'iroad_tenants:tenant_location_master_list')
        finally:
            connection.set_schema_to_public()


class TenantRouteMasterCreateView(View):
    template_name = 'iroad_tenants/Master_Data/location_master/Route-master.html'

    def get(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        if not context.get('is_tenant_admin'):
            messages.error(request, 'You do not have permission to view Route Management.', extra_tags='tenant')
            return _tenant_redirect(request, 'iroad_tenants:tenant_dashboard')

        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            context.update(
                {
                    'form': TenantRouteMasterForm(allow_inactive_status=False),
                    'preview_route_code': _preview_next_route_master_code(),
                    'is_edit': False,
                    'is_view': False,
                    'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()

    def post(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        if not context.get('is_tenant_admin'):
            messages.error(request, 'You do not have permission to view Route Management.', extra_tags='tenant')
            return _tenant_redirect(request, 'iroad_tenants:tenant_dashboard')
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            form = TenantRouteMasterForm(request.POST, allow_inactive_status=False)
            if not form.is_valid():
                context.update(
                    {
                        'form': form,
                        'preview_route_code': _preview_next_route_master_code(),
                        'is_edit': False,
                        'is_view': False,
                        'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                    }
                )
                messages.error(request, 'Please fix the highlighted errors.', extra_tags='tenant')
                return render(request, self.template_name, context)
            try:
                route_code, route_seq = _next_auto_number_for_form(
                    ROUTE_MASTER_AUTO_FORM_CODE,
                    ROUTE_MASTER_AUTO_FORM_LABEL,
                    ROUTE_MASTER_REF_PREFIX,
                )
                route = form.save(commit=False)
                route.route_code = route_code
                route.route_sequence = route_seq
                route.full_clean()
                route.save()
            except IntegrityError:
                form.add_error(
                    'destination_point',
                    'A route with this type and ordered origin/destination already exists.',
                )
                context.update(
                    {
                        'form': form,
                        'preview_route_code': _preview_next_route_master_code(),
                        'is_edit': False,
                        'is_view': False,
                        'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                    }
                )
                return render(request, self.template_name, context)

            messages.success(request, f'{route.route_code} created successfully.', extra_tags='tenant')
            return _tenant_redirect(request, 'iroad_tenants:tenant_route_master_list')
        finally:
            connection.set_schema_to_public()


class TenantRouteMasterDetailView(View):
    template_name = 'iroad_tenants/Master_Data/location_master/Route-master.html'

    def get(self, request, route_id):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        if not context.get('is_tenant_admin'):
            messages.error(request, 'You do not have permission to view Route Management.', extra_tags='tenant')
            return _tenant_redirect(request, 'iroad_tenants:tenant_dashboard')
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            route = TenantRouteMaster.objects.select_related('origin_point', 'destination_point').filter(
                pk=route_id
            ).first()
            if not route:
                messages.error(request, 'Route not found.', extra_tags='tenant')
                return _tenant_redirect(request, 'iroad_tenants:tenant_route_master_list')
            context.update(
                {
                    'form': TenantRouteMasterForm(instance=route, allow_inactive_status=True),
                    'route': route,
                    'preview_route_code': route.route_code,
                    'is_edit': False,
                    'is_view': True,
                    'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()


class TenantRouteMasterEditView(View):
    template_name = 'iroad_tenants/Master_Data/location_master/Route-master.html'

    def _get_route(self, route_id):
        return TenantRouteMaster.objects.select_related('origin_point', 'destination_point').filter(
            pk=route_id
        ).first()

    def get(self, request, route_id):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        if not context.get('is_tenant_admin'):
            messages.error(request, 'You do not have permission to view Route Management.', extra_tags='tenant')
            return _tenant_redirect(request, 'iroad_tenants:tenant_dashboard')
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            route = self._get_route(route_id)
            if not route:
                messages.error(request, 'Route not found.', extra_tags='tenant')
                return _tenant_redirect(request, 'iroad_tenants:tenant_route_master_list')
            context.update(
                {
                    'form': TenantRouteMasterForm(instance=route, allow_inactive_status=True),
                    'route': route,
                    'preview_route_code': route.route_code,
                    'is_edit': True,
                    'is_view': False,
                    'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                }
            )
            return render(request, self.template_name, context)
        finally:
            connection.set_schema_to_public()

    def post(self, request, route_id):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        if not context.get('is_tenant_admin'):
            messages.error(request, 'You do not have permission to view Route Management.', extra_tags='tenant')
            return _tenant_redirect(request, 'iroad_tenants:tenant_dashboard')
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            route = self._get_route(route_id)
            if not route:
                messages.error(request, 'Route not found.', extra_tags='tenant')
                return _tenant_redirect(request, 'iroad_tenants:tenant_route_master_list')
            form = TenantRouteMasterForm(request.POST, instance=route, allow_inactive_status=True)
            if not form.is_valid():
                context.update(
                    {
                        'form': form,
                        'route': route,
                        'preview_route_code': route.route_code,
                        'is_edit': True,
                        'is_view': False,
                        'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                    }
                )
                return render(request, self.template_name, context)
            try:
                route = form.save(commit=False)
                route.full_clean()
                route.save()
            except IntegrityError:
                form.add_error(
                    'destination_point',
                    'A route with this type and ordered origin/destination already exists.',
                )
                context.update(
                    {
                        'form': form,
                        'route': route,
                        'preview_route_code': route.route_code,
                        'is_edit': True,
                        'is_view': False,
                        'tenant_schema_name': getattr(tenant_registry, 'schema_name', ''),
                    }
                )
                return render(request, self.template_name, context)
            messages.success(request, f'{route.route_code} updated successfully.', extra_tags='tenant')
            return _tenant_redirect(request, 'iroad_tenants:tenant_route_master_list')
        finally:
            connection.set_schema_to_public()


class TenantRouteMasterDeleteView(View):
    def post(self, request, route_id):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        if not context.get('is_tenant_admin'):
            messages.error(request, 'You do not have permission to modify Route Management.', extra_tags='tenant')
            return _tenant_redirect(request, 'iroad_tenants:tenant_dashboard')
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            route = TenantRouteMaster.objects.filter(pk=route_id).first()
            if not route:
                messages.error(request, 'Route not found.', extra_tags='tenant')
                return _tenant_redirect(request, 'iroad_tenants:tenant_route_master_list')
            code = route.route_code
            route.delete()
            messages.success(request, f'{code} deleted successfully.', extra_tags='tenant')
            return _tenant_redirect(request, 'iroad_tenants:tenant_route_master_list')
        finally:
            connection.set_schema_to_public()


class TenantUsersAdministrationView(View):
    """Tenant users administration list page."""

    def get(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            search_query = (request.GET.get('q') or '').strip()
            users_qs = TenantUser.objects.all()
            if search_query:
                users_qs = users_qs.filter(
                    Q(full_name__icontains=search_query)
                    | Q(email__icontains=search_query)
                    | Q(role_name__icontains=search_query)
                    | Q(username__icontains=search_query)
                    | Q(tenant_ref_no__icontains=search_query)
                )
            tenant_users = list(users_qs.order_by('-created_at', '-updated_at')[:100])

            all_users_qs = TenantUser.objects.all()
            total_users = all_users_qs.count()
            active_users = all_users_qs.filter(status=TenantUser.Status.ACTIVE).count()
            inactive_users = total_users - active_users
            locked_accounts = all_users_qs.filter(login_attempts__gte=3).count()
            context.update(
                {
                    'tenant_users': tenant_users,
                    'users_total_count': total_users,
                    'users_active_count': active_users,
                    'users_inactive_count': inactive_users,
                    'users_locked_count': locked_accounts,
                    'search_query': search_query,
                    'tenant_schema_name': tenant_registry.schema_name,
                }
            )
        finally:
            connection.set_schema_to_public()
        return render(
            request,
            'iroad_tenants/User_Management/Users-administration.html',
            context,
        )


TENANT_USER_ROLE_OPTIONS = ['Administrator', 'Finance Manager', 'Operations Staff', 'Sales Executive']
TENANT_PERMISSION_MATRIX = [
    {'module_name': 'Master Data', 'form_name': 'Cargo Master'},
    {'module_name': 'Commercial', 'form_name': 'Sales Order'},
    {'module_name': 'Operations', 'form_name': 'Booking'},
    {'module_name': 'Operations', 'form_name': 'Shipment'},
    {'module_name': 'Finance', 'form_name': 'Sales Invoicing'},
    {'module_name': 'Finance', 'form_name': 'Purchase Invoicing'},
]


def _tenant_role_name_options():
    role_names = list(
        TenantRole.objects.order_by('role_name_en').values_list('role_name_en', flat=True)
    )
    if role_names:
        return role_names
    return TENANT_USER_ROLE_OPTIONS


def _tenant_user_login_url(request):
    configured_url = (getattr(settings, 'TENANT_PORTAL_LOGIN_URL', '') or '').strip()
    auth_payload = get_tenant_portal_cookie_payload(request) or {}
    tenant_id = str(auth_payload.get('tenant_id') or '').strip()
    if configured_url:
        if tenant_id and 'tid=' not in configured_url:
            separator = '&' if '?' in configured_url else '?'
            return f'{configured_url}{separator}tid={tenant_id}'
        return configured_url
    login_url = request.build_absolute_uri(reverse('login'))
    if tenant_id:
        login_url = f'{login_url}?tid={tenant_id}'
    return login_url


def _send_tenant_user_welcome_email(*, request, tenant_user, plaintext_password, role_name):
    context_dict = {
        'name': tenant_user.full_name,
        'email': tenant_user.email,
        'password': plaintext_password,
        'role_name': role_name,
        'login_url': _tenant_user_login_url(request),
        'user_name': tenant_user.full_name,
    }
    sent = send_named_notification_email(
        'TENANT_USER_WELCOME',
        recipient_email=tenant_user.email,
        context_dict=context_dict,
        language='en',
        default_subject='Welcome to iRoad - Tenant User Access',
        trigger_source='TemplateName: TENANT_USER_WELCOME',
        force_django_smtp=True,
    )
    if sent:
        return True
    return send_named_notification_email(
        'SUBADMIN_WELCOME',
        recipient_email=tenant_user.email,
        context_dict=context_dict,
        language='en',
        default_subject='Welcome to iRoad - Your Login Credentials',
        trigger_source='TemplateName: SUBADMIN_WELCOME',
        force_django_smtp=True,
    )


def _tenant_user_form_data_from_post(request):
    return {
        'username': (request.POST.get('username') or '').strip(),
        'full_name': (request.POST.get('full_name') or '').strip(),
        'email': (request.POST.get('email') or '').strip().lower(),
        'mobile_country_code': (request.POST.get('mobile_country_code') or '').strip(),
        'mobile_no': (request.POST.get('mobile_no') or '').strip(),
        'status': 'Active' if request.POST.get('status') == 'on' else 'Inactive',
        'roles': request.POST.getlist('roles'),
    }


def _tenant_user_form_data_from_model(tenant_user):
    return {
        'username': tenant_user.username,
        'full_name': tenant_user.full_name,
        'email': tenant_user.email,
        'mobile_country_code': tenant_user.mobile_country_code,
        'mobile_no': tenant_user.mobile_no,
        'status': tenant_user.status,
        'roles': [tenant_user.role_name] if tenant_user.role_name else [],
    }


class TenantUsersAdministrationCreateView(View):
    """Tenant users administration create page."""

    def get(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            context.update(
                {
                    'role_options': _tenant_role_name_options(),
                    'form_data': {
                        'mobile_country_code': '',
                        'status': '',
                        'roles': [],
                    },
                    'form_errors': {},
                    'tenant_schema_name': tenant_registry.schema_name,
                    'is_edit_mode': False,
                    'is_view_mode': False,
                }
            )
            return render(
                request,
                'iroad_tenants/User_Management/Users-administration-create.html',
                context,
            )
        finally:
            connection.set_schema_to_public()

    def post(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response

        form_data = _tenant_user_form_data_from_post(request)
        password = (request.POST.get('password') or '').strip()
        form_errors = {}

        try:
            role_options = _tenant_role_name_options()
            if not form_data['username']:
                form_errors['username'] = 'Username is required.'
            if not form_data['full_name']:
                form_errors['full_name'] = 'Full Name is required.'
            if not form_data['email']:
                form_errors['email'] = 'Email is required.'
            if not form_data['roles']:
                form_errors['roles'] = 'Select at least one role.'
            else:
                invalid_roles = [role for role in form_data['roles'] if role not in role_options]
                if invalid_roles:
                    form_errors['roles'] = 'Selected role is invalid. Please choose from Roles master.'
            if not password:
                form_errors['password'] = 'Password is required.'
            elif len(password) < 8:
                form_errors['password'] = 'Password must be at least 8 characters.'

            if form_data['username'] and TenantUser.objects.filter(username__iexact=form_data['username']).exists():
                form_errors['username'] = 'This username already exists in this tenant.'
            if form_data['email'] and TenantUser.objects.filter(email__iexact=form_data['email']).exists():
                form_errors['email'] = 'This email already exists in this tenant.'
            tenant_primary_email = (context['tenant'].primary_email or '').strip().lower()
            if form_data['email'] and tenant_primary_email and form_data['email'] == tenant_primary_email:
                form_errors['email'] = (
                    'Tenant user email cannot be the same as the tenant primary login email.'
                )

            if form_errors:
                context.update(
                    {
                        'role_options': role_options,
                        'form_data': form_data,
                        'form_errors': form_errors,
                        'tenant_schema_name': tenant_registry.schema_name,
                        'is_edit_mode': False,
                        'is_view_mode': False,
                    }
                )
                messages.error(request, 'Please fix the highlighted errors.', extra_tags='tenant')
                return render(
                    request,
                    'iroad_tenants/User_Management/Users-administration-create.html',
                    context,
                )

            user_ref_no, account_sequence = _next_auto_number_for_form(
                form_code='users-administration',
                form_label='Users Administration',
                prefix='USR',
            )

            selected_role = form_data['roles'][0] if form_data['roles'] else 'Administrator'
            tenant_user = TenantUser.objects.create(
                tenant_ref_no=user_ref_no,
                account_sequence=account_sequence,
                username=form_data['username'],
                full_name=form_data['full_name'],
                email=form_data['email'],
                mobile_country_code=form_data['mobile_country_code'],
                mobile_no=form_data['mobile_no'],
                password_hash=make_password(password),
                temp_password_expires_at=timezone.now() + timezone.timedelta(hours=24),
                role_name=selected_role,
                status=form_data['status'],
                created_by_label=(context.get('display_name') or '').strip(),
            )
            try:
                email_sent = _send_tenant_user_welcome_email(
                    request=request,
                    tenant_user=tenant_user,
                    plaintext_password=password,
                    role_name=selected_role,
                )
                if email_sent:
                    messages.success(
                        request,
                        'Login credentials email sent to the user.',
                        extra_tags='tenant',
                    )
                else:
                    messages.warning(
                        request,
                        'User created, but no active notification template found for login email.',
                        extra_tags='tenant',
                    )
            except Exception:
                logger.exception(
                    'Tenant user welcome email failed for %s',
                    tenant_user.email,
                )
                messages.warning(
                    request,
                    'User created, but login email could not be sent. Please verify email gateway/template settings.',
                    extra_tags='tenant',
                )
            messages.success(request, 'Tenant user created successfully.', extra_tags='tenant')
            return _tenant_redirect(request, 'iroad_tenants:tenant_users_administration')
        finally:
            connection.set_schema_to_public()


class TenantUsersAdministrationEditView(View):
    """Tenant users edit/view page."""

    def get(self, request, user_id):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            tenant_user = TenantUser.objects.filter(pk=user_id).first()
            if tenant_user is None:
                messages.error(request, 'User not found.', extra_tags='tenant')
                return _tenant_redirect(request, 'iroad_tenants:tenant_users_administration')
            is_view_mode = request.GET.get('mode') == 'view'
            role_options = _tenant_role_name_options()
            if tenant_user.role_name and tenant_user.role_name not in role_options:
                role_options = [tenant_user.role_name, *role_options]
            context.update(
                {
                    'role_options': role_options,
                    'form_data': _tenant_user_form_data_from_model(tenant_user),
                    'form_errors': {},
                    'tenant_schema_name': tenant_registry.schema_name,
                    'is_edit_mode': True,
                    'is_view_mode': is_view_mode,
                    'editing_user': tenant_user,
                }
            )
            return render(
                request,
                'iroad_tenants/User_Management/Users-administration-create.html',
                context,
            )
        finally:
            connection.set_schema_to_public()

    def post(self, request, user_id):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            tenant_user = TenantUser.objects.filter(pk=user_id).first()
            if tenant_user is None:
                messages.error(request, 'User not found.', extra_tags='tenant')
                return _tenant_redirect(request, 'iroad_tenants:tenant_users_administration')

            form_data = _tenant_user_form_data_from_post(request)
            form_errors = {}
            role_options = _tenant_role_name_options()
            if tenant_user.role_name and tenant_user.role_name not in role_options:
                role_options = [tenant_user.role_name, *role_options]

            if not form_data['username']:
                form_errors['username'] = 'Username is required.'
            if not form_data['full_name']:
                form_errors['full_name'] = 'Full Name is required.'
            if not form_data['email']:
                form_errors['email'] = 'Email is required.'
            if not form_data['roles']:
                form_errors['roles'] = 'Select at least one role.'
            else:
                invalid_roles = [role for role in form_data['roles'] if role not in role_options]
                if invalid_roles:
                    form_errors['roles'] = 'Selected role is invalid. Please choose from Roles master.'
            if form_data['username'] and TenantUser.objects.filter(username__iexact=form_data['username']).exclude(pk=tenant_user.pk).exists():
                form_errors['username'] = 'This username already exists in this tenant.'
            if form_data['email'] and TenantUser.objects.filter(email__iexact=form_data['email']).exclude(pk=tenant_user.pk).exists():
                form_errors['email'] = 'This email already exists in this tenant.'
            tenant_primary_email = (context['tenant'].primary_email or '').strip().lower()
            if form_data['email'] and tenant_primary_email and form_data['email'] == tenant_primary_email:
                form_errors['email'] = (
                    'Tenant user email cannot be the same as the tenant primary login email.'
                )

            if form_errors:
                context.update(
                    {
                        'role_options': role_options,
                        'form_data': form_data,
                        'form_errors': form_errors,
                        'tenant_schema_name': tenant_registry.schema_name,
                        'is_edit_mode': True,
                        'is_view_mode': False,
                        'editing_user': tenant_user,
                    }
                )
                messages.error(request, 'Please fix the highlighted errors.', extra_tags='tenant')
                return render(
                    request,
                    'iroad_tenants/User_Management/Users-administration-create.html',
                    context,
                )

            tenant_user.username = form_data['username']
            tenant_user.full_name = form_data['full_name']
            tenant_user.email = form_data['email']
            tenant_user.mobile_country_code = form_data['mobile_country_code']
            tenant_user.mobile_no = form_data['mobile_no']
            tenant_user.status = form_data['status']
            tenant_user.role_name = form_data['roles'][0]
            tenant_user.save()

            messages.success(request, 'Tenant user updated successfully.', extra_tags='tenant')
            return _tenant_redirect(request, 'iroad_tenants:tenant_users_administration')
        finally:
            connection.set_schema_to_public()


class TenantUsersAdministrationToggleStatusView(View):
    """Activate/deactivate tenant user."""

    def post(self, request, user_id):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            tenant_user = TenantUser.objects.filter(pk=user_id).first()
            if tenant_user is None:
                messages.error(request, 'User not found.', extra_tags='tenant')
                return _tenant_redirect(request, 'iroad_tenants:tenant_users_administration')
            tenant_user.status = (
                TenantUser.Status.INACTIVE
                if tenant_user.status == TenantUser.Status.ACTIVE
                else TenantUser.Status.ACTIVE
            )
            tenant_user.save(update_fields=['status', 'updated_at'])
            messages.success(request, f'User status changed to {tenant_user.status}.', extra_tags='tenant')
            return _tenant_redirect(request, 'iroad_tenants:tenant_users_administration')
        finally:
            connection.set_schema_to_public()


class TenantUsersAdministrationDeleteView(View):
    """Delete tenant user from current tenant schema."""

    def post(self, request, user_id):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            tenant_user = TenantUser.objects.filter(pk=user_id).first()
            if tenant_user is None:
                messages.error(request, 'User not found.', extra_tags='tenant')
                return _tenant_redirect(request, 'iroad_tenants:tenant_users_administration')
            tenant_user.delete()
            messages.success(request, 'User deleted successfully.', extra_tags='tenant')
            return _tenant_redirect(request, 'iroad_tenants:tenant_users_administration')
        finally:
            connection.set_schema_to_public()


class TenantUsersAdministrationExportView(View):
    """Export current tenant users as CSV."""

    def get(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            tenant_users = TenantUser.objects.all().order_by('created_at', 'updated_at')
            response = HttpResponse(content_type='text/csv')
            response['Content-Disposition'] = 'attachment; filename="tenant_users_export.csv"'

            writer = csv.writer(response)
            writer.writerow([
                'User Ref No',
                'User ID',
                'Full Name',
                'Email',
                'Username',
                'Role',
                'Status',
                'Last Login',
                'Login Attempts',
                'Created By',
                'Created At',
                'Updated At',
            ])
            for tenant_user in tenant_users:
                writer.writerow([
                    tenant_user.tenant_ref_no,
                    str(tenant_user.user_id),
                    tenant_user.full_name,
                    tenant_user.email,
                    tenant_user.username,
                    tenant_user.role_name,
                    tenant_user.status,
                    tenant_user.last_login_at.isoformat() if tenant_user.last_login_at else '',
                    tenant_user.login_attempts,
                    tenant_user.created_by_label,
                    tenant_user.created_at.isoformat() if tenant_user.created_at else '',
                    tenant_user.updated_at.isoformat() if tenant_user.updated_at else '',
                ])
            return response
        finally:
            connection.set_schema_to_public()


def _tenant_role_form_data_from_post(request):
    return {
        'role_name_en': (request.POST.get('role_name_en') or '').strip(),
        'role_name_ar': (request.POST.get('role_name_ar') or '').strip(),
        'description_en': (request.POST.get('description_en') or '').strip(),
        'description_ar': (request.POST.get('description_ar') or '').strip(),
        'status': 'Active' if request.POST.get('status') == 'on' else 'Inactive',
    }


def _tenant_role_form_data_from_model(role):
    return {
        'role_name_en': role.role_name_en,
        'role_name_ar': role.role_name_ar,
        'description_en': role.description_en,
        'description_ar': role.description_ar,
        'created_by_label': role.created_by_label,
        'status': role.status,
    }


def _permissions_payload_from_post(request):
    rows = []
    for idx, item in enumerate(TENANT_PERMISSION_MATRIX):
        rows.append(
            {
                'module_name': item['module_name'],
                'form_name': item['form_name'],
                'can_view': request.POST.get(f'perm_{idx}_view') == 'on',
                'can_create': request.POST.get(f'perm_{idx}_create') == 'on',
                'can_edit': request.POST.get(f'perm_{idx}_edit') == 'on',
                'can_delete': request.POST.get(f'perm_{idx}_delete') == 'on',
                'can_post': request.POST.get(f'perm_{idx}_post') == 'on',
                'can_approve': request.POST.get(f'perm_{idx}_approve') == 'on',
                'can_export': request.POST.get(f'perm_{idx}_export') == 'on',
                'can_print': request.POST.get(f'perm_{idx}_print') == 'on',
            }
        )
    return rows


def _permissions_by_key(role):
    perms = {}
    for permission in role.permissions.all():
        key = f'{permission.module_name}|{permission.form_name}'
        perms[key] = {
            'can_view': permission.can_view,
            'can_create': permission.can_create,
            'can_edit': permission.can_edit,
            'can_delete': permission.can_delete,
            'can_post': permission.can_post,
            'can_approve': permission.can_approve,
            'can_export': permission.can_export,
            'can_print': permission.can_print,
        }
    return perms


def _permission_matrix_with_values(permission_map=None):
    permission_map = permission_map or {}
    matrix_rows = []
    for item in TENANT_PERMISSION_MATRIX:
        key = f"{item['module_name']}|{item['form_name']}"
        matrix_rows.append(
            {
                'module_name': item['module_name'],
                'form_name': item['form_name'],
                'can_view': permission_map.get(key, {}).get('can_view', False),
                'can_create': permission_map.get(key, {}).get('can_create', False),
                'can_edit': permission_map.get(key, {}).get('can_edit', False),
                'can_delete': permission_map.get(key, {}).get('can_delete', False),
                'can_post': permission_map.get(key, {}).get('can_post', False),
                'can_approve': permission_map.get(key, {}).get('can_approve', False),
                'can_export': permission_map.get(key, {}).get('can_export', False),
                'can_print': permission_map.get(key, {}).get('can_print', False),
            }
        )
    return matrix_rows


class TenantRolesPermissionsView(View):
    """Tenant roles and permissions list page."""

    def get(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            tenant_roles = list(TenantRole.objects.all().order_by('-created_at', '-updated_at')[:100])
            total_roles = len(tenant_roles)
            active_roles = sum(1 for role in tenant_roles if role.status == TenantRole.Status.ACTIVE)
            inactive_roles = sum(1 for role in tenant_roles if role.status == TenantRole.Status.INACTIVE)
            draft_roles = sum(1 for role in tenant_roles if role.status == TenantRole.Status.DRAFT)
            context.update(
                {
                    'tenant_roles': tenant_roles,
                    'roles_total_count': total_roles,
                    'roles_active_count': active_roles,
                    'roles_inactive_count': inactive_roles,
                    'roles_draft_count': draft_roles,
                    'tenant_schema_name': tenant_registry.schema_name,
                }
            )
        finally:
            connection.set_schema_to_public()
        return render(
            request,
            'iroad_tenants/User_Management/Role/Roles--permissions.html',
            context,
        )


class TenantRolesPermissionsCreateView(View):
    """Tenant role create page."""

    def get(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        context.update(
            {
                'form_data': {
                    'status': 'Active',
                    'created_by_label': (context.get('display_name') or '').strip(),
                },
                'form_errors': {},
                'permission_matrix': _permission_matrix_with_values(),
                'is_edit_mode': False,
                'is_view_mode': False,
            }
        )
        return render(
            request,
            'iroad_tenants/User_Management/Role/Roles-permissions-Create.html',
            context,
        )

    def post(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        form_data = _tenant_role_form_data_from_post(request)
        form_data['created_by_label'] = (context.get('display_name') or '').strip()
        permissions_payload = _permissions_payload_from_post(request)
        form_errors = {}
        try:
            if not form_data['role_name_en']:
                form_errors['role_name_en'] = 'Role name in English is required.'
            if not form_data['role_name_ar']:
                form_errors['role_name_ar'] = 'Role name in Arabic is required.'
            if form_data['role_name_en'] and TenantRole.objects.filter(
                role_name_en__iexact=form_data['role_name_en']
            ).exists():
                form_errors['role_name_en'] = 'This role name already exists in this tenant.'
            if form_data['role_name_ar'] and TenantRole.objects.filter(
                role_name_ar__iexact=form_data['role_name_ar']
            ).exists():
                form_errors['role_name_ar'] = 'This Arabic role name already exists in this tenant.'

            if form_errors:
                context.update(
                    {
                        'form_data': form_data,
                        'form_errors': form_errors,
                        'permission_matrix': permissions_payload,
                        'tenant_schema_name': tenant_registry.schema_name,
                        'is_edit_mode': False,
                        'is_view_mode': False,
                    }
                )
                messages.error(request, 'Please fix the highlighted errors.', extra_tags='tenant')
                return render(
                    request,
                    'iroad_tenants/User_Management/Role/Roles-permissions-Create.html',
                    context,
                )

            tenant_role = TenantRole.objects.create(
                role_name_en=form_data['role_name_en'],
                role_name_ar=form_data['role_name_ar'],
                description_en=form_data['description_en'],
                description_ar=form_data['description_ar'],
                status=form_data['status'],
                created_by_label=(context.get('display_name') or '').strip(),
            )
            TenantRolePermission.objects.bulk_create(
                [
                    TenantRolePermission(
                        role=tenant_role,
                        module_name=item['module_name'],
                        form_name=item['form_name'],
                        can_view=item['can_view'],
                        can_create=item['can_create'],
                        can_edit=item['can_edit'],
                        can_delete=item['can_delete'],
                        can_post=item['can_post'],
                        can_approve=item['can_approve'],
                        can_export=item['can_export'],
                        can_print=item['can_print'],
                    )
                    for item in permissions_payload
                ]
            )
            messages.success(request, 'Role created successfully.', extra_tags='tenant')
            return _tenant_redirect(request, 'iroad_tenants:tenant_roles_permissions')
        finally:
            connection.set_schema_to_public()


class TenantRolesPermissionsEditView(View):
    """Tenant role edit/view page."""

    def get(self, request, role_id):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            tenant_role = TenantRole.objects.filter(pk=role_id).prefetch_related('permissions').first()
            if tenant_role is None:
                messages.error(request, 'Role not found.', extra_tags='tenant')
                return _tenant_redirect(request, 'iroad_tenants:tenant_roles_permissions')
            is_view_mode = request.GET.get('mode') == 'view'
            context.update(
                {
                    'form_data': {
                        **_tenant_role_form_data_from_model(tenant_role),
                        'created_by_label': (context.get('display_name') or '').strip(),
                    },
                    'form_errors': {},
                    'permission_matrix': _permission_matrix_with_values(_permissions_by_key(tenant_role)),
                    'tenant_schema_name': tenant_registry.schema_name,
                    'is_edit_mode': True,
                    'is_view_mode': is_view_mode,
                    'editing_role': tenant_role,
                }
            )
            return render(
                request,
                'iroad_tenants/User_Management/Role/Roles-permissions-Create.html',
                context,
            )
        finally:
            connection.set_schema_to_public()

    def post(self, request, role_id):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        form_data = _tenant_role_form_data_from_post(request)
        form_data['created_by_label'] = (context.get('display_name') or '').strip()
        permissions_payload = _permissions_payload_from_post(request)
        form_errors = {}
        try:
            tenant_role = TenantRole.objects.filter(pk=role_id).first()
            if tenant_role is None:
                messages.error(request, 'Role not found.', extra_tags='tenant')
                return _tenant_redirect(request, 'iroad_tenants:tenant_roles_permissions')

            if not form_data['role_name_en']:
                form_errors['role_name_en'] = 'Role name in English is required.'
            if not form_data['role_name_ar']:
                form_errors['role_name_ar'] = 'Role name in Arabic is required.'
            if form_data['role_name_en'] and TenantRole.objects.filter(
                role_name_en__iexact=form_data['role_name_en']
            ).exclude(pk=tenant_role.pk).exists():
                form_errors['role_name_en'] = 'This role name already exists in this tenant.'
            if form_data['role_name_ar'] and TenantRole.objects.filter(
                role_name_ar__iexact=form_data['role_name_ar']
            ).exclude(pk=tenant_role.pk).exists():
                form_errors['role_name_ar'] = 'This Arabic role name already exists in this tenant.'

            if form_errors:
                context.update(
                    {
                        'form_data': form_data,
                        'form_errors': form_errors,
                        'permission_matrix': permissions_payload,
                        'tenant_schema_name': tenant_registry.schema_name,
                        'is_edit_mode': True,
                        'is_view_mode': False,
                        'editing_role': tenant_role,
                    }
                )
                messages.error(request, 'Please fix the highlighted errors.', extra_tags='tenant')
                return render(
                    request,
                    'iroad_tenants/User_Management/Role/Roles-permissions-Create.html',
                    context,
                )

            tenant_role.role_name_en = form_data['role_name_en']
            tenant_role.role_name_ar = form_data['role_name_ar']
            tenant_role.description_en = form_data['description_en']
            tenant_role.description_ar = form_data['description_ar']
            tenant_role.status = form_data['status']
            tenant_role.created_by_label = (context.get('display_name') or '').strip()
            tenant_role.save()

            TenantRolePermission.objects.filter(role=tenant_role).delete()
            TenantRolePermission.objects.bulk_create(
                [
                    TenantRolePermission(
                        role=tenant_role,
                        module_name=item['module_name'],
                        form_name=item['form_name'],
                        can_view=item['can_view'],
                        can_create=item['can_create'],
                        can_edit=item['can_edit'],
                        can_delete=item['can_delete'],
                        can_post=item['can_post'],
                        can_approve=item['can_approve'],
                        can_export=item['can_export'],
                        can_print=item['can_print'],
                    )
                    for item in permissions_payload
                ]
            )
            messages.success(request, 'Role updated successfully.', extra_tags='tenant')
            return _tenant_redirect(request, 'iroad_tenants:tenant_roles_permissions')
        finally:
            connection.set_schema_to_public()


class TenantRolesPermissionsToggleStatusView(View):
    """Activate/deactivate tenant role."""

    def post(self, request, role_id):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            tenant_role = TenantRole.objects.filter(pk=role_id).first()
            if tenant_role is None:
                messages.error(request, 'Role not found.', extra_tags='tenant')
                return _tenant_redirect(request, 'iroad_tenants:tenant_roles_permissions')
            tenant_role.status = (
                TenantRole.Status.INACTIVE
                if tenant_role.status == TenantRole.Status.ACTIVE
                else TenantRole.Status.ACTIVE
            )
            tenant_role.save(update_fields=['status', 'updated_at'])
            messages.success(request, f'Role status changed to {tenant_role.status}.', extra_tags='tenant')
            return _tenant_redirect(request, 'iroad_tenants:tenant_roles_permissions')
        finally:
            connection.set_schema_to_public()


class TenantRolesPermissionsDeleteView(View):
    """Delete tenant role from current tenant schema."""

    def post(self, request, role_id):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            tenant_role = TenantRole.objects.filter(pk=role_id).first()
            if tenant_role is None:
                messages.error(request, 'Role not found.', extra_tags='tenant')
                return _tenant_redirect(request, 'iroad_tenants:tenant_roles_permissions')
            tenant_role.delete()
            messages.success(request, 'Role deleted successfully.', extra_tags='tenant')
            return _tenant_redirect(request, 'iroad_tenants:tenant_roles_permissions')
        finally:
            connection.set_schema_to_public()


class TenantRolesPermissionsExportView(View):
    """Export current tenant roles as CSV."""

    def get(self, request):
        context = _tenant_context_from_session(request)
        if context is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        tenant_registry = _activate_tenant_workspace_schema(request)
        if tenant_registry is None:
            response = redirect('login')
            clear_tenant_portal_cookie(response, request=request)
            return response
        try:
            tenant_roles = TenantRole.objects.all().order_by('created_at', 'updated_at')
            response = HttpResponse(content_type='text/csv')
            response['Content-Disposition'] = 'attachment; filename="tenant_roles_export.csv"'

            writer = csv.writer(response)
            writer.writerow(
                [
                    'Role ID',
                    'Role Name (English)',
                    'Role Name (Arabic)',
                    'Description (English)',
                    'Description (Arabic)',
                    'Status',
                    'Created By',
                    'Created At',
                    'Updated At',
                ]
            )
            for tenant_role in tenant_roles:
                writer.writerow(
                    [
                        str(tenant_role.role_id),
                        tenant_role.role_name_en,
                        tenant_role.role_name_ar,
                        tenant_role.description_en,
                        tenant_role.description_ar,
                        tenant_role.status,
                        tenant_role.created_by_label,
                        tenant_role.created_at.isoformat() if tenant_role.created_at else '',
                        tenant_role.updated_at.isoformat() if tenant_role.updated_at else '',
                    ]
                )
            return response
        finally:
            connection.set_schema_to_public()


def _active_currency_options():
    """Active ISO currencies from superadmin master data (``master_currencies``)."""
    return list(
        Currency.objects.filter(is_active=True)
        .order_by('name_en', 'currency_code')
        .values('currency_code', 'name_en')
    )


def _organization_form_context(profile):
    selected_timezone = (profile.timezone or 'Asia/Riyadh').strip() or 'Asia/Riyadh'
    return {
        'org': profile,
        'owner_label': _owner_user_label(profile.owner_user_id),
        'logo_display_name': _logo_display_name(profile),
        'countries': list(
            Country.objects.filter(is_active=True).order_by('name_en').values(
                'country_code',
                'name_en',
            ),
        ),
        'currencies': list(
            Currency.objects.filter(is_active=True).order_by('currency_code').values(
                'currency_code',
                'name_en',
            ),
        ),
        'date_format_choices': OrganizationProfile.DATE_FORMAT_CHOICES,
        'number_format_choices': OrganizationProfile.NUMBER_FORMAT_CHOICES,
        'negative_format_choices': OrganizationProfile.NEGATIVE_FORMAT_CHOICES,
        'language_choices': OrganizationProfile.SYSTEM_LANGUAGE_CHOICES,
        'selected_timezone': selected_timezone,
        'timezone_choices': [
            'Asia/Riyadh',
            'UTC',
            'Asia/Dubai',
            'Europe/London',
        ],
        'org_status_label': 'Active',
    }


def _owner_user_label(owner_user_id):
    if not owner_user_id:
        return 'N/A'
    # In tenant workspace, owner_user_id is seeded from signup tenant profile id.
    tenant_owner = TenantProfile.objects.filter(pk=owner_user_id).first()
    if tenant_owner:
        tenant_name = (
            f"{(tenant_owner.first_name or '').strip()} {(tenant_owner.last_name or '').strip()}"
        ).strip()
        if tenant_name:
            return tenant_name
        return (tenant_owner.company_name or tenant_owner.primary_email or 'N/A').strip()

    # Fallback: support admin user ids if used by future migrations.
    owner = AdminUser.objects.filter(pk=owner_user_id).first()
    if owner:
        label = f'{owner.first_name} {owner.last_name}'.strip()
        return label or owner.email
    return 'N/A'


def _logo_display_name(profile):
    if not getattr(profile, 'logo_file', None):
        return ''
    return os.path.basename(profile.logo_file.name or '')


def _organization_status_from_tenant(tenant):
    """Map superadmin account status to Organization Profile read-only status."""
    status = (getattr(tenant, 'account_status', '') or '').strip()
    if status == 'Active':
        return 'Active'
    if status.startswith('Suspended_'):
        return 'Suspended'
    # Keep UI constrained to the documented values.
    return 'Suspended'


def _get_or_create_organization_profile(tenant):
    config, _ = AutoNumberConfiguration.objects.get_or_create(
        form_code='organization-profile',
        defaults={
            'form_label': 'Organization Profile',
            'number_of_digits': 4,
            'sequence_format': AutoNumberConfiguration.SequenceFormat.NUMERIC,
            'is_unique': True,
        },
    )
    # Use a deterministic "active" record in case historical duplicates exist.
    profile = (
        OrganizationProfile.objects.order_by('-updated_at', '-created_at').first()
    )
    if profile:
        if not profile.owner_user_id:
            profile.owner_user_id = str(tenant.pk)
            profile.save(update_fields=['owner_user_id', 'updated_at'])
        return profile

    seq, _ = AutoNumberSequence.objects.get_or_create(
        form_code='organization-profile',
        defaults={'next_number': 1},
    )
    account_sequence = seq.next_number
    ref_no = _render_tenant_ref_no(account_sequence, config, prefix='ORG')

    default_currency = (
        Currency.objects.filter(is_active=True).order_by('currency_code').first()
    )
    default_country = (
        Country.objects.filter(is_active=True).order_by('name_en').first()
    )

    profile = OrganizationProfile.objects.create(
        tenant_ref_no=ref_no,
        account_sequence=account_sequence,
        owner_user_id=str(tenant.pk),
        name_ar=tenant.company_name or '',
        name_en=tenant.company_name or '',
        cr_number=tenant.registration_number or '',
        tax_number=tenant.tax_number or '',
        primary_email=tenant.primary_email or '',
        primary_mobile=tenant.primary_phone or '',
        country_code=(default_country.country_code if default_country else ''),
        base_currency_code=(default_currency.currency_code if default_currency else ''),
        timezone='Asia/Riyadh',
    )
    seq.next_number = account_sequence + 1
    seq.save(update_fields=['next_number', 'updated_at'])
    return profile


def _sync_tenant_ref_if_config_changed(profile):
    config = AutoNumberConfiguration.objects.filter(
        form_code='organization-profile',
    ).first()
    if not config:
        return
    expected = _render_tenant_ref_no(profile.account_sequence, config, prefix='ORG')
    if profile.tenant_ref_no != expected:
        profile.tenant_ref_no = expected
        profile.save(update_fields=['tenant_ref_no', 'updated_at'])


def _render_tenant_ref_no(sequence, config, prefix='ORG'):
    n = int(sequence or 1)
    digits = max(1, int(config.number_of_digits or 4))
    if config.sequence_format == AutoNumberConfiguration.SequenceFormat.ALPHA:
        rendered = _int_to_alpha(n).rjust(digits, 'A')
    elif config.sequence_format == AutoNumberConfiguration.SequenceFormat.ALPHANUMERIC:
        number_digits = max(1, digits - 1)
        rendered = f'{_int_to_alpha(n)}{str(n).zfill(number_digits)}'
    else:
        rendered = str(n).zfill(digits)
    return f'{prefix}-{rendered}'


def _preview_next_auto_number_for_form(*, form_code: str, form_label: str, prefix: str) -> str:
    """Preview next ref no for a form without consuming sequence.

    This is the shared, reusable preview helper. It reads:
    - AutoNumberConfiguration (creates default if missing)
    - AutoNumberSequence (reads next_number if exists; does NOT increment)
    and renders via `_render_tenant_ref_no(...)`.
    """
    config, _ = AutoNumberConfiguration.objects.get_or_create(
        form_code=form_code,
        defaults={
            'form_label': form_label,
            'number_of_digits': 4,
            'sequence_format': AutoNumberConfiguration.SequenceFormat.NUMERIC,
            'is_unique': True,
        },
    )
    sequence = AutoNumberSequence.objects.filter(form_code=form_code).first()
    next_seq = sequence.next_number if sequence else 1
    return _render_tenant_ref_no(next_seq, config, prefix=prefix)


def _next_auto_number_for_form(form_code, form_label, prefix):
    config, _ = AutoNumberConfiguration.objects.get_or_create(
        form_code=form_code,
        defaults={
            'form_label': form_label,
            'number_of_digits': 4,
            'sequence_format': AutoNumberConfiguration.SequenceFormat.NUMERIC,
            'is_unique': True,
        },
    )
    sequence, _ = AutoNumberSequence.objects.get_or_create(
        form_code=form_code,
        defaults={'next_number': 1},
    )
    account_sequence = int(sequence.next_number or 1)
    ref_no = _render_tenant_ref_no(account_sequence, config, prefix=prefix)
    sequence.next_number = account_sequence + 1
    sequence.save(update_fields=['next_number', 'updated_at'])
    return ref_no, account_sequence


def _sales_invoice_report_lock_state(status: str) -> str:
    s = (status or '').strip()
    if s == SalesInvoiceReport.Status.CONVERTED:
        return 'locked'
    if s == SalesInvoiceReport.Status.VERIFIED:
        return 'partial'
    return 'editable'


def _sales_invoice_report_forms_with_preview(*, instance=None, data=None):
    preview_no = _preview_next_auto_number_for_form(
        form_code=SALES_INVOICE_REPORT_AUTO_FORM_CODE,
        form_label=SALES_INVOICE_REPORT_AUTO_FORM_LABEL,
        prefix=SALES_INVOICE_REPORT_REF_PREFIX,
    )
    report_form = SalesInvoiceReportForm(data=data, instance=instance)
    if not getattr(instance, 'pk', None):
        report_form.fields['report_no'].initial = preview_no
    booking_form = SalesInvoiceReportBookingForm()
    surcharge_form = SalesInvoiceReportSurchargeForm()
    shipment_form = SalesInvoiceReportShipmentForm()
    return {
        'report_form': report_form,
        'booking_form': booking_form,
        'surcharge_form': surcharge_form,
        'shipment_form': shipment_form,
        'report_no_preview': preview_no,
    }


def _safe_get_tenant_model(model_name: str):
    try:
        return apps.get_model('tenant_workspace', model_name)
    except LookupError:
        return None


def _eligible_executed_bookings_for_report(*, client_id, booking_date_from, booking_date_to, currency):
    """
    Fetch source bookings eligible for Sales Invoice Report auto-load.
    Returns list[dict] snapshots ready for SalesInvoiceReportBooking rows.
    """
    BookingModel = _safe_get_tenant_model('Booking')
    if BookingModel is None:
        return []

    qs = BookingModel.objects.filter(client_id=client_id)
    if hasattr(BookingModel, 'status'):
        qs = qs.filter(status__iexact='executed')
    if hasattr(BookingModel, 'booking_date'):
        qs = qs.filter(booking_date__gte=booking_date_from, booking_date__lte=booking_date_to)
    if currency and hasattr(BookingModel, 'currency'):
        qs = qs.filter(currency=currency)

    # Exclude bookings already used in converted reports.
    converted_booking_ids = (
        SalesInvoiceReportBooking.objects.filter(
            report__status=SalesInvoiceReport.Status.CONVERTED,
            booking_ref__isnull=False,
        ).values_list('booking_ref', flat=True)
    )
    if hasattr(BookingModel, 'booking_id'):
        qs = qs.exclude(booking_id__in=list(converted_booking_ids))
    elif hasattr(BookingModel, 'id'):
        qs = qs.exclude(id__in=list(converted_booking_ids))

    rows = []
    seen_booking_refs = set()
    for b in qs.order_by('booking_date'):
        booking_pk = getattr(b, 'booking_id', None) or getattr(b, 'id', None)
        if booking_pk in seen_booking_refs:
            continue
        seen_booking_refs.add(booking_pk)
        rows.append(
            {
                'line_no': len(rows) + 1,
                'booking_ref': booking_pk,
                'so_ref': getattr(b, 'sales_order_no', '') or '',
                'service_name': getattr(b, 'service_name', '') or '',
                'trip_type': getattr(b, 'trip_type', '') or '',
                'sell_price': getattr(b, 'sell_price', 0) or 0,
                'booking_status': 'Executed',
            }
        )
    return rows


def _eligible_confirmed_surcharges_for_report(*, booking_refs):
    SurchargeModel = _safe_get_tenant_model('SurchargeSalesTransaction')
    if SurchargeModel is None or not booking_refs:
        return []

    qs = SurchargeModel.objects.all()
    if hasattr(SurchargeModel, 'status'):
        qs = qs.filter(status__iexact='confirmed')
    if hasattr(SurchargeModel, 'booking_ref'):
        qs = qs.filter(booking_ref__in=booking_refs)

    rows = []
    seen_refs = set()
    for s in qs.order_by('id'):
        trx_ref = getattr(s, 'id', None)
        if trx_ref in seen_refs:
            continue
        seen_refs.add(trx_ref)
        rows.append(
            {
                'line_no': len(rows) + 1,
                'surcharge_trx_ref': trx_ref,
                'booking_ref': getattr(s, 'booking_ref', None),
                'surcharge_type': getattr(s, 'surcharge_type', '') or '',
                'shipment_ref': str(getattr(s, 'shipment_ref', '') or ''),
                'service_name': getattr(s, 'service_name', '') or '',
                'amount': getattr(s, 'amount', 0) or 0,
            }
        )
    return rows


def _related_shipments_for_report(*, booking_refs):
    ShipmentModel = _safe_get_tenant_model('Shipment')
    if ShipmentModel is None or not booking_refs:
        return []

    qs = ShipmentModel.objects.all()
    if hasattr(ShipmentModel, 'booking_ref'):
        qs = qs.filter(booking_ref__in=booking_refs)

    rows = []
    seen_refs = set()
    for sh in qs.order_by('shipment_date'):
        shipment_ref = getattr(sh, 'id', None)
        if shipment_ref in seen_refs:
            continue
        seen_refs.add(shipment_ref)
        rows.append(
            {
                'line_no': len(rows) + 1,
                'shipment_ref': shipment_ref,
                'booking_ref': str(getattr(sh, 'booking_ref', '') or ''),
                'shipment_date': getattr(sh, 'shipment_date', None),
                'from_location': getattr(sh, 'from_location', '') or '',
                'to_location': getattr(sh, 'to_location', '') or '',
                'truck_plate': getattr(sh, 'truck_plate', '') or '',
                'customer_ref_docs': getattr(sh, 'customer_ref_docs', '') or '',
                'pod_date': getattr(sh, 'pod_date', None),
            }
        )
    return rows


def _sales_invoice_report_preview_payload(*, client_id, booking_date_from, booking_date_to, currency):
    if not client_id:
        raise ValidationError('Client is required.')
    if not booking_date_from or not booking_date_to:
        raise ValidationError('Booking date range is required.')
    if booking_date_from > booking_date_to:
        raise ValidationError('Booking date from cannot be later than booking date to.')
    if not currency:
        raise ValidationError('Currency is required.')

    rows = _eligible_executed_bookings_for_report(
        client_id=client_id,
        booking_date_from=booking_date_from,
        booking_date_to=booking_date_to,
        currency=currency,
    )
    booking_refs = [r.get('booking_ref') for r in rows if r.get('booking_ref')]
    surcharge_rows = _eligible_confirmed_surcharges_for_report(booking_refs=booking_refs)
    shipment_rows = _related_shipments_for_report(booking_refs=booking_refs)
    total_freight = sum(Decimal(str(r.get('sell_price') or 0)) for r in rows)
    total_surcharge = sum(Decimal(str(r.get('amount') or 0)) for r in surcharge_rows)

    return {
        'ok': True,
        'report_no_preview': _preview_next_auto_number_for_form(
            form_code=SALES_INVOICE_REPORT_AUTO_FORM_CODE,
            form_label=SALES_INVOICE_REPORT_AUTO_FORM_LABEL,
            prefix=SALES_INVOICE_REPORT_REF_PREFIX,
        ),
        'bookings': rows,
        'surcharges': surcharge_rows,
        'shipments': shipment_rows,
        'total_freight_amount': str(total_freight),
        'total_surcharge_amount': str(total_surcharge),
        'combined_total_amount': str(total_freight + total_surcharge),
    }


def _refresh_sales_invoice_report_children(report: SalesInvoiceReport):
    """
    Auto-populate child lines from source operational modules.
    Safe for repeated calls: it fully refreshes child rows.
    """
    if report.is_fully_locked:
        raise ValidationError('Converted reports are locked and cannot be refreshed.')

    bookings = _eligible_executed_bookings_for_report(
        client_id=report.client_id,
        booking_date_from=report.booking_date_from,
        booking_date_to=report.booking_date_to,
        currency=report.currency,
    )
    booking_refs = [row['booking_ref'] for row in bookings if row.get('booking_ref')]
    surcharges = _eligible_confirmed_surcharges_for_report(booking_refs=booking_refs)
    shipments = _related_shipments_for_report(booking_refs=booking_refs)

    with db_transaction.atomic():
        report.booking_lines.all().delete()
        report.surcharge_lines.all().delete()
        report.shipment_lines.all().delete()

        SalesInvoiceReportBooking.objects.bulk_create(
            [SalesInvoiceReportBooking(report=report, **row) for row in bookings]
        )
        SalesInvoiceReportSurcharge.objects.bulk_create(
            [SalesInvoiceReportSurcharge(report=report, **row) for row in surcharges]
        )
        SalesInvoiceReportShipment.objects.bulk_create(
            [SalesInvoiceReportShipment(report=report, **row) for row in shipments]
        )
        report.recalculate_totals(save=True)

    return {
        'bookings_loaded': len(bookings),
        'surcharges_loaded': len(surcharges),
        'shipments_loaded': len(shipments),
        'total_freight_amount': report.total_freight_amount,
        'total_surcharge_amount': report.total_surcharge_amount,
    }


def _create_sales_invoice_report_with_auto_number(*, form: SalesInvoiceReportForm, created_by: str):
    """
    Create Sales Invoice Report header with shared auto-number engine.
    Child auto-population runs after successful header save.
    """
    with db_transaction.atomic():
        report_no, report_seq = _next_auto_number_for_form(
            form_code=SALES_INVOICE_REPORT_AUTO_FORM_CODE,
            form_label=SALES_INVOICE_REPORT_AUTO_FORM_LABEL,
            prefix=SALES_INVOICE_REPORT_REF_PREFIX,
        )
        obj = form.save(commit=False)
        obj.report_no = report_no
        obj.report_sequence = report_seq
        obj.created_by = (created_by or '').strip()
        obj.updated_by = (created_by or '').strip()
        obj.full_clean()
        obj.save()
        _refresh_sales_invoice_report_children(obj)
    return obj


def _preview_next_address_master_code():
    return _preview_next_auto_number_for_form(
        form_code=ADDRESS_MASTER_AUTO_FORM_CODE,
        form_label=ADDRESS_MASTER_AUTO_FORM_LABEL,
        prefix=ADDRESS_MASTER_REF_PREFIX,
    )


def _preview_next_truck_type_code():
    return _preview_next_auto_number_for_form(
        form_code=TRUCK_TYPE_AUTO_FORM_CODE,
        form_label=TRUCK_TYPE_AUTO_FORM_LABEL,
        prefix=TRUCK_TYPE_REF_PREFIX,
    )


def _preview_next_truck_master_code():
    return _preview_next_auto_number_for_form(
        form_code=TRUCK_MASTER_AUTO_FORM_CODE,
        form_label=TRUCK_MASTER_AUTO_FORM_LABEL,
        prefix=TRUCK_MASTER_REF_PREFIX,
    )


def _preview_next_driver_master_code():
    return _preview_next_auto_number_for_form(
        form_code=DRIVER_MASTER_AUTO_FORM_CODE,
        form_label=DRIVER_MASTER_AUTO_FORM_LABEL,
        prefix=DRIVER_MASTER_REF_PREFIX,
    )


def _preview_next_truck_attachment_no():
    return _preview_next_auto_number_for_form(
        form_code=TRUCK_ATTACHMENT_AUTO_FORM_CODE,
        form_label=TRUCK_ATTACHMENT_AUTO_FORM_LABEL,
        prefix=TRUCK_ATTACHMENT_REF_PREFIX,
    )


def _preview_next_driver_attachment_no():
    return _preview_next_auto_number_for_form(
        form_code=DRIVER_ATTACHMENT_AUTO_FORM_CODE,
        form_label=DRIVER_ATTACHMENT_AUTO_FORM_LABEL,
        prefix=DRIVER_ATTACHMENT_REF_PREFIX,
    )


def _preview_next_cargo_master_code():
    config, _ = AutoNumberConfiguration.objects.get_or_create(
        form_code=CARGO_MASTER_AUTO_FORM_CODE,
        defaults={
            'form_label': CARGO_MASTER_AUTO_FORM_LABEL,
            'number_of_digits': 4,
            'sequence_format': AutoNumberConfiguration.SequenceFormat.NUMERIC,
            'is_unique': True,
        },
    )
    sequence = AutoNumberSequence.objects.filter(form_code=CARGO_MASTER_AUTO_FORM_CODE).first()
    next_seq = sequence.next_number if sequence else 1
    return _render_tenant_ref_no(next_seq, config, prefix=CARGO_MASTER_REF_PREFIX)


def _preview_next_cargo_category_code():
    config, _ = AutoNumberConfiguration.objects.get_or_create(
        form_code=CARGO_CATEGORY_AUTO_FORM_CODE,
        defaults={
            'form_label': CARGO_CATEGORY_AUTO_FORM_LABEL,
            'number_of_digits': 4,
            'sequence_format': AutoNumberConfiguration.SequenceFormat.NUMERIC,
            'is_unique': True,
        },
    )
    sequence = AutoNumberSequence.objects.filter(form_code=CARGO_CATEGORY_AUTO_FORM_CODE).first()
    next_seq = sequence.next_number if sequence else 1
    return _render_tenant_ref_no(next_seq, config, prefix=CARGO_CATEGORY_REF_PREFIX)


def _preview_next_location_master_code():
    config, _ = AutoNumberConfiguration.objects.get_or_create(
        form_code=LOCATION_MASTER_AUTO_FORM_CODE,
        defaults={
            'form_label': LOCATION_MASTER_AUTO_FORM_LABEL,
            'number_of_digits': 4,
            'sequence_format': AutoNumberConfiguration.SequenceFormat.NUMERIC,
            'is_unique': True,
        },
    )
    sequence = AutoNumberSequence.objects.filter(form_code=LOCATION_MASTER_AUTO_FORM_CODE).first()
    next_seq = sequence.next_number if sequence else 1
    return _render_tenant_ref_no(next_seq, config, prefix=LOCATION_MASTER_REF_PREFIX)


def _preview_next_route_master_code():
    config, _ = AutoNumberConfiguration.objects.get_or_create(
        form_code=ROUTE_MASTER_AUTO_FORM_CODE,
        defaults={
            'form_label': ROUTE_MASTER_AUTO_FORM_LABEL,
            'number_of_digits': 4,
            'sequence_format': AutoNumberConfiguration.SequenceFormat.NUMERIC,
            'is_unique': True,
        },
    )
    sequence = AutoNumberSequence.objects.filter(form_code=ROUTE_MASTER_AUTO_FORM_CODE).first()
    next_seq = sequence.next_number if sequence else 1
    return _render_tenant_ref_no(next_seq, config, prefix=ROUTE_MASTER_REF_PREFIX)


def _preview_next_service_item_code():
    config, _ = AutoNumberConfiguration.objects.get_or_create(
        form_code=SERVICE_ITEM_MASTER_AUTO_FORM_CODE,
        defaults={
            'form_label': SERVICE_ITEM_MASTER_AUTO_FORM_LABEL,
            'number_of_digits': 4,
            'sequence_format': AutoNumberConfiguration.SequenceFormat.NUMERIC,
            'is_unique': True,
        },
    )
    sequence = AutoNumberSequence.objects.filter(form_code=SERVICE_ITEM_MASTER_AUTO_FORM_CODE).first()
    next_seq = sequence.next_number if sequence else 1
    return _render_tenant_ref_no(next_seq, config, prefix=SERVICE_ITEM_MASTER_REF_PREFIX)


def _preview_next_price_list_code():
    config, _ = AutoNumberConfiguration.objects.get_or_create(
        form_code=PRICE_LIST_MASTER_AUTO_FORM_CODE,
        defaults={
            'form_label': PRICE_LIST_MASTER_AUTO_FORM_LABEL,
            'number_of_digits': 4,
            'sequence_format': AutoNumberConfiguration.SequenceFormat.NUMERIC,
            'is_unique': True,
        },
    )
    sequence = AutoNumberSequence.objects.filter(form_code=PRICE_LIST_MASTER_AUTO_FORM_CODE).first()
    next_seq = sequence.next_number if sequence else 1
    return _render_tenant_ref_no(next_seq, config, prefix=PRICE_LIST_MASTER_REF_PREFIX)


def _validate_cargo_attachment_upload(upload):
    if not upload:
        return ''
    try:
        size = int(upload.size)
    except (TypeError, ValueError):
        size = 0
    if size > MAX_CARGO_ATTACHMENT_BYTES:
        return 'Each attachment must be 10MB or smaller.'
    return ''


def _save_cargo_master_attachments_from_request(request, cargo):
    for upload in request.FILES.getlist('attachments'):
        err = _validate_cargo_attachment_upload(upload)
        if err:
            raise ValidationError(err)
        TenantCargoMasterAttachment.objects.create(cargo_master=cargo, file=upload)


def _cargo_master_list_stats(qs):
    """Stats over current filtered queryset (not paginated slice)."""
    total = qs.count()
    active = qs.filter(status=TenantCargoMaster.Status.ACTIVE).count()
    refrigerated = qs.filter(refrigerated_goods=True).count()
    dangerous = qs.filter(dangerous_goods=True).count()
    return {
        'total': total,
        'active': active,
        'refrigerated': refrigerated,
        'dangerous': dangerous,
    }


def _preview_next_contract_code():
    config, _ = AutoNumberConfiguration.objects.get_or_create(
        form_code=CLIENT_CONTRACT_AUTO_FORM_CODE,
        defaults={
            'form_label': CLIENT_CONTRACT_AUTO_FORM_LABEL,
            'number_of_digits': 4,
            'sequence_format': AutoNumberConfiguration.SequenceFormat.NUMERIC,
            'is_unique': True,
        },
    )
    sequence = AutoNumberSequence.objects.filter(
        form_code=CLIENT_CONTRACT_AUTO_FORM_CODE,
    ).first()
    next_seq = sequence.next_number if sequence else 1
    return _render_tenant_ref_no(next_seq, config, prefix=CLIENT_CONTRACT_REF_PREFIX)


def _int_to_alpha(value):
    num = max(1, int(value))
    chars = []
    while num > 0:
        num, rem = divmod(num - 1, 26)
        chars.append(chr(65 + rem))
    return ''.join(reversed(chars))


def _apply_organization_profile_post(request, profile):
    post = request.POST
    name_ar = (post.get('name_ar') or '').strip()
    name_en = (post.get('name_en') or '').strip()
    cr_number = (post.get('cr_number') or '').strip()
    tax_number = (post.get('tax_number') or '').strip()
    country_code = (post.get('country_code') or '').strip().upper()
    city = (post.get('city') or '').strip()
    street = (post.get('street') or '').strip()
    address_line_1 = (post.get('address_line_1') or '').strip()
    primary_email = (post.get('primary_email') or '').strip()
    primary_mobile = (post.get('primary_mobile') or '').strip()

    # Preserve existing required values when user updates only a subset
    # (e.g. uploading logo), instead of wiping them to empty strings.
    profile.name_ar = name_ar or profile.name_ar
    profile.name_en = name_en or profile.name_en
    profile.cr_number = cr_number or profile.cr_number
    profile.tax_number = tax_number or profile.tax_number
    profile.country_code = country_code or profile.country_code
    profile.city = city or profile.city
    profile.district = (post.get('district') or '').strip()
    profile.street = street or profile.street
    profile.building_no = (post.get('building_no') or '').strip()
    profile.postal_code = (post.get('postal_code') or '').strip()
    profile.address_line_1 = address_line_1 or profile.address_line_1
    profile.address_line_2 = (post.get('address_line_2') or '').strip()
    profile.primary_email = primary_email or profile.primary_email
    profile.primary_mobile = primary_mobile or profile.primary_mobile
    profile.website = (post.get('website') or '').strip()
    if 'secondary_currency_code' in post:
        profile.secondary_currency_code = (post.get('secondary_currency_code') or '').strip().upper()
    if 'support_email' in post:
        profile.support_email = (post.get('support_email') or '').strip()
    if 'support_mobile_1' in post:
        profile.support_mobile_1 = (post.get('support_mobile_1') or '').strip()
    if 'support_mobile_2' in post:
        profile.support_mobile_2 = (post.get('support_mobile_2') or '').strip()
    if 'driver_instructions' in post:
        profile.driver_instructions = (post.get('driver_instructions') or '').strip()
    profile.system_language = (post.get('system_language') or 'en').strip()
    profile.timezone = (post.get('timezone') or 'Asia/Riyadh').strip()
    profile.date_format = (post.get('date_format') or 'DD/MM/YYYY').strip()
    profile.number_format = (post.get('number_format') or '1,234.56').strip()
    profile.negative_format = (post.get('negative_format') or '-100').strip()

    new_base_currency = (post.get('base_currency_code') or '').strip().upper()
    if profile.base_currency_code and new_base_currency and new_base_currency != profile.base_currency_code:
        raise ValueError('Base Currency is immutable after initial setup.')
    if not profile.base_currency_code:
        profile.base_currency_code = new_base_currency

    logo_file = request.FILES.get('logo_file')
    clear_logo = (post.get('clear_logo') or '').strip() == '1'
    if clear_logo:
        if profile.logo_file:
            profile.logo_file.delete(save=False)
        profile.logo_file = None
    if logo_file:
        ext = os.path.splitext(logo_file.name or '')[1].lower() or '.png'
        logo_file.name = f'org_{profile.id}_{uuid.uuid4().hex[:10]}{ext}'
        profile.logo_file = logo_file

    # Keep updates resilient for partially-initialized legacy records:
    # allow partial saves (e.g. logo upload) while still validating
    # critical format rules when values are provided.
    if profile.cr_number and not profile.cr_number.isdigit():
        raise ValueError('CR Number must be numeric.')
