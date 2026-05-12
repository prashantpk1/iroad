"""
Helpers for bilingual CMS fields on the public frontend.
"""


def get_field(obj, field_en, field_ar, lang='en'):
    """
    Return EN or AR field value based on lang.
    Falls back to EN if AR is empty.
    """
    if lang == 'ar':
        val = getattr(obj, field_ar, '') or ''
        if isinstance(val, str) and val.strip():
            return val
    return getattr(obj, field_en, '') or ''
