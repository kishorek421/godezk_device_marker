def health_check(get_response):
    # K8s/kubelet probes hit the pod IP directly, so they fail ALLOWED_HOSTS
    # validation and SECURE_SSL_REDIRECT further down the chain. Answer the
    # bare /health probe path before any of that runs; /health/ keeps its
    # real view (admin detail, host validation).
    def middleware(request):
        if request.path == '/health':
            from django.http import JsonResponse
            return JsonResponse({'status': 'ok'})
        return get_response(request)
    return middleware


def no_store_html(get_response):
    # Authenticated pages must never be served from back-forward or HTTP
    # caches — they hold per-user, per-revision state that goes stale.
    def middleware(request):
        response = get_response(request)
        if response.get('Content-Type', '').startswith('text/html'):
            response['Cache-Control'] = 'no-store'
        return response
    return middleware
