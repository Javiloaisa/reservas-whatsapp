"""Utilidades de contrasena (hash/verify) para el panel admin.

Centraliza el contexto de passlib/bcrypt usado por el seed y por el login.
"""

from __future__ import annotations

from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)
