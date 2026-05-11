"""
ModelForms for IRoad marketing home page CMS (superadmin).
"""

from django import forms
from django.db import models as dj_models
from django.forms import ModelForm, TextInput, Textarea, FileInput, CheckboxInput

from iroad_frontend.models import (
    HomeMapLocation,
    HomePageContent,
    HomePricingTier,
    HomeServiceCard,
    HomeTestimonial,
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
