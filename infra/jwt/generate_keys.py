"""Generates the RSA key pair used to sign (private) and verify (public) JWTs.

Usage, from the project root:
    python infra/jwt/generate_keys.py

The keys are written to "secrets/", which is ignored by Git.
"""
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

SECRETS_DIR = Path(__file__).resolve().parents[2] / "secrets"
PRIVATE_KEY = SECRETS_DIR / "jwt_private.pem"
PUBLIC_KEY = SECRETS_DIR / "jwt_public.pem"


def main() -> None:
    if PRIVATE_KEY.exists() and "--force" not in sys.argv:
        print(f"{PRIVATE_KEY} already exists. Use --force to replace it.")
        print("Warning: replacing the keys invalidates all issued tokens.")
        return

    SECRETS_DIR.mkdir(exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    PRIVATE_KEY.write_bytes(key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ))
    PUBLIC_KEY.write_bytes(key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ))
    print(f"Created {PRIVATE_KEY}")
    print(f"Created {PUBLIC_KEY}")


if __name__ == "__main__":
    main()
