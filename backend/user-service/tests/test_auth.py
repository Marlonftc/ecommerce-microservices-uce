from app.models import User
from app.security import hash_password
from app.tokens import decode_access_token

NEW_USER = {"email": "Ana@Correo.com", "full_name": "Ana Pérez", "password": "Clave1234"}


def register(client, **changes):
    return client.post("/auth/register", json={**NEW_USER, **changes})


def login(client, email="ana@correo.com", password="Clave1234"):
    return client.post("/auth/login", json={"email": email, "password": password})


def auth_header(token):
    return {"Authorization": f"Bearer {token}"}


def create_admin(db_session):
    db_session.add(User(email="jefe@correo.com", full_name="Jefe",
                        password_hash=hash_password("Admin1234"), role="admin"))
    db_session.commit()


# --- register ---------------------------------------------------------------

def test_register_creates_customer_without_exposing_password(client):
    response = register(client)

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "ana@correo.com"  # stored in lowercase
    assert body["role"] == "customer"
    assert "password" not in body and "password_hash" not in body


def test_register_same_email_twice_returns_409(client):
    register(client)

    response = register(client, email="ANA@correo.com")

    assert response.status_code == 409


def test_register_cannot_choose_admin_role(client):
    response = register(client, role="admin")

    assert response.status_code == 422


def test_register_rejects_invalid_email_and_short_password(client):
    assert register(client, email="no-es-correo").status_code == 422
    assert register(client, password="corta").status_code == 422


# --- login ------------------------------------------------------------------

def test_login_returns_valid_token(client):
    user_id = register(client).json()["id"]

    response = login(client)

    assert response.status_code == 200
    claims = decode_access_token(response.json()["access_token"])
    assert claims["sub"] == str(user_id)
    assert claims["role"] == "customer"


def test_login_with_wrong_password_or_unknown_email_gives_same_error(client):
    register(client)

    wrong_password = login(client, password="otraClave")
    unknown_email = login(client, email="nadie@correo.com")

    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json()


def test_login_with_unusual_email_format_gives_401_not_422(client):
    # Login must not validate the format: it only looks the email up
    response = login(client, email="admin@ecommerce.local")

    assert response.status_code == 401


# --- users ------------------------------------------------------------------

def test_me_requires_token(client):
    assert client.get("/users/me").status_code == 401
    assert client.get("/users/me", headers=auth_header("token-falso")).status_code == 401


def test_me_returns_own_data(client):
    register(client)
    token = login(client).json()["access_token"]

    response = client.get("/users/me", headers=auth_header(token))

    assert response.status_code == 200
    assert response.json()["email"] == "ana@correo.com"


def test_inactive_user_token_is_rejected(client, db_session):
    user_id = register(client).json()["id"]
    token = login(client).json()["access_token"]
    db_session.get(User, user_id).is_active = False
    db_session.commit()

    assert client.get("/users/me", headers=auth_header(token)).status_code == 401


def test_customer_cannot_list_users(client):
    register(client)
    token = login(client).json()["access_token"]

    assert client.get("/users", headers=auth_header(token)).status_code == 403


def test_admin_can_list_users(client, db_session):
    create_admin(db_session)
    register(client)
    token = login(client, email="jefe@correo.com", password="Admin1234").json()["access_token"]

    response = client.get("/users", headers=auth_header(token))

    assert response.status_code == 200
    emails = [u["email"] for u in response.json()]
    assert "ana@correo.com" in emails and "jefe@correo.com" in emails
