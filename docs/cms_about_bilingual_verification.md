# About CMS — bilingual field verification

When auditing **About** models (`AboutPageContent`, `AboutApproachPillar`, `AboutHowWorkStep`, `AboutFaqItem`), treat **user-facing translatable copy** as requiring both `_en` and `_ar` where the model defines a pair.

## Fields that do **not** require Arabic counterparts

These are shared, non-locale-specific, structural, or binary — **not** missing bilingual coverage:

| Category | Examples |
|----------|-----------|
| Numeric / display tokens | `about_counter_1_value`, `about_counter_2_value`, `about_rating_value`, `how_rating_value`, `step_number` |
| URLs | `about_explore_url`, `about_footer_cta_url`, `approach_cta_url`, `how_footer_link_url`, `faq_view_all_url` |
| Media | All `FileField` / upload fields (`about_main_image`, `icon`, `step_image`, …) |
| Ordering & flags | `order`, `is_active` |
| Audit | `updated_at`, `updated_by` |
| Relations | `about` (FK) |

## Fields that **should** have `_en` / `_ar` pairs (when applicable)

CharField / TextField blocks used for on-page copy (titles, body, labels, SEO text) where the model exposes both `*_en` and `*_ar` — e.g. `page_title_en` / `page_title_ar`, `about_body_en` / `about_body_ar`, pillar `title_en` / `title_ar`, FAQ `question_en` / `question_ar`, etc.

Verification scripts or checklists should **exclude** the exception list above when counting “missing `_ar`”.

---

## Appendix: static files (`/static/...`) in development and tests

When `DEBUG` is `True`, `config/urls.py` appends `django.contrib.staticfiles.urls.staticfiles_urlpatterns()` so `django.test.Client` and `runserver` resolve `STATIC_URL` using `STATICFILES_DIRS` and the staticfiles finder (`findstatic`).

In production (`DEBUG=False`), serve collected assets via your web server or WhiteNoise; `staticfiles_urlpatterns()` is not appended.

---

## Appendix: custom 404 page

`handler404` in `config/urls.py` points to `iroad_frontend.views.page_not_found`, which renders `iroad_frontend/errors/404.html` (extends `iroad_frontend/base.html`, shared header/footer, `{% static %}` for the error image).

Django only uses `handler404` for unresolved URLs when **`DEBUG` is `False`**. With `DEBUG=True`, missing URL patterns still show Django’s technical 404 page; use `DEBUG=False` locally to preview the branded template, or rely on production behavior.
