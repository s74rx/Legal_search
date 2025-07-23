from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

class Citation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    journal = db.Column(db.String(100), nullable=False)
    parties = db.Column(db.String(200), nullable=False)
    court = db.Column(db.String(100), nullable=False)
    date_of_judgement = db.Column(db.Date, nullable=False)
    sections = db.Column(db.String(500))
    description = db.Column(db.Text, nullable=False)
    keywords = db.Column(db.String(500))
    pdf_path = db.Column(db.String(200))
    date_added = db.Column(db.DateTime, server_default=db.func.now())

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(120), nullable=False)
    role = db.Column(db.String(80), nullable=False, default='user')