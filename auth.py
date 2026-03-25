from flask import Blueprint, request, jsonify
from extensions import db, bcrypt
from models import Student, Admin
from flask_jwt_extended import create_access_token
from datetime import timedelta
import os

auth_bp = Blueprint("auth", __name__)

# ─────────────────────────────────────────────
# Student Register
# ─────────────────────────────────────────────
@auth_bp.route("/register", methods=["POST"])
def register():
    data = request.get_json()

    name = data.get("name")
    register_number = data.get("register_number")
    department = data.get("department")
    year = data.get("year")
    password = data.get("password")

    if not all([name, register_number, department, year, password]):
        return jsonify({"error": "All fields are required"}), 400

    existing_user = Student.query.filter_by(register_number=register_number).first()
    if existing_user:
        return jsonify({"error": "Register number already exists"}), 400

    hashed_password = bcrypt.generate_password_hash(password).decode("utf-8")

    new_student = Student(
        name=name,
        register_number=register_number,
        department=department,
        year=int(year),
        password_hash=hashed_password,
    )

    db.session.add(new_student)
    db.session.commit()

    return jsonify({"message": "User registered successfully"}), 201


# ─────────────────────────────────────────────
# Student Login
# ─────────────────────────────────────────────
@auth_bp.route("/login", methods=["POST"])
def login():
    data = request.get_json()

    register_number = data.get("register_number")
    password = data.get("password")

    if not register_number or not password:
        return jsonify({"error": "Register number and password required"}), 400

    student = Student.query.filter_by(register_number=register_number).first()

    if not student:
        return jsonify({"error": "Invalid credentials"}), 401

    if not bcrypt.check_password_hash(student.password_hash, password):
        return jsonify({"error": "Invalid credentials"}), 401

    access_token = create_access_token(
        identity=str(student.id),
        additional_claims={"role": "student"},
        expires_delta=timedelta(days=1)
    )

    return jsonify({
        "access_token": access_token,
        "role": "student",
        "name": student.name,
        "department": student.department,
        "year": student.year
    }), 200


# ─────────────────────────────────────────────
# Admin Register  (requires secret key)
# ─────────────────────────────────────────────
@auth_bp.route("/admin/register", methods=["POST"])
def admin_register():
    data = request.get_json()

    username = data.get("username", "").strip()
    password = data.get("password", "").strip()
    secret_key = data.get("secret_key", "").strip()

    if not all([username, password, secret_key]):
        return jsonify({"error": "username, password and secret_key are required"}), 400

    # Compare against env variable ADMIN_SECRET_KEY
    expected_key = os.getenv("ADMIN_SECRET_KEY", "")
    if not expected_key:
        return jsonify({"error": "Admin registration is not configured on this server"}), 503

    if secret_key != expected_key:
        return jsonify({"error": "Invalid secret key"}), 403

    if Admin.query.filter_by(username=username).first():
        return jsonify({"error": "Admin username already exists"}), 400

    hashed = bcrypt.generate_password_hash(password).decode("utf-8")
    admin = Admin(username=username, password_hash=hashed)
    db.session.add(admin)
    db.session.commit()

    return jsonify({"message": f"Admin '{username}' registered successfully"}), 201


# ─────────────────────────────────────────────
# Admin Login
# ─────────────────────────────────────────────
@auth_bp.route("/admin/login", methods=["POST"])
def admin_login():
    data = request.get_json()

    username = data.get("username", "").strip()
    password = data.get("password", "").strip()

    if not username or not password:
        return jsonify({"error": "Username and password required"}), 400

    admin = Admin.query.filter_by(username=username).first()

    if not admin or not bcrypt.check_password_hash(admin.password_hash, password):
        return jsonify({"error": "Invalid admin credentials"}), 401

    access_token = create_access_token(
        identity=str(admin.id),
        additional_claims={"role": "admin"},
        expires_delta=timedelta(days=1)
    )

    return jsonify({
        "access_token": access_token,
        "role": "admin",
        "username": admin.username,
    }), 200
