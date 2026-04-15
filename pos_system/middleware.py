class HtmxSuccessAutoRefreshMiddleware:
    """Refresh current page after successful HTMX write actions."""

    MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
    ERROR_MARKERS = (
        "message-error",
        "errorlist",
        "aria-invalid=\"true\"",
        "aria-invalid='true'",
        "is-invalid",
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        if not getattr(request, "htmx", False):
            return response
        if request.method.upper() not in self.MUTATING_METHODS:
            return response
        if not (200 <= response.status_code < 300):
            return response
        if response.headers.get("X-Skip-HX-Refresh", "").lower() == "true":
            return response
        if response.headers.get("HX-Refresh"):
            return response
        if self._looks_like_error_response(response):
            return response

        response.headers["HX-Refresh"] = "true"
        return response

    def _looks_like_error_response(self, response):
        content_type = response.get("Content-Type", "")
        if "text/html" not in content_type or not hasattr(response, "content"):
            return False

        charset = getattr(response, "charset", None) or "utf-8"
        body = response.content.decode(charset, errors="ignore").lower()
        return any(marker in body for marker in self.ERROR_MARKERS)
