from django import forms
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm
from django.contrib.auth import get_user_model
from .models import CustomerProfile, SupplierProfile

User = get_user_model()


class CustomerProfileUpdateForm(forms.ModelForm):
    """
    Form for customers to update their profile information
    Includes name fields from User model and address fields from CustomerProfile
    """

    # User model fields
    first_name = forms.CharField(
        max_length=150,
        required=True,
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "First Name"}
        ),
    )
    last_name = forms.CharField(
        max_length=150,
        required=True,
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "Last Name"}
        ),
    )
    email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(
            attrs={
                "class": "form-control",
                "placeholder": "Email Address",
                "readonly": "readonly",  # Email can't be changed here
            }
        ),
    )


    # CustomerProfile fields
    phone = forms.CharField(
        max_length=15,
        required=False,
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "+63 912 345 6789"}
        ),
    )
    street_address = forms.CharField(
        max_length=255,
        required=True,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "House/Building No., Street Name",
            }
        ),
    )
    city = forms.CharField(
        max_length=100,
        required=True,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "City"}),
    )
    province = forms.CharField(
        max_length=100,
        required=True,
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "Province"}
        ),
    )
    zip_code = forms.CharField(
        max_length=20,
        required=True,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "4027"}),
    )

    class Meta:
        model = CustomerProfile
        fields = ["phone", "street_address", "city", "province", "zip_code"]

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)

        # Pre-populate User fields if user exists
        if self.user:
            self.fields["first_name"].initial = self.user.first_name
            self.fields["last_name"].initial = self.user.last_name
            self.fields["email"].initial = self.user.email

    def save(self, commit=True):
        """Save both User and CustomerProfile fields"""
        profile = super().save(commit=False)

        # Update User fields
        if self.user:
            self.user.first_name = self.cleaned_data.get("first_name")
            self.user.last_name = self.cleaned_data.get("last_name")
            # Email is readonly, so we don't update it
            if commit:
                self.user.save()

        if commit:
            profile.save()

        return profile


