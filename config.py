import os
from dotenv import load_dotenv

load_dotenv()  # reads .env file in project root
class Config:
    # Flask
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-this")

    # Database
    SQLALCHEMY_DATABASE_URI = "sqlite:///secure_notes.db"
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Encryption (used by encryption.py)
    ENCRYPTION_KEY = os.environ.get("ENCRYPTION_KEY")
  
    # Mail / Alerts (used by alerts.py)
    MAIL_SERVER = (os.environ.get("MAIL_SERVER") or "smtp.gmail.com").strip()
    _raw_mail_port = (os.environ.get("MAIL_PORT") or "").strip()
    MAIL_PORT = int(_raw_mail_port) if _raw_mail_port.isdigit() else 587
    MAIL_USERNAME = os.environ.get("MAIL_USERNAME")
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD")
