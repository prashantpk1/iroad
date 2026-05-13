"""
Shared bilingual CMS field resolution (*_en / *_ar) for views and templates.
"""


def localized_cms_field(obj, base: str, lang: str) -> str:
    """
    Return localized text for ``{base}_en`` / ``{base}_ar`` on ``obj``.

    ``lang`` should be ``'en'`` or ``'ar'``. Falls back to the other language
    when the preferred column is empty (same rules as ``cms_txt``).
    """
    v_en = getattr(obj, f'{base}_en', None)
    v_ar = getattr(obj, f'{base}_ar', None)
    if v_en is None and v_ar is None:
        return ''
    en = '' if v_en is None else str(v_en).strip()
    ar = '' if v_ar is None else str(v_ar).strip()
    if str(lang or 'en').lower() == 'ar':
        return ar or en
    return en or ar
