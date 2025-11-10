# apps/purchasing/forms.py

from django import forms
from django.forms import inlineformset_factory
from apps.users.models import SupplierProfile
from .models import PurchaseOrder, PurchaseOrderItem
from apps.inventory.models import Product


class PurchaseOrderForm(forms.ModelForm):
    supplier_profile = forms.ModelChoiceField(
        queryset=SupplierProfile.objects.all(),
        empty_label="Select a supplier",
        widget=forms.Select(attrs={"class": "form-control"}),
    )

    class Meta:
        model = PurchaseOrder
        fields = [
            "supplier_profile",
            "status",
            "payment_method",
            "payment_due_date",
            "notes",
        ]
        widgets = {
            "status": forms.Select(
                choices=PurchaseOrder.PO_STATUS_CHOICES, attrs={"class": "form-control"}
            ),
            "payment_method": forms.Select(
                choices=PurchaseOrder.PAYMENT_CHOICES, attrs={"class": "form-control"}
            ),
            "payment_due_date": forms.DateInput(
                attrs={"type": "date", "class": "form-control"},
                format="%Y-%m-%d"
            ),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Apply Bootstrap-style form-control to all fields
        for name, field in self.fields.items():
            if "class" not in field.widget.attrs:
                field.widget.attrs.update({"class": "form-control"})


class PurchaseOrderItemForm(forms.ModelForm):
    product = forms.ModelChoiceField(
        queryset=Product.objects.all(),
        required=False,
        empty_label="Select a product (optional)",
        widget=forms.Select(attrs={"class": "form-control form-control-sm"}),
    )

    class Meta:
        model = PurchaseOrderItem
        fields = [
            "product",
            "product_name_text",
            "description",
            "quantity_ordered",
            "unit_cost",
        ]
        widgets = {
            "product_name_text": forms.TextInput(
                attrs={
                    "class": "form-control form-control-sm",
                    "placeholder": "e.g., Custom widget",
                }
            ),
            "description": forms.Textarea(
                attrs={
                    "class": "form-control form-control-sm",
                    "rows": 2,
                    "placeholder": "Optional description",
                }
            ),
            "quantity_ordered": forms.NumberInput(
                attrs={
                    "class": "form-control form-control-sm",
                    "min": "1",
                    "placeholder": "1",
                }
            ),
            "unit_cost": forms.NumberInput(
                attrs={
                    "class": "form-control form-control-sm",
                    "step": "0.01",
                    "min": "0",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Make fields not required by default for formset empty rows
        self.fields["product_name_text"].required = False
        self.fields["description"].required = False
        self.fields["unit_cost"].required = False

    def clean(self):
        cleaned_data = super().clean()

        product = cleaned_data.get("product")
        product_name_text = cleaned_data.get("product_name_text")
        quantity = cleaned_data.get("quantity_ordered")

        # Check if this row is marked for deletion
        if self.cleaned_data.get("DELETE"):
            return cleaned_data

        # If quantity is provided, we need an identifier (product OR custom name)
        if quantity is not None and quantity > 0:
            if not product and not product_name_text:
                raise forms.ValidationError(
                    "Please select a product OR enter a custom item name when specifying a quantity."
                )

        # If any data is entered (not just an empty extra row), validate completely
        has_any_data = any(
            [product, product_name_text, quantity, cleaned_data.get("description")]
        )

        if has_any_data and not quantity:
            raise forms.ValidationError("Quantity is required when adding an item.")

        return cleaned_data


# apps/purchasing/forms.py (Add this new form)

class POConfirmationForm(forms.ModelForm):
    payment_due_date = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}),
        required=False,  # initially optional, we'll validate it manually
    )
    payment_proof_image = forms.ImageField(
        required=False,
        widget=forms.FileInput(attrs={"class": "form-control", "accept": "image/*"}),
        help_text="Upload proof of payment (required for prepaid orders)"
    )

    class Meta:
        model = PurchaseOrder
        fields = [
            "payment_method",
            "payment_due_date",
            "payment_proof_image",
            "notes",
        ]
        widgets = {
            "payment_method": forms.Select(
                choices=PurchaseOrder.PAYMENT_CHOICES, attrs={"class": "form-control"}
            ),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Expected delivery date is set by supplier, not editable by staff
        
        # Make payment proof required for prepaid if no existing proof
        if self.instance:
            if self.instance.payment_method == 'prepaid' and not self.instance.payment_proof_image:
                self.fields['payment_proof_image'].required = True

        # Apply Bootstrap-style form-control to all fields
        for name, field in self.fields.items():
            if "class" not in field.widget.attrs:
                field.widget.attrs.update({"class": "form-control"})
    
    def clean(self):
        cleaned_data = super().clean()
        payment_method = cleaned_data.get("payment_method")
        payment_proof_image = cleaned_data.get("payment_proof_image")
        
        # If payment method is prepaid and no existing proof, require proof
        if payment_method == 'prepaid':
            if not payment_proof_image and not self.instance.payment_proof_image:
                raise forms.ValidationError(
                    "Payment proof is required for prepaid orders."
                )
        
        # Auto-set payment_due_date to 30 days ahead when net_30 is selected
        if payment_method == "net_30":
            from django.utils import timezone
            from datetime import timedelta
            payment_due_date = cleaned_data.get("payment_due_date")
            if not payment_due_date:
                # Set to 30 days from today
                cleaned_data["payment_due_date"] = timezone.now().date() + timedelta(days=30)
            # Note: If user manually entered a date, we keep it

        return cleaned_data


class PaymentProofUploadForm(forms.ModelForm):
    """Form for uploading payment proof for pay later/net_30 orders"""
    payment_proof_image = forms.ImageField(
        required=True,
        widget=forms.FileInput(attrs={"class": "form-control", "accept": "image/*"}),
        help_text="Upload proof of payment (receipt, screenshot, etc.)"
    )

    class Meta:
        model = PurchaseOrder
        fields = ["payment_proof_image"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if "class" not in field.widget.attrs:
                field.widget.attrs.update({"class": "form-control"})


# === Formset for multiple items ===
PurchaseOrderItemFormSet = inlineformset_factory(
    PurchaseOrder,
    PurchaseOrderItem,
    form=PurchaseOrderItemForm,
    extra=1,  # Show 3 empty rows by default
    can_delete=True,  # Allow removing items
    validate_min=False,  # Don't require at least one item (draft can be empty initially)
)
