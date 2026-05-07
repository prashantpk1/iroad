from django import forms

from tenant_workspace.models import (
    DriverTreasury,
    DriverTreasuryTransaction,
    DriverMaster,
)


class DriverTreasuryForm(forms.ModelForm):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Only Active drivers selectable
        self.fields['driver'].queryset = (
            DriverMaster.active_objects.all()
        )
        self.fields['driver'].empty_label = (
            '— Select Driver —'
        )
        # current_balance is read-only — never in form
        # treasury_code is auto-generated — never in form

    class Meta:
        model = DriverTreasury
        fields = ['driver', 'status']
        widgets = {
            'driver': forms.Select(
                attrs={'class': 'form-select'}
            ),
            'status': forms.Select(
                attrs={'class': 'form-select'}
            ),
        }

    def clean_driver(self):
        driver = self.cleaned_data.get('driver')
        if not driver:
            raise forms.ValidationError(
                'Driver is required'
            )
        return driver


class DriverTreasuryTransactionForm(forms.ModelForm):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Only Active treasuries selectable
        self.fields[
            'driver_treasury'
        ].queryset = (
            DriverTreasury.active_objects.all()
        )
        self.fields[
            'driver_treasury'
        ].empty_label = '— Select Treasury —'

    class Meta:
        model = DriverTreasuryTransaction
        fields = [
            'transaction_date',
            'driver_treasury',
            'transaction_type',
            'transaction_category',
            'amount',
            'related_shipment',
            'description',
        ]
        widgets = {
            'transaction_date': forms.DateTimeInput(
                attrs={
                    'class': 'form-control',
                    'type': 'datetime-local',
                }
            ),
            'driver_treasury': forms.Select(
                attrs={'class': 'form-select'}
            ),
            'transaction_type': forms.Select(
                attrs={'class': 'form-select'}
            ),
            'transaction_category': forms.Select(
                attrs={'class': 'form-select'}
            ),
            'amount': forms.NumberInput(
                attrs={
                    'class': 'form-control',
                    'min': '0',
                    'step': '0.01',
                }
            ),
            'related_shipment': forms.TextInput(
                attrs={'class': 'form-control'}
            ),
            'description': forms.Textarea(
                attrs={
                    'class': 'form-control',
                    'rows': 3,
                }
            ),
        }

    def clean_amount(self):
        amount = self.cleaned_data.get('amount')
        if amount is not None and amount < 0:
            raise forms.ValidationError(
                'Amount must be 0 or greater'
            )
        return amount

    def clean_transaction_date(self):
        dt = self.cleaned_data.get('transaction_date')
        if not dt:
            raise forms.ValidationError(
                'Transaction date is required'
            )
        return dt
