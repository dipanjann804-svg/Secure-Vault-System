import os
from cryptography.fernet import Fernet

try:
    from config import Config
    ENCRYPTION_KEY = getattr(Config, 'ENCRYPTION_KEY', None)
except ImportError:
    ENCRYPTION_KEY = None

if not ENCRYPTION_KEY:
    ENCRYPTION_KEY = os.environ.get("ENCRYPTION_KEY", "s4xzdDtj_Aa1wb8ojuqmyWgHHrXr-RBO1_cCQ4ZrzmE=")

fernet = Fernet(ENCRYPTION_KEY.encode())

# ENCRYPT / DECRYPT
def encrypt_note(plain_text: str) -> str:
    """Encrypts note content before it's saved to the database."""
    return fernet.encrypt(plain_text.encode()).decode()

def decrypt_note(encrypted_text: str) -> str:
    """Decrypts note content after it's read from the database."""
    return fernet.decrypt(encrypted_text.encode()).decode()
