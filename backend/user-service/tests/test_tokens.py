import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.config import settings
from app.tokens import _public_key, create_access_token, decode_access_token


def valid_claims(**changes):
    now = datetime.now(UTC)
    claims = {"sub": "1", "role": "customer", "iss": settings.jwt_issuer,
              "aud": settings.jwt_audience, "iat": now, "exp": now + timedelta(minutes=5)}
    claims.update(changes)
    return claims


def test_token_contains_user_and_role():
    token = create_access_token(user_id=7, role="admin")

    claims = decode_access_token(token)

    assert claims["sub"] == "7"
    assert claims["role"] == "admin"


def test_expired_token_is_rejected():
    from app.tokens import _private_key
    token = jwt.encode(valid_claims(exp=datetime.now(UTC) - timedelta(seconds=1)),
                       _private_key(), algorithm="RS256")

    with pytest.raises(jwt.ExpiredSignatureError):
        decode_access_token(token)


def test_token_for_another_audience_is_rejected():
    from app.tokens import _private_key
    token = jwt.encode(valid_claims(aud="other-system"), _private_key(), algorithm="RS256")

    with pytest.raises(jwt.InvalidAudienceError):
        decode_access_token(token)


def test_token_signed_with_another_key_is_rejected():
    attacker_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = attacker_key.private_bytes(serialization.Encoding.PEM,
                                     serialization.PrivateFormat.PKCS8,
                                     serialization.NoEncryption())
    token = jwt.encode(valid_claims(role="admin"), pem, algorithm="RS256")

    with pytest.raises(jwt.InvalidSignatureError):
        decode_access_token(token)


def test_modified_token_is_rejected():
    token = create_access_token(user_id=1, role="customer")
    header, payload, signature = token.split(".")
    forged_payload = jwt.utils.base64url_encode(b'{"sub":"1","role":"admin"}').decode()

    with pytest.raises(jwt.InvalidTokenError):
        decode_access_token(f"{header}.{forged_payload}.{signature}")


def test_unsigned_token_is_rejected():
    # "alg: none" attack: a token without signature
    token = jwt.encode(valid_claims(role="admin"), key=None, algorithm="none")

    with pytest.raises(jwt.InvalidTokenError):
        decode_access_token(token)


def test_public_key_used_as_hmac_secret_is_rejected():
    # Algorithm confusion attack: the attacker knows the public key and builds
    # an HS256 token by hand, using that public key as the HMAC secret.
    def b64(data: bytes) -> str:
        return jwt.utils.base64url_encode(data).decode()

    claims = valid_claims(role="admin")
    claims["iat"] = int(claims["iat"].timestamp())
    claims["exp"] = int(claims["exp"].timestamp())
    signing_input = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode()) + "." + b64(json.dumps(claims).encode())
    signature = hmac.new(_public_key().encode(), signing_input.encode(), hashlib.sha256).digest()

    with pytest.raises(jwt.InvalidAlgorithmError):
        decode_access_token(f"{signing_input}.{b64(signature)}")
