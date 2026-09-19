from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer


bearer = HTTPBearer(auto_error=False)


def current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> dict[str, Any]:
    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Autenticação necessária")
    user = request.app.state.database.get_user_by_token(credentials.credentials)
    if not user or not user.get("active"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sessão inválida ou expirada")
    return user


def manager_user(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    if user["role"] != "manager":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Acesso exclusivo de gestor")
    return user


def guard_user(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    if user["role"] != "guard":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Acesso exclusivo de guarda")
    return user
