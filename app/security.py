"""Password hashing, token signing, and alias generation."""
import base64
import hashlib
import hmac
import json
import os
import random

SECRET = os.environ.get("FORGE_SECRET", "dev").encode()
PBKDF2_ITERATIONS = 200_000
TOKEN_TTL_SECONDS = 86400

ADJECTIVES = [
    "Happy", "Quiet", "Bold", "Clever", "Gentle", "Brave", "Calm", "Eager",
    "Fuzzy", "Jolly", "Kind", "Lucky", "Mighty", "Nimble", "Proud", "Quick",
    "Silly", "Tidy", "Witty", "Zesty", "Bright", "Chill", "Dapper", "Epic",
]

ANIMALS = [
    "Otter", "Falcon", "Panda", "Tiger", "Koala", "Rabbit", "Wolf", "Fox",
    "Eagle", "Dolphin", "Heron", "Lynx", "Moose", "Newt", "Owl", "Puffin",
    "Quail", "Raven", "Seal", "Turtle", "Viper", "Walrus", "Yak", "Zebra",
]


def hash_password(password: str):
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return dk.hex(), salt.hex()


def verify_password(password: str, pw_hash: str, salt: str) -> bool:
    salt_bytes = bytes.fromhex(salt)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt_bytes, PBKDF2_ITERATIONS)
    return hmac.compare_digest(dk.hex(), pw_hash)


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _b64decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def make_token(user_id: int) -> str:
    from app import main
    iat = int(main.now().timestamp())
    payload = json.dumps({"uid": user_id, "iat": iat}, separators=(",", ":")).encode()
    sig = hmac.new(SECRET, payload, hashlib.sha256).digest()
    return f"{_b64encode(payload)}.{_b64encode(sig)}"


def verify_token(token: str):
    from app import main
    try:
        payload_b64, sig_b64 = token.split(".", 1)
        payload = _b64decode(payload_b64)
        sig = _b64decode(sig_b64)
        expected_sig = hmac.new(SECRET, payload, hashlib.sha256).digest()
        if not hmac.compare_digest(sig, expected_sig):
            return None
        data = json.loads(payload)
        uid = data.get("uid")
        iat = data.get("iat")
        if uid is None or iat is None:
            return None
        now_ts = main.now().timestamp()
        if iat + TOKEN_TTL_SECONDS < now_ts:
            return None
        return int(uid)
    except Exception:
        return None


def generate_alias(conn, email: str) -> str:
    local_part = email.split("@", 1)[0].lower() if "@" in email else email.lower()
    for _ in range(1000):
        alias = f"{random.choice(ADJECTIVES)}{random.choice(ANIMALS)}{random.randint(10, 99)}"
        if local_part and local_part in alias.lower():
            continue
        row = conn.execute(
            "SELECT 1 FROM users WHERE alias = ? COLLATE NOCASE", (alias,)
        ).fetchone()
        if row:
            continue
        return alias
    raise RuntimeError("could not generate unique alias")
