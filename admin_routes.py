"""Admin API — JWT role `admin` required for all routes."""
from __future__ import annotations

from datetime import datetime, date
from functools import wraps

from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import get_jwt, jwt_required
from sqlalchemy import or_

from extensions import db, bcrypt
from models import CalendarDay, Semester, Student, TimetableDocument

admin_bp = Blueprint("admin_api", __name__)


def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if get_jwt().get("role") != "admin":
            return jsonify({"error": "Admin only"}), 403
        return fn(*args, **kwargs)

    return wrapper


def _parse_iso_date(s: str) -> date | None:
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


@admin_bp.route("/stats", methods=["GET"])
@jwt_required()
@admin_required
def stats():
    return jsonify({
        "students": Student.query.count(),
        "calendar_days": CalendarDay.query.count(),
        "semesters": Semester.query.count(),
        "timetable_chars": (
            len(doc.body)
            if (doc := TimetableDocument.query.filter_by(slug="timetable").first()) and doc.body
            else 0
        ),
    }), 200


@admin_bp.route("/students", methods=["GET"])
@jwt_required()
@admin_required
def list_students():
    q = (request.args.get("q") or "").strip().lower()
    query = Student.query.order_by(Student.id.desc())
    if q:
        query = query.filter(
            or_(
                Student.register_number.ilike(f"%{q}%"),
                Student.name.ilike(f"%{q}%"),
                Student.department.ilike(f"%{q}%"),
            )
        )
    students = query.limit(500).all()
    return jsonify([
        {
            "id": s.id,
            "name": s.name,
            "register_number": s.register_number,
            "department": s.department,
            "year": s.year,
            "is_active": getattr(s, "is_active", True),
        }
        for s in students
    ]), 200


@admin_bp.route("/students/<int:sid>", methods=["DELETE"])
@jwt_required()
@admin_required
def delete_student(sid):
    s = db.session.get(Student, sid)
    if not s:
        return jsonify({"error": "Not found"}), 404
    db.session.delete(s)
    db.session.commit()
    return jsonify({"message": "Deleted"}), 200


@admin_bp.route("/students/<int:sid>", methods=["PATCH"])
@jwt_required()
@admin_required
def patch_student(sid):
    s = db.session.get(Student, sid)
    if not s:
        return jsonify({"error": "Not found"}), 404
    data = request.get_json() or {}
    if "is_active" in data:
        s.is_active = bool(data["is_active"])
    db.session.commit()
    return jsonify({"id": s.id, "is_active": s.is_active}), 200


@admin_bp.route("/students/<int:sid>/reset-password", methods=["POST"])
@jwt_required()
@admin_required
def reset_student_password(sid):
    s = db.session.get(Student, sid)
    if not s:
        return jsonify({"error": "Not found"}), 404
    data = request.get_json() or {}
    new_password = data.get("password") or ""
    if len(new_password) < 6:
        return jsonify({"error": "Password must be at least 6 characters"}), 400
    s.password_hash = bcrypt.generate_password_hash(new_password).decode("utf-8")
    db.session.commit()
    return jsonify({"message": "Password updated"}), 200


@admin_bp.route("/semesters", methods=["GET"])
@jwt_required()
@admin_required
def list_semesters():
    rows = Semester.query.order_by(Semester.start_date.desc()).all()
    return jsonify([
        {
            "id": r.id,
            "name": r.name,
            "start_date": r.start_date.isoformat(),
            "start_day_order": r.start_day_order,
            "total_day_orders": r.total_day_orders,
        }
        for r in rows
    ]), 200


@admin_bp.route("/semesters", methods=["POST"])
@jwt_required()
@admin_required
def create_semester():
    data = request.get_json() or {}
    name = (data.get("name") or "").strip()
    try:
        start_date = date.fromisoformat(data.get("start_date") or "")
    except ValueError:
        return jsonify({"error": "Invalid start_date (use YYYY-MM-DD)"}), 400
    try:
        start_day_order = int(data.get("start_day_order"))
        total_day_orders = int(data.get("total_day_orders", 6))
    except (TypeError, ValueError):
        return jsonify({"error": "start_day_order and total_day_orders must be integers"}), 400
    if not name:
        return jsonify({"error": "name required"}), 400
    row = Semester(
        name=name,
        start_date=start_date,
        start_day_order=start_day_order,
        total_day_orders=total_day_orders,
    )
    db.session.add(row)
    db.session.commit()
    return jsonify({"id": row.id}), 201


@admin_bp.route("/semesters/<int:eid>", methods=["PATCH"])
@jwt_required()
@admin_required
def patch_semester(eid):
    row = db.session.get(Semester, eid)
    if not row:
        return jsonify({"error": "Not found"}), 404
    data = request.get_json() or {}
    if "name" in data:
        row.name = (data["name"] or "").strip() or row.name
    if "start_date" in data:
        try:
            row.start_date = date.fromisoformat(data["start_date"])
        except ValueError:
            return jsonify({"error": "Invalid start_date"}), 400
    if "start_day_order" in data:
        row.start_day_order = int(data["start_day_order"])
    if "total_day_orders" in data:
        row.total_day_orders = int(data["total_day_orders"])
    db.session.commit()
    return jsonify({"id": row.id}), 200


