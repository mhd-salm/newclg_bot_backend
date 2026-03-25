from flask import Blueprint, request, jsonify
from extensions import db, bcrypt
from models import Student, Admin
from flask_jwt_extended import create_access_token
from datetime import timedelta

auth_bp = Blueprint("auth", __name__)


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

    if not getattr(student, "is_active", True):
        return jsonify({"error": "Account is disabled."}), 403

    if not bcrypt.check_password_hash(student.password_hash, password):
        return jsonify({"error": "Invalid credentials"}), 401

    access_token = create_access_token(
        identity=str(student.id),
        additional_claims={"role": "student"},
        expires_delta=timedelta(days=1),
    )

    return jsonify({
        "access_token": access_token,
        "role": "student",
        "name": student.name,
        "department": student.department,
        "year": student.year,
    }), 200


@auth_bp.route("/admin/login", methods=["POST"])
def admin_login():
    data = request.get_json() or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    if not username or not password:
        return jsonify({"error": "Username and password required"}), 400

    admin = Admin.query.filter_by(username=username).first()
    if not admin or not admin.is_active:
        return jsonify({"error": "Invalid credentials"}), 401

    if not bcrypt.check_password_hash(admin.password_hash, password):
        return jsonify({"error": "Invalid credentials"}), 401

    access_token = create_access_token(
        identity=str(admin.id),
        additional_claims={"role": "admin"},
        expires_delta=timedelta(hours=12),
    )

    return jsonify({
        "access_token": access_token,
        "role": "admin",
        "username": admin.username,
    }), 200
