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
    contacts = db.relationship('Contact', backref='owner', lazy=True, cascade='all, delete-orphan')
    voice_context = db.relationship(
        'VoiceContext', backref='owner', uselist=False,
        lazy=True, cascade='all, delete-orphan'
    )

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

    # --- Trash (30-day recycle bin) ---
    # deleted_at is NULL for live files. A trashed file has no live file_path;
    # its bytes live in trash_path as a lossless gzip stream and the original
    # size/checksum are kept so a restore can be verified byte for byte.
    deleted_at = db.Column(db.DateTime, nullable=True)
    trash_path = db.Column(db.String(500), nullable=True)
    trash_size = db.Column(db.Integer, nullable=True)          # compressed size on disk
    trash_original_size = db.Column(db.Integer, nullable=True)  # uncompressed size
    trash_checksum = db.Column(db.String(64), nullable=True)    # sha256 of the original bytes


class Note(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    title = db.Column(db.String(255), nullable=True)
    content = db.Column(db.Text, nullable=False)  # stores ENCRYPTED text
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, onupdate=datetime.utcnow)


class Contact(db.Model):
    """A saved recipient. Shares are addressed by contact id, never a dictated email."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    name = db.Column(db.String(150), nullable=False)
    email = db.Column(db.String(150), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('user_id', 'email', name='uq_contact_user_email'),
    )


class ShareLink(db.Model):
    """Expiring public link to one document or a frozen folder bundle.

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

    # Protected / revocable share links
    password_hash = db.Column(db.String(255), nullable=True)  # optional access gate
    revoked_at = db.Column(db.DateTime, nullable=True)
    share_kind = db.Column(db.String(20), nullable=True)  # 'document' or 'folder'
    folder = db.Column(db.String(50), nullable=True)  # folder label for bundles

    # Recipient snapshot: a later contact edit/delete must not change a sent link.
    recipient_contact_id = db.Column(
        db.Integer, db.ForeignKey('contact.id'), nullable=True
    )
    recipient_name = db.Column(db.String(150), nullable=True)
    recipient_email = db.Column(db.String(150), nullable=True)

    items = db.relationship(
        'ShareLinkItem', backref='share', lazy=True, cascade='all, delete-orphan'
    )
    accesses = db.relationship(
        'ShareAccess', backref='share', lazy=True, cascade='all, delete-orphan'
    )


class ShareLinkItem(db.Model):
    """A live document snapshotted into a folder share at creation time."""
    id = db.Column(db.Integer, primary_key=True)
    share_id = db.Column(db.Integer, db.ForeignKey('share_link.id'), nullable=False)
    document_id = db.Column(db.Integer, db.ForeignKey('document.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('share_id', 'document_id', name='uq_share_item'),
    )


class ShareAccess(db.Model):
    """One open, password attempt or download against a share link."""
    id = db.Column(db.Integer, primary_key=True)
    share_id = db.Column(db.Integer, db.ForeignKey('share_link.id'), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    ip_address = db.Column(db.String(45))
    user_agent = db.Column(db.String(255))
    outcome = db.Column(db.String(30))  # opened / granted / password_failed / ...


class VoiceContext(db.Model):
    """Short-lived server-side state for the tap-to-talk assistant.

    No audio or full transcript is stored here. ``context_json`` keeps only
    small references (for example the last document IDs) so follow-up commands
    such as "open that file" can be resolved safely. Pending mutations are
    encrypted and bound to this owner until they are confirmed or expire.
    """
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, unique=True)
    context_json = db.Column(db.Text, nullable=True)
    pending_action = db.Column(db.Text, nullable=True)  # encrypted JSON, never raw note text
    pending_nonce = db.Column(db.String(64), nullable=True)
    pending_summary = db.Column(db.String(255), nullable=True)
    pending_expires_at = db.Column(db.DateTime, nullable=True)
    window_started_at = db.Column(db.DateTime, nullable=True)
    command_count = db.Column(db.Integer, nullable=False, default=0, server_default='0')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SecurityLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    login_id = db.Column(db.String(150))
    ip_address = db.Column(db.String(45))  # 45 chars covers IPv6
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(20))       # 'SUCCESS' or 'FAILED'
    user_agent = db.Column(db.String(255))
    alert_sent = db.Column(db.Boolean, default=False)
    # Voice / sharing audit. Nullable so existing rows stay valid.
    tool = db.Column(db.String(60), nullable=True)
    outcome = db.Column(db.String(30), nullable=True)
