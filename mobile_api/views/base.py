"""
mobile_api/views/base.py

Base APIView for all Mobile API endpoints.

Every mobile API view should extend MobileAPIView instead
of directly extending APIView. This ensures:
  1. Language activated from request header on every request
  2. Request logging for debugging
  3. Helper methods available (success, error, paginate)
  4. Consistent error handling

Usage:
  from mobile_api.views.base import MobileAPIView
  from mobile_api.permissions import AllowAnyMobile

  class LoginView(MobileAPIView):
      authentication_classes = []
      permission_classes = [AllowAnyMobile]

      def post(self, request):
          lang = self.get_language()
          # ... login logic
          return self.success(
              message=_('mobile.auth.login_success'),
              data={'access_token': '...'},
          )
"""
import logging
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status as http_status

from mobile_api.helpers.i18n import activate_request_language
from mobile_api.pagination import MobileApiPagination

logger = logging.getLogger('mobile_api')


class MobileAPIView(APIView):
    """
    Base class for all Mobile API views.

    Provides:
    - Language activation on every request
    - Standardized response helpers
    - Pagination helper
    - Request logging
    """

    def initialize_request(self, request, *args, **kwargs):
        """Activate language before any view logic runs."""
        result = super().initialize_request(
            request, *args, **kwargs
        )
        return result

    def initial(self, request, *args, **kwargs):
        """Called before dispatch — activate i18n here."""
        super().initial(request, *args, **kwargs)
        activate_request_language(request)

    # ── Response helpers ──────────────────────────────────────────

    def success(
        self,
        message: str,
        data=None,
        http_code: int = http_status.HTTP_200_OK,
    ) -> Response:
        """Return a success response with status=1."""
        return Response(
            {
                'status': 1,
                'message': message,
                'data': data if data is not None else {},
            },
            status=http_code,
        )

    def error(
        self,
        message: str,
        data=None,
        http_code: int = http_status.HTTP_400_BAD_REQUEST,
    ) -> Response:
        """Return an error response with status=0."""
        return Response(
            {
                'status': 0,
                'message': message,
                'data': data if data is not None else {},
            },
            status=http_code,
        )

    def auth_error(
        self,
        message: str,
        data=None,
    ) -> Response:
        """Return an auth error response with status=2."""
        return Response(
            {
                'status': 2,
                'message': message,
                'data': data if data is not None else {},
            },
            status=http_status.HTTP_401_UNAUTHORIZED,
        )

    def not_found(self, message: str) -> Response:
        """Return a 404 response with status=0."""
        return Response(
            {
                'status': 0,
                'message': message,
                'data': {},
            },
            status=http_status.HTTP_404_NOT_FOUND,
        )

    # ── Pagination helper ─────────────────────────────────────────

    def paginate(
        self,
        queryset,
        serializer_class,
        message: str = 'Data retrieved successfully',
        serializer_kwargs: dict = None,
    ) -> Response:
        """
        Paginate a queryset and return standard envelope.

        Args:
            queryset: Django QuerySet to paginate
            serializer_class: DRF Serializer class
            message: Success message string
            serializer_kwargs: Extra kwargs for serializer

        Returns:
            Paginated Response with standard envelope
        """
        paginator = MobileApiPagination()
        page = paginator.paginate_queryset(
            queryset, self.request
        )

        kwargs = serializer_kwargs or {}
        kwargs['many'] = True

        if page is not None:
            serializer = serializer_class(page, **kwargs)
            return paginator.get_paginated_response(
                serializer.data,
                message=message,
            )

        # No pagination — return all
        serializer = serializer_class(queryset, **kwargs)
        return self.success(message=message, data=serializer.data)

    # ── Language helper ───────────────────────────────────────────

    def get_language(self) -> str:
        """Get currently activated language code."""
        from mobile_api.helpers.i18n import get_request_language
        return get_request_language(self.request)

    # ── Logging helpers ───────────────────────────────────────────

    def log_info(self, message: str, **kwargs):
        logger.info(
            '[MobileAPI] %s | user=%s | schema=%s | %s',
            message,
            getattr(
                getattr(self.request, 'user', None),
                'user_id', 'anon'
            ),
            getattr(
                getattr(self.request, 'user', None),
                'tenant_schema', '-'
            ),
            kwargs,
        )

    def log_error(self, message: str, **kwargs):
        logger.error(
            '[MobileAPI] ERROR %s | user=%s | %s',
            message,
            getattr(
                getattr(self.request, 'user', None),
                'user_id', 'anon'
            ),
            kwargs,
        )
