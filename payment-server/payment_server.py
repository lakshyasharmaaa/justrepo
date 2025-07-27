# payment_server.py
from flask import Flask, request, jsonify, render_template_string
from flask_cors import CORS
import json
import os
import re
import requests
from datetime import datetime
import firebase_admin
from config import get_firebase_credentials
from firebase_admin import credentials, firestore

app = Flask(__name__)
CORS(app)  # Enable CORS for cross-origin requests

# Firebase Configuration
# FIREBASE_CONFIG = {
#     "apiKey": "AIzaSyC7MDxa3Zaf6E1Ea-4aHzv-p23NYWEbjYQ",
#     "authDomain": "ranexis-a43d8.firebaseapp.com",
#     "projectId": "ranexis-a43d8",
#     "storageBucket": "ranexis-a43d8.firebasestorage.app",
#     "messagingSenderId": "856967305477",
#     "appId": "1:856967305477:web:1bf7b8703d2d156cc3ff6c"
# }

# Initialize Firebase Admin SDK - FIXED VERSION (same as app.py)
try:
    if not firebase_admin._apps:
        # Method 1: Try environment variable first (for production deployment)
        firebase_creds = get_firebase_credentials()
        if firebase_creds:
            cred = credentials.Certificate(firebase_creds)
            firebase_admin.initialize_app(cred)
            print("✅ Firebase initialized with environment credentials")

        # Method 2: Try service account key file (for local development)
        elif os.path.exists("serviceAccountKey.json"):
            cred = credentials.Certificate("serviceAccountKey.json")
            firebase_admin.initialize_app(cred)
            print("✅ Firebase initialized with service account key")

        # Method 3: Default credentials (fallback)
        else:
            firebase_admin.initialize_app()
            print("✅ Firebase initialized with default credentials")

    db = firestore.client()
    FIRESTORE_ENABLED = True
    print("✅ Firestore client initialized successfully")
except Exception as e:
    print(f"❌ Firebase/Firestore initialization error: {e}")
    FIRESTORE_ENABLED = False
    db = None

# WhatsApp API Configuration - Import from your config
try:
    from config import PHONE_NUMBER_ID, ACCESS_TOKEN
    WHATSAPP_ENABLED = True
    print("✅ WhatsApp configuration loaded successfully")
except ImportError:
    print("⚠️ WhatsApp configuration not found. Please ensure config.py has PHONE_NUMBER_ID and ACCESS_TOKEN")
    WHATSAPP_ENABLED = False
    PHONE_NUMBER_ID = None
    ACCESS_TOKEN = None


def save_to_firestore(data):
    """Save user data to Firestore"""
    if not FIRESTORE_ENABLED or not db:
        print("❌ Firestore not available")
        return False

    try:
        # Use unique_id as document ID for easy retrieval
        doc_ref = db.collection('payment_requests').document(data.get('unique_id', ''))

        # Prepare data for Firestore
        firestore_data = {
            'unique_id': data.get('unique_id', ''),
            'first_name': data.get('first_name', ''),
            'last_name': data.get('last_name', ''),
            'email': data.get('email', ''),
            'whatsapp': data.get('whatsapp', ''),
            'customer_upi_id': data.get('customer_upi_id', ''),
            'timestamp': data.get('timestamp', ''),
            'expiry_time': data.get('expiry_time', ''),
            'status': data.get('status', 'pending'),
            'created_at': firestore.SERVER_TIMESTAMP
        }

        # Save to Firestore
        doc_ref.set(firestore_data)
        print(f"📝 User data saved to Firestore: {data.get('unique_id', 'Unknown')}")
        return True

    except Exception as e:
        print(f"❌ Error saving to Firestore: {e}")
        return False


def update_firestore_status(unique_id, status):
    """Update status of a specific record in Firestore"""
    if not FIRESTORE_ENABLED or not db:
        print("❌ Firestore not available")
        return False

    try:
        doc_ref = db.collection('payment_requests').document(unique_id)
        doc_ref.update({
            'status': status,
            'updated_at': firestore.SERVER_TIMESTAMP
        })
        print(f"✅ Updated Firestore status for {unique_id}: {status}")
        return True

    except Exception as e:
        print(f"❌ Error updating Firestore status: {e}")
        return False


