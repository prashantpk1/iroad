"""
Bilingual CMS helpers: pick *_ar vs *_en using ``lang`` from template context.
"""
from django import template

from iroad_frontend.cms_text import localized_cms_field

register = template.Library()


@register.simple_tag(takes_context=True)
def cms_txt(context, obj, base):
    """
    Return localized text for a model field pair ``{base}_en`` / ``{base}_ar``.

    Uses ``lang`` from context (set by ``get_lang_context``). Falls back to the
    other language when the preferred column is empty.
    """
    lang = context.get('lang') or 'en'
    return localized_cms_field(obj, base, lang)
