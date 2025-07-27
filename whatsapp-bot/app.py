# app.py
from flask import Flask, request
import qrcode
import io
import os
import json
import threading
import requests
from datetime import datetime, timedelta
import random
import string
from PIL import Image, ImageDraw, ImageFont
import tempfile
import importlib
import sys
from config import get_firebase_credentials
# Firebase imports for Firestore integration
import firebase_admin
from firebase_admin import credentials, firestore

# Your existing imports (only keeping what's needed)
from config import PHONE_NUMBER_ID, ACCESS_TOKEN, UPI_CONFIG, VERIFY_TOKEN

app = Flask(__name__)

processed_messages = set()

# Initialize Firebase Admin SDK for Firestore
# Initialize Firebase Admin SDK for Firestore
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


def get_current_payment_code_from_firestore():
    """Get the most recent pending payment code from Firestore"""
    if not FIRESTORE_ENABLED or not db:
        print("[❌] Firestore not available, falling back to config method")
        return get_current_payment_code_from_config()

    try:
        # Get the most recent pending payment request
        docs = db.collection('payment_requests').where('status', '==', 'pending').order_by('created_at',
                                                                                           direction=firestore.Query.DESCENDING).limit(
            1).stream()

        for doc in docs:
            payment_data = doc.to_dict()

            # Check if payment code is still valid (not expired)
            expiry_time = payment_data.get('expiry_time')
            if expiry_time:
                try:
                    if isinstance(expiry_time, str):
                        expiry_datetime = datetime.fromisoformat(expiry_time.replace('Z', '+00:00'))
                    else:
                        expiry_datetime = expiry_time

                    if datetime.now(expiry_datetime.tzinfo) > expiry_datetime:
                        print(f"[⚠️] Payment code {payment_data.get('unique_id')} has expired")
                        continue
                except Exception as e:
                    print(f"[⚠️] Error parsing expiry time: {e}")

            print(f"[📋] Found active payment code from Firestore: {payment_data.get('unique_id')}")
            return {
                'unique_id': payment_data.get('unique_id', ''),
                'customer_name': f"{payment_data.get('first_name', '')} {payment_data.get('last_name', '')}".strip(),
                'email': payment_data.get('email', ''),
                'customer_upi_id': payment_data.get('customer_upi_id', ''),
                'whatsapp': payment_data.get('whatsapp', ''),
                'created_at': payment_data.get('timestamp', ''),
                'expires_at': payment_data.get('expiry_time', ''),
                'status': payment_data.get('status', 'pending')
            }

        print("[⚠️] No active payment codes found in Firestore")
        return None

    except Exception as e:
        print(f"[❌] Error getting payment code from Firestore: {e}")
        # Fallback to config method
        return get_current_payment_code_from_config()


def get_current_payment_code_from_config():
    """Fallback method: Get current payment code from config.py"""
    try:
        # Remove config from cache to get fresh data
        if 'config' in sys.modules:
            del sys.modules['config']

        # Re-import config to get latest data
        import config

        if hasattr(config, 'CURRENT_PAYMENT_CODE') and config.CURRENT_PAYMENT_CODE:
            print(f"[📋] Found payment code from config: {config.CURRENT_PAYMENT_CODE.get('unique_id', 'No ID')}")
            return config.CURRENT_PAYMENT_CODE
        else:
            print("[⚠️] No CURRENT_PAYMENT_CODE found in config.py or it's empty")
            return None
    except Exception as e:
        print(f"[❌] Error getting current payment code from config: {e}")
        return None


def get_current_payment_code():
    """Main function to get current payment code - tries Firestore first, then config"""
    # Try Firestore first
    payment_code = get_current_payment_code_from_firestore()

    if payment_code:
        return payment_code

    # Fallback to config method
    print("[⚠️] Falling back to config.py method")
    return get_current_payment_code_from_config()


