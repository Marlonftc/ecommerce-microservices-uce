from app.security import hash_password, verify_password


def test_hash_is_not_the_plain_password():
    assert hash_password("Secreto123") != "Secreto123"


def test_verify_accepts_correct_password():
    password_hash = hash_password("Secreto123")

    assert verify_password("Secreto123", password_hash)


def test_verify_rejects_wrong_password():
    password_hash = hash_password("Secreto123")

    assert not verify_password("otra", password_hash)


def test_same_password_gives_different_hashes():
    # bcrypt uses a random salt each time
    assert hash_password("Secreto123") != hash_password("Secreto123")
