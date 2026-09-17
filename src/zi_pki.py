"""ZoomInfo PKI client-JWT generation (only needed for key-based auth)."""

import time
import uuid

import jwt  # PyJWT


def generate_pki_jwt(username, client_id, private_key):
    now = int(time.time())
    claims = {
        "iss": "api-client@zoominfo.com",
        "aud": "enterprise_api",
        "iat": now,
        "exp": now + 300,
        "username": username,
        "client_id": client_id,
    }
    return jwt.encode(
        claims,
        private_key,
        algorithm="RS256",
        headers={"typ": "JWT", "alg": "RS256", "kid": str(uuid.uuid4())},
    )