def get_firestore_data():
    """Get all payment requests from Firestore"""
    if not FIRESTORE_ENABLED or not db:
        print("❌ Firestore not available")
        return []

    try:
        # Get all documents from payment_requests collection
        docs = db.collection('payment_requests').order_by('created_at', direction=firestore.Query.DESCENDING).stream()

        firestore_data = []
        for doc in docs:
            data = doc.to_dict()
            # Convert Firestore timestamp to string for JSON serialization
            if 'created_at' in data and data['created_at']:
                data['created_at'] = data['created_at'].isoformat() if hasattr(data['created_at'],
                                                                               'isoformat') else str(data['created_at'])
            if 'updated_at' in data and data['updated_at']:
                data['updated_at'] = data['updated_at'].isoformat() if hasattr(data['updated_at'],
                                                                               'isoformat') else str(data['updated_at'])

            firestore_data.append(data)

        return firestore_data

    except Exception as e:
        print(f"❌ Error reading from Firestore: {e}")
        return []


def send_whatsapp_confirmation(to_number, customer_name, unique_id):
    """Send WhatsApp payment confirmation message"""
    if not WHATSAPP_ENABLED:
        print("⚠️ WhatsApp not enabled - skipping message")
        return False

    try:
        # Remove any '+' or spaces from phone number
        clean_number = to_number.replace('+', '').replace(' ', '').replace('-', '')

        # Ensure the number has country code (assuming +91 for India if not present)
        if not clean_number.startswith('91') and len(clean_number) == 10:
            clean_number = '91' + clean_number

        # Create confirmation message
        message = f"""🎉 *Payment Confirmed!*

Hello {customer_name}! 👋

✅ Your payment has been successfully confirmed!

📋 *Payment Details:*
• Transaction ID: {unique_id}
• Status: CONFIRMED
• Date: {datetime.now().strftime('%d/%m/%Y %I:%M %p')}

🎁 Thank you for choosing LegionEdge!
📱 *Access Your Account:*
👉 https://legionedge.com/dashboard
Your Password to access the Notes is {unique_id}
If you have any questions, feel free to reach out to us.

Best regards,
LegionEdge Team"""

        # WhatsApp API endpoint
        url = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages"

        # Request payload
        payload = {
            "messaging_product": "whatsapp",
            "to": clean_number,
            "type": "text",
            "text": {"body": message}
        }

        # Request headers
        headers = {
            "Authorization": f"Bearer {ACCESS_TOKEN}",
            "Content-Type": "application/json"
        }

        # Send the message
        response = requests.post(url, json=payload, headers=headers)

        if response.status_code == 200:
            print(f"✅ WhatsApp confirmation sent to {clean_number}")
            return True
        else:
            print(f"❌ Failed to send WhatsApp message: {response.status_code} - {response.text}")
            return False

    except Exception as e:
        print(f"❌ Error sending WhatsApp message: {e}")
        return False


def update_config_with_payment_code(payment_data):
    """Update config.py with new payment code WITHOUT changing UPI_CONFIG"""
    try:
        # Read current config.py
        config_path = 'config.py'

        if os.path.exists(config_path):
            with open(config_path, 'r') as f:
                config_content = f.read()
        else:
            # Create basic config if it doesn't exist
            config_content = '''# config.py

VERIFY_TOKEN = "my_secure_token"
ACCESS_TOKEN = "EAAX6QYQURsoBO2QxrcAVVMBMkud9q5ZA9zhwvDHMXPorZBHQyUAayZC1rT7JVymFcyIItoRhgVFEmyCoC3swxvfwSGg1DIOAdEiCEu3jRJtJdXzppI"
PHONE_NUMBER_ID = "712084191980784"
QR_IMAGE_PATH = "static/qr.jpg"
MEDIA_ID_FILE = "media_id.txt"
UPI_CONFIG = {
    "upi_id": "paytmqr281005050101sy8hsntdk0xt@paytm",
    "name": "LegionEdge",
    "amount": "1"
}
'''

        # Prepare payment code data (NO UPI_ID from customer)
        payment_code_data = {
            "unique_id": payment_data['unique_id'],
            "customer_name": f"{payment_data['first_name']} {payment_data['last_name']}",
            "email": payment_data['email'],
            "customer_upi_id": payment_data.get('customer_upi_id', ''),  # Customer UPI for reference only
            "whatsapp": payment_data['whatsapp'],
            "created_at": payment_data['timestamp'],
            "expires_at": payment_data['expiry_time'],
            "status": "pending"
        }

        # Check if CURRENT_PAYMENT_CODE section exists
        if 'CURRENT_PAYMENT_CODE' not in config_content:
            # Add CURRENT_PAYMENT_CODE section at the end
            config_content += '\n\n# Current Payment Code - Auto Updated by Payment Server\n'
            config_content += 'CURRENT_PAYMENT_CODE = {}\n'

        # Use regex to replace only the CURRENT_PAYMENT_CODE section
        # This ensures we don't accidentally modify UPI_CONFIG
        payment_code_str = json.dumps(payment_code_data, indent=4)

        # Pattern to match CURRENT_PAYMENT_CODE = {...}
        pattern = r'(CURRENT_PAYMENT_CODE\s*=\s*){[^}]*}'

        if re.search(pattern, config_content):
            # Replace existing CURRENT_PAYMENT_CODE
            config_content = re.sub(
                pattern,
                f'\\1{payment_code_str}',
                config_content,
                flags=re.DOTALL
            )
        else:
            # Add new CURRENT_PAYMENT_CODE if not found
            config_content = config_content.rstrip() + f'\n\nCURRENT_PAYMENT_CODE = {payment_code_str}\n'

        # Write back to config.py
        with open(config_path, 'w') as f:
            f.write(config_content)

        # Also save to a separate log file for history
        log_payment_code(payment_data)

        print(f"✅ Payment code updated: {payment_data['unique_id']}")
        print("✅ Your merchant UPI ID in UPI_CONFIG remains unchanged!")

        return True

    except Exception as e:
        print(f"❌ Error updating config.py: {e}")
        return False


