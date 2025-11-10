from django.shortcuts import render, redirect
from django.contrib.auth import login, logout
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

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from django.db import transaction

# from .forms import CustomUserCreationForm
from .models import User, EmailVerification
from django.views.decorators.csrf import csrf_exempt
from .forms import (
    CustomerRegistrationForm,
    SupplierRegistrationForm,
    CustomAuthenticationForm,
)


def generate_verification_code():
    return str(random.randint(100000, 999999))


def send_verification_email(email, code):
    send_mail(
        subject="Your Verification Code",
        message=f"Your verification code is: {code}",
        from_email="SupplyTrack <danegela13@gmail.com>",
        recipient_list=[email],
    )


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
        except:
            return Response({"success": False}, status=400)


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
        except:
            return Response({"refreshed": False}, status=400)


def send_registration_success_email(user):
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
        fail_silently=False,
    )


def send_login_notification_email(user):
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
        fail_silently=True,  # don't crash if email fails
    )


# ----------------------------
# EMAIL VERIFICATION VIEW
# ----------------------------
def verify_email_code_view(request):
    user_id = request.session.get("unverified_user_id")
    if not user_id:
        return redirect("users:login_landing")

    user = User.objects.filter(id=user_id).first()

    if request.method == "POST":
        entered_code = request.POST.get("code")

        # Force database to sync state (commit or clear stale cache)
        transaction.on_commit(lambda: None)

        try:
            code_entry = EmailVerification.objects.select_for_update().get(
                user=user, code=entered_code
            )
            user.is_active = True
            user.save()
            code_entry.delete()
            del request.session["unverified_user_id"]
            messages.success(request, "Email verified. You can now log in.")
            return redirect("users:customer_login")
        except EmailVerification.DoesNotExist:
            messages.error(request, "Invalid code.")

    return render(request, "users/verify_email.html", {"user": user})


def resend_verification_code_view(request):
    temp_user_data = request.session.get("temp_user_data")

    if not temp_user_data:
        messages.error(request, "No registration in progress.")
        return redirect("users:register")

    code = generate_verification_code()
    request.session["verification_code"] = code
    request.session.set_expiry(180)

    send_verification_email(temp_user_data["email"], code)
    messages.success(request, "📧 A new verification code has been sent to your email.")
    return redirect("users:verify_email")


def redirect_based_on_role(user):
    if user.role == "admin":
        return redirect("inventory:dashboard")
    elif user.role == "manager":
        return redirect("inventory:dashboard")
    elif user.role == "staff":
        return redirect("inventory:dashboard")
    elif user.role == "supplier":
        return redirect("suppliers:supplier_order_list")
    elif user.role == "customer":
        return redirect("store:product_list")
    else:
        return redirect("delivery:delivery_list")


def login_landing_view(request):
    return render(request, "users/login_landing.html")


# ----------------------------
# CUSTOMER LOGIN VIEW
# ----------------------------
def customer_login_view(request):
    if request.method == "POST":
        form = CustomAuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            if user.role == "customer":
                login(request, user)
                return redirect("store:product_list")  # Adjust to your actual dashboard
            else:
                messages.error(request, "Unauthorized login attempt.")
    else:
        form = CustomAuthenticationForm()
    return render(request, "users/customer_login.html", {"form": form})


def customer_register_view(request):
    if request.method == "POST":
        form = CustomerRegistrationForm(request.POST)
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
            request.session["unverified_user_id"] = user.id
            return redirect("users:verify_email")
    else:
        form = CustomerRegistrationForm()
    return render(request, "users/customer_register.html", {"form": form})


# ----------------------------
# SUPPLIER LOGIN VIEW
# ----------------------------
def supplier_login_view(request):
    if request.method == "POST":
        form = CustomAuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            if user.role == "supplier":
                if not user.is_approved:
                    messages.warning(request, "Your account is pending approval.")
                else:
                    login(request, user)
                    return redirect("suppliers:supplier_order_list")
            else:
                messages.error(request, "Unauthorized login attempt.")
    else:
        form = CustomAuthenticationForm()
    return render(request, "users/supplier_login.html", {"form": form})


# ----------------------------
# SUPPLIER REGISTRATION
# ----------------------------
def supplier_register_view(request):
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
            request.session["unverified_user_id"] = user.id
            return redirect("users:verify_email")
    else:
        form = SupplierRegistrationForm()
    return render(request, "users/supplier_register.html", {"form": form})


def staff_login_view(request):
    if request.method == "POST":
        form = CustomAuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            if user.role in ["staff", "manager", "admin", "delivery"]:
                login(request, user)
                return redirect("inventory:dashboard")
            else:
                messages.error(request, "Unauthorized login attempt.")
    else:
        form = CustomAuthenticationForm()
    return render(request, "users/staff_login.html", {"form": form})


# def login_view(request):
#     if request.user.is_authenticated:
#         return redirect_based_on_role(request.user)

#     if request.method == "POST":
#         form = AuthenticationForm(request, data=request.POST)
#         if form.is_valid():
#             user = form.get_user()
#             login(request, user)

#             # Send login notification email
#             send_login_notification_email(user)

#             redirect_url = redirect_based_on_role(user).url
#             messages.success(request, f"Welcome back, {user.username}!")
#             return render(request, "users/login.html", {
#                 "form": AuthenticationForm(),
#                 "messages": messages,
#             })
#         else:
#             messages.error(request, "Invalid credentials.")
#     else:
#         form = AuthenticationForm()
#     return render(request, "users/login.html", {"form": form})