def update_payment_status_in_firestore(unique_id, status):
    """Update payment status in Firestore when QR is generated"""
    if not FIRESTORE_ENABLED or not db:
        print("[⚠️] Firestore not available, skipping status update")
        return

    try:
        doc_ref = db.collection('payment_requests').document(unique_id)
        doc_ref.update({
            'status': status,
            'qr_generated_at': firestore.SERVER_TIMESTAMP,
            'updated_at': firestore.SERVER_TIMESTAMP
        })
        print(f"[✅] Updated Firestore status for {unique_id}: {status}")
    except Exception as e:
        print(f"[❌] Error updating Firestore status: {e}")


def generate_transaction_note():
    """Generate transaction note - use payment code if available, otherwise generate random"""
    payment_code = get_current_payment_code()

    if payment_code and payment_code.get('unique_id'):
        # Use the unique_id from payment server as transaction note
        print(f"[✅] Using payment code as TXN: {payment_code['unique_id']}")
        return payment_code['unique_id']
    else:
        # Fallback to random generation
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        random_code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
        fallback_txn = f"TXN-{timestamp}-{random_code}"
        print(f"[⚠️] No payment code found, using fallback TXN: {fallback_txn}")
        return fallback_txn


def create_upi_url(transaction_note):
    """Create UPI URL with transaction note"""
    payment_code = get_current_payment_code()

    # Always use merchant UPI from config (never use customer UPI for payment)
    # Customer UPI is only for reference/tracking
    upi_id = UPI_CONFIG['upi_id']
    name = UPI_CONFIG['name']

    # Use customer name in transaction note if available
    if payment_code and payment_code.get('customer_name'):
        display_name = f"{name} - {payment_code['customer_name']}"
    else:
        display_name = name

    return f"upi://pay?pa={upi_id}&pn={display_name}&am={UPI_CONFIG['amount']}&tn={transaction_note}"


def load_company_logo(logo_path, size=(120, 80)):
    """
    Helper function to load and resize company logo
    Returns None if logo doesn't exist or fails to load
    """
    try:
        if os.path.exists(logo_path):
            logo = Image.open(logo_path)
            # Convert to RGBA if not already
            if logo.mode != 'RGBA':
                logo = logo.convert('RGBA')
            logo = logo.resize(size, Image.Resampling.LANCZOS)
            return logo
    except Exception as e:
        print(f"Failed to load logo {logo_path}: {e}")
    return None


def create_styled_qr_image(upi_url, transaction_note, company_logo_path="logo.png"):
    """
    Create QR code image with company logo on top and UPI brand logos at bottom

    Args:
        upi_url: UPI payment URL
        transaction_note: Transaction reference
        company_logo_path: Path to your company logo file
    """

    # Generate QR code with higher error correction
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_H,  # High error correction
        box_size=10,
        border=4
    )
    qr.add_data(upi_url)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white")

    # Calculate canvas size
    qr_size = 300
    top_space = 100  # Space for company logo
    bottom_space = 100  # Space for UPI brand logos
    canvas_height = qr_size + top_space + bottom_space
    canvas_width = max(qr_size + 40, 400)  # Minimum width for UPI logos

    # Create canvas with white background
    canvas = Image.new('RGB', (canvas_width, canvas_height), 'white')

    # Add company logo at top
    add_company_logo_top(canvas, canvas_width, company_logo_path)

    # Resize and center QR code
    qr_img = qr_img.resize((qr_size, qr_size))
    qr_x = (canvas_width - qr_size) // 2
    qr_y = top_space
    canvas.paste(qr_img, (qr_x, qr_y))

    # Add UPI brand logos at bottom
    add_upi_brand_logos(canvas, canvas_width, canvas_height - 80)

    return canvas


def add_company_logo_top(canvas, canvas_width, logo_path):
    """
    Add company logo at the top of the QR code
    """
    try:
        # Try to load company logo
        company_logo = load_company_logo(logo_path, size=(100, 100))

        if company_logo:
            # Calculate center position for logo
            logo_width, logo_height = company_logo.size
            logo_x = (canvas_width - logo_width) // 2
            logo_y = 10  # 10px from top

            # Add white background behind logo for better visibility
            background_padding = 10
            background = Image.new('RGB',
                                   (logo_width + background_padding * 2,
                                    logo_height + background_padding * 2), 'white')
            canvas.paste(background, (logo_x - background_padding, logo_y - background_padding))

            # Paste the logo
            canvas.paste(company_logo, (logo_x, logo_y), company_logo)
            print(f"[✅] Company logo added from: {logo_path}")
        else:
            # Fallback: Add company name as text
            add_company_text_fallback(canvas, canvas_width)

    except Exception as e:
        print(f"[⚠️] Error adding company logo: {e}")
        # Fallback to text
        add_company_text_fallback(canvas, canvas_width)


