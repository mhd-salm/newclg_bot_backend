from extensions import db


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