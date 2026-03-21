from flask import Flask, render_template, request, redirect, url_for, session
from pymongo import MongoClient
from datetime import datetime
import uuid
import pickle
import numpy as np
import os
from dotenv import load_dotenv
load_dotenv()
import threading

from flask import session
from werkzeug.security import generate_password_hash, check_password_hash
from pymongo.errors import DuplicateKeyError
from bson.objectid import ObjectId
from flask import send_file
import smtplib
from email.mime.text import MIMEText
import secrets  # For generating secure tokens
from datetime import datetime, timedelta


# Email Configuration (Use an App Password for Gmail)
MAIL_SERVER = "smtp.gmail.com"
MAIL_PORT = 587
MAIL_USERNAME = os.getenv("MAIL_USERNAME")
MAIL_PASSWORD = os.getenv("MAIL_PASSWORD")     # Not your login password, a Google App Password
ADMIN_EMAIL = "pradhanmramasankar15@gmail.com"    # Where the alerts go

# Add 'recipient_email' as a parameter
def send_low_stock_alert(item_name, current_qty, recipient_email):
    try:
        subject = f"⚠️ LOW STOCK ALERT: {item_name}"
        body = f"Hello! Your item '{item_name}' is running low. Only {current_qty} left in stock."
        msg = MIMEText(body)
        msg['Subject'] = subject
        msg['From'] = MAIL_USERNAME
        msg['To'] = recipient_email

        # Added timeout=10 to prevent Render SIGKILL
        with smtplib.SMTP(MAIL_SERVER, MAIL_PORT, timeout=10) as server:
            server.starttls()
            server.login(MAIL_USERNAME, MAIL_PASSWORD)
            server.send_message(msg)
            print(f"Low stock alert sent to {recipient_email}")
    except Exception as e:
        print(f"Error sending low stock alert: {e}")

def send_reset_email(recipient_email, reset_url):
    try:
        subject = "Password Reset Request - SmartInv"
        body = f"Click the link below to reset your password. This link expires in 30 minutes:\n\n{reset_url}"
        msg = MIMEText(body)
        msg['Subject'] = subject
        msg['From'] = MAIL_USERNAME
        msg['To'] = recipient_email

        with smtplib.SMTP(MAIL_SERVER, MAIL_PORT, timeout=10) as server:
            server.starttls()
            server.login(MAIL_USERNAME, MAIL_PASSWORD)
            server.send_message(msg)
            print(f"Reset email sent to {recipient_email}")
    except Exception as e:
        print(f"Error sending reset email: {e}")



app = Flask(__name__)
app.secret_key = "inventory_secret"
app.config['PREFERRED_URL_SCHEME'] = 'https'

# MongoDB Connection
client = MongoClient("mongodb+srv://Rudra:Rudra123@cluster0.5zoihpi.mongodb.net/?appName=Cluster0")
db = client["smart_inventory"]
items_col = db["items"]
transactions_col = db["transactions"]
users_col = db["users"]


existing_user = users_col.find_one({"username": "admin"})

if not existing_user:
    hashed_password = generate_password_hash("admin123")
    users_col.insert_one({
        "username": "admin",
        "password": hashed_password,
        "email": "your-admin-email@gmail.com",
        "role": "admin"
    })
    print("Admin user created successfully.")



# Load ML Model
with open("stock_model.pkl", "rb") as f:
    model = pickle.load(f)

# Generate Unique RFID Tag

def generate_rfid():
    return str(uuid.uuid4())[:8]

# -------- HOME --------
@app.route("/")
def home():
    return redirect("/login")
# -------- USER LOGIN --------
@app.route("/login", methods=["GET","POST"])
def login():

    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        user = users_col.find_one({"username": username})

        from werkzeug.security import check_password_hash

        if user and check_password_hash(user["password"], password):

            session["user"] = username

            # store role in session
            if username == "admin":
                session["role"] = "admin"
            else:
                session["role"] = "user"

            return redirect("/dashboard")

        return render_template("login.html", error="Invalid credentials")

    return render_template("login.html")


