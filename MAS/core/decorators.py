from functools import wraps
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect


def role_required(*allowed_roles):
    """Restrict view access to users holding one of the specified LMS roles."""
    def decorator(view_func):
        @login_required
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            user = request.user
            if user.is_superuser and 'ADMIN' in allowed_roles:
                return view_func(request, *args, **kwargs)
            if user.role in allowed_roles:
                return view_func(request, *args, **kwargs)
            messages.error(request, 'Access denied: You do not have permission for this action.')
            return redirect('dashboard')
        return _wrapped
    return decorator
