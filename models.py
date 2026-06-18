from extensions import db
from datetime import datetime



class DayOrderOverride(db.Model):
    __tablename__ = "day_order_overrides"

    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, unique=True, nullable=False)
    day_order = db.Column(db.Integer, nullable=False)
    reason = db.Column(db.String(255), nullable=True)
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
    Stores timetable periods per department, year, day_order, and period number.
    department: e.g. "B.Sc AI", "B.Com", "B.Sc CS"
    year: 1, 2, or 3 (or more)
    day_order: 1–6
    period: 1–N (default 5)
    subject: e.g. "Python Theory (JM)"
    """
    __tablename__ = "timetable_entries"

    id = db.Column(db.Integer, primary_key=True)
    department = db.Column(db.String(100), nullable=False, default="B.Sc AI")
    year = db.Column(db.Integer, nullable=False)
    day_order = db.Column(db.Integer, nullable=False)
    period = db.Column(db.Integer, nullable=False)
    subject = db.Column(db.String(255), nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('department', 'year', 'day_order', 'period', name='uq_timetable_slot'),
    )

    def __repr__(self):
        return f"<TimetableEntry {self.department} Y{self.year} DO{self.day_order} P{self.period}: {self.subject}>"

    def to_dict(self):
        return {
            "id": self.id,
            "department": self.department,
            "year": self.year,
            "day_order": self.day_order,
            "period": self.period,
            "subject": self.subject,
        }


class Announcement(db.Model):
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