# --- FORGOT PASSWORD: STEP 1 (Request) ---
@app.route("/forgot_password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form["email"]
        user = users_col.find_one({"email": email})
        
        if user:
            token = secrets.token_urlsafe(32)
            # Token expires in 30 minutes
            expiration = datetime.now() + timedelta(minutes=30)
            
            users_col.update_one(
                {"email": email},
                {"$set": {"reset_token": token, "token_expiry": expiration}}
            )
            
            # Send the Email
            reset_url = url_for('reset_password', token=token, _external=True)
            
            threading.Thread(target=send_reset_email, args=(email, reset_url)).start()
            
            return render_template("login.html", success_message="Reset link sent to your email!")
            
        return render_template("forgot_password.html", error="Email not found")
    return render_template("forgot_password.html")

# --- FORGOT PASSWORD: STEP 2 (Actual Reset) ---
@app.route("/reset_password/<token>", methods=["GET", "POST"])
def reset_password(token):
    user = users_col.find_one({
        "reset_token": token,
        "token_expiry": {"$gt": datetime.now()} # Must not be expired
    })
    
    if not user:
        return "Invalid or expired token", 400
    
    if request.method == "POST":
        new_password = generate_password_hash(request.form["password"])
        users_col.update_one(
            {"_id": user["_id"]},
            {"$set": {"password": new_password}, "$unset": {"reset_token": "", "token_expiry": ""}}
        )
        return redirect("/login?success=reset")
        
    return render_template("reset_password.html", token=token)

# -------- DASHBOARD --------
@app.route('/dashboard')
def dashboard():

    if 'user' not in session:
        return redirect(url_for('login'))

    search_query = request.args.get("search")

    # Filter items by logged in user
    query = {"owner": session["user"]}

    # Apply search if provided
    
    if search_query:
        query["name"] = {"$regex": search_query, "$options": "i"}

    items = list(items_col.find(query))
    transactions = list(transactions_col.find({"owner": session["user"]}))

    # Inventory Value
    total_value = sum(item.get("quantity", 0) * item.get("price", 0) for item in items)

    # Dashboard Stats
    total_products = len(items)
    total_stock = sum(item.get('quantity', 0) for item in items)
    low_stock_count = sum(1 for item in items if item.get('quantity', 0) <= 5)

    # Chart Data
    item_names = [item.get('name', '') for item in items]
    item_quantities = [item.get('quantity', 0) for item in items]

    # Transaction Trend
    from collections import Counter

    dates = []
    for t in transactions:
        if 'timestamp' in t:
            dates.append(t['timestamp'].strftime("%Y-%m-%d"))

    date_count = Counter(dates)

    trend_dates = list(date_count.keys())
    trend_counts = list(date_count.values())

    return render_template(
        'dashboard.html',
        items=items,
        total_value=total_value,
        search_query=search_query,
        total_products=total_products,
        total_stock=total_stock,
        low_stock_count=low_stock_count,
        item_names=item_names,
        item_quantities=item_quantities,
        trend_dates=trend_dates,
        trend_counts=trend_counts,
        username=session["user"]
    )

    

#--------- Create Account --------
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"]
        email = request.form["email"]
        password = request.form["password"]

        # 1. Check if Username OR Email already exists
        existing_user = users_col.find_one({
            "$or": [
                {"username": username},
                {"email": email}
            ]
        })

        if existing_user:
            msg = "Username or Email already registered!"
            return render_template("register.html", error_message=msg)

        # 2. If unique, create the user
        hashed = generate_password_hash(password)
        users_col.insert_one({
            "username": username,
            "email": email,
            "password": hashed
        })
        return redirect(url_for('login', success='registered'))

    return render_template("register.html")

#-------- ADMIN DASHBOARD --------
@app.route('/admin')
def admin():

    # check if user logged in
    if 'user' not in session:
        return redirect(url_for('login'))

    # allow only admin user
    if session.get('user') != "admin":
        return "Access Denied"

    users = list(users_col.find())

    total_users = users_col.count_documents({})
    total_products = items_col.count_documents({})
    total_transactions = transactions_col.count_documents({})

    user_data = []

    for u in users:

        product_count = items_col.count_documents({"owner": u["username"]})
        transaction_count = transactions_col.count_documents({"owner": u["username"]})

        user_data.append({
            "username": u["username"],
            "products": product_count,
            "transactions": transaction_count
        })

    return render_template(
        "admin.html",
        users=user_data,
        total_users=total_users,
        total_products=total_products,
        total_transactions=total_transactions
    )
# -------- ADD ITEM --------
@app.route("/add_item", methods=["POST"])
def add_item():

    name = request.form["name"]
    quantity = int(request.form["quantity"])
    price = float(request.form["price"])

    # Check if item already exists
    existing = items_col.find_one({
    "name": name,
    "owner": session["user"]
    })

    if existing:
        return redirect("/dashboard?error=duplicate")

    rfid_tag = generate_rfid()

    items_col.insert_one({
        "rfid_tag": rfid_tag,
        "name": name,
        "quantity": quantity,
        "price": price,
        "owner": session["user"]
    })

    return redirect("/dashboard?success=added")


# -------- SCAN ITEM (SIMULATED RFID) --------
@app.route('/scan/<rfid_tag>/<action>')
def scan_item(rfid_tag, action):
    item = items_col.find_one({"rfid_tag": rfid_tag})

    if item:
        new_quantity = item["quantity"]
        
        if action == "in":
            new_quantity += 1
            items_col.update_one({"rfid_tag": rfid_tag}, {"$set": {"quantity": new_quantity}})
            
        elif action == "out":
            if item["quantity"] > 0:
                new_quantity -= 1
                items_col.update_one({"rfid_tag": rfid_tag}, {"$set": {"quantity": new_quantity}})
                
                # TRIGGER EMAIL ALERT: If stock hits 5 or less
                if new_quantity <= 5:
                    # 1. Find the user who owns this item
                    owner_user = users_col.find_one({"username": item["owner"]})
                    
                    # 2. If they have an email saved, send the alert to THEM
                    if owner_user and "email" in owner_user:
                        print(f"DEBUG: Found email for {item['owner']}: {owner_user['email']}")
                        threading.Thread(
                            target=send_low_stock_alert,
                            args=(item["name"], new_quantity, owner_user["email"])
                        ).start()
                    else:
                        print(f"No email found for user {item['owner']}")

        transactions_col.insert_one({
            "rfid_tag": rfid_tag,
            "action": action,
            "item_name": item["name"],
            "timestamp": datetime.now(),
            "owner": session["user"]
        })

    # Redirect to dashboard with a 'scanned' success message for the Toast notification
    return redirect(url_for('dashboard', success='scanned'))

# -------- EDIT ITEM --------
@app.route("/edit_item/<id>", methods=["GET","POST"])
def edit_item(id):
    item = items_col.find_one({"_id": ObjectId(id)})

    if request.method == "POST":
        name = request.form["name"]
        quantity = int(request.form["quantity"])
        price = float(request.form["price"])

        items_col.update_one(
            {"_id": ObjectId(id)},
            {"$set":{
                "name": name,
                "quantity": quantity,
                "price": price
            }}
        )

        return redirect("/dashboard")

    return render_template("edit_item.html", item=item)

# -------- DELETE ITEM --------
@app.route("/delete_item/<id>")
def delete_item(id):

    items_col.delete_one({"_id": ObjectId(id)})

    return redirect("/dashboard")

# -------- EXPORT TO EXCEL --------
@app.route("/export")
def export():

    if 'user' not in session:
        return redirect(url_for('login'))

    import pandas as pd

    items = list(items_col.find({"owner": session["user"]}))

    # Remove MongoDB _id field
    for item in items:
        item.pop('_id', None)

    df = pd.DataFrame(items)

    file_path = "inventory.xlsx"

    df.to_excel(file_path, index=False)

    return send_file(file_path, as_attachment=True)

# -------- VIEW TRANSACTIONS --------
@app.route("/transactions")
def transactions():

    if 'user' not in session:
        return redirect(url_for('login'))

    transactions = list(transactions_col.find({"owner": session["user"]}).sort("timestamp", -1))

    return render_template(
        "transactions.html",
        transactions=transactions
    )    
# -------- ML PREDICTION --------
@app.route('/predict')
def predict():

    if 'user' not in session:
        return redirect(url_for('login'))

    items = list(items_col.find({"owner": session["user"]}))
    predictions = []

    for item in items:

        quantity = item.get("quantity", 0)

        try:
            result = model.predict([[quantity]])
            predicted_value = int(result[0])
        except:
            predicted_value = 0

        predictions.append({
            "name": item.get("name", "Unknown"),
            "rfid": item.get("rfid_tag", "N/A"),
            "quantity": quantity,
            "prediction": predicted_value
        })

    return render_template("prediction.html", predictions=predictions)
# -------- LOGOUT --------
@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect(url_for('login'))


# -------- SCANNER INTERFACE --------
@app.route("/scanner")
def scanner_view():
    if 'user' not in session:
        return redirect(url_for('login'))
    
    # Get all items so the user can "pick" one to simulate a scan
    items = list(items_col.find({"owner": session["user"]}))
    return render_template("scanner.html", items=items)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=True)
# No app.run() needed for Render (Gunicorn handles it)