from django import forms
from django.forms import inlineformset_factory
from .models import Order, ManualOrder, ManualOrderItem
from apps.store.models import ProductVariant
from django.core.exceptions import ValidationError


class OrderForm(forms.ModelForm):
    """
    Form for admin/staff to manage orders. (Unchanged)
    Note: Addresses are NOT editable here - they come from CustomerProfile.
    """

    class Meta:
        model = Order
        fields = ["customer", "payment_method", "expected_delivery_date", "status"]
        widgets = {
            "expected_delivery_date": forms.DateInput(attrs={"type": "date"}),
            "status": forms.Select(choices=Order.ORDER_STATUS_CHOICES),
        }

    def clean_customer(self):
        """Ensure customer has a profile with address information."""
        customer = self.cleaned_data.get("customer")
        if customer:
            try:
                profile = customer.customer_profile
                if not profile.street_address or not profile.city:
                    raise ValidationError(
                        f"Customer {customer.username} does not have a complete address in their profile. "
                        "Please update their CustomerProfile before creating an order."
                    )
            except Exception:
                raise ValidationError(
                    f"Customer {customer.username} does not have a CustomerProfile. "
                    "Please create one before placing orders."
                )
        return customer


class CheckoutForm(forms.Form):
    """
    REFACTORED: Checkout form NO LONGER collects address information.
    All addresses come from the logged-in user's CustomerProfile.

    This form now only collects:
    - Payment method
    - Optional delivery date preferences

    Address display/review happens in the template using profile data.
    """

    payment_method = forms.ChoiceField(
        choices=Order.PAYMENT_METHODS,
        widget=forms.RadioSelect(attrs={"class": "form-check-input"}),
        initial="COD",
        required=True,
        help_text="Choose your payment method.",
    )

    gcash_reference_image = forms.ImageField(
        required=False,
        label="GCash Payment Proof (Required for GCash)",
        help_text="Upload a screenshot of your successful GCash transaction.",
    )

    def clean(self):
        """
        Validate that user has complete address in their profile.
        This validation happens at form level, not field level.
        """
        cleaned_data = super().clean()
        # Additional validation will be done in the view to check CustomerProfile
        payment_method = cleaned_data.get("payment_method")
        gcash_image = cleaned_data.get("gcash_reference_image")

        if payment_method == "GCASH" and not gcash_image:
            self.add_error(
                "gcash_reference_image",
                "Proof of payment is required for GCash transfers.",
            )

        # Note: No need to worry about expected_delivery_date in the clean method now.
        return cleaned_data

    # REMOVED METHODS:
    # - get_full_address() - No longer needed, addresses come from profile
    # All address-related fields have been removed


class CheckoutForm(forms.Form):
    """
    Checkout form for customers. Collects payment method and GCash proof.
    """

    payment_method = forms.ChoiceField(
        choices=Order.PAYMENT_METHODS,
        widget=forms.RadioSelect(attrs={"class": "form-check-input"}),
        initial="COD",
        required=True,
        help_text="Choose your payment method.",
    )

    # Added ImageField for GCash proof
    gcash_reference_image = forms.ImageField(
        required=False,
        label="GCash Payment Proof (Required for GCash)",
        help_text="Upload a screenshot of your successful GCash transaction.",
    )

    # Re-added Date field if it's expected to be saved to the Order model
    expected_delivery_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}),
        help_text="Optional: Preferred delivery date.",
    )

    def clean(self):
        """
        Performs conditional validation to require image upload for GCash.
        """
        cleaned_data = super().clean()
        payment_method = cleaned_data.get("payment_method")
        gcash_image = cleaned_data.get("gcash_reference_image")

        # CRITICAL: Require image if payment method is GCASH
        if payment_method == "GCASH" and not gcash_image:
            self.add_error(
                "gcash_reference_image",
                "Proof of payment is required for GCash transfers.",
            )

        return cleaned_data


