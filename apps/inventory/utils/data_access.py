import pandas as pd
from django.db.models import Sum
from apps.orders.models import Order


def get_sales_timeseries(product_id, freq="D"):
    orders = (
        Order.objects.filter(
            product_id=product_id, is_deleted=False, status="Completed"
        )
        .values("order_date")
        .annotate(quantity_sold=Sum("quantity"))
        .order_by("order_date")
    )

    df = pd.DataFrame.from_records(orders)
    if df.empty:
        return None

    df["order_date"] = pd.to_datetime(df["order_date"])
    df.set_index("order_date", inplace=True)
    ts = df["quantity_sold"].resample(freq).sum().fillna(0)
    return ts
