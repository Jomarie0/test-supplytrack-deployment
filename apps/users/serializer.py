from rest_framework import serializers
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from .models import User, CustomerProfile, SupplierProfile
from apps.transactions.models import Transaction


# ==================== PROFILE SERIALIZERS ====================


class CustomerProfileSerializer(serializers.ModelSerializer):
    """Serializer for customer profile data"""

    full_address = serializers.SerializerMethodField()

    class Meta:
        model = CustomerProfile
        fields = [
            "id",
            "phone",
            "street_address",
            "city",
            "province",
            "zip_code",
            "full_address",
        ]
        read_only_fields = ["id"]

    def get_full_address(self, obj):
        """Return formatted full address"""
        return obj.full_address()


class SupplierProfileSerializer(serializers.ModelSerializer):
    """Serializer for supplier profile data"""

    class Meta:
        model = SupplierProfile
        fields = ["id", "phone", "address", "company_name", "business_registration"]
        read_only_fields = ["id"]


# ==================== USER SERIALIZERS ====================


class UserSerializer(serializers.ModelSerializer):
    """Main user serializer for retrieving user data"""

    customer_profile = CustomerProfileSerializer(read_only=True)
    supplier_profile = SupplierProfileSerializer(read_only=True)
    role_display = serializers.CharField(source="get_role_display", read_only=True)

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "first_name",
            "last_name",
            "role",
            "role_display",
            "is_active",
            "is_approved",
            "date_requested",
            "date_joined",
            "last_login",
            "customer_profile",
            "supplier_profile",
        ]
        read_only_fields = [
            "id",
            "is_approved",
            "date_requested",
            "date_joined",
            "last_login",
        ]


class UserRegistrationSerializer(serializers.ModelSerializer):
    """
    Serializer for user registration (API-based)
    This is for general user creation via API, not form-based registration
    """

    password = serializers.CharField(
        write_only=True, required=True, style={"input_type": "password"}
    )
    password_confirm = serializers.CharField(
        write_only=True, required=True, style={"input_type": "password"}
    )

    # Profile fields (optional at registration)
    phone = serializers.CharField(max_length=15, required=False, allow_blank=True)
    company_name = serializers.CharField(
        max_length=100, required=False, allow_blank=True
    )
    business_registration = serializers.CharField(
        max_length=50, required=False, allow_blank=True
    )

    class Meta:
        model = User
        fields = [
            "username",
            "email",
            "password",
            "password_confirm",
            "first_name",
            "last_name",
            "role",
            "phone",
            "company_name",
            "business_registration",
        ]
        extra_kwargs = {
            "email": {"required": True},
            "first_name": {"required": False},
            "last_name": {"required": False},
        }

    def validate_email(self, value):
        """Ensure email is unique among active users"""
        if User.objects.filter(email=value, is_active=True).exists():
            raise serializers.ValidationError("A user with this email already exists.")
        return value.lower()

    def validate_username(self, value):
        """Ensure username is unique"""
        if User.objects.filter(username=value).exists():
            raise serializers.ValidationError("This username is already taken.")
        return value

    def validate(self, attrs):
        """Validate password match and strength"""
        password = attrs.get("password")
        password_confirm = attrs.pop("password_confirm", None)

        # Check password match
        if password != password_confirm:
            raise serializers.ValidationError({"password": "Passwords do not match."})

        # Validate password strength
        try:
            validate_password(password)
        except ValidationError as e:
            raise serializers.ValidationError({"password": list(e.messages)})

        # Role-specific validation
        role = attrs.get("role", "customer")
        if role == "supplier":
            if not attrs.get("company_name"):
                raise serializers.ValidationError(
                    {"company_name": "Company name is required for suppliers."}
                )

        return attrs

    def create(self, validated_data):
        """Create user with appropriate profile based on role"""
        # Extract profile-specific fields
        phone = validated_data.pop("phone", "")
        company_name = validated_data.pop("company_name", "")
        business_registration = validated_data.pop("business_registration", "")

        # Extract and hash password
        password = validated_data.pop("password")
        role = validated_data.get("role", "customer")

        # Create user (inactive until email verification)
        user = User(**validated_data)
        user.set_password(password)
        user.is_active = False  # Require email verification

        # Set approval status based on role
        if role == "supplier":
            user.is_approved = False  # Suppliers need admin approval
        else:
            user.is_approved = True  # Other roles are auto-approved

        user.save()

        # Create appropriate profile
        if role == "customer":
            CustomerProfile.objects.create(user=user, phone=phone)
        elif role == "supplier":
            SupplierProfile.objects.create(
                user=user,
                phone=phone,
                company_name=company_name,
                business_registration=business_registration,
            )

        # Create transaction log
        Transaction.objects.create(
            user=user,
            transaction_type="user_registration",
            description=f"New user '{user.username}' registered with role '{role}' via API.",
        )

        return user


