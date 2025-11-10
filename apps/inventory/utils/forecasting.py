"""
Demand Forecasting Utilities - Linear Regression Method
Optimized for 8 years of historical order data
"""

import pandas as pd
import numpy as np
from django.db.models import Sum
from apps.orders.models import OrderItem, ManualOrderItem


# ----------------------------------------
# 1. ERROR METRICS
# ----------------------------------------

def calculate_forecast_metrics(actual, predicted):
    """
    Calculate forecasting accuracy metrics
    
    Args:
        actual: numpy array of actual values
        predicted: numpy array of predicted values
    
    Returns:
        dict with MAE, RMSE, MAPE, and accuracy percentage
    """
    actual = np.array(actual)
    predicted = np.array(predicted)
    
    # Remove any zero or negative values for MAPE calculation
    mask = actual > 0
    
    # Mean Absolute Error
    mae = np.mean(np.abs(actual - predicted))
    
    # Root Mean Squared Error
    rmse = np.sqrt(np.mean((actual - predicted) ** 2))
    
    # Mean Absolute Percentage Error (only where actual > 0)
    if mask.sum() > 0:
        mape = np.mean(np.abs((actual[mask] - predicted[mask]) / actual[mask])) * 100
    else:
        mape = None
    
    # Accuracy percentage (100% - MAPE)
    accuracy = (100 - mape) if mape is not None else None
    
    return {
        "mae": float(mae),
        "rmse": float(rmse),
        "mape": float(mape) if mape is not None else None,
        "accuracy_percent": float(accuracy) if accuracy is not None else None,
    }


def train_test_split_timeseries(df, test_size=0.2):
    """
    Split time series DataFrame into train and test sets
    
    Args:
        df: pandas DataFrame with time_index column
        test_size: proportion for test set (default 20%)
    
    Returns:
        train_df, test_df
    """
    split_idx = int(len(df) * (1 - test_size))
    train = df.iloc[:split_idx]
    test = df.iloc[split_idx:]
    return train, test


# ----------------------------------------
# 2. DATA ACCESS LAYER
# ----------------------------------------

def get_sales_timeseries(product_id, freq="D"):
    """
    Build a time series of quantities sold for a given product.
    Includes BOTH customer orders AND manual orders.
    
    Args:
        product_id: Product ID from inventory.Product
        freq: Frequency ('D' for daily, 'W' for weekly, 'M' for monthly)
    
    Returns:
        pandas Series with datetime index and quantity values, or None if no data
    """
    # Customer order sales
    customer_sales_qs = (
        OrderItem.objects.filter(
            product_variant__product__product_id=product_id,
            order__is_deleted=False,
            order__status="Completed",
        )
        .values("order__order_date")
        .annotate(quantity_sold=Sum("quantity"))
        .order_by("order__order_date")
    )

    # Manual order sales
    manual_sales_qs = (
        ManualOrderItem.objects.filter(
            product_variant__product__product_id=product_id,
            order__is_deleted=False,
            order__status="Completed",
        )
        .values("order__order_date")
        .annotate(quantity_sold=Sum("quantity"))
        .order_by("order__order_date")
    )

    # Combine both datasets
    all_sales_data = {}

    for entry in customer_sales_qs:
        date = entry["order__order_date"]
        if date not in all_sales_data:
            all_sales_data[date] = 0
        all_sales_data[date] += entry["quantity_sold"] or 0

    for entry in manual_sales_qs:
        date = entry["order__order_date"]
        if date not in all_sales_data:
            all_sales_data[date] = 0
        all_sales_data[date] += entry["quantity_sold"] or 0

    if not all_sales_data:
        return None

    # Convert to DataFrame
    df_data = [
        {"order_date": date, "quantity_sold": qty}
        for date, qty in all_sales_data.items()
    ]
    df = pd.DataFrame(df_data)

    df["order_date"] = pd.to_datetime(df["order_date"])
    df.set_index("order_date", inplace=True)
    
    # Resample to specified frequency and fill missing values with 0
    freq_map = {'M': 'ME', 'D': 'D', 'W': 'W'}
    resample_freq = freq_map.get(freq, freq)
    ts = df["quantity_sold"].resample(resample_freq).sum().fillna(0)

    
    return ts


# ----------------------------------------
# 3. LINEAR REGRESSION FORECAST
# ----------------------------------------

