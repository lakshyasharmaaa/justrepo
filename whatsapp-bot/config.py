# config.py - SAFE VERSION (Upload this to Railway/Render)
import os
import json

# 🔒 All real values come from ENVIRONMENT VARIABLES
# These are just fallback placeholders - NOT your real secrets!

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "PLACEHOLDER_VERIFY_TOKEN")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN", "PLACEHOLDER_ACCESS_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID", "PLACEHOLDER_PHONE_ID")

# Static configurations (safe to show)
QR_IMAGE_PATH = "static/qr.jpg"
MEDIA_ID_FILE = "media_id.txt"

# UPI Configuration from environment variables
UPI_CONFIG = {
    "upi_id": os.getenv("UPI_ID", "PLACEHOLDER_UPI_ID"),
    "name": os.getenv("UPI_NAME", "Your Business Name"),
    "amount": os.getenv("UPI_AMOUNT", "1")
}

# Firebase credentials helper function
def get_firebase_credentials():
    """Get Firebase credentials from environment variable"""
    firebase_key = os.getenv('FIREBASE_SERVICE_ACCOUNT_KEY')
    if firebase_key:
        try:
            return json.loads(firebase_key)
        except json.JSONDecodeError:
            print("❌ Invalid Firebase credentials format")
            return None
    return None

# Current Payment Code - Auto Updated by Payment Server
CURRENT_PAYMENT_CODE = {}

# 📝 IMPORTANT:
# The real values are set as ENVIRONMENT VARIABLES in Railway/Render dashboard
# This file only contains placeholders and the logic to read from environment