# --------------------
# CUSTOMER REGISTRATION
# --------------------
class CustomerRegistrationForm(UserCreationForm):
    """Customer registration with profile fields and unique email validation"""

    first_name = forms.CharField(
        max_length=150,
        required=True,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "First Name"}),
    )
    last_name = forms.CharField(
        max_length=150,
        required=True,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Last Name"}),
    )
    phone = forms.CharField(
        max_length=15,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "+63 912 345 6789"}),
    )
    street_address = forms.CharField(
        max_length=255,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "House/Building No., Street Name"}),
    )
    city = forms.CharField(
        max_length=100,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "City"}),
    )
    province = forms.CharField(
        max_length=100,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Province"}),
    )
    zip_code = forms.CharField(
        max_length=20,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "4027"}),
    )

    class Meta:
        model = User
        fields = (
            "first_name",
            "last_name",
            "username",
            "email",
            "phone",
            "street_address",
            "city",
            "province",
            "zip_code",
            "password1",
            "password2",
        )
        widgets = {
            "username": forms.TextInput(attrs={"class": "form-control", "placeholder": "Choose a unique username"}),
            "email": forms.EmailInput(attrs={"class": "form-control", "placeholder": "your.email@example.com"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Password field styling
        self.fields["password1"].widget.attrs.update({"class": "form-control", "placeholder": "Enter a strong password"})
        self.fields["password2"].widget.attrs.update({"class": "form-control", "placeholder": "Re-enter your password"})

    # ✅ Ensure email is unique
    def clean_email(self):
        email = self.cleaned_data.get("email")
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError("This email is already registered. Please use a different email.")
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.role = "customer"
        user.is_active = False  # Require email verification
        user.first_name = self.cleaned_data.get("first_name")
        user.last_name = self.cleaned_data.get("last_name")

        if commit:
            user.save()
            CustomerProfile.objects.update_or_create(
                user=user,
                defaults={
                    "phone": self.cleaned_data.get("phone", ""),
                    "street_address": self.cleaned_data.get("street_address", ""),
                    "city": self.cleaned_data.get("city", ""),
                    "province": self.cleaned_data.get("province", ""),
                    "zip_code": self.cleaned_data.get("zip_code", ""),
                },
            )
        return user

# --------------------
# SUPPLIER REGISTRATION
# --------------------
class SupplierRegistrationForm(UserCreationForm):
    """Supplier registration with profile fields and unique email validation"""

    phone = forms.CharField(
        max_length=15,
        required=True,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "+63 912 345 6789"}),
    )
    street_address = forms.CharField(
        max_length=255,
        required=True,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Business Address"}),
    )
    city = forms.CharField(
        max_length=100,
        required=True,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "City"}),
    )
    province = forms.CharField(
        max_length=100,
        required=True,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Province"}),
    )
    zip_code = forms.CharField(
        max_length=20,
        required=True,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "4027"}),
    )
    company_name = forms.CharField(
        max_length=100,
        required=True,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Your Company Name"}),
    )
    business_registration = forms.CharField(
        max_length=50,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "DTI/SEC Registration Number (Optional)"}),
    )

    class Meta:
        model = User
        fields = (
            "username",
            "email",
            "phone",
            "street_address",
            "city",
            "province",
            "zip_code",
            "company_name",
            "business_registration",
            "password1",
            "password2",
        )
        widgets = {
            "username": forms.TextInput(attrs={"class": "form-control", "placeholder": "Choose a username"}),
            "email": forms.EmailInput(attrs={"class": "form-control", "placeholder": "business@example.com"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["password1"].widget.attrs.update({"class": "form-control", "placeholder": "Enter a strong password"})
        self.fields["password2"].widget.attrs.update({"class": "form-control", "placeholder": "Re-enter your password"})

    # ✅ Ensure email is unique
    def clean_email(self):
        email = self.cleaned_data.get("email")
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError("This email is already registered. Please use a different email.")
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.role = "supplier"
        user.is_approved = False
        user.is_active = False  # Require email verification

        if commit:
            user.save()
            SupplierProfile.objects.update_or_create(
                user=user,
                defaults={
                    "phone": self.cleaned_data.get("phone", ""),
                    "address": ", ".join([
                        self.cleaned_data.get("street_address", ""),
                        self.cleaned_data.get("city", ""),
                        self.cleaned_data.get("province", ""),
                        self.cleaned_data.get("zip_code", ""),
                    ]),
                    "company_name": self.cleaned_data.get("company_name", ""),
                    "business_registration": self.cleaned_data.get("business_registration", ""),
                },
            )
        return user

# --------------------
# ADMIN/STAFF CREATION
# --------------------
class AdminUserCreationForm(UserCreationForm):
    """Admin/Staff creation with role selection and unique email validation"""

    role = forms.ChoiceField(
        choices=[
            ("admin", "Admin"),
            ("manager", "Manager"),
            ("staff", "Staff"),
            ("delivery", "Delivery Confirmation"),
            ("customer", "Customer"),
            ("supplier", "Supplier"),
        ],
        widget=forms.Select(attrs={"class": "form-control"}),
    )

    class Meta:
        model = User
        fields = ("username", "email", "role", "password1", "password2")
        widgets = {
            "username": forms.TextInput(attrs={"class": "form-control"}),
            "email": forms.EmailInput(attrs={"class": "form-control"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["password1"].widget.attrs.update({"class": "form-control"})
        self.fields["password2"].widget.attrs.update({"class": "form-control"})

    # ✅ Ensure email is unique
    def clean_email(self):
        email = self.cleaned_data.get("email")
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError("This email is already registered. Please use a different email.")
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.role = self.cleaned_data.get("role")
        user.is_active = True  # Admin-created users are automatically active
        if commit:
            user.save()
        return user

# --------------------
# LOGIN FORM
# --------------------
class CustomAuthenticationForm(AuthenticationForm):
    username = forms.CharField(
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "Username or Email"}
        )
    )
    password = forms.CharField(
        widget=forms.PasswordInput(
            attrs={"class": "form-control", "placeholder": "Password"}
        )
    )
