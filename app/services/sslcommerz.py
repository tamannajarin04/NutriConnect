"""
app/services/sslcommerz.py
SSLCommerz payment gateway integration.
Sandbox docs: https://developer.sslcommerz.com/doc/v4/
"""
import requests
import os


class SSLCommerzService:
    SANDBOX_URL = "https://sandbox.sslcommerz.com/gwprocess/v4/api.php"
    LIVE_URL    = "https://securepay.sslcommerz.com/gwprocess/v4/api.php"

    SANDBOX_VALIDATE = "https://sandbox.sslcommerz.com/validator/api/validationserverAPI.php"
    LIVE_VALIDATE    = "https://securepay.sslcommerz.com/validator/api/validationserverAPI.php"

    def __init__(self):
        self.store_id   = os.environ.get("SSLCOMMERZ_STORE_ID", "testbox")
        self.store_pass = os.environ.get("SSLCOMMERZ_STORE_PASS", "qwerty")
        self.is_live    = os.environ.get("SSLCOMMERZ_IS_LIVE", "0") == "1"

        self.api_url      = self.LIVE_URL      if self.is_live else self.SANDBOX_URL
        self.validate_url = self.LIVE_VALIDATE if self.is_live else self.SANDBOX_VALIDATE

    def initiate_payment(self, order, user, success_url, fail_url, cancel_url,
                         amount=None, tran_id=None):
        """
        Initiate a payment session with SSLCommerz.
        Returns dict with 'status', 'GatewayPageURL', and full response.
        """
        pay_amount = amount if amount is not None else order.total_price

        post_data = {
            # Auth
            "store_id":    self.store_id,
            "store_passwd": self.store_pass,

            # Transaction
            "total_amount": pay_amount,
            "currency":     "USD",
            "tran_id":      tran_id or order.order_number,

            # Callbacks
            "success_url":  success_url,
            "fail_url":     fail_url,
            "cancel_url":   cancel_url,

            # Customer info
            "cus_name":    user.full_name or user.username,
            "cus_email":   user.email,
            "cus_phone":   getattr(user, "phone", "01700000000") or "01700000000",
            "cus_add1":    "Bangladesh",
            "cus_city":    "Dhaka",
            "cus_country": "Bangladesh",

            # Product info
            "product_name":     f"Order {order.order_number}",
            "product_category": "Food",
            "product_profile":  "general",

            # Shipping (required by SSLCommerz)
            "shipping_method": "NO",
            "num_of_item":     order.items.count() if hasattr(order.items, 'count') else 1,
            "weight_of_items": "0.5",
            "ship_name":       user.full_name or user.username,
            "ship_add1":       "Bangladesh",
            "ship_city":       "Dhaka",
            "ship_country":    "Bangladesh",
        }

        try:
            response = requests.post(self.api_url, data=post_data, timeout=30)
            data = response.json()
            return data
        except Exception as e:
            return {"status": "FAILED", "failedreason": str(e)}

    def validate_payment(self, val_id):
        """Validate a payment using SSLCommerz validation API."""
        params = {
            "val_id":      val_id,
            "store_id":    self.store_id,
            "store_passwd": self.store_pass,
            "format":      "json",
        }
        try:
            response = requests.get(self.validate_url, params=params, timeout=30)
            return response.json()
        except Exception as e:
            return {"status": "INVALID", "error": str(e)}


# Singleton
sslcommerz = SSLCommerzService()