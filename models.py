from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime

db = SQLAlchemy()

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    name = db.Column(db.String(150), nullable=True)
    phone = db.Column(db.String(30), nullable=True)
    date_of_birth = db.Column(db.String(30), nullable=True)
    gender = db.Column(db.String(30), nullable=True)
    location = db.Column(db.String(150), nullable=True)
    profile_photo = db.Column(db.Text, nullable=True)  # URL or base64 data
    hidden_folders = db.Column(db.Text, nullable=True)  # JSON list of built-in folders removed by the user
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, onupdate=datetime.utcnow)

    notes = db.relationship('Note', backref='author', lazy=True, cascade='all, delete-orphan')
    documents = db.relationship('Document', backref='author', lazy=True, cascade='all, delete-orphan')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Document(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    original_filename = db.Column(db.String(255), nullable=False)
    file_path = db.Column(db.String(500), nullable=True)
    file_size = db.Column(db.Integer, nullable=True)
    file_type = db.Column(db.String(100), nullable=True)
    folder = db.Column(db.String(50), nullable=False)  # built-in folder or a user-created folder name
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, onupdate=datetime.utcnow)


class Note(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    title = db.Column(db.String(255), nullable=True)
    content = db.Column(db.Text, nullable=False)  # stores ENCRYPTED text
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, onupdate=datetime.utcnow)


class ShareLink(db.Model):
    """Expiring public link to one document.

    ``token`` stores a SHA-256 hash of the secret token that appears in the URL.
    The plaintext token is only ever returned once, at creation time, so a
    database leak cannot reveal working links.
    """
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    document_id = db.Column(db.Integer, db.ForeignKey('document.id'), nullable=True)
    token = db.Column(db.String(64), unique=True, nullable=False)  # sha256 hex digest
    max_uses = db.Column(db.Integer, nullable=True)  # None = unlimited downloads
    use_count = db.Column(db.Integer, nullable=False, default=0, server_default='0')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=True)


class SecurityLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    login_id = db.Column(db.String(150))
    ip_address = db.Column(db.String(45))  # 45 chars covers IPv6
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(20))       # 'SUCCESS' or 'FAILED'
    user_agent = db.Column(db.String(255))
    alert_sent = db.Column(db.Boolean, default=False)
