from django.shortcuts import render, redirect
from django.contrib.auth import login, logout as django_logout
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.mail import send_mail
from django.http import HttpResponseForbidden, JsonResponse
from django.utils.timezone import now
from datetime import timedelta
import random
import json
from django.utils.crypto import get_random_string
from django.conf import settings
from django.db import transaction  # <-- added

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from apps.store.context_processors import merge_session_cart_with_user
from apps.store.views import process_pending_add_to_cart

from .models import User, EmailVerification, CustomerProfile, SupplierProfile
from django.views.decorators.csrf import csrf_exempt
from .forms import (
    CustomerRegistrationForm,
    SupplierRegistrationForm,
    CustomAuthenticationForm,
)
from apps.transactions.models import (
    Transaction,
    log_audit,
)  # ensure Transaction is available


# ==================== UTILITY FUNCTIONS ====================


def generate_verification_code():
    """Generate a 6-digit verification code"""
    return str(random.randint(100000, 999999))


def send_verification_email(email, code):
    """Send verification code to user's email"""
    send_mail(
        subject="Your Verification Code",
        message=f"Your verification code is: {code}",
        from_email="SupplyTrack <danegela13@gmail.com>",
        recipient_list=[email],
        fail_silently=False,
    )


def send_registration_success_email(user):
    """Send welcome email after successful registration"""
    send_mail(
        subject="Welcome to SupplyTrack!",
        message=(
            f"Hi {user.username},\n\n"
            "Thank you for registering at SupplyTrack. Your account has been successfully created.\n\n"
            "If you did not create this account, please contact the admin immediately.\n\n"
            "Best regards,\nSupplyTrack Team"
        ),
        from_email="SupplyTrack <danegela13@gmail.com>",
        recipient_list=[user.email],
        fail_silently=True,
    )


def send_login_notification_email(user):
    """Send notification email on login"""
    send_mail(
        subject="New Login to Your SupplyTrack Account",
        message=(
            f"Hi {user.username},\n\n"
            "You have just logged into your SupplyTrack account.\n\n"
            "If this wasn't you, please contact the admin immediately.\n\n"
            "Best regards,\nSupplyTrack Team"
        ),
        from_email="SupplyTrack <danegela13@gmail.com>",
        recipient_list=[user.email],
        fail_silently=True,
    )


def redirect_based_on_role(user):
    """Redirect users to appropriate dashboard based on role"""
    role_redirects = {
        "admin": "inventory:dashboard",
        "manager": "inventory:dashboard",
        "staff": "inventory:dashboard",
        "delivery": "delivery:delivery_list",
        "supplier": "suppliers:supplier_dashboard",
        "customer": "store:product_list",
    }
    return redirect(role_redirects.get(user.role, "store:product_list"))


# ==================== JWT TOKEN VIEWS ====================


class CustomTokenObtainPairView(TokenObtainPairView):
    def post(self, request, *args, **kwargs):
        try:
            response = super().post(request, *args, **kwargs)
            tokens = response.data

            access_token = tokens.get("access")
            refresh_token = tokens.get("refresh")

            res = Response({"success": True})
            res.set_cookie(
                key="access_token",
                value=access_token,
                httponly=True,
                secure=True,
                samesite=None,
                path="/",
            )
            res.set_cookie(
                key="refresh_token",
                value=refresh_token,
                httponly=True,
                secure=True,
                samesite=None,
                path="/",
            )
            return res
        except Exception as e:
            return Response({"success": False, "error": str(e)}, status=400)


class CustomRefreshTokenView(TokenRefreshView):
    def post(self, request, *args, **kwargs):
        try:
            refresh_token = request.COOKIES.get("refresh_token")
            if not refresh_token:
                return Response({"refreshed": False}, status=400)

            request.data["refresh"] = refresh_token
            response = super().post(request, *args, **kwargs)
            tokens = response.data
            access_token = tokens.get("access")

            if not access_token:
                return Response({"refreshed": False}, status=400)

            res = Response({"refreshed": True})
            res.set_cookie(
                key="access_token",
                value=access_token,
                httponly=True,
                secure=True,
                samesite=None,
                path="/",
            )
            return res
        except Exception as e:
            return Response({"refreshed": False, "error": str(e)}, status=400)


# ==================== EMAIL VERIFICATION ====================


def verify_email_code_view(request):
    """Verify email using 6-digit code sent to user"""
    user_id = request.session.get("unverified_user_id")
    if not user_id:
        messages.error(request, "No verification in progress. Please register first.")
        return redirect("users:login_landing")

    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        messages.error(request, "User not found.")
        del request.session["unverified_user_id"]
        return redirect("users:login_landing")

    if request.method == "POST":
        entered_code = request.POST.get("code")
        try:
            code_entry = EmailVerification.objects.get(user=user, code=entered_code)

            # Check if code is expired
            if code_entry.is_expired():
                messages.error(
                    request, "Verification code has expired. Please request a new one."
                )
                return render(request, "users/verify_email.html", {"user": user})

            # ✅ Activate user WITHOUT triggering approval logic
            User.objects.filter(id=user.id).update(is_active=True)

            # Delete verification code
            code_entry.delete()

            # Clear session
            del request.session["unverified_user_id"]

            # Send welcome email
            send_registration_success_email(user)

            # Create transaction log
            Transaction.objects.create(
                user=user,
                transaction_type="email_verification",
                description=f"User '{user.username}' verified their email address.",
            )
            try:
                log_audit(
                    user=user,
                    action="create",
                    instance=user,
                    changes={"email_verified": True},
                    request=request,
                )
            except Exception:
                pass

            # Show appropriate success message based on role
            if user.role == "supplier":
                messages.success(
                    request, "Email verified! Your account is pending admin approval."
                )
            else:
                messages.success(request, "Email verified! You can now log in.")

            # Redirect to appropriate login page
            if user.role == "customer":
                return redirect("users:customer_login")
            elif user.role == "supplier":
                return redirect("users:supplier_login")
            else:
                return redirect("users:staff_login")

        except EmailVerification.DoesNotExist:
            messages.error(request, "Invalid verification code.")

    return render(request, "users/verify_email.html", {"user": user})