def linear_regression_forecast(product_id, steps=30, freq="D", test_size=0.2):
    """
    Forecast using Linear Regression with validation
    
    Args:
        product_id: Product ID
        steps: Number of periods to forecast
        freq: Frequency ('D', 'W', 'M')
        test_size: Proportion for test set (for validation)
    
    Returns:
        tuple: (forecast_series, metrics_dict, error_message)
    """
    try:
        from sklearn.linear_model import LinearRegression
        
        # Get time series data
        ts = get_sales_timeseries(product_id, freq=freq)
        
        if ts is None or len(ts) < 2:
            return None, None, "Not enough sales data (minimum 2 data points required)"
        
        # Prepare data
        df = ts.reset_index()
        df.columns = ['date', 'quantity']
        df['time_index'] = np.arange(len(df))
        
        # Validate with train-test split if enough data
        metrics = None
        if len(df) >= 10:
            train_df, test_df = train_test_split_timeseries(df, test_size=test_size)
            
            # Train model on training set
            X_train = train_df[['time_index']].values
            y_train = train_df['quantity'].values
            
            model = LinearRegression()
            model.fit(X_train, y_train)
            
            # Validate on test set
            X_test = test_df[['time_index']].values
            y_test = test_df['quantity'].values
            test_predictions = model.predict(X_test)
            
            metrics = calculate_forecast_metrics(y_test, test_predictions)
        
        # Train on full dataset for final forecast
        X_full = df[['time_index']].values
        y_full = df['quantity'].values
        
        final_model = LinearRegression()
        final_model.fit(X_full, y_full)
        
        # Generate future predictions
        future_indices = np.arange(len(df), len(df) + steps).reshape(-1, 1)
        predictions = final_model.predict(future_indices)
        
        # Ensure non-negative predictions
        predictions = np.maximum(predictions, 0)
        
        # Create forecast series with proper datetime index
        if freq == 'D':
            future_dates = pd.date_range(
                ts.index[-1] + pd.Timedelta(days=1), 
                periods=steps, 
                freq='D'
            )
        elif freq == 'W':
            future_dates = pd.date_range(
                ts.index[-1] + pd.Timedelta(weeks=1), 
                periods=steps, 
                freq='W'
            )
        elif freq == 'M':
            future_dates = pd.date_range(
                ts.index[-1] + pd.DateOffset(months=1), 
                periods=steps, 
                freq='MS'
            )
        else:
            future_dates = pd.date_range(
                ts.index[-1] + pd.Timedelta(days=1), 
                periods=steps, 
                freq=freq
            )
        
        forecast = pd.Series(predictions, index=future_dates)
        
        return forecast, metrics, None
        
    except Exception as e:
        return None, None, f"Linear Regression error: {str(e)}"


# ----------------------------------------
# 4. CONVENIENCE FUNCTIONS
# ----------------------------------------

def get_forecast_with_accuracy(product_id, steps=30, freq="D"):
    """
    Get Linear Regression forecast with accuracy metrics
    
    Args:
        product_id: Product ID
        steps: Number of periods to forecast
        freq: Frequency ('D' for daily, 'W' for weekly, 'M' for monthly)
    
    Returns:
        dict with forecast data and metrics
    """
    forecast, metrics, error = linear_regression_forecast(
        product_id, 
        steps=steps, 
        freq=freq
    )
    
    if error:
        return {"error": error}
    
    return {
        "method": "Linear Regression",
        "forecast_values": forecast.tolist(),
        "forecast_dates": [d.strftime("%Y-%m-%d") for d in forecast.index],
        "total_forecast": int(forecast.sum()),
        "metrics": metrics,
    }


def get_monthly_forecast_for_reorder(product_id):
    """
    Get 1-month forecast specifically for reorder level calculation
    
    Args:
        product_id: Product ID
    
    Returns:
        tuple: (forecasted_quantity, error_message)
    """
    forecast, metrics, error = linear_regression_forecast(
        product_id, 
        steps=1,  # 1 month ahead
        freq="M"  # Monthly frequency
    )
    
    if error:
        return None, error
    
    if forecast is None or len(forecast) == 0:
        return None, "Unable to generate forecast"
    
    forecasted_qty = int(round(forecast.iloc[0]))
    
    return forecasted_qty, None