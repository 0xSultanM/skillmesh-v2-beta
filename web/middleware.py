CSP = "; ".join([
    "default-src 'self'",
    # tesseract.js (in-browser OCR) is loaded from jsDelivr and runs WebAssembly in a worker.
    # YouTube IFrame Player API (in-app video popup) + its widget script.
    "script-src 'self' https://cdn.jsdelivr.net https://www.youtube.com https://s.ytimg.com 'wasm-unsafe-eval' blob:",
    # Video player + content popup (vetted https pages, shown sandboxed).
    "frame-src https:",
    "worker-src 'self' blob: https://cdn.jsdelivr.net",
    "connect-src 'self' https://cdn.jsdelivr.net https://tessdata.projectnaptha.com data: blob:",
    "img-src 'self' data: blob: https://i.ytimg.com",
    "style-src 'self' 'unsafe-inline'",
    "font-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])


class PrivacyHeadersMiddleware:
    """API responses contain personal data, so they must never be cached anywhere."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.path.startswith("/api/"):
            response["Cache-Control"] = "no-store, max-age=0"
            response["Pragma"] = "no-cache"
        if not request.path.startswith("/admin/"):
            response.setdefault("Content-Security-Policy", CSP)
        response.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        return response