class CustomerRegistrationSerializer(serializers.ModelSerializer):
    """Serializer specifically for customer registration via API"""

    password = serializers.CharField(write_only=True, required=True)
    password_confirm = serializers.CharField(write_only=True, required=True)

    # Customer profile fields
    phone = serializers.CharField(max_length=15, required=False, allow_blank=True)
    street_address = serializers.CharField(
        max_length=255, required=False, allow_blank=True
    )
    city = serializers.CharField(max_length=100, required=False, allow_blank=True)
    province = serializers.CharField(max_length=100, required=False, allow_blank=True)
    zip_code = serializers.CharField(max_length=20, required=False, allow_blank=True)

    class Meta:
        model = User
        fields = [
            "username",
            "email",
            "password",
            "password_confirm",
            "first_name",
            "last_name",
            "phone",
            "street_address",
            "city",
            "province",
            "zip_code",
        ]

    def validate(self, attrs):
        """Validate password match"""
        if attrs.get("password") != attrs.pop("password_confirm", None):
            raise serializers.ValidationError({"password": "Passwords do not match."})

        try:
            validate_password(attrs.get("password"))
        except ValidationError as e:
            raise serializers.ValidationError({"password": list(e.messages)})

        return attrs

    def create(self, validated_data):
        """Create customer user with profile"""
        # Extract profile fields
        profile_fields = {
            "phone": validated_data.pop("phone", ""),
            "street_address": validated_data.pop("street_address", ""),
            "city": validated_data.pop("city", ""),
            "province": validated_data.pop("province", ""),
            "zip_code": validated_data.pop("zip_code", ""),
        }

        # Create user
        password = validated_data.pop("password")
        user = User(**validated_data)
        user.role = "customer"
        user.is_active = False  # Require email verification
        user.is_approved = True
        user.set_password(password)
        user.save()

        # Create customer profile
        CustomerProfile.objects.create(user=user, **profile_fields)

        # Create transaction log
        Transaction.objects.create(
            user=user,
            transaction_type="user_registration",
            description=f"New customer '{user.username}' registered via API.",
        )

        return user


class SupplierRegistrationSerializer(serializers.ModelSerializer):
    """Serializer specifically for supplier registration via API"""

    password = serializers.CharField(write_only=True, required=True)
    password_confirm = serializers.CharField(write_only=True, required=True)

    # Supplier profile fields
    phone = serializers.CharField(max_length=15, required=True)
    address = serializers.CharField(required=False, allow_blank=True)
    company_name = serializers.CharField(max_length=100, required=True)
    business_registration = serializers.CharField(
        max_length=50, required=False, allow_blank=True
    )

    class Meta:
        model = User
        fields = [
            "username",
            "email",
            "password",
            "password_confirm",
            "first_name",
            "last_name",
            "phone",
            "address",
            "company_name",
            "business_registration",
        ]

    def validate(self, attrs):
        """Validate password match"""
        if attrs.get("password") != attrs.pop("password_confirm", None):
            raise serializers.ValidationError({"password": "Passwords do not match."})

        try:
            validate_password(attrs.get("password"))
        except ValidationError as e:
            raise serializers.ValidationError({"password": list(e.messages)})

        return attrs

    def create(self, validated_data):
        """Create supplier user with profile"""
        # Extract profile fields
        profile_fields = {
            "phone": validated_data.pop("phone", ""),
            "address": validated_data.pop("address", ""),
            "company_name": validated_data.pop("company_name", ""),
            "business_registration": validated_data.pop("business_registration", ""),
        }

        # Create user
        password = validated_data.pop("password")
        user = User(**validated_data)
        user.role = "supplier"
        user.is_active = False  # Require email verification
        user.is_approved = False  # Require admin approval
        user.set_password(password)
        user.save()

        # Create supplier profile
        SupplierProfile.objects.create(user=user, **profile_fields)

        # Create transaction log
        Transaction.objects.create(
            user=user,
            transaction_type="user_registration",
            description=f"New supplier '{user.username}' from '{profile_fields['company_name']}' registered via API (pending approval).",
        )

        return user


