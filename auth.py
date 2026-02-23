from flask import Blueprint, request, jsonify
from extensions import db, bcrypt
from models import Student
from flask_jwt_extended import create_access_token
from datetime import timedelta

auth_bp = Blueprint("auth", __name__)


# ─────────────────────────────────────────────
# Register Route
# ─────────────────────────────────────────────
@auth_bp.route("/register", methods=["POST"])
def register():

    data = request.get_json()

    name = data.get("name")
    register_number = data.get("register_number")
    department = data.get("department")
    year = data.get("year")
    password = data.get("password")

    # Basic validation
    if not all([name, register_number, department, year, password]):
        return jsonify({"error": "All fields are required"}), 400

    # Check if user already exists
    existing_user = Student.query.filter_by(register_number=register_number).first()
    if existing_user:
        return jsonify({"error": "Register number already exists"}), 400

    # Hash password
    hashed_password = bcrypt.generate_password_hash(password).decode("utf-8")

    # Create student
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
# Login Route
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

    # Create JWT (valid for 1 day)
    access_token = create_access_token(
        identity=str(student.id),
        expires_delta=timedelta(days=1)
    )

    return jsonify({
        "access_token": access_token,
        "name": student.name,
        "department": student.department,
        "year": student.year
    }), 200