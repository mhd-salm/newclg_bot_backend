"""
admin.py — API blueprint for day orders, timetables, and announcements.

Routes:

  GET    /admin/day-orders                – list all overrides
  POST   /admin/day-orders                – create / update an override
  DELETE /admin/day-orders/<id>           – remove an override

  GET    /admin/timetable                 – full timetable (all depts, years, day orders)
  GET    /admin/timetable/departments     – list distinct departments
  POST   /admin/timetable                 – upsert a single period slot
  DELETE /admin/timetable/<id>            – remove a slot
  DELETE /admin/timetable/department/<name> – remove ALL entries for a department

  GET    /admin/announcements             – list all announcements
  POST   /admin/announcements             – create announcement
  PUT    /admin/announcements/<id>        – update announcement
  DELETE /admin/announcements/<id>        – delete announcement
  GET    /admin/announcements/active      – active announcements
"""

from flask import Blueprint, request, jsonify
from extensions import db
from models import DayOrderOverride, TimetableEntry, Announcement
from datetime import date as date_type
import datetime

admin_bp = Blueprint("admin", __name__)


# ── Auth guard ────────────────────────────────────────────────────────────────

# ══════════════════════════════════════════════════════════════════════════════
#  DAY ORDER OVERRIDES
# ══════════════════════════════════════════════════════════════════════════════

@admin_bp.route("/day-orders", methods=["GET"])
def list_day_orders():
    overrides = DayOrderOverride.query.order_by(DayOrderOverride.date).all()
    return jsonify([o.to_dict() for o in overrides]), 200


@admin_bp.route("/day-orders", methods=["POST"])
def upsert_day_order():
    data = request.get_json() or {}
    date_str  = data.get("date", "").strip()
    day_order = data.get("day_order")
    reason    = data.get("reason", "").strip()

    if not date_str:
        return jsonify({"error": "date is required (YYYY-MM-DD)"}), 400
    if day_order is None:
        return jsonify({"error": "day_order is required (0=holiday, 1-6)"}), 400

    try:
        parsed_date = date_type.fromisoformat(date_str)
    except ValueError:
        return jsonify({"error": "Invalid date format. Use YYYY-MM-DD"}), 400

    day_order = int(day_order)
    if day_order < 0 or day_order > 6:
        return jsonify({"error": "day_order must be 0 (holiday) or 1–6"}), 400

    existing = DayOrderOverride.query.filter_by(date=parsed_date).first()
    if existing:
        existing.day_order = day_order
        existing.reason    = reason
        existing.updated_at = datetime.datetime.utcnow()
    else:
        existing = DayOrderOverride(date=parsed_date, day_order=day_order, reason=reason)
        db.session.add(existing)

    db.session.commit()
    return jsonify(existing.to_dict()), 200


@admin_bp.route("/day-orders/<int:override_id>", methods=["DELETE"])
def delete_day_order(override_id):
    override = db.session.get(DayOrderOverride, override_id)
    if not override:
        return jsonify({"error": "Override not found"}), 404
    db.session.delete(override)
    db.session.commit()
    return jsonify({"message": "Override deleted"}), 200


# ══════════════════════════════════════════════════════════════════════════════
#  TIMETABLE
# ══════════════════════════════════════════════════════════════════════════════

@admin_bp.route("/timetable/departments", methods=["GET"])
def list_departments():
    """Return sorted list of distinct departments that have timetable entries."""
    rows = db.session.query(TimetableEntry.department).distinct().order_by(TimetableEntry.department).all()
    return jsonify([r[0] for r in rows]), 200


@admin_bp.route("/timetable", methods=["GET"])
def get_timetable():
    dept      = request.args.get("department")
    year      = request.args.get("year")
    day_order = request.args.get("day_order")

    q = TimetableEntry.query
    if dept:      q = q.filter_by(department=dept)
    if year:      q = q.filter_by(year=int(year))
    if day_order: q = q.filter_by(day_order=int(day_order))

    entries = q.order_by(
        TimetableEntry.department,
        TimetableEntry.year,
        TimetableEntry.day_order,
        TimetableEntry.period
    ).all()
    return jsonify([e.to_dict() for e in entries]), 200