class UserUpdateSerializer(serializers.ModelSerializer):
    """Serializer for updating user information"""

    class Meta:
        model = User
        fields = ["first_name", "last_name", "email"]

    def validate_email(self, value):
        """Ensure email is unique (excluding current user)"""
        user = self.instance
        if User.objects.filter(email=value).exclude(id=user.id).exists():
            raise serializers.ValidationError("This email is already in use.")
        return value.lower()

    def update(self, instance, validated_data):
        """Update user and log transaction"""
        user = super().update(instance, validated_data)

        # Create transaction log
        Transaction.objects.create(
            user=user,
            transaction_type="profile_update",
            description=f"User '{user.username}' updated their information.",
        )

        return user


class PasswordChangeSerializer(serializers.Serializer):
    """Serializer for changing password while logged in"""

    old_password = serializers.CharField(required=True, write_only=True)
    new_password = serializers.CharField(required=True, write_only=True)
    new_password_confirm = serializers.CharField(required=True, write_only=True)

    def validate_old_password(self, value):
        """Check if old password is correct"""
        user = self.context["request"].user
        if not user.check_password(value):
            raise serializers.ValidationError("Old password is incorrect.")
        return value

    def validate(self, attrs):
        """Validate new password match and strength"""
        if attrs["new_password"] != attrs["new_password_confirm"]:
            raise serializers.ValidationError(
                {"new_password": "New passwords do not match."}
            )

        try:
            validate_password(attrs["new_password"])
        except ValidationError as e:
            raise serializers.ValidationError({"new_password": list(e.messages)})

        return attrs

    def save(self):
        """Change password and log transaction"""
        user = self.context["request"].user
        user.set_password(self.validated_data["new_password"])
        user.save()

        # Create transaction log
        Transaction.objects.create(
            user=user,
            transaction_type="password_change",
            description=f"User '{user.username}' changed their password.",
        )

        return user


class CustomerProfileUpdateSerializer(serializers.ModelSerializer):
    """Serializer for updating customer profile via API"""

    class Meta:
        model = CustomerProfile
        fields = ["phone", "street_address", "city", "province", "zip_code"]

    def update(self, instance, validated_data):
        """Update profile and log transaction"""
        profile = super().update(instance, validated_data)

        # Create transaction log
        Transaction.objects.create(
            user=profile.user,
            transaction_type="profile_update",
            description=f"Customer '{profile.user.username}' updated their profile.",
        )

        return profile


class SupplierProfileUpdateSerializer(serializers.ModelSerializer):
    """Serializer for updating supplier profile via API"""

    class Meta:
        model = SupplierProfile
        fields = ["phone", "address", "company_name", "business_registration"]

    def update(self, instance, validated_data):
        """Update profile and log transaction"""
        profile = super().update(instance, validated_data)

        # Create transaction log
        Transaction.objects.create(
            user=profile.user,
            transaction_type="profile_update",
            description=f"Supplier '{profile.user.username}' updated their profile.",
        )

        return profile


class SupplierApprovalSerializer(serializers.ModelSerializer):
    """Serializer for admin to approve/reject suppliers"""

    class Meta:
        model = User
        fields = ["is_approved"]

    def validate(self, attrs):
        """Ensure user is a supplier"""
        if self.instance.role != "supplier":
            raise serializers.ValidationError("Only suppliers can be approved.")
        return attrs

    def update(self, instance, validated_data):
        """
        Update approval status
        Note: Transaction log is created in User model's save() method
        """
        is_approved = validated_data.get("is_approved")
        instance.is_approved = is_approved
        instance.save()

        return instance
