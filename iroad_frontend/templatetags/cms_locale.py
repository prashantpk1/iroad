"""
Bilingual CMS helpers: pick *_ar vs *_en using ``lang`` from template context.
"""
from django import template

register = template.Library()


@register.simple_tag(takes_context=True)
def cms_txt(context, obj, base):
    """
    Return localized text for a model field pair ``{base}_en`` / ``{base}_ar``.

    Uses ``lang`` from context (set by ``get_lang_context``). Falls back to the
    other language when the preferred column is empty.
    """
    lang = context.get('lang') or 'en'
    v_en = getattr(obj, f'{base}_en', None)
    v_ar = getattr(obj, f'{base}_ar', None)
    if v_en is None and v_ar is None:
        return ''
    en = '' if v_en is None else str(v_en).strip()
    ar = '' if v_ar is None else str(v_ar).strip()
    if str(lang).lower() == 'ar':
        return ar or en
    return en or ar
