from datetime import datetime

from sqlalchemy import text

from extensions import db


class Student(db.Model):
    __tablename__ = "students"

    id = db.Column(db.Integer, primary_key=True)

    name = db.Column(db.String(100), nullable=False)
    register_number = db.Column(db.String(50), unique=True, nullable=False)

    department = db.Column(db.String(100), nullable=False)
    year = db.Column(db.Integer, nullable=False)

    password_hash = db.Column(db.String(255), nullable=False)

    # Existing Postgres DBs: run:
    # ALTER TABLE students ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE NOT NULL;
    is_active = db.Column(
        db.Boolean,
        nullable=False,
        server_default=text("true"),
        default=True,
    )

    def __repr__(self):
        return f"<Student {self.register_number}>"


class Admin(db.Model):
    __tablename__ = "admins"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    is_active = db.Column(
        db.Boolean,
        nullable=False,
        server_default=text("true"),
        default=True,
    )
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def __repr__(self):
        return f"<Admin {self.username}>"


class CalendarDay(db.Model):
    """Overrides / extends day-order calendar from rr.txt for a given date."""

    __tablename__ = "calendar_days"

    entry_date = db.Column(db.Date, primary_key=True)
    day_order = db.Column(db.Integer, nullable=True)  # 1–6 when not holiday
    is_holiday = db.Column(
        db.Boolean,
        nullable=False,
        server_default=text("false"),
        default=False,
    )
    note = db.Column(db.String(255), nullable=True)

    def __repr__(self):
        return f"<CalendarDay {self.entry_date}>"


class TimetableDocument(db.Model):
    """Persisted timetable / knowledge text (replaces ephemeral rr.txt edits on Render)."""

    __tablename__ = "timetable_documents"

    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(64), unique=True, nullable=False, default="timetable")
    body = db.Column(db.Text, nullable=False, default="")
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<TimetableDocument {self.slug}>"


class Semester(db.Model):
    __tablename__ = "semesters"

    id = db.Column(db.Integer, primary_key=True)

    name = db.Column(db.String(100), nullable=False)

    start_date = db.Column(db.Date, nullable=False)

    start_day_order = db.Column(db.Integer, nullable=False)

    total_day_orders = db.Column(db.Integer, nullable=False, default=6)

    def __repr__(self):
        return f"<Semester {self.name}>"