def add_company_text_fallback(canvas, canvas_width):
    """
    Fallback function to add company name as text if logo fails
    """
    try:
        draw = ImageDraw.Draw(canvas)
        try:
            title_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 24)
        except:
            title_font = ImageFont.load_default()

        company_name = "LegionEdge"

        # Company name
        name_bbox = draw.textbbox((0, 0), company_name, font=title_font)
        name_width = name_bbox[2] - name_bbox[0]
        name_x = (canvas_width - name_width) // 2
        draw.text((name_x, 30), company_name, font=title_font, fill="black")

        print("[⚠️] Using text fallback for company branding")

    except Exception as e:
        print(f"[❌] Error in text fallback: {e}")


def add_upi_brand_logos(canvas, canvas_width, start_y):
    """
    Add UPI brand logos at the bottom of the QR code
    Uses actual logo images from the same folder
    """
    draw = ImageDraw.Draw(canvas)
    try:
        small_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 9)
        medium_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 10)
    except:
        small_font = medium_font = ImageFont.load_default()

    # Single UPI logo configuration
    upi_logo = {
        "name": "UPI",
        "logo_file": "upi_logo.png"
    }

    # Calculate positions for single logo
    logo_width = 80
    logo_height = 80
    start_x = (canvas_width - logo_width) // 2

    # Add "Scan & Pay with UPI:" text
    pay_text = "Scan & Pay with UPI:"
    pay_bbox = draw.textbbox((0, 0), pay_text, font=small_font)
    pay_width = pay_bbox[2] - pay_bbox[0]
    pay_x = (canvas_width - pay_width) // 2
    draw.text((pay_x, start_y - 25), pay_text, font=small_font, fill="#666")

    # Draw single UPI logo
    x = start_x
    y = start_y

    try:
        # Load and resize logo image
        logo_path = upi_logo["logo_file"]
        if os.path.exists(logo_path):
            logo_img = Image.open(logo_path)

            # Convert to RGBA if not already
            if logo_img.mode != 'RGBA':
                logo_img = logo_img.convert('RGBA')

            # Resize logo to fit the box while maintaining aspect ratio
            logo_img.thumbnail((logo_width - 4, logo_height - 4), Image.Resampling.LANCZOS)

            # Calculate position to center the logo
            logo_x = x + (logo_width - logo_img.width) // 2
            logo_y = y + (logo_height - logo_img.height) // 2

            # Paste the logo onto the canvas
            canvas.paste(logo_img, (logo_x, logo_y), logo_img)

        else:
            # Fallback: draw a placeholder rectangle with UPI text if image not found
            draw.rectangle([x, y, x + logo_width, y + logo_height],
                           fill="#f0f0f0", outline="#ddd", width=1)

            # Add UPI text as fallback
            upi_name = upi_logo["name"]
            text_bbox = draw.textbbox((0, 0), upi_name, font=medium_font)
            text_width = text_bbox[2] - text_bbox[0]
            text_height = text_bbox[3] - text_bbox[1]
            text_x = x + (logo_width - text_width) // 2
            text_y = y + (logo_height - text_height) // 2

            draw.text((text_x, text_y), upi_name, font=medium_font, fill="#333")
            print(f"[⚠️] Logo not found: {logo_path}, using placeholder")

    except Exception as e:
        # Error handling: draw placeholder if image loading fails
        draw.rectangle([x, y, x + logo_width, y + logo_height],
                       fill="#f0f0f0", outline="#ddd", width=1)

        upi_name = upi_logo["name"]
        text_bbox = draw.textbbox((0, 0), upi_name, font=medium_font)
        text_width = text_bbox[2] - text_bbox[0]
        text_height = text_bbox[3] - text_bbox[1]
        text_x = x + (logo_width - text_width) // 2
        text_y = y + (logo_height - text_height) // 2

        draw.text((text_x, text_y), upi_name, font=medium_font, fill="#333")
        print(f"[❌] Error loading logo {upi_logo['logo_file']}: {e}")

    print("[✅] UPI brand logo added")


