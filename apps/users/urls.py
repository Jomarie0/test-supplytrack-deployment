from django.urls import path
from .views import (
    CustomTokenObtainPairView,
    CustomRefreshTokenView,
    logout as jwt_logout,
    is_authenticated,
    logout_view,
    user_management,
    delete_users,
    user_archive_list,
    restore_users,
    permanently_delete_users,
    verify_email_code_view,
    resend_verification_code_view,
    forgot_password_view,
    verify_reset_code_view,
    resend_reset_code_view,
    reset_password_view,
    login_landing_view,
    customer_login_view,
    customer_register_view,
    supplier_login_view,
    staff_login_view,
    supplier_register_view,
    customer_profile_edit,
    customer_profile_view,
    admin_create_staff_view,
    api_bulk_create_staff,
    staff_profile_completion_view,
    api_create_staff_account,
    api_list_staff_accounts,
    api_delete_staff_account,
    api_staff_profile_view,
    api_staff_profile_update,
    api_check_profile_completion,
    api_staff_change_password,
    toggle_supplier_approval,
    # ADD THIS IMPORT
)


app_name = "users"

urlpatterns = [
    # HTML Views
    path("logout/", logout_view, name="logout_view"),
    path("user-management/", user_management, name="user_management"),
    path("archive/", user_archive_list, name="user_archive_list"),
    path("delete/", delete_users, name="delete_users"),
    path("restore/", restore_users, name="restore_users"),
    path("permanently-delete/", permanently_delete_users, name="permanently_delete_users"),
    path("verify/", verify_email_code_view, name="verify_email"),
    path("resend-code/", resend_verification_code_view, name="resend_code"),
    path("forgot-password/", forgot_password_view, name="forgot_password"),
    path("verify-reset-code/", verify_reset_code_view, name="verify_reset_code"),
    path("resend-reset-code/", resend_reset_code_view, name="resend_reset_code"),
    path("reset-password/", reset_password_view, name="reset_password"),
    # API Endpoints
    path("api/login/", CustomTokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("api/refresh/", CustomRefreshTokenView.as_view(), name="token_refresh"),
    path("api/logout/", jwt_logout, name="logout"),
    path("api/is-authenticated/", is_authenticated, name="is_authenticated"),
    # Login/Register Views
    path("", login_landing_view, name="login_landing"),
    path("customer/login/", customer_login_view, name="customer_login"),
    path("customer/register/", customer_register_view, name="customer_register"),
    path("supplier/login/", supplier_login_view, name="supplier_login"),
    path("supplier/register/", supplier_register_view, name="supplier_register"),
    path("staff/login/", staff_login_view, name="staff_login"),
    # Profile Management - ADD THIS LINE
    path("profile/", customer_profile_view, name="profile_view"),
    path("profile/edit/", customer_profile_edit, name="profile_edit"),
    # usermanagement paths can be added here
    # Admin staff creation
    path('admin/create-staff/', admin_create_staff_view, name='admin_create_staff'),
    path('api/bulk-create-staff/', api_bulk_create_staff, name='api_bulk_create_staff'),
    
    # Staff profile completion
    path('staff/complete-profile/', staff_profile_completion_view, name='staff_profile_completion'),
    # Admin endpoints
    path('create-staff/', api_create_staff_account, name='create_staff'),
    path('staff/list/', api_list_staff_accounts, name='list_staff'),
    path('staff/<int:user_id>/delete/', api_delete_staff_account, name='delete_staff'),
    
    # Staff endpoints
    path('staff/profile/', api_staff_profile_view, name='staff_profile'),
    path('staff/profile/update/', api_staff_profile_update, name='staff_profile_update'),
    path('staff/profile-status/', api_check_profile_completion, name='profile_status'),
    path('staff/change-password/', api_staff_change_password, name='staff_change_password'),
    path('supplier/<int:user_id>/toggle-approval/', toggle_supplier_approval, name='toggle_supplier_approval'),
]

