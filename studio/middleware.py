from django.http import HttpResponse


class HealthCheckMiddleware:
    """Short-circuit /health before host validation and SSL redirect so
    kubelet probes, which hit the pod IP directly, always get a 200."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path == '/health':
            return HttpResponse('ok')
        return self.get_response(request)