def log_payment_code(payment_data):
    """Log payment codes to a separate file for history"""
    try:
        log_file = 'payment_codes_log.json'

        # Load existing log
        if os.path.exists(log_file):
            with open(log_file, 'r') as f:
                log_data = json.load(f)
        else:
            log_data = []

        # Add new payment code
        log_entry = {
            **payment_data,
            'logged_at': datetime.now().isoformat()
        }
        log_data.append(log_entry)

        # Keep only last 100 entries
        if len(log_data) > 100:
            log_data = log_data[-100:]

        # Save updated log
        with open(log_file, 'w') as f:
            json.dump(log_data, f, indent=2)

        print(f"📝 Payment code logged to {log_file}")

    except Exception as e:
        print(f"⚠️ Error logging payment code: {e}")


@app.route('/confirm-payment', methods=['POST'])
def confirm_payment():
    """API endpoint to confirm payment and send WhatsApp message"""
    try:
        confirm_data = request.json

        if not confirm_data:
            return jsonify({'error': 'No data provided'}), 400

        # Extract data
        unique_id = confirm_data.get('uniqueId')
        customer_name = f"{confirm_data.get('firstName', '')} {confirm_data.get('lastName', '')}"
        whatsapp = confirm_data.get('whatsapp')

        if not unique_id or not whatsapp:
            return jsonify({'error': 'Missing required fields: uniqueId or whatsapp'}), 400

        print(f"🔄 Confirming payment for: {customer_name} ({unique_id})")

        # Update Firestore status
        firestore_updated = update_firestore_status(unique_id, 'confirmed')

        # Send WhatsApp confirmation
        whatsapp_sent = send_whatsapp_confirmation(whatsapp, customer_name, unique_id)

        if firestore_updated and whatsapp_sent:
            return jsonify({
                'message': 'Payment confirmed successfully and WhatsApp message sent',
                'unique_id': unique_id,
                'customer_name': customer_name,
                'whatsapp_sent': True,
                'firestore_updated': True
            }), 200
        elif firestore_updated:
            return jsonify({
                'message': 'Payment confirmed but WhatsApp message failed',
                'unique_id': unique_id,
                'customer_name': customer_name,
                'whatsapp_sent': False,
                'firestore_updated': True
            }), 200
        else:
            return jsonify({
                'error': 'Failed to confirm payment',
                'firestore_updated': False,
                'whatsapp_sent': whatsapp_sent
            }), 500

    except Exception as e:
        print(f"❌ Error confirming payment: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/save-payment-code', methods=['POST'])
def save_payment_code():
    """API endpoint to save payment code to config.py AND Firestore"""
    try:
        payment_data = request.json

        if not payment_data or 'unique_id' not in payment_data:
            return jsonify({'error': 'Invalid payment data'}), 400

        # Validate required fields
        required_fields = ['unique_id', 'first_name', 'last_name', 'email', 'whatsapp']
        for field in required_fields:
            if field not in payment_data or not payment_data[field]:
                return jsonify({'error': f'Missing required field: {field}'}), 400

        print(f"🔄 Processing payment code: {payment_data['unique_id']}")

        # Add default status if not provided
        if 'status' not in payment_data:
            payment_data['status'] = 'pending'

        # Save to Firestore first
        firestore_saved = save_to_firestore(payment_data)

        # Update config.py (preserving UPI_CONFIG)
        config_updated = update_config_with_payment_code(payment_data)

        if config_updated and firestore_saved:
            return jsonify({
                'message': 'Payment code saved successfully to both config.py and Firestore. Your merchant UPI ID remains unchanged.',
                'unique_id': payment_data['unique_id'],
                'note': 'Data saved to Firestore and payment tracking code updated. UPI_CONFIG is preserved.',
                'firestore_saved': True
            }), 200
        elif config_updated:
            return jsonify({
                'message': 'Payment code saved to config.py but Firestore save failed.',
                'unique_id': payment_data['unique_id'],
                'firestore_saved': False
            }), 200
        else:
            return jsonify({'error': 'Failed to save payment code'}), 500

    except Exception as e:
        print(f"❌ API Error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/get-current-payment-code', methods=['GET'])
def get_current_payment_code():
    """API endpoint to get current payment code"""
    try:
        # Import the current config
        import importlib
        import sys

        # Remove config from cache to get fresh data
        if 'config' in sys.modules:
            del sys.modules['config']

        import config

        if hasattr(config, 'CURRENT_PAYMENT_CODE'):
            return jsonify(config.CURRENT_PAYMENT_CODE), 200
        else:
            return jsonify({'message': 'No current payment code found'}), 404

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/get-upi-config', methods=['GET'])
def get_upi_config():
    """API endpoint to check current UPI configuration (merchant info)"""
    try:
        import importlib
        import sys

        # Remove config from cache to get fresh data
        if 'config' in sys.modules:
            del sys.modules['config']

        import config

        if hasattr(config, 'UPI_CONFIG'):
            return jsonify({
                'upi_config': config.UPI_CONFIG,
                'note': 'This is your merchant UPI configuration - it should never change'
            }), 200
        else:
            return jsonify({'message': 'No UPI configuration found'}), 404

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/payment-history', methods=['GET'])
def get_payment_history():
    """API endpoint to get payment history"""
    try:
        log_file = 'payment_codes_log.json'

        if os.path.exists(log_file):
            with open(log_file, 'r') as f:
                log_data = json.load(f)
            return jsonify(log_data), 200
        else:
            return jsonify([]), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/firestore-data', methods=['GET'])
def get_firestore_data_endpoint():
    """API endpoint to get all payment data from Firestore"""
    try:
        if not FIRESTORE_ENABLED:
            return jsonify({
                'error': 'Firestore not enabled',
                'message': 'Please configure Firebase Admin SDK'
            }), 500

        firestore_data = get_firestore_data()

        return jsonify({
            'firestore_enabled': True,
            'total_records': len(firestore_data),
            'data': firestore_data
        }), 200

    except Exception as e:
        print(f"❌ Error reading Firestore data: {e}")
        return jsonify({'error': str(e)}), 500


# Legacy CSV endpoint for compatibility
@app.route('/csv-data', methods=['GET'])
def get_csv_data():
    """Legacy endpoint - now returns Firestore data in CSV-like format for compatibility"""
    try:
        if not FIRESTORE_ENABLED:
            return jsonify({
                'error': 'Firestore not enabled - CSV functionality migrated to Firestore',
                'message': 'Please use /firestore-data endpoint'
            }), 500

        firestore_data = get_firestore_data()

        # Convert Firestore data to CSV-like format for compatibility
        csv_like_data = []
        for item in firestore_data:
            csv_like_data.append({
                'Unique ID': item.get('unique_id', ''),
                'First Name': item.get('first_name', ''),
                'Last Name': item.get('last_name', ''),
                'Email': item.get('email', ''),
                'WhatsApp': item.get('whatsapp', ''),
                'Customer UPI ID': item.get('customer_upi_id', ''),
                'Timestamp': item.get('timestamp', ''),
                'Expiry Time': item.get('expiry_time', ''),
                'Status': item.get('status', 'pending')
            })

        return jsonify({
            'csv_file': 'Migrated to Firestore',
            'total_records': len(csv_like_data),
            'data': csv_like_data,
            'note': 'Data is now stored in Firestore instead of CSV'
        }), 200

    except Exception as e:
        print(f"❌ Error reading Firestore data: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/')
def serve_payment_form():
    """Serve the payment form HTML"""
    firestore_count = 0

    if FIRESTORE_ENABLED:
        try:
            firestore_data = get_firestore_data()
            firestore_count = len(firestore_data)
        except:
            firestore_count = 0

    whatsapp_status = "✅ Enabled" if WHATSAPP_ENABLED else "❌ Disabled (Check config.py)"
    firestore_status = "✅ Enabled" if FIRESTORE_ENABLED else "❌ Disabled (Check Firebase config)"

    return f"""
    <h1>🔧 Payment Server Running (Firestore Edition)</h1>
    <p><strong>Payment server is running successfully with Firestore integration!</strong></p>

    <h3>📊 Status Overview:</h3>
    <ul>
        <li><strong>Database:</strong> Firestore (Cloud)</li>
        <li><strong>Firestore Status:</strong> {firestore_status}</li>
        <li><strong>Records:</strong> {firestore_count}</li>
        <li><strong>WhatsApp:</strong> {whatsapp_status}</li>
    </ul>

    <h3>🔗 Available Endpoints:</h3>
    <ul>
        <li><code>POST /save-payment-code</code> - Save new payment code (+ Firestore)</li>
        <li><code>POST /confirm-payment</code> - Confirm payment & send WhatsApp</li>
        <li><code>GET /get-current-payment-code</code> - Get current payment code</li>
        <li><code>GET /get-upi-config</code> - Check merchant UPI configuration</li>
        <li><code>GET /payment-history</code> - Get payment history</li>
        <li><code>GET /firestore-data</code> - Get Firestore data as JSON</li>
        <li><code>GET /csv-data</code> - Legacy endpoint (returns Firestore data)</li>
    </ul>

    <h3>🔥 Firestore Configuration:</h3>
    <ul>
        <li><strong>Project ID:</strong> {FIREBASE_CONFIG.get('projectId', 'Not configured')}</li>
        <li><strong>Collection:</strong> payment_requests</li>
        <li><strong>Admin SDK:</strong> {"✅ Initialized" if FIRESTORE_ENABLED else "❌ Not configured"}</li>
    </ul>

    <h3>⚠️ Important Notes:</h3>
    <ul>
        <li><strong>Migration:</strong> CSV functionality has been replaced with Firestore</li>
        <li><strong>UPI_CONFIG preservation:</strong> This server will NEVER modify your UPI_CONFIG!</li>
        <li><strong>Firestore tracking:</strong> All user form data is saved to Firestore with status tracking</li>
        <li><strong>WhatsApp integration:</strong> Confirmation messages are sent automatically</li>
        <li><strong>Status updates:</strong> Firestore status is updated when payments are confirmed</li>
        <li><strong>Real-time sync:</strong> Data is synchronized across all clients</li>
    </ul>

    <h3>🚀 Setup Instructions:</h3>
    <ol>
        <li>Install Firebase Admin SDK: <code>pip install firebase-admin</code></li>
        <li>Download service account key from Firebase Console</li>
        <li>Place serviceAccountKey.json in project directory</li>
        <li>Restart the server</li>
    </ol>
    """


if __name__ == '__main__':
    print("🚀 Starting Enhanced Payment Server with Firestore...")
    print("📋 Server Configuration:")
    print("   - Port: 5000")
    print("   - Host: 0.0.0.0 (accessible from anywhere)")
    print("   - CORS: Enabled")
    print("   - Database: Firestore")
    print("   - WhatsApp: " + ("Enabled" if WHATSAPP_ENABLED else "Disabled"))
    print("   - Firestore: " + ("Enabled" if FIRESTORE_ENABLED else "Disabled"))
    print()

    print("✅ Features:")
    print("   - Preserves your merchant UPI_CONFIG")
    print("   - Only updates payment tracking codes")
    print("   - Logs all payment codes for history")
    print("   - Saves user form data to Firestore with status tracking")
    print("   - Sends WhatsApp confirmation messages")
    print("   - Updates Firestore status when payments are confirmed")
    print("   - API endpoints for debugging")
    print("   - Real-time cloud database with Firestore")
    print("   - Scalable and reliable data storage")
    print()

    if not FIRESTORE_ENABLED:
        print("⚠️ FIRESTORE SETUP REQUIRED:")
        print("   1. Install: pip install firebase-admin")
        print("   2. Download service account key from Firebase Console")
        print("   3. Place serviceAccountKey.json in project directory")
        print("   4. Restart this server")
        print()

    print("🔗 Access the server at: http://localhost:5000")
    print("=" * 50)

    app.run(debug=True, host='0.0.0.0', port=5000)