# Add this to your serializers.py file

from rest_framework import serializers
from .models import User
from apps.transactions.models import Transaction


class StaffCreationSerializer(serializers.ModelSerializer):
    """
    Serializer for admin to create staff accounts via API
    Only requires username, email, password, and role
    """
    password = serializers.CharField(
        write_only=True, 
        required=True,
        min_length=8,
        style={"input_type": "password"}
    )
    
    class Meta:
        model = User
        fields = ["username", "email", "password", "role"]
        extra_kwargs = {
            "email": {"required": True},
        }
    
    def validate_role(self, value):
        """Only allow staff-related roles"""
        valid_roles = ["staff", "manager", "delivery"]
        if value not in valid_roles:
            raise serializers.ValidationError(
                f"Invalid role. Must be one of: {', '.join(valid_roles)}"
            )
        return value
    
    def validate_username(self, value):
        """Ensure username is unique"""
        if User.objects.filter(username=value).exists():
            raise serializers.ValidationError("This username is already taken.")
        return value
    
    def validate_email(self, value):
        """Ensure email is unique"""
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError("This email is already registered.")
        return value.lower()
    
    def create(self, validated_data):
        """
        Create staff user with minimal information
        Staff can complete their profile later
        """
        password = validated_data.pop("password")
        role = validated_data.get("role", "staff")
        
        # Create user
        user = User(**validated_data)
        user.set_password(password)
        user.is_active = True  # Staff accounts are immediately active
        user.is_approved = True  # Staff accounts are pre-approved
        user.save()
        
        # Get admin user from context if available
        request = self.context.get("request")
        admin_user = request.user if request and request.user.is_authenticated else None
        
        # Create transaction log
        description = f"New {role} account '{user.username}' created"
        if admin_user:
            description = f"Admin '{admin_user.username}' created {role} account for '{user.username}'"
        
        Transaction.objects.create(
            user=admin_user or user,
            transaction_type="user_creation",
            description=description
        )
        
        return user


class StaffProfileUpdateSerializer(serializers.ModelSerializer):
    """
    Serializer for staff to update their own profile information
    """
    class Meta:
        model = User
        fields = ["first_name", "last_name", "email"]
    
    def validate_email(self, value):
        """Ensure email is unique (excluding current user)"""
        user = self.instance
        if User.objects.filter(email=value).exclude(id=user.id).exists():
            raise serializers.ValidationError("This email is already in use.")
        return value.lower()
    
    def update(self, instance, validated_data):
        """Update user and log transaction"""
        user = super().update(instance, validated_data)
        
        # Create transaction log
        Transaction.objects.create(
            user=user,
            transaction_type="profile_update",
            description=f"{user.role.capitalize()} '{user.username}' updated their profile information."
        )
        
        return user


class StaffPasswordChangeSerializer(serializers.Serializer):
    """
    Serializer for staff to change their password
    Can be used for first-time password change or regular updates
    """
    new_password = serializers.CharField(
        required=True, 
        write_only=True,
        min_length=8,
        style={"input_type": "password"}
    )
    new_password_confirm = serializers.CharField(
        required=True, 
        write_only=True,
        min_length=8,
        style={"input_type": "password"}
    )
    
    def validate(self, attrs):
        """Validate new password match"""
        if attrs["new_password"] != attrs["new_password_confirm"]:
            raise serializers.ValidationError(
                {"new_password": "Passwords do not match."}
            )
        
        # Validate password strength
        from django.contrib.auth.password_validation import validate_password
        from django.core.exceptions import ValidationError
        
        try:
            validate_password(attrs["new_password"])
        except ValidationError as e:
            raise serializers.ValidationError({"new_password": list(e.messages)})
        
        return attrs
    
    def save(self):
        """Change password and log transaction"""
        user = self.context["request"].user
        user.set_password(self.validated_data["new_password"])
        user.save()
        
        # Create transaction log
        Transaction.objects.create(
            user=user,
            transaction_type="password_change",
            description=f"{user.role.capitalize()} '{user.username}' changed their password."
        )
        
        return user