"""
mobile_api/helpers/i18n.py

Language activation helper for Mobile API.

Mobile clients send language preference via:
  X-Language: ar       (preferred — explicit)
  Accept-Language: ar  (fallback — standard HTTP)

If neither is provided or language is unsupported,
defaults to English (en).

Supported languages: en, ar

Usage in a view:
    from mobile_api.helpers.i18n import activate_request_language
    from django.utils.translation import gettext as _

    activate_request_language(request)
    message = _('mobile.truck.list.success')
    return api_success(message, data=...)
"""
from django.utils import translation


SUPPORTED_LANGUAGES = {'en', 'ar'}
DEFAULT_LANGUAGE = 'en'


def get_request_language(request) -> str:
    """
    Determine language from request headers.

    Priority:
      1. X-Language header (explicit mobile preference)
      2. Accept-Language header (standard HTTP)
      3. Default: 'en'

    Returns:
      Language code string: 'en' or 'ar'
    """
    # Priority 1: explicit X-Language header
    x_lang = request.headers.get('X-Language', '').strip().lower()
    if x_lang:
        # Accept 'ar', 'ar-SA', 'ar_SA' etc — take first 2 chars
        lang_code = x_lang[:2]
        if lang_code in SUPPORTED_LANGUAGES:
            return lang_code

    # Priority 2: Accept-Language header
    accept_lang = request.headers.get(
        'Accept-Language', ''
    ).strip().lower()
    if accept_lang:
        # Accept-Language can be 'ar,en;q=0.9' — take first entry
        first = accept_lang.split(',')[0].strip()
        lang_code = first[:2]
        if lang_code in SUPPORTED_LANGUAGES:
            return lang_code

    # Fallback
    return DEFAULT_LANGUAGE


def activate_request_language(request) -> str:
    """
    Detect language from request and activate Django translation.

    Call this at the start of every API view.
    Returns the activated language code.

    Example:
        lang = activate_request_language(request)
        # Now _('key') returns in the correct language
    """
    lang = get_request_language(request)
    translation.activate(lang)
    return lang


def deactivate_language():
    """
    Deactivate translation after request.
    Call in finally block if needed.
    """
    translation.deactivate()

