"""Dependencias FastAPI: sesion de DB y autenticacion del admin.

La sesion del panel se guarda en una cookie firmada (Starlette SessionMiddleware,
configurado en main.py con SECRET_KEY). `require_admin` protege los endpoints.
"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import UsuarioAdmin

SESSION_KEY = "admin_id"


def get_db() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def require_admin(request: Request, db: Session = Depends(get_db)) -> UsuarioAdmin:
    """Devuelve el admin de la sesion o 401 si no hay sesion valida."""
    admin_id = request.session.get(SESSION_KEY)
    if admin_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No autenticado")
    admin = db.get(UsuarioAdmin, admin_id)
    if admin is None:
        request.session.clear()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sesion invalida")
    return admin
