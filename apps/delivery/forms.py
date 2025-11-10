# my_delivery_app/forms.py
from django import forms
from .models import Delivery


class ProofOfDeliveryForm(forms.ModelForm):
    """
    Form used by delivery personnel to confirm delivery and upload the proof image.
    """

    # A cleaner field for the actual status update
    new_status = forms.ChoiceField(
        choices=[
            (Delivery.DELIVERED, "Delivered"),
            (Delivery.FAILED, "Failed"),
        ],
        label="Final Delivery Status",
    )

    class Meta:
        model = Delivery
        fields = ["new_status", "proof_of_delivery_image", "delivery_note"]

    def clean(self):
        cleaned = super().clean()
        new_status = cleaned.get("new_status")
        proof = cleaned.get("proof_of_delivery_image") or (
            self.instance and getattr(self.instance, "proof_of_delivery_image", None)
        )
        # If marking as delivered, require proof image (either newly uploaded or already on the instance)
        if new_status == Delivery.DELIVERED and not proof:
            raise forms.ValidationError(
                "Photo proof is required to mark the delivery as Delivered."
            )
        return cleaned

    def save(self, commit=True):
        # Update the delivery_status on the model instance from the form's new_status field
        self.instance.delivery_status = self.cleaned_data.get("new_status")
        # Parent save handles file upload for proof_of_delivery_image and delivery_note
        return super().save(commit)