@admin_bp.route("/timetable", methods=["POST"])
def upsert_timetable():
    data       = request.get_json() or {}
    department = (data.get("department") or "").strip()
    year       = data.get("year")
    day_order  = data.get("day_order")
    period     = data.get("period")
    subject    = (data.get("subject") or "").strip()

    if not all([department, year, day_order, period]):
        return jsonify({"error": "department, year, day_order, period are required"}), 400

    year, day_order, period = int(year), int(day_order), int(period)

    if day_order < 1 or day_order > 6:
        return jsonify({"error": "day_order must be 1–6"}), 400
    if period < 1:
        return jsonify({"error": "period must be >= 1"}), 400

    entry = TimetableEntry.query.filter_by(
        department=department, year=year, day_order=day_order, period=period
    ).first()

    if entry:
        entry.subject    = subject
        entry.updated_at = datetime.datetime.utcnow()
    else:
        entry = TimetableEntry(
            department=department, year=year,
            day_order=day_order, period=period, subject=subject
        )
        db.session.add(entry)

    db.session.commit()
    return jsonify(entry.to_dict()), 200


@admin_bp.route("/timetable/<int:entry_id>", methods=["DELETE"])
def delete_timetable_entry(entry_id):
    entry = db.session.get(TimetableEntry, entry_id)
    if not entry:
        return jsonify({"error": "Entry not found"}), 404
    db.session.delete(entry)
    db.session.commit()
    return jsonify({"message": "Entry deleted"}), 200


@admin_bp.route("/timetable/department/<path:dept_name>", methods=["DELETE"])
def delete_department(dept_name):
    """Delete ALL timetable entries for a department."""
    deleted = TimetableEntry.query.filter_by(department=dept_name).delete()
    db.session.commit()
    return jsonify({"message": f"Deleted {deleted} entries for {dept_name}"}), 200


# ══════════════════════════════════════════════════════════════════════════════
#  ANNOUNCEMENTS
# ══════════════════════════════════════════════════════════════════════════════

@admin_bp.route("/announcements/active", methods=["GET"])
def active_announcements():
    items = Announcement.query.filter_by(active=True).order_by(
        Announcement.created_at.desc()
    ).all()
    return jsonify([a.to_dict() for a in items]), 200


@admin_bp.route("/announcements", methods=["GET"])
def list_announcements():
    items = Announcement.query.order_by(Announcement.created_at.desc()).all()
    return jsonify([a.to_dict() for a in items]), 200


@admin_bp.route("/announcements", methods=["POST"])
def create_announcement():
    data   = request.get_json() or {}
    title  = (data.get("title") or "").strip()
    body   = (data.get("body") or "").strip()
    active = bool(data.get("active", True))

    if not title or not body:
        return jsonify({"error": "title and body are required"}), 400

    ann = Announcement(title=title, body=body, active=active)
    db.session.add(ann)
    db.session.commit()
    return jsonify(ann.to_dict()), 201


@admin_bp.route("/announcements/<int:ann_id>", methods=["PUT"])
def update_announcement(ann_id):
    ann = db.session.get(Announcement, ann_id)
    if not ann:
        return jsonify({"error": "Announcement not found"}), 404

    data = request.get_json() or {}
    if "title"  in data: ann.title  = (data["title"] or "").strip()
    if "body"   in data: ann.body   = (data["body"] or "").strip()
    if "active" in data: ann.active = bool(data["active"])
    ann.updated_at = datetime.datetime.utcnow()

    db.session.commit()
    return jsonify(ann.to_dict()), 200


@admin_bp.route("/announcements/<int:ann_id>", methods=["DELETE"])
def delete_announcement(ann_id):
    ann = db.session.get(Announcement, ann_id)
    if not ann:
        return jsonify({"error": "Announcement not found"}), 404

    db.session.delete(ann)
    db.session.commit()
    return jsonify({"message": "Announcement deleted"}), 200