def resend_verification_code_view(request):
    """Resend verification code to user's email"""
    user_id = request.session.get("unverified_user_id")
    if not user_id:
        messages.error(request, "No verification in progress.")
        return redirect("users:login_landing")

    try:
        user = User.objects.get(id=user_id)

        # Delete old verification code
        EmailVerification.objects.filter(user=user).delete()

        # Generate new code
        code = get_random_string(length=6, allowed_chars="0123456789")
        EmailVerification.objects.create(user=user, code=code)

        # Send email
        send_mail(
            "SupplyTrack Email Verification Code",
            f"Your new verification code is: {code}",
            settings.DEFAULT_FROM_EMAIL,
            [user.email],
            fail_silently=False,
        )

        messages.success(
            request, "📧 A new verification code has been sent to your email."
        )
    except User.DoesNotExist:
        messages.error(request, "User not found.")
        del request.session["unverified_user_id"]
        return redirect("users:login_landing")

    return redirect("users:verify_email")


# ==================== REGISTRATION VIEWS ====================


def customer_register_view(request):
    """Customer registration with email verification"""
    if request.method == "POST":
        form = CustomerRegistrationForm(request.POST)
        if form.is_valid():
            user = form.save()

            # ✅ Generate 6-digit code
            code = get_random_string(length=6, allowed_chars="0123456789")

            # ✅ Create EmailVerification entry
            EmailVerification.objects.create(user=user, code=code)

            # ✅ Force DB to commit before redirect — fixes "invalid code" on first try
            transaction.on_commit(lambda: None)

            # ✅ Send verification email
            send_mail(
                "SupplyTrack Email Verification Code",
                f"Your verification code is: {code}",
                settings.DEFAULT_FROM_EMAIL,
                [user.email],
                fail_silently=False,
            )

            # ✅ Store unverified user session
            request.session["unverified_user_id"] = user.id

            # ✅ Create audit trail entry
            Transaction.objects.create(
                user=user,
                transaction_type="user_registration",
                description=f"New customer '{user.username}' registered.",
            )
            try:
                log_audit(
                    user=user,
                    action="create",
                    instance=user,
                    changes={"registration": "customer"},
                    request=request,
                )
            except Exception:
                pass

            messages.success(
                request,
                "Registration successful! Please check your email for the verification code.",
            )
            return redirect("users:verify_email")
    else:
        form = CustomerRegistrationForm()

    return render(request, "users/customer_register.html", {"form": form})


def supplier_register_view(request):
    """Supplier registration with email verification"""
    if request.method == "POST":
        form = SupplierRegistrationForm(request.POST)
        if form.is_valid():
            user = form.save()

            # Create and send email verification code
            code = get_random_string(length=6, allowed_chars="0123456789")
            EmailVerification.objects.create(user=user, code=code)
            send_mail(
                "SupplyTrack Email Verification Code",
                f"Your verification code is: {code}",
                settings.DEFAULT_FROM_EMAIL,
                [user.email],
                fail_silently=False,
            )

            # Store user ID in session for verification
            request.session["unverified_user_id"] = user.id

            # Create transaction log
            Transaction.objects.create(
                user=user,
                transaction_type="user_registration",
                description=f"New supplier '{user.username}' from company '{user.supplier_profile.company_name}' registered (pending approval).",
            )
            try:
                log_audit(
                    user=user,
                    action="create",
                    instance=user,
                    changes={"registration": "supplier"},
                    request=request,
                )
            except Exception:
                pass

            messages.success(
                request,
                "Registration successful! Please verify your email. Your account will be reviewed by admin.",
            )
            return redirect("users:verify_email")
    else:
        form = SupplierRegistrationForm()

    return render(request, "users/supplier_register.html", {"form": form})


# ==================== LOGIN VIEWS ====================


def login_landing_view(request):
    """Landing page to choose login type"""
    return render(request, "users/login_landing.html")


from django.utils.http import url_has_allowed_host_and_scheme


# make sure merge_session_cart_with_user is imported or in the same file
def customer_login_view1(request):
    """Customer login page with ?next= redirect support"""
    next_url = request.GET.get("next", request.POST.get("next", ""))

    if request.method == "POST":
        form = CustomAuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()

            # Check if user is a customer
            if user.role != "customer":
                messages.error(request, "Unauthorized login attempt.")
                return render(
                    request,
                    "users/customer_login.html",
                    {"form": form, "next": next_url},
                )

            # Check if email is verified
            if not user.is_active:
                messages.warning(request, "Please verify your email first.")
                return render(
                    request,
                    "users/customer_login.html",
                    {"form": form, "next": next_url},
                )
            

            # Login successful
            login(request, user)

            # Send notification email
            send_login_notification_email(user)

            # ✅ Merge session cart into user cart
            merge_session_cart_with_user(request, user)

            # ✅ Process any pending add-to-cart action
            from apps.store.views import process_pending_add_to_cart

            process_pending_add_to_cart(request)

            # Create transaction log
            Transaction.objects.create(
                user=user,
                transaction_type="user_login",
                description=f"Customer '{user.username}' logged in.",
            )
            try:
                log_audit(
                    user=user,
                    action="login",
                    instance=user,
                    changes={"login": True},
                    request=request,
                )
            except Exception:
                pass

            # ✅ Safe redirect: only allow local URLs
            if next_url and url_has_allowed_host_and_scheme(
                next_url, allowed_hosts={request.get_host()}
            ):
                return redirect(next_url)
            return redirect("store:product_list")
    else:
        # form = CustomAuthenticationForm()
        # Invalid credentials
        messages.error(request, "Invalid username or password. Please try again.")


    return render(
        request, "users/customer_login.html", {"form": form, "next": next_url}
    )



