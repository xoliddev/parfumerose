import hashlib
import os
import base64


def hash_password(password: str) -> str:
    try:
        import bcrypt  # type: ignore

        return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    except Exception:
        salt = os.urandom(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
        return "pbkdf2_sha256$" + base64.b64encode(salt).decode("ascii") + "$" + base64.b64encode(digest).decode(
            "ascii"
        )
