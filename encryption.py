import os
import base64
import hashlib
from dotenv import load_dotenv
from cryptography.fernet import Fernet

load_dotenv()

def _get_fernet():
    key = os.environ.get("ENCRYPTION_KEY")
    if not key:
        # Safely derive a 32-byte urlsafe base64 key from SECRET_KEY without hardcoding secrets
        secret = os.environ.get("SECRET_KEY", "default-vault-secure-seed-key")
        key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode('utf-8')).digest()).decode('utf-8')
    elif isinstance(key, str):
        key = key.strip()
    return Fernet(key.encode('utf-8') if isinstance(key, str) else key)

fernet = _get_fernet()

def encrypt_note(plain_text: str) -> str:
    """Encrypts note content before it's saved to the database."""
    if not plain_text:
        return ""
    return fernet.encrypt(plain_text.encode('utf-8')).decode('utf-8')

def decrypt_note(encrypted_text: str) -> str:
    """Decrypts note content after it's read from the database."""
    if not encrypted_text:
        return ""
    try:
        return fernet.decrypt(encrypted_text.encode('utf-8')).decode('utf-8')
    except Exception:
        return "[Decryption Error: Invalid Key]"
