
from cryptography.fernet import Fernet
from config import Config

if not Config.ENCRYPTION_KEY:
    raise RuntimeError(
        "ENCRYPTION_KEY not set in .env. Generate one with "
        "Fernet.generate_key() and add it before running the app."
    )
fernet = Fernet(Config.ENCRYPTION_KEY.encode())

# ENCRYPT / DECRYPT
def encrypt_note(plain_text: str) -> str:
    """Encrypts note content before it's saved to the database."""
    return fernet.encrypt(plain_text.encode()).decode()

def decrypt_note(encrypted_text: str) -> str:
    """Decrypts note content after it's read from the database."""
    return fernet.decrypt(encrypted_text.encode()).decode()
