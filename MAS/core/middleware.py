from django.shortcuts import redirect
from django.urls import reverse


class ForcePasswordChangeMiddleware:
    """Enforces 'Set New Private Password' screen on first login before accessing LMS modules."""

    EXEMPT_PREFIXES = (
        '/static/',
        '/media/',
        '/django-admin/',
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated and getattr(request.user, 'must_change_password', False):
            force_url = reverse('force_password_change')
            logout_url = reverse('logout')
            path = request.path

            is_exempt = (
                path == force_url
                or path == logout_url
                or any(path.startswith(prefix) for prefix in self.EXEMPT_PREFIXES)
            )
            if not is_exempt:
                return redirect(force_url)

        return self.get_response(request)