def customer_login_view(request):
    """Customer login page with ?next= redirect support"""
    next_url = request.GET.get("next", request.POST.get("next", ""))
    form = CustomAuthenticationForm(request, data=request.POST or None)

    if request.method == "POST":
        if form.is_valid():
            user = form.get_user()

            # ✅ Only customers can use this login
            if user.role != "customer":
                messages.error(request, "Unauthorized login attempt. This portal is for customers only.")
                return render(request, "users/customer_login.html", {"form": form, "next": next_url})

            # ✅ Ensure account is verified/active
            if not user.is_active:
                messages.warning(request, "Your account is inactive. Please verify your email before logging in.")
                return render(request, "users/customer_login.html", {"form": form, "next": next_url})

            # ✅ Proceed to login
            login(request, user)
            messages.success(request, f"Welcome back, {user.username}!")

            # ✅ Email notification
            send_login_notification_email(user)

            # ✅ Merge session cart into user cart
            merge_session_cart_with_user(request, user)

            # ✅ Handle any pending add-to-cart actions
            process_pending_add_to_cart(request)

            # ✅ Log login transaction
            Transaction.objects.create(
                user=user,
                transaction_type="user_login",
                description=f"Customer '{user.username}' logged in.",
            )
            try:
                log_audit(
                    user=user,
                    action="login",
                    instance=user,
                    changes={"login": True},
                    request=request,
                )
            except Exception:
                pass

            # ✅ Safe redirect
            if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
                return redirect(next_url)
            return redirect("store:product_list")

        else:
            # Invalid credentials
            messages.error(request, "Invalid username or password. Please try again.")

    # GET request or invalid form re-render
    return render(request, "users/customer_login.html", {"form": form, "next": next_url})


def supplier_login_view(request):
    """Supplier login page with ?next= redirect support"""
    next_url = request.GET.get("next", request.POST.get("next", ""))
    form = CustomAuthenticationForm(request, data=request.POST or None)

    if request.method == "POST":
        if form.is_valid():
            user = form.get_user()

            # ✅ Role check
            if user.role != "supplier":
                messages.error(request, "Unauthorized login attempt. This portal is for suppliers only.")
                return render(request, "users/supplier_login.html", {"form": form, "next": next_url})

            # ✅ Email verification check
            if not user.is_active:
                messages.warning(request, "Your account is inactive. Please verify your email before logging in.")
                return render(request, "users/supplier_login.html", {"form": form, "next": next_url})

            # ✅ Admin approval check
            if not user.is_approved:
                messages.warning(request, "Your account is pending admin approval.")
                return render(request, "users/supplier_login.html", {"form": form, "next": next_url})

            # ✅ Login success
            login(request, user)
            messages.success(request, f"Welcome back, {user.username}!")

            send_login_notification_email(user)

            # ✅ Log transaction and audit
            Transaction.objects.create(
                user=user,
                transaction_type="user_login",
                description=f"Supplier '{user.username}' logged in.",
            )
            try:
                log_audit(
                    user=user,
                    action="login",
                    instance=user,
                    changes={"login": True},
                    request=request,
                )
            except Exception:
                pass

            # ✅ Safe redirect
            if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
                return redirect(next_url)
            return redirect("suppliers:supplier_dashboard")

        else:
            messages.error(request, "Invalid username or password. Please try again.")

    return render(request, "users/supplier_login.html", {"form": form, "next": next_url})



def staff_login_view(request):
    """Staff/Admin/Manager/Delivery login page with ?next= redirect support"""
    next_url = request.GET.get("next", request.POST.get("next", ""))
    form = CustomAuthenticationForm(request, data=request.POST or None)

    if request.method == "POST":
        if form.is_valid():
            user = form.get_user()

            # ✅ Role check
            if user.role not in ["staff", "manager", "admin", "delivery"]:
                messages.error(request, "Unauthorized login attempt. This portal is for internal staff only.")
                return render(request, "users/staff_login.html", {"form": form, "next": next_url})

            # ✅ Active check
            if not user.is_active:
                messages.warning(request, "Your account is inactive. Please contact admin.")
                return render(request, "users/staff_login.html", {"form": form, "next": next_url})

            # ✅ Login success
            login(request, user)
            messages.success(request, f"Welcome back, {user.get_full_name() or user.username}!")

            send_login_notification_email(user)

            # ✅ Log transaction and audit
            Transaction.objects.create(
                user=user,
                transaction_type="user_login",
                description=f"{user.role.capitalize()} '{user.username}' logged in.",
            )
            try:
                log_audit(
                    user=user,
                    action="login",
                    instance=user,
                    changes={"login": True},
                    request=request,
                )
            except Exception:
                pass

            # ✅ Profile completion reminder
            if user.role in ["staff", "manager", "delivery"]:
                if not (user.first_name and user.last_name):
                    messages.info(request, "Welcome! Please complete your profile to get started.")
                    return redirect("users:staff_profile_completion")

            # ✅ Role-based redirects
            if user.role == "delivery":
                return redirect("delivery:delivery_list")

            # ✅ Safe redirect or dashboard
            if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
                return redirect(next_url)
            return redirect("inventory:dashboard")

        else:
            messages.error(request, "Invalid username or password. Please try again.")

    return render(request, "users/staff_login.html", {"form": form, "next": next_url})

# ==================== PASSWORD RESET VIEWS ====================


def forgot_password_view(request):
    """Generate reset code and send to user's email"""
    if request.method == "POST":
        email = request.POST.get("email")
        try:
            user = User.objects.get(email=email)

            # ✅ Check if user has verified their email
            if not user.is_active:
                messages.error(
                    request, "Please verify your email first before resetting password."
                )
                return render(request, "users/forgot_password.html")

            # Generate code
            code = generate_verification_code()
            request.session["reset_code"] = code
            request.session["reset_email"] = email
            request.session.set_expiry(180)  # 3 minutes

            # Send code
            send_verification_email(email, code)
            messages.success(
                request, "📨 A verification code has been sent to your email."
            )

            return redirect("users:verify_reset_code")

        except User.DoesNotExist:
            messages.error(request, "No account with this email was found.")

    return render(request, "users/forgot_password.html")


