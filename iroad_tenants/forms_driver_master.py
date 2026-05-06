import re

from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django_tenants.utils import schema_context

from superadmin.models import Country
from tenant_workspace.models import DriverMaster

from iroad_tenants.forms_tenant_address import PublicCountryChoiceField


class DriverMasterForm(forms.ModelForm):
    """
    Driver master create/edit. ``driver_code`` is never collected here (auto /
    sequence elsewhere). ``nationality_country`` uses ``PublicCountryChoiceField``;
    it is attached in ``__init__`` because that field requires ``address_instance``.
    """

    class Meta:
        model = DriverMaster
        fields = [
            'driver_source',
            'vendor_account_id',
            'driver_status',
            'driver_type',
            'arabic_name',
            'english_name',
            'nationality_country',
            'birth_date',
            'id_number',
            'id_expiry_date',
            'id_image',
            'passport_number',
            'passport_expiry_date',
            'passport_image',
            'dl_number',
            'dl_expiry_date',
            'dl_image',
            'card_number',
            'card_expiry_date',
            'card_image',
            'mobile_number',
            'whatsapp_number',
            'whatsapp_same_as_mobile',
        ]
        widgets = {
            'driver_source': forms.Select(
                attrs={'class': 'form-select'}
            ),
            'vendor_account_id': forms.TextInput(
                attrs={'class': 'form-control'}
            ),
            'driver_status': forms.Select(
                attrs={'class': 'form-select'}
            ),
            'driver_type': forms.Select(
                attrs={'class': 'form-select'}
            ),
            'arabic_name': forms.TextInput(
                attrs={'class': 'form-control'}
            ),
            'english_name': forms.TextInput(
                attrs={'class': 'form-control'}
            ),
            'birth_date': forms.DateInput(
                attrs={
                    'class': 'form-control',
                    'type': 'date',
                }
            ),
            'id_number': forms.TextInput(
                attrs={
                    'class': 'form-control',
                    'inputmode': 'numeric',
                }
            ),
            'id_expiry_date': forms.DateInput(
                attrs={
                    'class': 'form-control',
                    'type': 'date',
                }
            ),
            'id_image': forms.FileInput(
                attrs={'class': 'form-control'}
            ),
            'passport_number': forms.TextInput(
                attrs={'class': 'form-control'}
            ),
            'passport_expiry_date': forms.DateInput(
                attrs={
                    'class': 'form-control',
                    'type': 'date',
                }
            ),
            'passport_image': forms.FileInput(
                attrs={'class': 'form-control'}
            ),
            'dl_number': forms.TextInput(
                attrs={'class': 'form-control'}
            ),
            'dl_expiry_date': forms.DateInput(
                attrs={
                    'class': 'form-control',
                    'type': 'date',
                }
            ),
            'dl_image': forms.FileInput(
                attrs={'class': 'form-control'}
            ),
            'card_number': forms.TextInput(
                attrs={'class': 'form-control'}
            ),
            'card_expiry_date': forms.DateInput(
                attrs={
                    'class': 'form-control',
                    'type': 'date',
                }
            ),
            'card_image': forms.FileInput(
                attrs={'class': 'form-control'}
            ),
            'mobile_number': forms.TextInput(
                attrs={
                    'class': 'form-control',
                    'inputmode': 'numeric',
                }
            ),
            'whatsapp_number': forms.TextInput(
                attrs={
                    'class': 'form-control',
                    'inputmode': 'numeric',
                }
            ),
            'whatsapp_same_as_mobile': forms.CheckboxInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields.pop('nationality_country', None)
        country_pairs = self._build_country_choices()
        self.fields['nationality_country'] = PublicCountryChoiceField(
            address_instance=self.instance,
            country_fk_attr='nationality_country_id',
            choices=country_pairs,
            required=False,
            label=_('Nationality'),
            widget=forms.Select(attrs={'class': 'form-select'}),
        )
        nid = getattr(self.instance, 'nationality_country_id', None)
        if self.instance.pk and nid:
            self.fields['nationality_country'].initial = nid

        self.fields['vendor_account_id'].required = False
        self.fields['arabic_name'].required = True
        self.fields['mobile_number'].required = True
        self.fields['driver_type'].required = False
        self.fields['english_name'].required = False
        self.fields['birth_date'].required = False

        for name in (
            'id_number',
            'id_expiry_date',
            'id_image',
            'passport_number',
            'passport_expiry_date',
            'passport_image',
            'dl_number',
            'dl_expiry_date',
            'dl_image',
            'card_number',
            'card_expiry_date',
            'card_image',
            'whatsapp_number',
        ):
            self.fields[name].required = False

    def _build_country_choices(self):
        with schema_context('public'):
            cid = getattr(self.instance, 'nationality_country_id', None)
            if cid:
                qs = (
                    Country.objects.filter(Q(is_active=True) | Q(pk=cid))
                    .distinct()
                    .order_by('name_en')
                )
            else:
                qs = Country.objects.filter(is_active=True).order_by('name_en')
            rows = list(qs)
        return [('', _('Select country...'))] + [
            (c.country_code, f'{c.country_code} — {c.name_en}') for c in rows
        ]

    def clean(self):
        cleaned = super().clean()
        errors = {}

        vendor_raw = (cleaned.get('vendor_account_id') or '').strip()
        if (
            cleaned.get('driver_source')
            == DriverMaster.DriverSource.OUT_SOURCE
            and not vendor_raw
        ):
            errors['vendor_account_id'] = _(
                'Vendor account is required for Out-Source drivers'
            )

        mobile = (cleaned.get('mobile_number') or '').strip()
        if mobile and not re.match(r'^\d+$', mobile):
            errors['mobile_number'] = _(
                'Mobile number must contain digits only'
            )

        wa = (cleaned.get('whatsapp_number') or '').strip()
        if wa and not re.match(r'^\d+$', wa):
            errors['whatsapp_number'] = _(
                'WhatsApp number must contain digits only'
            )

        id_num = (cleaned.get('id_number') or '').strip()
        if id_num and not re.match(r'^\d+$', id_num):
            errors['id_number'] = _('ID number must contain digits only')

        def _check_unique(field_name, value, label):
            if not value or not str(value).strip():
                return
            qs = DriverMaster.objects.filter(**{field_name: value})
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                errors[field_name] = _(
                    '%(label)s already exists for another driver'
                ) % {'label': label}

        _check_unique('id_number', id_num, _('ID number'))
        _check_unique(
            'passport_number',
            (cleaned.get('passport_number') or '').strip(),
            _('Passport number'),
        )
        _check_unique(
            'dl_number',
            (cleaned.get('dl_number') or '').strip(),
            _('DL number'),
        )
        _check_unique(
            'card_number',
            (cleaned.get('card_number') or '').strip(),
            _('Card number'),
        )

        if cleaned.get('whatsapp_same_as_mobile'):
            cleaned['whatsapp_number'] = cleaned.get('mobile_number') or ''

        today = timezone.localdate()
        is_create = not self.instance.pk

        def _check_expiry(number, expiry, expiry_field, label):
            if not number or not str(number).strip():
                return
            if not expiry:
                return
            if is_create and expiry < today:
                errors[expiry_field] = _(
                    '%(label)s expiry date cannot be in the past'
                ) % {'label': label}

        _check_expiry(id_num, cleaned.get('id_expiry_date'), 'id_expiry_date', _('ID'))
        _check_expiry(
            (cleaned.get('passport_number') or '').strip(),
            cleaned.get('passport_expiry_date'),
            'passport_expiry_date',
            _('Passport'),
        )
        _check_expiry(
            (cleaned.get('dl_number') or '').strip(),
            cleaned.get('dl_expiry_date'),
            'dl_expiry_date',
            _('Driving license'),
        )
        _check_expiry(
            (cleaned.get('card_number') or '').strip(),
            cleaned.get('card_expiry_date'),
            'card_expiry_date',
            _('Driver card'),
        )

        if errors:
            raise ValidationError(errors)

        return cleaned
