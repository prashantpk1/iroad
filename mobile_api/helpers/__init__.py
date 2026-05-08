"""
mobile_api/helpers
Active helpers:
  auth.py  — JWT token generation and verification
  i18n.py  — Language activation from request headers
Removed:
  response.py   — replaced by MobileAPIView.success()/error()
  pagination.py — replaced by MobileApiPagination + MobileAPIView.paginate()
"""