def verify_reset_code_view(request):
    """Verify the reset code"""
    code = request.session.get("reset_code")
    if not code:
        messages.error(request, "Session expired or invalid. Please try again.")
        return redirect("users:forgot_password")

    if request.method == "POST":
        input_code = request.POST.get("code")
        if input_code == code:
            request.session["code_verified"] = True
            return redirect("users:reset_password")
        else:
            messages.error(request, "Invalid or expired code.")

    expiration_time = now() + timedelta(minutes=3)
    return render(
        request,
        "users/verify_reset_code.html",
        {"expiration_timestamp": int(expiration_time.timestamp() * 1000)},
    )


def resend_reset_code_view(request):
    """Resend password reset code"""
    email = request.session.get("reset_email")
    if not email:
        messages.error(request, "No reset request found.")
        return redirect("users:forgot_password")

    code = generate_verification_code()
    request.session["reset_code"] = code
    request.session.set_expiry(180)

    send_verification_email(email, code)
    messages.success(request, "A new reset code has been sent.")

    return redirect("users:verify_reset_code")


def reset_password_view(request):
    """Final password reset"""
    if not request.session.get("code_verified"):
        messages.error(request, "You must verify the code first.")
        return redirect("users:forgot_password")

    if request.method == "POST":
        password = request.POST.get("new_password")
        confirm = request.POST.get("confirm_password")

        if password != confirm:
            messages.error(request, "Passwords do not match.")
        else:
            email = request.session.get("reset_email")
            try:
                user = User.objects.get(email=email)
                user.set_password(password)
                user.save()

                # Create transaction log
                Transaction.objects.create(
                    user=user,
                    transaction_type="password_reset",
                    description=f"User '{user.username}' reset their password.",
                )
                try:
                    log_audit(
                        user=user,
                        action="update",
                        instance=user,
                        changes={"password_reset": True},
                        request=request,
                    )
                except Exception:
                    pass

                # Clear reset session
                for key in ["reset_code", "reset_email", "code_verified"]:
                    if key in request.session:
                        del request.session[key]

                messages.success(
                    request, "Password reset successful! You can now log in."
                )

                # Redirect based on role
                if user.role == "customer":
                    return redirect("users:customer_login")
                elif user.role == "supplier":
                    return redirect("users:supplier_login")
                else:
                    return redirect("users:staff_login")

            except User.DoesNotExist:
                messages.error(request, "Error resetting password. Please try again.")

    return render(request, "users/reset_password.html")


# ==================== LOGOUT VIEWS ====================


@api_view(["POST"])
def logout(request):
    """API logout - clear JWT cookies"""
    try:
        res = Response({"success": True})
        res.delete_cookie("access_token", path="/", samesite="None")
        res.delete_cookie("refresh_token", path="/", samesite="None")
        return res
    except Exception as e:
        return Response({"success": False, "error": str(e)}, status=400)


def logout_view(request):
    """Regular logout view"""
    if request.user.is_authenticated:
        # Create transaction log before logout
        Transaction.objects.create(
            user=request.user,
            transaction_type="user_logout",
            description=f"User '{request.user.username}' logged out.",
        )
        try:
            log_audit(
                user=request.user,
                action="logout",
                instance=request.user,
                changes={"logout": True},
                request=request,
            )
        except Exception:
            pass

    django_logout(request)
    request.session.flush()
    messages.success(request, "You have been logged out successfully.")
    return redirect("store:landing_page")


# ==================== USER MANAGEMENT ====================


@login_required
def user_management(request):
    """Admin page to manage users"""
    if request.user.role != "admin":
        return HttpResponseForbidden("You do not have permission to view this page.")

    users = User.objects.all()

    if request.method == "POST":
        user_id = request.POST.get("user_id")
        role = request.POST.get("role")
        username = request.POST.get("username")
        email = request.POST.get("email")
        is_approved = request.POST.get("is_approved") == "true"  # ✅ Checkbox handling

        valid_roles = ["admin", "manager", "staff", "supplier", "delivery", "customer"]

        if role not in valid_roles:
            messages.error(request, "Invalid role selected.")
            return redirect("users:user_management")

        if user_id:  # Update existing user
            try:
                user = User.objects.get(id=user_id)
                old_role = user.role
                old_approval = user.is_approved  # ✅ track previous state

                user.username = username
                user.email = email
                user.role = role

                # ✅ Only admins can toggle supplier approval
                if role == "supplier":
                    user.is_approved = is_approved
                else:
                    user.is_approved = True  # Auto-approved for other roles

                user.save()

                # ✅ Log if approval status changed
                if old_approval != user.is_approved:
                    status_text = "approved" if user.is_approved else "revoked"
                    Transaction.objects.create(
                        user=user,
                        transaction_type="supplier_approval",
                        description=f"Supplier '{user.username}' approval {status_text} by admin {request.user.username}.",
                    )

                # Log role changes (existing code)
                Transaction.objects.create(
                    user=user,
                    transaction_type="user_update",
                    description=f"Admin updated user '{username}' from role '{old_role}' to '{role}'.",
                )

                try:
                    log_audit(
                        user=request.user,
                        action="update",
                        instance=user,
                        changes={"role": [old_role, role], "is_approved": [old_approval, user.is_approved]},
                        request=request,
                    )
                except Exception:
                    pass

                messages.success(request, f"User '{username}' updated successfully.")
                return redirect("users:user_management")

            except User.DoesNotExist:
                messages.error(request, "User not found.")
                return redirect("users:user_management")

    context = {
        "users": users.filter(is_active=True),  # Only show active users
        "admins": User.objects.filter(role="admin", is_active=True),
        "managers": User.objects.filter(role="manager", is_active=True),
        "staffs": User.objects.filter(role="staff", is_active=True),
        "customers": User.objects.filter(role="customer", is_active=True),
        "suppliers": User.objects.filter(role="supplier", is_active=True),
        "deliverys": User.objects.filter(role="delivery", is_active=True),
    }
    return render(request, "users/user_management.html", context)


