from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from django_tenants.utils import schema_context

from superadmin.models import Country
from tenant_workspace.models import DriverMaster, TruckMaster, TruckTypeMaster

from iroad_tenants.forms_tenant_address import PublicCountryChoiceField


class TruckMasterForm(forms.ModelForm):
    class Meta:
        model = TruckMaster
        fields = [
            'status',
            'sourcing_mode',
            'registration_country',
            'vendor_account_id',
            'is_vendor_same_as_owner',
            'owner_id',
            'owner_name',
            'plate_number',
            'saudi_plate_number',
            'saudi_english_letters',
            'saudi_arabic_letters',
            'non_saudi_plate_number',
            'plate_image',
            'truck_type',
            'chassis_number_vin',
            'serial_number',
            'color',
            'vehicle_registration_image',
            'axle_count',
            'tires_count',
            'tare_weight_ton',
            'payload_capacity_ton',
            'gross_weight_ton',
            'volume_m3',
            'default_driver_id',
        ]
        widgets = {
            'status': forms.Select(attrs={'class': 'form-select'}),
            'sourcing_mode': forms.Select(attrs={'class': 'form-select'}),
            'vendor_account_id': forms.TextInput(attrs={'class': 'form-control'}),
            'is_vendor_same_as_owner': forms.CheckboxInput(),
            'owner_id': forms.TextInput(attrs={'class': 'form-control'}),
            'owner_name': forms.TextInput(attrs={'class': 'form-control'}),
            'plate_number': forms.TextInput(attrs={'class': 'form-control'}),
            'saudi_plate_number': forms.TextInput(attrs={'class': 'form-control'}),
            'saudi_english_letters': forms.TextInput(attrs={'class': 'form-control'}),
            'saudi_arabic_letters': forms.TextInput(attrs={'class': 'form-control'}),
            'non_saudi_plate_number': forms.TextInput(attrs={'class': 'form-control'}),
            'plate_image': forms.FileInput(attrs={'class': 'form-control'}),
            'truck_type': forms.Select(attrs={'class': 'form-select'}),
            'chassis_number_vin': forms.TextInput(attrs={'class': 'form-control'}),
            'serial_number': forms.TextInput(attrs={'class': 'form-control'}),
            'color': forms.TextInput(attrs={'class': 'form-control'}),
            'vehicle_registration_image': forms.FileInput(attrs={'class': 'form-control'}),
            'axle_count': forms.NumberInput(attrs={'class': 'form-control'}),
            'tires_count': forms.NumberInput(attrs={'class': 'form-control'}),
            'tare_weight_ton': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'payload_capacity_ton': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'gross_weight_ton': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'volume_m3': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'default_driver_id': forms.TextInput(attrs={'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields.pop('registration_country', None)
        country_pairs = self._build_country_choices()
        self.fields['registration_country'] = PublicCountryChoiceField(
            address_instance=self.instance,
            country_fk_attr='registration_country_id',
            choices=country_pairs,
            required=True,
            label=_('Registration country'),
            widget=forms.Select(attrs={'class': 'form-select'}),
        )
        cid_val = getattr(self.instance, 'registration_country_id', None)
        if self.instance.pk and cid_val:
            self.fields['registration_country'].initial = cid_val

        tt = self.fields['truck_type']
        tt.queryset = TruckTypeMaster.active_objects.all()
        tt.empty_label = _('— Select Truck Type —')
        tt.required = False

        # Link Truck -> Driver module: pick a driver from Driver Master.
        # If the truck currently points to an inactive driver, still include it in the dropdown.
        self.fields.pop('default_driver_id', None)
        instance_driver = getattr(self.instance, 'default_driver_id', None)
        base_qs = DriverMaster.active_objects.all()
        if instance_driver and instance_driver.pk:
            queryset = (DriverMaster.objects.filter(pk=instance_driver.pk) | base_qs).distinct()
        else:
            queryset = base_qs

        self.fields['default_driver_id'] = forms.ModelChoiceField(
            queryset=queryset.order_by('driver_code'),
            required=False,
            label=_('Default driver'),
            empty_label=_('— Select Driver —'),
            widget=forms.Select(attrs={'class': 'form-select'}),
        )
        if instance_driver and instance_driver.pk:
            self.fields['default_driver_id'].initial = instance_driver.pk

        self.fields['vendor_account_id'].required = False

        for name in (
            'saudi_plate_number',
            'saudi_english_letters',
            'saudi_arabic_letters',
            'non_saudi_plate_number',
        ):
            self.fields[name].required = False

    def _build_country_choices(self):
        with schema_context('public'):
            cid = getattr(self.instance, 'registration_country_id', None)
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
        country = cleaned.get('registration_country')
        vendor_raw = cleaned.get('vendor_account_id')

        if (
            str(cleaned.get('sourcing_mode') or '')
            == str(TruckMaster.SourcingMode.OUT_SOURCE)
            and not (vendor_raw or '').strip()
        ):
            raise ValidationError(
                {'vendor_account_id': _('Vendor account is required for Out-Source trucks')}
            )

        cc = getattr(country, 'country_code', None) or getattr(country, 'pk', None)
        cc = str(cc).strip().upper() if cc is not None else ''

        sp = (cleaned.get('saudi_plate_number') or '').strip()

        if cc == 'SA':
            if not sp:
                raise ValidationError(
                    {'saudi_plate_number': _('Saudi plate number is required')}
                )
            if not (cleaned.get('saudi_english_letters') or '').strip():
                raise ValidationError(
                    {
                        'saudi_english_letters': _(
                            'Saudi English letters are required'
                        )
                    }
                )
            if not (cleaned.get('saudi_arabic_letters') or '').strip():
                raise ValidationError(
                    {
                        'saudi_arabic_letters': _(
                            'Saudi Arabic letters are required'
                        )
                    }
                )

        elif cc:
            nons = (cleaned.get('non_saudi_plate_number') or '').strip()
            if not nons:
                raise ValidationError(
                    {'non_saudi_plate_number': _('Plate number is required')}
                )
            cleaned['saudi_plate_number'] = ''
            cleaned['saudi_english_letters'] = ''
            cleaned['saudi_arabic_letters'] = ''

        plate = (cleaned.get('plate_number') or '').strip()
        if country is not None and plate:
            q = TruckMaster.objects.filter(
                plate_number=plate,
                registration_country=country,
            )
            if self.instance.pk:
                q = q.exclude(pk=self.instance.pk)
            if q.exists():
                raise ValidationError(
                    {
                        'plate_number': _('This plate number is already registered for this country')
                    }
                )

        vin = (cleaned.get('chassis_number_vin') or '').strip()
        if vin:
            q = TruckMaster.objects.filter(chassis_number_vin__iexact=vin)
            if self.instance.pk:
                q = q.exclude(pk=self.instance.pk)
            if q.exists():
                raise ValidationError(
                    {'chassis_number_vin': _('This chassis/VIN number already exists')}
                )

        payload = cleaned.get('payload_capacity_ton')
        gross = cleaned.get('gross_weight_ton')
        if payload is not None and gross is not None:
            if payload > gross:
                raise ValidationError(
                    {
                        'payload_capacity_ton': _(
                            'Payload capacity cannot exceed gross weight'
                        )
                    }
                )

        truck_type = cleaned.get('truck_type')
        if truck_type is None:
            raise ValidationError({'truck_type': _('Select a truck type.')})

        return cleaned
