from extensions import db
from datetime import datetime


class Student(db.Model):
    __tablename__ = "students"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    register_number = db.Column(db.String(50), unique=True, nullable=False)
    department = db.Column(db.String(100), nullable=False)
    year = db.Column(db.Integer, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)

    def __repr__(self):
        return f"<Student {self.register_number}>"


class Admin(db.Model):
    __tablename__ = "admins"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<Admin {self.username}>"


class DayOrderOverride(db.Model):
    """
    Overrides the day order for a specific calendar date.
    day_order = 0  → treat as holiday (no classes)
    day_order = 1–6 → use this day order instead of the default from rr.txt
    """
    __tablename__ = "day_order_overrides"

    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, unique=True, nullable=False)          # e.g. 2026-03-25
    day_order = db.Column(db.Integer, nullable=False)               # 0 = holiday, 1-6
    reason = db.Column(db.String(255), nullable=True)               # e.g. "Unexpected holiday"
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<DayOrderOverride {self.date} → {self.day_order}>"

    def to_dict(self):
        return {
            "id": self.id,
            "date": self.date.isoformat(),
            "day_order": self.day_order,
            "reason": self.reason or "",
            "created_at": self.created_at.isoformat(),
        }


class TimetableEntry(db.Model):
    """
    Stores timetable periods per year, day_order, and period number.
    year: 1, 2, or 3
    day_order: 1–6
    period: 1–5
    subject: e.g. "Python Theory (JM)"
    """
    __tablename__ = "timetable_entries"

    id = db.Column(db.Integer, primary_key=True)
    year = db.Column(db.Integer, nullable=False)          # 1, 2, 3
    day_order = db.Column(db.Integer, nullable=False)     # 1–6
    period = db.Column(db.Integer, nullable=False)        # 1–5
    subject = db.Column(db.String(255), nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('year', 'day_order', 'period', name='uq_timetable_slot'),
    )

    def __repr__(self):
        return f"<TimetableEntry Y{self.year} DO{self.day_order} P{self.period}: {self.subject}>"

    def to_dict(self):
        return {
            "id": self.id,
            "year": self.year,
            "day_order": self.day_order,
            "period": self.period,
            "subject": self.subject,
        }


class Announcement(db.Model):
    """
    Admin-managed announcements shown to students on login / chatbot welcome.
    """
    __tablename__ = "announcements"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    body = db.Column(db.Text, nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<Announcement {self.id}: {self.title}>"

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "body": self.body,
            "active": self.active,
            "created_at": self.created_at.isoformat(),
        }


class Semester(db.Model):
    __tablename__ = "semesters"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    start_day_order = db.Column(db.Integer, nullable=False)
    total_day_orders = db.Column(db.Integer, nullable=False, default=6)

    def __repr__(self):
        return f"<Semester {self.name}>"