@csrf_exempt
@login_required
def delete_users(request):
    """Archive (soft delete) multiple users (admin only)"""
    if request.user.role != "admin":
        return HttpResponseForbidden("You do not have permission to archive users.")

    if request.method == "POST":
        try:
            data = json.loads(request.body)
            user_ids_to_delete = data.get("ids", [])

            if not user_ids_to_delete:
                return JsonResponse(
                    {"success": False, "error": "No user IDs provided."}
                )

            # Get usernames before archiving for transaction log
            users_to_delete = User.objects.filter(id__in=user_ids_to_delete, is_active=True)
            deleted_usernames = list(users_to_delete.values_list("username", flat=True))

            # Soft delete: set is_active=False
            archived_count = users_to_delete.update(is_active=False)

            if archived_count > 0:
                # Create transaction log
                Transaction.objects.create(
                    user=request.user,
                    transaction_type="user_archive",
                    description=f"Admin archived {archived_count} user(s): {', '.join(deleted_usernames)}",
                )
                try:
                    log_audit(
                        user=request.user,
                        action="archive",
                        instance=None,
                        changes={"archived_users": deleted_usernames},
                        request=request,
                    )
                except Exception:
                    pass

                return JsonResponse({
                    "success": True,
                    "user_names": deleted_usernames,
                    "message": f"Successfully archived {archived_count} user(s)."
                })
            else:
                return JsonResponse(
                    {"success": False, "error": "No active users found to archive."}
                )

        except Exception as e:
            return JsonResponse({"success": False, "error": f"Error: {str(e)}"})

    return JsonResponse({"success": False, "error": "Invalid request method"})


from django.shortcuts import get_object_or_404, redirect
from django.contrib import messages
from django.db import transaction
from django.contrib.auth.decorators import login_required
from apps.users.models import User  # adjust path if needed
# from apps.core.utils import log_audit  # if you have audit logging

@login_required
def toggle_supplier_approval(request, user_id):
    """
    Toggle supplier's approval status (Pending ↔ Approved).
    Works similarly to the product active toggle.
    """
    supplier = get_object_or_404(User, id=user_id, role="supplier")

    try:
        with transaction.atomic():
            old_status = "Approved" if supplier.is_approved else "Pending"
            supplier.is_approved = not supplier.is_approved
            new_status = "Approved" if supplier.is_approved else "Pending"
            supplier.save()

            # Optional: Audit log
            changes = {
                "Supplier Approval": f"{old_status} → {new_status}"
            }

            def _log_toggle():
                try:
                    log_audit(
                        user=request.user,
                        action="update",
                        instance=supplier,
                        changes=changes,
                        request=request,
                    )
                except Exception:
                    pass

            transaction.on_commit(_log_toggle)

            messages.success(
                request,
                f"Supplier '{supplier.username}' status updated to {new_status}."
            )

    except Exception as e:
        messages.error(request, f"An error occurred while toggling approval: {e}")

    return redirect("users:user_management")  # adjust to your correct view name


@login_required
def user_archive_list(request):
    """Display archived (inactive) users"""
    if request.user.role != "admin":
        return HttpResponseForbidden("You do not have permission to view this page.")
    
    archived_users = User.objects.filter(is_active=False).order_by("-date_joined")
    
    context = {
        "users": archived_users,
        "admins": archived_users.filter(role="admin"),
        "managers": archived_users.filter(role="manager"),
        "staffs": archived_users.filter(role="staff"),
        "customers": archived_users.filter(role="customer"),
        "suppliers": archived_users.filter(role="supplier"),
        "deliverys": archived_users.filter(role="delivery"),
    }
    
    return render(request, "users/user_archive_list.html", context)


@csrf_exempt
@login_required
def restore_users(request):
    """Restore archived users"""
    if request.user.role != "admin":
        return HttpResponseForbidden("You do not have permission to restore users.")
    
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            ids = data.get("ids", [])
            
            if not ids:
                return JsonResponse(
                    {"success": False, "error": "No user IDs provided."},
                    status=400,
                )
            
            users_to_restore = User.objects.filter(id__in=ids, is_active=False)
            count = users_to_restore.count()
            user_names = list(users_to_restore.values_list("username", flat=True))
            
            # Restore users by setting is_active=True
            users_to_restore.update(is_active=True)
            
            try:
                log_audit(
                    user=request.user,
                    action="restore",
                    instance=None,
                    changes={"restored_users": user_names},
                    request=request,
                )
            except Exception:
                pass
            
            return JsonResponse({
                "success": True,
                "user_names": user_names,
                "message": f"Successfully restored {count} user(s)."
            })
        except Exception as e:
            return JsonResponse({
                "success": False,
                "error": f"An error occurred during restore: {str(e)}"
            }, status=500)
    
    return JsonResponse({"success": False, "error": "Invalid request method."}, status=405)


