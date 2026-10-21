import bcrypt


def hash_password(password: str) -> str:
    """Returns a bcrypt hash. The salt is random and stored inside the hash."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """Checks a password against a stored bcrypt hash."""
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
