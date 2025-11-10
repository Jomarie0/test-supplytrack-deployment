from django import forms
from django.forms.models import inlineformset_factory
from .models import Product, StockMovement
from apps.store.models import ProductVariant  # <-- Ensure this import is correct
from .models import Category
from apps.users.models import SupplierProfile

# ==============================================================================
# 1. PRIMARY PRODUCT FORM
# ==============================================================================
class ProductForm(forms.ModelForm):
    category = forms.CharField(
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control border",
                "placeholder": "Select or type a category...",
                "list": "categoryList",
            }
        ),
    )

    supplier_profile = forms.CharField(
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control border",
                "placeholder": "Select or type a supplier...",
                "list": "supplierList",
            }
        ),
    )

    class Meta:
        model = Product
        fields = [
            "name",
            "description",
            "category",
            "supplier_profile",
            "price",
            "cost_price",
            "stock_quantity",
            "unit",
            "image",
        ]

        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control border"}),
            "description": forms.Textarea(attrs={"class": "form-control border", "rows": 3}),
            "price": forms.NumberInput(attrs={"class": "form-control border", "step": "0.01"}),
            "cost_price": forms.NumberInput(attrs={"class": "form-control border", "step": "0.01"}),
            "stock_quantity": forms.NumberInput(attrs={"class": "form-control border"}),
            "unit": forms.TextInput(attrs={"class": "form-control border", "placeholder": "e.g., ml, pcs"}),
            "image": forms.FileInput(attrs={"class": "form-control border"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Pre-fill category with "Parent → Child"
        if self.instance and self.instance.pk and self.instance.category:
            self.initial["category"] = self.instance.category.get_full_path()

        # ✅ Pre-fill supplier with readable label
        if self.instance and self.instance.pk and self.instance.supplier_profile:
            supplier = self.instance.supplier_profile
            self.initial["supplier_profile"] = f"Supplier: {supplier.company_name} ({supplier.user.username})"

    def clean_category(self):
        raw_value = self.cleaned_data.get("category", "").strip()
        if not raw_value:
            return None

        parts = [p.strip() for p in raw_value.split("→") if p.strip()]
        parent = None
        for name in parts:
            category, _ = Category.objects.get_or_create(name=name, defaults={"parent": parent})
            if category.parent != parent:
                category.parent = parent
                category.save(update_fields=["parent"])
            parent = category
        return category

    def clean_supplier_profile(self):
        raw_value = self.cleaned_data.get("supplier_profile", "").strip()
        if not raw_value:
            return None

        # Expected format: "Supplier: Company (username)"
        import re
        match = re.search(r"\(([^)]+)\)$", raw_value)
        if match:
            username = match.group(1)
            try:
                return SupplierProfile.objects.get(user__username=username)
            except SupplierProfile.DoesNotExist:
                raise forms.ValidationError(f"No supplier found with username '{username}'.")
        else:
            # Optional: allow direct company name search
            supplier = SupplierProfile.objects.filter(company_name__iexact=raw_value).first()
            if supplier:
                return supplier
            raise forms.ValidationError("Supplier not recognized. Please select from the list.")



# ==============================================================================
# 2. PRODUCT VARIANT FORM & FORMSET (THE FIX)
# ==============================================================================


class ProductVariantForm(forms.ModelForm):
    """
    Form for managing individual ProductVariant instances.
    """

    class Meta:
        model = ProductVariant
        fields = ["sku", "size", "color", "price", "is_active"]
        widgets = {
            # Hide the product foreign key, it's handled by the Formset
            "product": forms.HiddenInput(),
            "size": forms.TextInput(attrs={"placeholder": "e.g., Small"}),
            "color": forms.TextInput(attrs={"placeholder": "e.g., Red"}),
            "price": forms.NumberInput(attrs={"step": "0.01"}),
        }


# Define the Formset using inlineformset_factory
ProductVariantFormset = inlineformset_factory(
    parent_model=Product,  # The parent model (Product)
    model=ProductVariant,  # The child model (Variant)
    form=ProductVariantForm,  # The form to use for each variant
    extra=0,  # Start with 1 empty variant form
    can_delete=True,  # Allow existing variants to be deleted
    # The minimum number of forms to display (optional)
    min_num=1,
)

# ==============================================================================
# 3. STOCK MOVEMENT FORM (Kept)
# ==============================================================================


class StockMovementForm(forms.ModelForm):
    class Meta:
        model = StockMovement
        fields = ["product", "movement_type", "quantity"]


# ==============================================================================
# 3. Panggawa ng Category FORM (Kept)
# ==============================================================================


class CategoryForm(forms.ModelForm):
    # Field to select the parent category for the new category
    # Only show active categories that could be parents
    parent = forms.ModelChoiceField(
        queryset=Category.objects.active().order_by("name"),  # Use the custom manager
        label="Parent Category (Optional)",
        empty_label="--- Select Root Category ---",
        required=False,
        widget=forms.Select(attrs={"class": "form-control category-form-control"}),
    )

    class Meta:
        model = Category
        # Added 'parent' field
        fields = ["name", "parent", "description"]
        widgets = {
            # Use 'category-form-control' for fields within the AJAX form
            "name": forms.TextInput(
                attrs={
                    "class": "form-control category-form-control",
                    "placeholder": "e.g., Canned Goods",
                }
            ),
            "description": forms.Textarea(
                attrs={"class": "form-control category-form-control", "rows": 3}
            ),
        }
        labels = {
            "name": "Category Name",
            "description": "Description",
        }