class ManualOrderForm(forms.ModelForm):
    """
    Form for creating/editing manual orders (admin/staff).
    """

    class Meta:
        model = ManualOrder
        fields = [
            "customer",
            "customer_name",
            "customer_email",
            "customer_phone",
            "order_source",
            "payment_method",
            "shipping_address",
            "billing_address",
            "expected_delivery_date",
            "status",
            "notes",
            "gcash_reference_image",  # ADDED: Included in ModelForm fields
        ]
        widgets = {
            "customer": forms.Select(attrs={"class": "form-control"}),
            "customer_name": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Customer or company name",
                }
            ),
            "customer_email": forms.EmailInput(
                attrs={"class": "form-control", "placeholder": "customer@email.com"}
            ),
            "customer_phone": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "+63 XXX XXX XXXX"}
            ),
            "order_source": forms.Select(attrs={"class": "form-control"}),
            "payment_method": forms.Select(attrs={"class": "form-control"}),
            "shipping_address": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 3,
                    "placeholder": "Full shipping address",
                }
            ),
            "billing_address": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 3,
                    "placeholder": "Billing address (optional)",
                }
            ),
            "expected_delivery_date": forms.DateInput(
                attrs={"class": "form-control", "type": "date"}
            ),
            "status": forms.Select(attrs={"class": "form-control"}),
            "notes": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 3,
                    "placeholder": "Internal notes...",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Make these fields optional
        self.fields["customer"].required = False
        self.fields["customer_email"].required = False
        self.fields["customer_phone"].required = False
        self.fields["billing_address"].required = False
        self.fields["expected_delivery_date"].required = False
        self.fields["notes"].required = False

        # Set default for status
        if not self.instance.pk:
            self.fields["status"].initial = "Pending"

        self.fields["customer"].empty_label = "No linked account (B2B/Guest)"

    def clean(self):
        cleaned_data = super().clean()
        customer = cleaned_data.get("customer")
        customer_name = cleaned_data.get("customer_name")

        # Customer name is required
        if not customer and not customer_name:
            raise ValidationError(
                "Please select a customer account or provide a customer name."
            )

        # Auto-fill customer name from linked account if not provided
        if customer and not customer_name:
            cleaned_data["customer_name"] = (
                customer.get_full_name() or customer.username
            )

        # Auto-copy shipping to billing if billing is empty
        if not cleaned_data.get("billing_address"):
            cleaned_data["billing_address"] = cleaned_data.get("shipping_address", "")

        # ⚠️ Removed the dangerous logic that discarded gcash_reference_image if not 'GCASH'.
        # Admins can now upload a reference image regardless of payment method.

        return cleaned_data


class ManualOrderItemForm(forms.ModelForm):
    """Form for individual manual order items"""

    class Meta:
        model = ManualOrderItem
        fields = ["product_variant", "quantity", "price_at_order"]
        widgets = {
            "product_variant": forms.Select(
                attrs={"class": "form-control product-variant-select", "required": True}
            ),
            "quantity": forms.NumberInput(
                attrs={
                    "class": "form-control quantity-input",
                    "min": 1,
                    "value": 1,
                    "required": True,
                }
            ),
            "price_at_order": forms.NumberInput(
                attrs={
                    "class": "form-control price-input",
                    "step": "0.01",
                    "min": "0",
                    "placeholder": "Price per unit",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product_variant"].queryset = (
            ProductVariant.objects.filter(
                is_active=True, product__is_deleted=False, product__stock_quantity__gt=0
            )
            .select_related("product")
            .order_by("product__name")
        )
        self.fields["price_at_order"].required = False


# Formset for managing multiple order items
ManualOrderItemFormSet = inlineformset_factory(
    ManualOrder,
    ManualOrderItem,
    form=ManualOrderItemForm,
    extra=1,
    can_delete=True,
    min_num=1,
    validate_min=True,
)
