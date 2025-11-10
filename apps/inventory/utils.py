# import pandas as pd
# from apps.orders.models import Order
# from apps.inventory.models import *

# from statsmodels.tsa.holtwinters import SimpleExpSmoothing
# import matplotlib.pyplot as plt
# import io
# import base64


# def get_sales_data(product_code):
#     orders_qs = Order.objects.filter(
#         product__product_code=product_code, status="Completed", is_deleted=False
#     ).values("order_date", "quantity")

#     df = pd.DataFrame(list(orders_qs))
#     if df.empty:
#         return None

#     df["order_date"] = pd.to_datetime(df["order_date"]).dt.date
#     df = df.groupby("order_date")["quantity"].sum().reset_index()
#     df = df.set_index("order_date").asfreq("D").fillna(0)
#     return df


# def forecast_sales(sales_df, forecast_days=30):
#     model = SimpleExpSmoothing(sales_df["quantity"]).fit()
#     forecast = model.forecast(forecast_days)
#     return forecast


# def plot_forecast(sales_df, forecast):
#     plt.figure(figsize=(10, 5))
#     plt.plot(sales_df.index, sales_df["quantity"], label="Historical Sales")
#     plt.plot(forecast.index, forecast, label="Forecast", linestyle="--")
#     plt.title("Demand Forecast")
#     plt.xlabel("Date")
#     plt.ylabel("Quantity Sold")
#     plt.legend()

#     # Save plot to PNG in-memory string
#     buf = io.BytesIO()
#     plt.savefig(buf, format="png")
#     plt.close()
#     buf.seek(0)
#     img_base64 = base64.b64encode(buf.read()).decode("utf-8")
#     return img_base64
