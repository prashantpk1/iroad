"""
ModelForms for IRoad marketing home page CMS (superadmin).
"""

from django import forms
from django.db import models as dj_models
from django.forms import ModelForm, TextInput, Textarea, FileInput, CheckboxInput

from iroad_frontend.models import (
    AboutApproachPillar,
    AboutFaqItem,
    AboutHowWorkStep,
    AboutPageContent,
    ContactPageContent,
    HomeMapLocation,
    HomePageContent,
    HomePricingBenefit,
    HomePricingTier,
    HomeServiceCard,
    HomeTestimonial,
    PricingInteractiveStep,
    PricingPageContent,
)

_CTRL = {'class': 'form-control'}
_TEXTAREA_ATTRS = {'class': 'form-control', 'rows': 3}


def _apply_home_page_widgets(form):
    for name, field in form.fields.items():
        mf = HomePageContent._meta.get_field(name)
        if isinstance(mf, dj_models.TextField):
            field.widget = Textarea(attrs=_TEXTAREA_ATTRS.copy())
        elif isinstance(mf, dj_models.FileField):
            field.widget = FileInput(attrs=_CTRL.copy())
        elif isinstance(mf, dj_models.CharField):
            field.widget = TextInput(attrs=_CTRL.copy())


def _apply_child_widgets(form, model):
    for name, field in form.fields.items():
        mf = model._meta.get_field(name)
        if isinstance(mf, dj_models.TextField):
            field.widget = Textarea(attrs=_TEXTAREA_ATTRS.copy())
        elif isinstance(mf, dj_models.FileField):
            field.widget = FileInput(attrs=_CTRL.copy())
        elif isinstance(mf, dj_models.BooleanField):
            field.widget = CheckboxInput(attrs={'class': 'form-check-input'})
        elif isinstance(mf, dj_models.CharField):
            field.widget = TextInput(attrs=_CTRL.copy())
        elif isinstance(mf, dj_models.PositiveSmallIntegerField):
            field.widget = TextInput(attrs=_CTRL.copy())


def _apply_about_page_widgets(form):
    """About singleton: TextInput / Textarea / FileInput by field type."""
    for name, field in form.fields.items():
        mf = AboutPageContent._meta.get_field(name)
        if isinstance(mf, dj_models.TextField):
            field.widget = Textarea(attrs=_TEXTAREA_ATTRS.copy())
        elif isinstance(mf, dj_models.FileField):
            field.widget = FileInput(attrs=_CTRL.copy())
        elif isinstance(mf, dj_models.CharField):
            field.widget = TextInput(attrs=_CTRL.copy())


def _apply_pricing_page_widgets(form):
    """Pricing singleton: same widget rules as about page CMS."""
    for name, field in form.fields.items():
        mf = PricingPageContent._meta.get_field(name)
        if isinstance(mf, dj_models.TextField):
            field.widget = Textarea(attrs=_TEXTAREA_ATTRS.copy())
        elif isinstance(mf, dj_models.FileField):
            field.widget = FileInput(attrs=_CTRL.copy())
        elif isinstance(mf, dj_models.CharField):
            field.widget = TextInput(attrs=_CTRL.copy())


def _apply_contact_page_widgets(form):
    """Contact singleton: TextInput / Textarea / FileInput (ImageField is FileField subclass)."""
    for name, field in form.fields.items():
        mf = ContactPageContent._meta.get_field(name)
        if isinstance(mf, dj_models.TextField):
            field.widget = Textarea(attrs=_TEXTAREA_ATTRS.copy())
        elif isinstance(mf, dj_models.FileField):
            field.widget = FileInput(attrs=_CTRL.copy())
        elif isinstance(mf, dj_models.CharField):
            field.widget = TextInput(attrs=_CTRL.copy())


class HomePageContentForm(ModelForm):
    """Singleton home page — all editable columns except id / auto timestamp."""

    class Meta:
        model = HomePageContent
        exclude = ('id', 'updated_at')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_home_page_widgets(self)


class HomeServiceCardForm(ModelForm):
    class Meta:
        model = HomeServiceCard
        exclude = ('home',)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_child_widgets(self, HomeServiceCard)


class HomePricingTierForm(ModelForm):
    class Meta:
        model = HomePricingTier
        exclude = ('home',)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_child_widgets(self, HomePricingTier)


class HomeTestimonialForm(ModelForm):
    class Meta:
        model = HomeTestimonial
        exclude = ('home',)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_child_widgets(self, HomeTestimonial)


class HomeMapLocationForm(ModelForm):
    class Meta:
        model = HomeMapLocation
        exclude = ('home',)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_child_widgets(self, HomeMapLocation)


class HomePricingBenefitForm(ModelForm):
    class Meta:
        model = HomePricingBenefit
        exclude = ('home',)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_child_widgets(self, HomePricingBenefit)


class AboutPageContentForm(ModelForm):
    """Singleton about page — same widget rules as home CMS."""

    class Meta:
        model = AboutPageContent
        exclude = ('updated_at', 'updated_by')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_about_page_widgets(self)


class AboutApproachPillarForm(ModelForm):
    class Meta:
        model = AboutApproachPillar
        exclude = ('about',)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_child_widgets(self, AboutApproachPillar)


class AboutHowWorkStepForm(ModelForm):
    class Meta:
        model = AboutHowWorkStep
        exclude = ('about',)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_child_widgets(self, AboutHowWorkStep)


class AboutFaqItemForm(ModelForm):
    class Meta:
        model = AboutFaqItem
        exclude = ('about',)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_child_widgets(self, AboutFaqItem)


class PricingPageContentForm(ModelForm):
    """Singleton pricing page — same widget rules as about page CMS."""

    class Meta:
        model = PricingPageContent
        exclude = ('updated_at', 'updated_by')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_pricing_page_widgets(self)


class ContactPageContentForm(ModelForm):
    """Singleton contact page — bilingual labels, form copy, sidebar info."""

    class Meta:
        model = ContactPageContent
        exclude = ('updated_at', 'updated_by')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_contact_page_widgets(self)


class PricingInteractiveStepForm(ModelForm):
    class Meta:
        model = PricingInteractiveStep
        fields = (
            'order',
            'title_en',
            'title_ar',
            'subtitle_en',
            'subtitle_ar',
            'body_en',
            'body_ar',
            'icon',
            'bg_image',
            'detail_url',
            'is_active',
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_child_widgets(self, PricingInteractiveStep)
