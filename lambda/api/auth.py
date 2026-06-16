"""
Authentication helpers for the dashboard API Lambda — standard library only.

- Passwords: PBKDF2-HMAC-SHA256, stored as
      pbkdf2_sha256$<iterations>$<salt_b64url>$<hash_b64url>
- Tokens: compact JWT (HS256) signed with a key read from Secrets Manager
  (env var JWT_SECRET_ARN).

No third-party crypto dependencies, so the shared `api_deps` Lambda layer is
unchanged. boto3 is provided by the Lambda runtime.
"""

import base64
import hashlib
import hmac
import json
import os
import time

import boto3

_secrets = boto3.client('secretsmanager')

_PBKDF2_ALGO = 'sha256'
_PBKDF2_ITERATIONS = 240_000
_SALT_BYTES = 16

_JWT_ALG = 'HS256'
_JWT_TTL_SECONDS = 12 * 60 * 60  # 12 hours

_signing_key_cache = None


class AuthError(Exception):
    """Authentication / authorization failure. `status` maps to an HTTP code."""

    def __init__(self, message, status=401):
        super().__init__(message)
        self.message = message
        self.status = status


# ── base64url ─────────────────────────────────────────────────────────────────

def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode('ascii')


def _b64url_decode(data: str) -> bytes:
    pad = '=' * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


# ── password hashing (PBKDF2) ─────────────────────────────────────────────────

def hash_password(password: str) -> str:
    salt = os.urandom(_SALT_BYTES)
    dk = hashlib.pbkdf2_hmac(_PBKDF2_ALGO, password.encode('utf-8'), salt, _PBKDF2_ITERATIONS)
    return f'pbkdf2_{_PBKDF2_ALGO}${_PBKDF2_ITERATIONS}${_b64url_encode(salt)}${_b64url_encode(dk)}'


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iterations_s, salt_b64, hash_b64 = stored.split('$')
        algo = scheme.split('_', 1)[1]
        iterations = int(iterations_s)
        salt = _b64url_decode(salt_b64)
        expected = _b64url_decode(hash_b64)
    except (ValueError, IndexError):
        return False
    dk = hashlib.pbkdf2_hmac(algo, password.encode('utf-8'), salt, iterations)
    return hmac.compare_digest(dk, expected)


# ── JWT (HS256) ───────────────────────────────────────────────────────────────

def _signing_key() -> bytes:
    global _signing_key_cache
    if _signing_key_cache is None:
        secret = _secrets.get_secret_value(SecretId=os.environ['JWT_SECRET_ARN'])['SecretString']
        try:
            parsed = json.loads(secret)
            if isinstance(parsed, dict):
                secret = parsed.get('signing_key', secret)
        except (json.JSONDecodeError, TypeError):
            pass
        _signing_key_cache = secret.encode('utf-8')
    return _signing_key_cache


def _sign(signing_input: bytes) -> str:
    sig = hmac.new(_signing_key(), signing_input, hashlib.sha256).digest()
    return _b64url_encode(sig)


def sign_jwt(claims: dict, ttl_seconds: int = _JWT_TTL_SECONDS) -> str:
    now = int(time.time())
    header = {'alg': _JWT_ALG, 'typ': 'JWT'}
    payload = {**claims, 'iat': now, 'exp': now + ttl_seconds}
    segments = [
        _b64url_encode(json.dumps(header, separators=(',', ':')).encode('utf-8')),
        _b64url_encode(json.dumps(payload, separators=(',', ':')).encode('utf-8')),
    ]
    segments.append(_sign('.'.join(segments).encode('ascii')))
    return '.'.join(segments)


def verify_jwt(token: str) -> dict:
    try:
        header_b64, payload_b64, sig_b64 = token.split('.')
    except ValueError:
        raise AuthError('Malformed token')
    expected_sig = _sign(f'{header_b64}.{payload_b64}'.encode('ascii'))
    if not hmac.compare_digest(expected_sig, sig_b64):
        raise AuthError('Invalid token signature')
    try:
        payload = json.loads(_b64url_decode(payload_b64))
    except (ValueError, json.JSONDecodeError):
        raise AuthError('Invalid token payload')
    if int(payload.get('exp', 0)) < int(time.time()):
        raise AuthError('Token expired')
    return payload


# ── request helpers ───────────────────────────────────────────────────────────

def get_bearer_token(event: dict) -> str:
    headers = event.get('headers') or {}
    # API Gateway HTTP API delivers header names lowercased.
    auth_header = headers.get('authorization') or headers.get('Authorization') or ''
    if not auth_header.lower().startswith('bearer '):
        raise AuthError('Missing bearer token')
    return auth_header[7:].strip()


def authenticate(event: dict) -> dict:
    """Verify the request's bearer token and return its claims, or raise AuthError."""
    return verify_jwt(get_bearer_token(event))