@csrf_exempt
@login_required
def permanently_delete_users(request):
    """Permanently delete archived users"""
    if request.user.role != "admin":
        return HttpResponseForbidden("You do not have permission to permanently delete users.")
    
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            ids = data.get("ids", [])
            
            if not ids:
                return JsonResponse(
                    {"success": False, "error": "No user IDs provided."},
                    status=400,
                )
            
            # Only delete inactive (archived) users
            users_to_delete = User.objects.filter(id__in=ids, is_active=False)
            user_names = list(users_to_delete.values_list("username", flat=True))
            
            deleted_count, _ = users_to_delete.delete()
            
            try:
                log_audit(
                    user=request.user,
                    action="permanent_delete",
                    instance=None,
                    changes={
                        "permanently_deleted_users": user_names,
                        "count": deleted_count,
                    },
                    request=request,
                )
            except Exception:
                pass
            
            return JsonResponse({
                "success": True,
                "user_names": user_names,
                "message": f"Successfully permanently deleted {deleted_count} user(s)."
            })
        except Exception as e:
            return JsonResponse({
                "success": False,
                "error": f"An error occurred during deletion: {str(e)}"
            }, status=500)
    
    return JsonResponse({"success": False, "error": "Invalid request method."}, status=405)


from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.contrib import messages
from django.http import HttpResponseForbidden, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils.crypto import get_random_string
import json

from .models import User
from apps.transactions.models import Transaction, log_audit


@login_required
def admin_create_staff_view(request):
    """
    Admin page to create staff accounts with minimal information
    Staff can complete their profile later
    """
    if request.user.role != "admin":
        return HttpResponseForbidden("You do not have permission to access this page.")
    
    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        email = request.POST.get("email", "").strip()
        password = request.POST.get("password", "").strip()
        role = request.POST.get("role", "staff")  # Default to staff
        
        # Validate role (only allow staff-related roles)
        valid_roles = ["staff", "manager", "delivery"]
        if role not in valid_roles:
            messages.error(request, "Invalid role selected.")
            return render(request, "users/admin_create_staff.html", {"valid_roles": valid_roles})
        
        # Validate required fields
        if not username or not email or not password:
            messages.error(request, "Username, email, and password are required.")
            return render(request, "users/admin_create_staff.html", {"valid_roles": valid_roles})
        
        # Check if username exists
        if User.objects.filter(username=username).exists():
            messages.error(request, f"Username '{username}' already exists.")
            return render(request, "users/admin_create_staff.html", {"valid_roles": valid_roles})
        
        # Check if email exists
        if User.objects.filter(email=email).exists():
            messages.error(request, f"Email '{email}' is already registered.")
            return render(request, "users/admin_create_staff.html", {"valid_roles": valid_roles})
        
        # Create staff user
        try:
            user = User.objects.create_user(
                username=username,
                email=email,
                role=role,
                is_active=True,  # Staff accounts are immediately active
                is_approved=True  # Staff accounts are pre-approved
            )
            user.set_password(password)
            user.save()
            
            # Create transaction log
            Transaction.objects.create(
                user=request.user,
                transaction_type="user_creation",
                description=f"Admin '{request.user.username}' created new {role} account for '{username}'."
            )
            
            try:
                log_audit(
                    user=request.user,
                    action="create",
                    instance=user,
                    changes={"created_by": "admin", "role": role},
                    request=request,
                )
            except Exception:
                pass
            
            messages.success(
                request, 
                f"✅ {role.capitalize()} account '{username}' created successfully! "
                f"They can now log in and complete their profile."
            )
            return redirect("users:admin_create_staff")
            
        except Exception as e:
            messages.error(request, f"Error creating account: {str(e)}")
            return render(request, "users/admin_create_staff.html", {"valid_roles": valid_roles})
    
    # GET request - show form
    valid_roles = ["staff", "manager", "delivery"]
    recent_staff = User.objects.filter(role__in=valid_roles).order_by("-date_joined")[:10]
    
    context = {
        "valid_roles": valid_roles,
        "recent_staff": recent_staff,
    }
    return render(request, "users/admin_create_staff.html", context)


@login_required
def staff_profile_completion_view(request):
    """
    Allow staff members to complete their profile information
    This is for accounts created by admin with minimal info
    """
    user = request.user
    
    # Only allow staff, manager, delivery roles
    if user.role not in ["staff", "manager", "delivery"]:
        return HttpResponseForbidden("This page is only for staff members.")
    
    if request.method == "POST":
        # Update user information
        first_name = request.POST.get("first_name", "").strip()
        last_name = request.POST.get("last_name", "").strip()
        email = request.POST.get("email", "").strip()
        
        # Optional: Allow password change
        new_password = request.POST.get("new_password", "").strip()
        confirm_password = request.POST.get("confirm_password", "").strip()
        
        # Update basic info
        user.first_name = first_name
        user.last_name = last_name
        
        # Validate email if changed
        if email != user.email:
            if User.objects.filter(email=email).exclude(id=user.id).exists():
                messages.error(request, "This email is already in use.")
                return render(request, "users/staff_profile_completion.html")
            user.email = email
        
        # Handle password change if provided
        if new_password:
            if new_password != confirm_password:
                messages.error(request, "Passwords do not match.")
                return render(request, "users/staff_profile_completion.html")
            
            if len(new_password) < 8:
                messages.error(request, "Password must be at least 8 characters long.")
                return render(request, "users/staff_profile_completion.html")
            
            user.set_password(new_password)
            messages.info(request, "Password updated. Please log in again with your new password.")
        
        user.save()
        
        # Create transaction log
        Transaction.objects.create(
            user=user,
            transaction_type="profile_update",
            description=f"{user.role.capitalize()} '{user.username}' completed their profile information."
        )
        
        try:
            log_audit(
                user=user,
                action="update",
                instance=user,
                changes={"profile_completed": True},
                request=request,
            )
        except Exception:
            pass
        
        messages.success(request, "✅ Profile updated successfully!")
        
        # If password was changed, logout and redirect to login
        if new_password:
            from django.contrib.auth import logout as django_logout
            django_logout(request)
            return redirect("users:staff_login")
        
        return redirect("inventory:dashboard")
    
    # GET request - show form
    context = {
        "user": user,
        "is_profile_complete": bool(user.first_name and user.last_name),
    }
    return render(request, "users/staff_profile_completion.html", context)


