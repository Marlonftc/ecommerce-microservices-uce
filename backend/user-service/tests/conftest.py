import os
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

# Tests must not depend on a real ".env" file or on the real keys.
os.environ.setdefault("DB_PASSWORD", "test-password")

_keys_dir = Path(tempfile.mkdtemp())
_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
(_keys_dir / "private.pem").write_bytes(_key.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
(_keys_dir / "public.pem").write_bytes(_key.public_key().public_bytes(
    serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))

os.environ["JWT_PRIVATE_KEY_PATH"] = str(_keys_dir / "private.pem")
os.environ["JWT_PUBLIC_KEY_PATH"] = str(_keys_dir / "public.pem")