@admin_bp.route("/semesters/<int:eid>", methods=["DELETE"])
@jwt_required()
@admin_required
def delete_semester(eid):
    row = db.session.get(Semester, eid)
    if not row:
        return jsonify({"error": "Not found"}), 404
    db.session.delete(row)
    db.session.commit()
    return jsonify({"message": "Deleted"}), 200


@admin_bp.route("/calendar", methods=["GET"])
@jwt_required()
@admin_required
def list_calendar():
    d = request.args.get("from")
    d2 = request.args.get("to")
    if not d or not d2:
        return jsonify({"error": "Query params from and to (YYYY-MM-DD) required"}), 400
    df = _parse_iso_date(d)
    dt = _parse_iso_date(d2)
    if not df or not dt or df > dt:
        return jsonify({"error": "Invalid date range"}), 400
    rows = (
        CalendarDay.query.filter(CalendarDay.entry_date >= df, CalendarDay.entry_date <= dt)
        .order_by(CalendarDay.entry_date)
        .all()
    )
    return jsonify([
        {
            "entry_date": r.entry_date.isoformat(),
            "day_order": r.day_order,
            "is_holiday": r.is_holiday,
            "note": r.note,
        }
        for r in rows
    ]), 200


@admin_bp.route("/calendar/<iso_date>", methods=["PUT"])
@jwt_required()
@admin_required
def put_calendar_day(iso_date):
    d = _parse_iso_date(iso_date)
    if not d:
        return jsonify({"error": "Invalid date URL"}), 400
    data = request.get_json() or {}
    is_holiday = bool(data.get("is_holiday", False))
    day_order = data.get("day_order")
    if day_order is not None:
        try:
            day_order = int(day_order)
        except (TypeError, ValueError):
            return jsonify({"error": "day_order must be integer or null"}), 400
        if not is_holiday and (day_order < 1 or day_order > 6):
            return jsonify({"error": "day_order must be 1–6 when not holiday"}), 400
    if is_holiday:
        day_order = None
    elif day_order is None:
        return jsonify({"error": "day_order required (1–6) when is_holiday is false"}), 400

    row = db.session.get(CalendarDay, d)
    if not row:
        row = CalendarDay(entry_date=d)
        db.session.add(row)
    row.is_holiday = is_holiday
    row.day_order = None if is_holiday else day_order
    row.note = (data.get("note") or None) or None
    db.session.commit()

    _run_invalidate_caches()

    return jsonify({
        "entry_date": row.entry_date.isoformat(),
        "day_order": row.day_order,
        "is_holiday": row.is_holiday,
        "note": row.note,
    }), 200


@admin_bp.route("/calendar/bulk", methods=["POST"])
@jwt_required()
@admin_required
def bulk_calendar():
    data = request.get_json() or {}
    entries = data.get("entries") or []
    if not isinstance(entries, list):
        return jsonify({"error": "entries must be a list"}), 400
    try:
        for e in entries:
            d = _parse_iso_date(e.get("date") or e.get("entry_date") or "")
            if not d:
                continue
            is_holiday = bool(e.get("is_holiday", False))
            do = e.get("day_order")
            if do is not None:
                do = int(do)
            if is_holiday:
                do = None
            elif do is not None and (do < 1 or do > 6):
                raise ValueError("day_order 1–6")
            row = db.session.get(CalendarDay, d)
            if not row:
                row = CalendarDay(entry_date=d)
                db.session.add(row)
            row.is_holiday = is_holiday
            row.day_order = do
            row.note = (e.get("note") or None) or None
        db.session.commit()
    except ValueError as ex:
        db.session.rollback()
        return jsonify({"error": str(ex)}), 400

    _run_invalidate_caches()
    return jsonify({"message": f"Upserted {len(entries)} rows"}), 200


@admin_bp.route("/calendar/<iso_date>", methods=["DELETE"])
@jwt_required()
@admin_required
def delete_calendar_day(iso_date):
    d = _parse_iso_date(iso_date)
    if not d:
        return jsonify({"error": "Invalid date"}), 400
    row = db.session.get(CalendarDay, d)
    if row:
        db.session.delete(row)
        db.session.commit()
    _run_invalidate_caches()
    return jsonify({"message": "OK"}), 200


@admin_bp.route("/timetable", methods=["GET"])
@jwt_required()
@admin_required
def get_timetable_doc():
    doc = TimetableDocument.query.filter_by(slug="timetable").first()
    if not doc:
        return jsonify({"slug": "timetable", "body": ""}), 200
    return jsonify({"slug": doc.slug, "body": doc.body, "updated_at": doc.updated_at.isoformat() if doc.updated_at else None}), 200


@admin_bp.route("/timetable", methods=["PUT"])
@jwt_required()
@admin_required
def put_timetable_doc():
    data = request.get_json() or {}
    body = data.get("body")
    if body is None:
        return jsonify({"error": "body required"}), 400
    if not isinstance(body, str):
        return jsonify({"error": "body must be string"}), 400

    doc = TimetableDocument.query.filter_by(slug="timetable").first()
    if not doc:
        doc = TimetableDocument(slug="timetable", body=body)
        db.session.add(doc)
    else:
        doc.body = body
        doc.updated_at = datetime.utcnow()
    db.session.commit()

    _run_invalidate_caches()
    return jsonify({"slug": "timetable", "updated_at": doc.updated_at.isoformat()}), 200


def _run_invalidate_caches():
    fn = current_app.extensions.get("invalidate_content_caches")
    if fn:
        fn()
