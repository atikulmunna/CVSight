from __future__ import annotations

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy.exc import SQLAlchemyError

from shelfsight_api.api_errors import api_error
from shelfsight_api.auth import SESSION_COOKIE, CurrentUser
from shelfsight_api.auth_service import (
    DUMMY_PASSWORD_HASH,
    create_session,
    password_matches,
    record_auth_event,
    revoke_session,
)
from shelfsight_api.config import (
    ConfigurationError,
    Role,
    get_auth_users,
    session_cookie_is_secure,
)
from shelfsight_api.database import get_engine

router = APIRouter(prefix="/api/auth")


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    password: SecretStr = Field(min_length=1, max_length=1024)


class SessionResponse(BaseModel):
    username: str
    role: Role


@router.post("/login", response_model=SessionResponse)
def login(request: LoginRequest, response: Response) -> SessionResponse:
    try:
        users = get_auth_users()
        cookie_secure = session_cookie_is_secure()
    except ConfigurationError as error:
        raise api_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "authentication_unavailable",
            "authentication is not configured",
        ) from error

    configured = users.get(request.username.casefold())
    password = request.password.get_secret_value()
    candidate_hash = configured.password_hash if configured is not None else DUMMY_PASSWORD_HASH
    valid = password_matches(password, candidate_hash)
    try:
        with get_engine().begin() as connection:
            if not valid or configured is None:
                record_auth_event(
                    connection,
                    "login_failure",
                    "/api/auth/login",
                    username=request.username,
                )
            else:
                token, user = create_session(connection, configured)
                record_auth_event(connection, "login_success", "/api/auth/login", user=user)
    except SQLAlchemyError as error:
        raise api_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "authentication_unavailable",
            "authentication service is unavailable",
        ) from error
    if not valid or configured is None:
        raise api_error(
            status.HTTP_401_UNAUTHORIZED,
            "invalid_credentials",
            "username or password is incorrect",
        )

    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=12 * 60 * 60,
        httponly=True,
        secure=cookie_secure,
        samesite="strict",
        path="/api",
    )
    return SessionResponse(username=user.username, role=user.role)


@router.get("/session", response_model=SessionResponse)
def session(user: CurrentUser) -> SessionResponse:
    return SessionResponse(username=user.username, role=user.role)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(user: CurrentUser, response: Response) -> None:
    try:
        with get_engine().begin() as connection:
            revoke_session(connection, user.session_id)
            record_auth_event(connection, "logout", "/api/auth/logout", user=user)
    except SQLAlchemyError as error:
        raise api_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "authentication_unavailable",
            "authentication service is unavailable",
        ) from error
    response.delete_cookie(SESSION_COOKIE, path="/api")
