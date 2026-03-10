from flask import Flask, render_template, request, redirect, url_for, session
from pymongo import MongoClient
from datetime import datetime
import uuid
import pickle
import numpy as np
import os
from dotenv import load_dotenv
from flask import session
from werkzeug.security import generate_password_hash, check_password_hash
from pymongo.errors import DuplicateKeyError
from bson.objectid import ObjectId
from flask import send_file

load_dotenv()

app = Flask(__name__)
app.secret_key = "inventory_secret"

# MongoDB Connection
client = MongoClient("mongodb+srv://Rudra:Rudra123@cluster0.5zoihpi.mongodb.net/?appName=Cluster0")
db = client["smart_inventory"]
items_col = db["items"]
transactions_col = db["transactions"]
users_col = db["users"]



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
            return redirect("/dashboard")

        return render_template("login.html", error="Invalid credentials")

    return render_template("login.html")
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
@app.route("/register", methods=["GET","POST"])
def register():

    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        from werkzeug.security import generate_password_hash
        hashed = generate_password_hash(password)

        existing = users_col.find_one({"username": username})

        if existing:
            return render_template("register.html", error_message="User already exists")

        users_col.insert_one({
            "username": username,
            "password": hashed
        })

        return redirect("/login")

    return render_template("register.html")
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

    return redirect("/dashboard")


# -------- SCAN ITEM (SIMULATED RFID) --------
@app.route('/scan/<rfid_tag>/<action>')
def scan_item(rfid_tag, action):
    item = items_col.find_one({"rfid_tag": rfid_tag})

    if item:
        if action == "in":
            items_col.update_one({"rfid_tag": rfid_tag}, {"$inc": {"quantity": 1}})
        elif action == "out":
            if item["quantity"] > 0:
                items_col.update_one({"rfid_tag": rfid_tag}, {"$inc": {"quantity": -1}})

        transactions_col.insert_one({
            "rfid_tag": rfid_tag,
            "action": action,
            "item_name": item["name"],
            "timestamp": datetime.now(),
            "owner": session["user"]
        })

    return redirect(url_for('dashboard'))

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

    transactions = list(transactions_col.find({"owner": session["user"]}))

    return render_template(
        "transactions.html",
        transactions=transactions
    )    
# -------- ML PREDICTION --------
@app.route('/predict')
def predict():
    items = list(items_col.find({"owner": session["user"]}))
    predictions = []

    for item in items:
        pred = model.predict(np.array([[item['quantity']]]))[0]
        predictions.append({
            "name": item['name'],
            "rfid": item['rfid_tag'],
            "predicted_weekly_sales": int(pred)
        })

    return render_template('predictions.html', predictions=predictions)


# -------- LOGOUT --------
@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect(url_for('login'))


if __name__ == "__main__":

    # Ensure users collection exists and admin user exists
    existing_user = users_col.find_one({"username": "admin"})

    if not existing_user:
        from werkzeug.security import generate_password_hash
        hashed_password = generate_password_hash("admin123")
        users_col.insert_one({
            "username": "admin",
            "password": hashed_password
        })

    app.run(host="0.0.0.0", port=10000)