def upload_image_to_whatsapp(image_path):
    """Fixed version with proper MIME type handling"""
    url = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/media"
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}"
    }

    # Determine MIME type properly
    if image_path.lower().endswith('.png'):
        mime_type = "image/png"
    elif image_path.lower().endswith(('.jpg', '.jpeg')):
        mime_type = "image/jpeg"
    else:
        mime_type = "image/png"  # Default to PNG

    try:
        with open(image_path, 'rb') as image_file:
            files = {
                'file': (os.path.basename(image_path), image_file, mime_type)
            }
            data = {
                "messaging_product": "whatsapp"
            }

            response = requests.post(url, headers=headers, files=files, data=data)

        if response.status_code == 200:
            media_id = response.json().get("id")
            print(f"[✅] Uploaded image with media ID: {media_id}")
            return media_id
        else:
            print(f"[❌] Failed to upload image: {response.text}")
            return None
    except Exception as e:
        print(f"[❌] Exception during image upload: {e}")
        return None


def test_access_token():
    """Test if access token has required permissions"""
    url = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}"
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}

    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        print("✅ Access token is valid and has permissions")
        return True
    else:
        print(f"❌ Access token issue: {response.text}")
        return False


def send_whatsapp_text(phone_id, message):
    url = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": phone_id,
        "type": "text",
        "text": {"body": message}
    }
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}", "Content-Type": "application/json"}

    response = requests.post(url, json=payload, headers=headers)
    if response.status_code != 200:
        print(f"❌ Failed to send text message: {response.text}")
    else:
        print("✅ Text message sent successfully")

    return response


def send_whatsapp_image_with_media_id(to_number, media_id, caption):
    """Send image using already uploaded media ID"""
    message_url = f'https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages'
    message_headers = {
        'Authorization': f'Bearer {ACCESS_TOKEN}',
        'Content-Type': 'application/json'
    }
    message_data = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "image",
        "image": {
            "id": media_id,
            "caption": caption
        }
    }

    message_response = requests.post(message_url, headers=message_headers, json=message_data)
    if message_response.status_code != 200:
        print(f"[❌] Failed to send message: {message_response.text}")
    else:
        print("[✅] Image message sent successfully.")


def send_whatsapp_image(to_number, image_path, caption):
    """Updated function with better error handling"""
    try:
        # First upload the image
        media_id = upload_image_to_whatsapp(image_path)

        if not media_id:
            print("[❌] Failed to upload image, cannot send message")
            send_whatsapp_text(to_number, "❌ Failed to generate QR code. Please try again.")
            return

        # Then send the message with the media ID
        send_whatsapp_image_with_media_id(to_number, media_id, caption)
        print(f"[📤] Image sent to {to_number}")

    except Exception as e:
        print(f"[❌] Error in send_whatsapp_image: {e}")
        send_whatsapp_text(to_number, "❌ Failed to send QR code. Please try again.")