# API endpoint for bulk staff creation (optional)
@csrf_exempt
@login_required
def api_bulk_create_staff(request):
    """
    API endpoint for creating multiple staff accounts at once
    Expects JSON: [{"username": "...", "email": "...", "password": "...", "role": "staff"}, ...]
    """
    if request.user.role != "admin":
        return JsonResponse({"success": False, "error": "Admin access required."}, status=403)
    
    if request.method != "POST":
        return JsonResponse({"success": False, "error": "POST method required."}, status=405)
    
    try:
        data = json.loads(request.body)
        staff_list = data.get("staff", [])
        
        if not staff_list or not isinstance(staff_list, list):
            return JsonResponse({"success": False, "error": "Invalid data format."}, status=400)
        
        created_users = []
        errors = []
        
        for idx, staff_data in enumerate(staff_list):
            username = staff_data.get("username", "").strip()
            email = staff_data.get("email", "").strip()
            password = staff_data.get("password", "").strip()
            role = staff_data.get("role", "staff")
            
            # Validate
            if not username or not email or not password:
                errors.append(f"Entry {idx + 1}: Missing required fields")
                continue
            
            if role not in ["staff", "manager", "delivery"]:
                errors.append(f"Entry {idx + 1}: Invalid role '{role}'")
                continue
            
            if User.objects.filter(username=username).exists():
                errors.append(f"Entry {idx + 1}: Username '{username}' already exists")
                continue
            
            if User.objects.filter(email=email).exists():
                errors.append(f"Entry {idx + 1}: Email '{email}' already registered")
                continue
            
            # Create user
            try:
                user = User.objects.create_user(
                    username=username,
                    email=email,
                    role=role,
                    is_active=True,
                    is_approved=True
                )
                user.set_password(password)
                user.save()
                
                created_users.append({
                    "username": username,
                    "email": email,
                    "role": role
                })
                
                # Log transaction
                Transaction.objects.create(
                    user=request.user,
                    transaction_type="user_creation",
                    description=f"Admin bulk-created {role} account for '{username}'."
                )
                
            except Exception as e:
                errors.append(f"Entry {idx + 1}: {str(e)}")
        
        return JsonResponse({
            "success": True,
            "created": len(created_users),
            "created_users": created_users,
            "errors": errors
        })
        
    except json.JSONDecodeError:
        return JsonResponse({"success": False, "error": "Invalid JSON format."}, status=400)
    except Exception as e:
        return JsonResponse({"success": False, "error": str(e)}, status=500)
# Add these to your views.py or create a new api_views.py file

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from django.shortcuts import get_object_or_404

from .models import User
from .serializer import (
    StaffCreationSerializer,
    StaffProfileUpdateSerializer,
    StaffPasswordChangeSerializer,
    UserSerializer
)


# ==================== ADMIN API: CREATE STAFF ====================

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def api_create_staff_account(request):
    """
    API endpoint for admin to create a single staff account
    POST /api/users/create-staff/
    
    Required fields:
    - username
    - email
    - password
    - role (staff, manager, or delivery)
    """
    # Check admin permission
    if request.user.role != "admin":
        return Response(
            {"error": "Admin access required."},
            status=status.HTTP_403_FORBIDDEN
        )
    
    serializer = StaffCreationSerializer(data=request.data, context={"request": request})
    
    if serializer.is_valid():
        user = serializer.save()
        return Response(
            {
                "success": True,
                "message": f"Staff account '{user.username}' created successfully.",
                "user": UserSerializer(user).data
            },
            status=status.HTTP_201_CREATED
        )
    
    return Response(
        {"success": False, "errors": serializer.errors},
        status=status.HTTP_400_BAD_REQUEST
    )


# ==================== STAFF API: VIEW PROFILE ====================

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def api_staff_profile_view(request):
    """
    API endpoint for staff to view their own profile
    GET /api/users/staff/profile/
    """
    # Only allow staff-related roles
    if request.user.role not in ["admin","staff", "manager", "delivery"]:
        return Response(
            {"error": "This endpoint is for staff members only."},
            status=status.HTTP_403_FORBIDDEN
        )
    
    serializer = UserSerializer(request.user)
    
    # Check if profile is complete
    is_complete = bool(
        request.user.first_name and 
        request.user.last_name
    )
    
    return Response({
        "user": serializer.data,
        "is_profile_complete": is_complete
    })


# ==================== STAFF API: UPDATE PROFILE ====================

@api_view(["PUT", "PATCH"])
@permission_classes([IsAuthenticated])
def api_staff_profile_update(request):
    """
    API endpoint for staff to update their profile information
    PUT/PATCH /api/users/staff/profile/update/
    
    Accepts:
    - first_name
    - last_name
    - email
    """
    # Only allow staff-related roles
    if request.user.role not in ["admin","staff", "manager", "delivery"]:
        return Response(
            {"error": "This endpoint is for staff members only."},
            status=status.HTTP_403_FORBIDDEN
        )
    
    serializer = StaffProfileUpdateSerializer(
        request.user,
        data=request.data,
        partial=(request.method == "PATCH")
    )
    
    if serializer.is_valid():
        user = serializer.save()
        return Response({
            "success": True,
            "message": "Profile updated successfully.",
            "user": UserSerializer(user).data
        })
    
    return Response(
        {"success": False, "errors": serializer.errors},
        status=status.HTTP_400_BAD_REQUEST
    )


# ==================== STAFF API: CHANGE PASSWORD ====================

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def api_staff_change_password(request):
    """
    API endpoint for staff to change their password
    POST /api/users/staff/change-password/
    
    Required fields:
    - new_password
    - new_password_confirm
    """
    # Only allow staff-related roles
    if request.user.role not in["admin","staff", "manager", "delivery"]:
        return Response(
            {"error": "This endpoint is for staff members only."},
            status=status.HTTP_403_FORBIDDEN
        )
    
    serializer = StaffPasswordChangeSerializer(
        data=request.data,
        context={"request": request}
    )
    
    if serializer.is_valid():
        serializer.save()
        return Response({
            "success": True,
            "message": "Password changed successfully. Please log in again with your new password."
        })
    
    return Response(
        {"success": False, "errors": serializer.errors},
        status=status.HTTP_400_BAD_REQUEST
    )