# Generate reset code and send to user's email
def forgot_password_view(request):
    if request.method == "POST":
        email = request.POST.get("email")
        try:
            user = User.objects.get(email=email)
            code = generate_verification_code()
            request.session["reset_code"] = code
            request.session["reset_email"] = email
            request.session.set_expiry(180)  # expires in 3 mins
            send_verification_email(email, code)

            # ✅ SUCCESS MESSAGE ADDED HERE
            messages.success(
                request, "📨 A verification code has been sent to your email."
            )
            return redirect("users:verify_reset_code")
        except User.DoesNotExist:
            messages.error(request, "No account with this email was found.")

    return render(request, "users/forgot_password.html")


# Code verification for reset
def verify_reset_code_view(request):
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


# Resend reset code
def resend_reset_code_view(request):
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


# Final password reset
def reset_password_view(request):
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
                # Clear reset session
                for key in ["reset_code", "reset_email", "code_verified"]:
                    if key in request.session:
                        del request.session[key]
                messages.success(request, "Password reset successful!")
                return redirect("users:login")
            except User.DoesNotExist:
                messages.error(request, "Error resetting password. Please try again.")

    return render(request, "users/reset_password.html")


@api_view(["POST"])
def logout(request):
    try:
        res = Response()
        res.data = {"success": True}
        res.delete_cookie("access_token", path="/", samesite="None")
        res.delete_cookie("refresh_token", path="/", samesite="None")
        return res
    except:
        return Response({"success": False}, status=400)


def logout_view(request):
    logout(request)
    request.session.flush()
    return redirect("store:product_list")


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def is_authenticated(request):
    return Response({"authenticated": True})


@login_required
def dashboard_view(request):
    return render(request, "users/dashboard.html", {"user": request.user})


@login_required
def user_management(request):
    if request.user.role != "admin":
        return HttpResponseForbidden("You do not have permission to view this page.")

    users = User.objects.all()

    if request.method == "POST":
        user_id = request.POST.get("user_id")
        role = request.POST.get("role")
        username = request.POST.get("username")
        email = request.POST.get("email")

        valid_roles = ["admin", "manager", "staff", "supplier", "delivery"]

        if role not in valid_roles:
            messages.error(request, "Invalid role selected.")
            return redirect("users:user_management")

        if user_id:  # Update existing user
            try:
                user = User.objects.get(id=user_id)
                user.username = username
                user.email = email
                user.role = role
                user.save()
                messages.success(request, f"User '{username}' updated successfully.")
                return redirect("users:user_management")
            except User.DoesNotExist:
                messages.error(request, "User not found.")
                return redirect("users:user_management")

        else:  # Add new user
            if User.objects.filter(username=username).exists():
                messages.error(request, "Username already exists.")
                return redirect("users:user_management")
            user = User.objects.create_user(username=username, email=email, role=role)
            # Optionally set a default password or require password input
            user.set_password(
                "defaultpassword123"
            )  # Change or generate password securely
            user.save()
            messages.success(request, f"User '{username}' added successfully.")
            return redirect("users:user_management")

    context = {
        "users": users,
        "admins": User.objects.filter(role="admin"),
        "managers": User.objects.filter(role="manager"),
        "staffs": User.objects.filter(role="staff"),
        "customers": User.objects.filter(role="customer"),
        "suppliers": User.objects.filter(role="supplier"),
        "deliverys": User.objects.filter(role="delivery"),
    }
    return render(request, "users/user_management.html", context)


@csrf_exempt
@login_required
def delete_users(request):
    # Only admin can delete users
    if request.user.role != "admin":
        return HttpResponseForbidden(
            "You do not have permission to delete users."
        )  # 403 Forbidden if not admin

    if request.method == "POST":
        try:
            # Parse JSON data from the request body
            data = json.loads(request.body)
            user_ids_to_delete = data.get("ids", [])

            if not user_ids_to_delete:
                return JsonResponse(
                    {"success": False, "error": "No user IDs provided to delete."}
                )

            # Delete users with the provided user_ids
            deleted_count, _ = User.objects.filter(id__in=user_ids_to_delete).delete()

            if deleted_count > 0:
                messages.success(
                    request, f"{deleted_count} user(s) deleted successfully."
                )
                return JsonResponse({"success": True})
            else:
                return JsonResponse(
                    {"success": False, "error": "No users found to delete."}
                )

        except Exception as e:
            return JsonResponse(
                {"success": False, "error": f"Error occurred: {str(e)}"}
            )

    return JsonResponse({"success": False, "error": "Invalid request method"})


from django.contrib.auth.decorators import login_required
from apps.users.models import CustomerProfile


@login_required
def customer_profile_edit(request):
    """
    Allow customers to view and edit their profile information,
    especially their address details needed for checkout.
    """
    try:
        profile = request.user.customer_profile
    except CustomerProfile.DoesNotExist:
        # Create profile if it doesn't exist
        profile = CustomerProfile.objects.create(user=request.user)
        messages.info(
            request, "Profile created! Please complete your address information."
        )

    if request.method == "POST":
        # Update profile fields
        profile.phone = request.POST.get("phone", "")
        profile.street_address = request.POST.get("street_address", "")
        profile.city = request.POST.get("city", "")
        profile.province = request.POST.get("province", "")
        profile.zip_code = request.POST.get("zip_code", "")
        profile.save()

        messages.success(request, "Profile updated successfully!")

        # If user came from checkout, redirect back
        next_url = request.GET.get("next", "store:product_list")
        return redirect(next_url)

    context = {
        "profile": profile,
        "user": request.user,
    }
    return render(request, "users/profile_edit.html", context)
