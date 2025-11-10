# Create this file: apps/users/middleware.py
# This is OPTIONAL - forces staff to complete profile on first login

from django.shortcuts import redirect
from django.urls import reverse

class StaffProfileCompletionMiddleware:
    """
    Middleware to redirect staff with incomplete profiles to completion page
    Only applies to staff, manager, and delivery roles
    """
    
    def __init__(self, get_response):
        self.get_response = get_response
    
    def __call__(self, request):
        # Check if user is authenticated
        if request.user.is_authenticated:
            # Check if user is staff-related role
            if request.user.role in ['staff', 'manager', 'delivery']:
                # Check if profile is incomplete
                is_incomplete = not (
                    request.user.first_name and 
                    request.user.last_name
                )
                
                # Get current path
                current_path = request.path
                profile_completion_url = reverse('users:staff_profile_completion')
                logout_url = reverse('users:logout_view')
                
                # Allowed URLs (don't redirect if user is on these pages)
                allowed_urls = [
                    profile_completion_url,
                    logout_url,
                    '/static/',  # Allow static files
                    '/media/',   # Allow media files
                    '/api/',     # Allow API access
                ]
                
                # Check if current URL is allowed
                is_allowed = any(current_path.startswith(url) for url in allowed_urls)
                
                # Redirect to profile completion if incomplete and not on allowed page
                if is_incomplete and not is_allowed:
                    return redirect('users:staff_profile_completion')
        
        response = self.get_response(request)
        return response


# =====================================================
# TO USE THIS MIDDLEWARE:
# =====================================================
# Add to settings.py in MIDDLEWARE list:
#
# MIDDLEWARE = [
#     # ... other middleware ...
#     'apps.users.middleware.StaffProfileCompletionMiddleware',  # Add this
# ]
#
# This will automatically redirect staff with incomplete profiles
# to the completion page whenever they try to access any page.
# =====================================================


class SoftStaffProfileReminderMiddleware:
    """
    Alternative: Adds a context variable to show reminder banner
    instead of forcing redirect (less intrusive)
    """
    
    def __init__(self, get_response):
        self.get_response = get_response
    
    def __call__(self, request):
        # Add profile completion status to request
        if request.user.is_authenticated:
            if request.user.role in ['staff', 'manager', 'delivery']:
                request.profile_incomplete = not (
                    request.user.first_name and 
                    request.user.last_name
                )
            else:
                request.profile_incomplete = False
        else:
            request.profile_incomplete = False
        
        response = self.get_response(request)
        return response


# =====================================================
# TO USE SOFT REMINDER:
# =====================================================
# 1. Add to settings.py MIDDLEWARE:
#    'apps.users.middleware.SoftStaffProfileReminderMiddleware',
#
# 2. Add to your base staff template:
#    {% if request.profile_incomplete %}
#    <div class="alert alert-info">
#        <strong>Reminder:</strong> 
#        <a href="{% url 'users:staff_profile_completion' %}">
#            Complete your profile
#        </a>
#    </div>
#    {% endif %}
# =====================================================