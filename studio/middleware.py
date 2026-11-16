def no_store_html(get_response):
    # Authenticated pages must never be served from back-forward or HTTP
    # caches — they hold per-user, per-revision state that goes stale.
    def middleware(request):
        response = get_response(request)
        if response.get('Content-Type', '').startswith('text/html'):
            response['Cache-Control'] = 'no-store'
        return response
    return middleware