# ==================== ADMIN API: LIST ALL STAFF ====================

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def api_list_staff_accounts(request):
    """
    API endpoint for admin to list all staff accounts
    GET /api/users/staff/list/
    
    Query params:
    - role (optional): filter by role (staff, manager, delivery)
    """
    # Check admin permission
    if request.user.role != "admin":
        return Response(
            {"error": "Admin access required."},
            status=status.HTTP_403_FORBIDDEN
        )
    
    # Get role filter if provided
    role_filter = request.query_params.get("role", None)
    
    # Query staff accounts
    if role_filter and role_filter in ["admin","staff", "manager", "delivery"]:
        staff_users = User.objects.filter(role=role_filter).order_by("-date_joined")
    else:
        staff_users = User.objects.filter(
            role__in=["admin","staff", "manager", "delivery"]
        ).order_by("-date_joined")
    
    serializer = UserSerializer(staff_users, many=True)
    
    return Response({
        "count": staff_users.count(),
        "staff": serializer.data
    })


# ==================== ADMIN API: DELETE STAFF ACCOUNT ====================

@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
def api_delete_staff_account(request, user_id):
    """
    API endpoint for admin to delete a staff account
    DELETE /api/users/staff/<user_id>/delete/
    """
    # Check admin permission
    if request.user.role != "admin":
        return Response(
            {"error": "Admin access required."},
            status=status.HTTP_403_FORBIDDEN
        )
    
    # Get staff user
    user = get_object_or_404(User, id=user_id)
    
    # Verify it's a staff account
    if user.role not in ["admin","staff", "manager", "delivery"]:
        return Response(
            {"error": "Can only delete staff, manager, or delivery accounts."},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    # Prevent deleting self
    if user.id == request.user.id:
        return Response(
            {"error": "Cannot delete your own account."},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    # Store username before deletion
    username = user.username
    role = user.role
    
    # Create transaction log before deletion
    from apps.transactions.models import Transaction
    Transaction.objects.create(
        user=request.user,
        transaction_type="user_deletion",
        description=f"Admin '{request.user.username}' deleted {role} account '{username}'."
    )
    
    # Delete user
    user.delete()
    
    return Response({
        "success": True,
        "message": f"Staff account '{username}' deleted successfully."
    })


# ==================== STAFF API: CHECK PROFILE STATUS ====================

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def api_check_profile_completion(request):
    """
    API endpoint to check if staff profile is complete
    GET /api/users/staff/profile-status/
    
    Returns boolean indicating if profile needs completion
    """
    # Only allow staff-related roles
    if request.user.role not in ["admin","staff", "manager", "delivery"]:
        return Response(
            {"error": "This endpoint is for staff members only."},
            status=status.HTTP_403_FORBIDDEN
        )
    
    is_complete = bool(
        request.user.first_name and 
        request.user.last_name and
        request.user.email
    )
    
    missing_fields = []
    if not request.user.first_name:
        missing_fields.append("first_name")
    if not request.user.last_name:
        missing_fields.append("last_name")
    if not request.user.email:
        missing_fields.append("email")
    
    return Response({
        "is_complete": is_complete,
        "missing_fields": missing_fields,
        "needs_completion": not is_complete
    })
# ==================== PROFILE MANAGEMENT ====================

from .forms import CustomerProfileUpdateForm


@login_required
def customer_profile_edit(request):
    """
    Allow customers to edit their profile/address and name
    Uses CustomerProfileUpdateForm to handle both User and CustomerProfile fields
    """
    if request.user.role != "customer":
        return HttpResponseForbidden("Only customers can access this page.")

    try:
        profile = request.user.customer_profile
    except CustomerProfile.DoesNotExist:
        profile = CustomerProfile.objects.create(user=request.user)
        messages.info(request, "Profile created! Please complete your information.")

    if request.method == "POST":
        form = CustomerProfileUpdateForm(
            request.POST, instance=profile, user=request.user
        )

        if form.is_valid():
            form.save()

            # Create transaction log
            Transaction.objects.create(
                user=request.user,
                transaction_type="profile_update",
                description=f"Customer '{request.user.username}' updated their profile.",
            )

            messages.success(request, "✅ Profile updated successfully!")

            # Redirect to next URL or default
            next_url = request.GET.get("next")
            if next_url:
                return redirect(next_url)
            return redirect("store:product_list")
        else:
            # Show form errors
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"{field}: {error}")
    else:
        form = CustomerProfileUpdateForm(instance=profile, user=request.user)

    context = {
        "form": form,
        "profile": profile,
        "user": request.user,
    }
    return render(request, "users/profile_edit.html", context)


# ==================== NEW: VIEW PROFILE (READ-ONLY) ====================


@login_required
def customer_profile_view(request):
    """
    Display customer profile in read-only mode
    """
    if request.user.role != "customer":
        return HttpResponseForbidden("Only customers can access this page.")

    try:
        profile = request.user.customer_profile
    except CustomerProfile.DoesNotExist:
        messages.warning(request, "Please complete your profile first.")
        return redirect("users:profile_edit")

    # Check if profile is complete
    is_complete = all(
        [
            request.user.first_name,
            request.user.last_name,
            profile.street_address,
            profile.city,
            profile.province,
            profile.zip_code,
        ]
    )

    context = {
        "profile": profile,
        "user": request.user,
        "is_complete": is_complete,
    }
    return render(request, "users/profile_view.html", context)


# ==================== API VIEWS ====================


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def is_authenticated(request):
    """Check if user is authenticated"""
    return Response({"authenticated": True})