def generate_and_upload_qr(sender_id):
    try:
        # Get current payment code info from Firestore
        payment_code = get_current_payment_code()

        # Generate transaction note (will use payment code unique_id if available)
        transaction_note = generate_transaction_note()

        # Create UPI URL (always uses merchant UPI from config)
        upi_url = create_upi_url(transaction_note)

        print(f"[🔍] Payment Code: {payment_code}")
        print(f"[🏷️] Transaction Note: {transaction_note}")
        print(f"[🔗] UPI URL: {upi_url}")

        # Update payment status in Firestore to indicate QR was generated
        if payment_code and payment_code.get('unique_id'):
            update_payment_status_in_firestore(payment_code['unique_id'], 'qr_generated')

        # Create QR code with company logo on top and UPI brands at bottom
        # Make sure you have a logo.png file in your project directory
        company_logo_path = "logo.png"  # Change this to your logo file path
        qr_img = create_styled_qr_image(upi_url, transaction_note, company_logo_path)

        # Create temporary file with proper extension
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.png', mode='wb')
        temp_file.close()  # Close the file so PIL can write to it

        # Save the QR image
        qr_img.save(temp_file.name, 'PNG')
        print(f"[📁] QR image saved to: {temp_file.name}")

        # Create caption with payment code info from Firestore
        if payment_code:
            # Calculate time remaining
            expires_at = payment_code.get('expires_at', '')
            time_remaining = ""
            if expires_at:
                try:
                    if isinstance(expires_at, str):
                        expiry_datetime = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
                    else:
                        expiry_datetime = expires_at

                    now = datetime.now(expiry_datetime.tzinfo)
                    if expiry_datetime > now:
                        remaining = expiry_datetime - now
                        minutes = int(remaining.total_seconds() // 60)
                        time_remaining = f" ({minutes} min left)"
                    else:
                        time_remaining = " (EXPIRED)"
                except:
                    pass

            caption = (
                f"*🔥 LegionEdge Payment*\n"
                f"👤 Customer: {payment_code.get('customer_name', 'N/A')}\n"
                f"📧 Email: {payment_code.get('email', 'N/A')}\n"
                f"💰 Amount: ₹{UPI_CONFIG['amount']}\n"
                f"⏰ Valid till: {expires_at}{time_remaining}\n"
                f"🆔 Payment ID: {payment_code.get('unique_id', 'N/A')}\n\n"
                f"📱 Scan & pay via any UPI app\n"
                f"💳 Payment to: {UPI_CONFIG['name']}\n"
                f"✅ Payment will be verified automatically.\n\n"
                f"🔥 *Powered by Firestore Database*"
            )
        else:
            caption = (
                f"*🔥 LegionEdge Payment*\n"
                f"💰 Amount: ₹{UPI_CONFIG['amount']}\n"
                f"👤 Payee: {UPI_CONFIG['name']}\n"
                f"🔖 TXN: {transaction_note}\n\n"
                f"📱 Scan & pay via any UPI app\n"
                f"✅ Payment will be verified automatically.\n\n"
                f"⚠️ No active payment code found in Firestore"
            )

        # Send the image
        send_whatsapp_image(sender_id, temp_file.name, caption)

        # Clean up temporary file
        try:
            os.unlink(temp_file.name)
            print(f"[🗑️] Cleaned up temp file: {temp_file.name}")
        except Exception as cleanup_error:
            print(f"[⚠️] Could not clean up temp file: {cleanup_error}")

    except Exception as e:
        print(f"[❌] Error in generate_and_upload_qr: {e}")
        send_whatsapp_text(sender_id, "❌ Failed to generate QR code. Please try again.")


def webhook_logic(data):
    value = data.get("entry", [])[0].get("changes", [])[0].get("value", {})
    messages = value.get("messages", [])
    if not messages:
        return 'No message found', 200

    entry = messages[0]
    message_id = entry.get("id")
    sender_id = entry.get("from")
    msg_type = entry.get("type")

    if message_id in processed_messages:
        return 'MESSAGE_ALREADY_PROCESSED', 200

    processed_messages.add(message_id)

    if msg_type == 'text':
        msg_text = entry['text']['body'].lower().strip()
        if any(keyword in msg_text for keyword in ["hi", "hello", "pay", "qr", "payment", "buy"]):
            # Check if payment code is available from Firestore
            payment_code = get_current_payment_code()
            if payment_code:
                send_whatsapp_text(sender_id,
                                   f"🔄 Generating QR code for {payment_code.get('customer_name', 'customer')} (from Firestore)...")
            else:
                send_whatsapp_text(sender_id, "🔄 Generating QR code... (no active payment found)")

            threading.Thread(target=generate_and_upload_qr, args=(sender_id,)).start()
        else:
            send_whatsapp_text(sender_id, "👋 Send 'pay' to get payment QR code.")

    return 'EVENT_RECEIVED', 200


@app.route('/webhook', methods=['GET', 'POST'])
def webhook():
    if request.method == 'GET':
        mode = request.args.get("hub.mode")
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")

        if mode == "subscribe" and token == VERIFY_TOKEN:
            print("✅ Webhook verified.")
            return challenge, 200
        else:
            print("❌ Webhook verification failed.")
            return "Verification failed", 403

    elif request.method == 'POST':
        data = request.get_json()
        try:
            return webhook_logic(data)
        except Exception as e:
            print(f"❌ Error processing webhook: {e}")
            return "ERROR", 500


@app.route('/')
def home():
    return "🤖 LegionEdge WhatsApp Bot is running with Firestore integration! 🔥"


@app.route('/status')
def status():
    """Check current payment code status from Firestore"""
    payment_code = get_current_payment_code()
    if payment_code:
        return f"""
        <h2>🔥 WhatsApp Bot Status (Firestore Integration)</h2>
        <p><strong>Current Payment Code:</strong> {payment_code['unique_id']}</p>
        <p><strong>Customer:</strong> {payment_code.get('customer_name', 'N/A')}</p>
        <p><strong>Email:</strong> {payment_code.get('email', 'N/A')}</p>
        <p><strong>WhatsApp:</strong> {payment_code.get('whatsapp', 'N/A')}</p>
        <p><strong>Status:</strong> {payment_code.get('status', 'N/A')}</p>
        <p><strong>Expires:</strong> {payment_code.get('expires_at', 'N/A')}</p>
        <p><strong>Database:</strong> 🔥 Firestore</p>
        """
    else:
        return """
        <h2>⚠️ WhatsApp Bot Status</h2>
        <p>No active payment code found in Firestore</p>
        <p><strong>Database:</strong> 🔥 Firestore (Connected: """ + str(FIRESTORE_ENABLED) + ")</p>"


@app.route('/firestore-test')
def firestore_test():
    """Test Firestore connection"""
    if not FIRESTORE_ENABLED:
        return "❌ Firestore not enabled. Please configure Firebase Admin SDK."

    try:
        # Try to read from payment_requests collection
        docs = db.collection('payment_requests').limit(5).stream()
        count = 0
        for doc in docs:
            count += 1

        return f"✅ Firestore connection successful! Found {count} payment requests."
    except Exception as e:
        return f"❌ Firestore connection error: {e}"


if __name__ == '__main__':
    print("🚀 WhatsApp Bot running with Firestore integration:")
    print(f"💳 UPI: {UPI_CONFIG['upi_id']}, Name: {UPI_CONFIG['name']}, Amount: ₹{UPI_CONFIG['amount']}")
    print(f"🔥 Firestore: {'✅ Enabled' if FIRESTORE_ENABLED else '❌ Disabled'}")

    # Test access token before starting
    if test_access_token():
        print("🔑 Access token verified successfully")
    else:
        print("⚠️ Access token verification failed - check permissions")

    # Check current payment code on startup from Firestore
    payment_code = get_current_payment_code()
    if payment_code:
        print(f"💳 Current Payment Code: {payment_code['unique_id']} for {payment_code.get('customer_name', 'N/A')}")
        print(f"📧 Email: {payment_code.get('email', 'N/A')}")
        print(f"📱 WhatsApp: {payment_code.get('whatsapp', 'N/A')}")
        print(f"⏰ Expires: {payment_code.get('expires_at', 'N/A')}")
        print(f"🔥 Source: Firestore Database")
    else:
        print("⚠️ No active payment code found in Firestore - using default settings")

    if not FIRESTORE_ENABLED:
        print("\n⚠️ FIRESTORE SETUP REQUIRED:")
        print("   1. Install: pip install firebase-admin")
        print("   2. Download service account key from Firebase Console")
        print("   3. Set GOOGLE_APPLICATION_CREDENTIALS environment variable")
        print("   4. Restart this bot")
        print("   5. Bot will work with basic functionality but won't sync with payment server")

    print("\n🔗 Bot endpoints:")
    print("   - http://localhost:5001/ - Home page")
    print("   - http://localhost:5001/status - Current payment status")
    print("   - http://localhost:5001/firestore-test - Test Firestore connection")
    print("=" * 60)

    app.run(debug=True, port=5001)  # Changed to port 5001