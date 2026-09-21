from __future__ import annotations

import re
from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, Request, status
from sqlalchemy.exc import SQLAlchemyError

from shelfsight_api.api_errors import api_error
from shelfsight_api.auth_service import AuthenticatedUser, find_session, record_auth_event
from shelfsight_api.config import ConfigurationError, Role, get_auth_users
from shelfsight_api.database import get_engine

SESSION_COOKIE = "shelfsight_session"
SESSION_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")


def get_current_user(
    session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
) -> AuthenticatedUser:
    if session_token is None or SESSION_TOKEN_PATTERN.fullmatch(session_token) is None:
        raise _authentication_required()
    try:
        users = get_auth_users()
        with get_engine().connect() as connection:
            user = find_session(connection, session_token, users)
    except ConfigurationError as error:
        raise api_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "authentication_unavailable",
            "authentication is not configured",
        ) from error
    except SQLAlchemyError as error:
        raise api_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "authentication_unavailable",
            "authentication service is unavailable",
        ) from error
    if user is None:
        raise _authentication_required()
    return user


CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]


def require_owner(user: CurrentUser, request: Request) -> AuthenticatedUser:
    return _require_roles(user, request, {"owner"})


def require_annotation_user(user: CurrentUser, request: Request) -> AuthenticatedUser:
    return _require_roles(user, request, {"owner", "annotator", "reviewer"})


def require_reviewer(user: CurrentUser, request: Request) -> AuthenticatedUser:
    return _require_roles(user, request, {"owner", "reviewer"})


OwnerUser = Annotated[AuthenticatedUser, Depends(require_owner)]
AnnotationUser = Annotated[AuthenticatedUser, Depends(require_annotation_user)]
ReviewerUser = Annotated[AuthenticatedUser, Depends(require_reviewer)]


def owner_actor(user: OwnerUser) -> str:
    return user.actor


def annotation_actor(user: AnnotationUser) -> str:
    return user.actor


def reviewer_actor(user: ReviewerUser) -> str:
    return user.actor


OwnerActor = Annotated[str, Depends(owner_actor)]
AnnotationActor = Annotated[str, Depends(annotation_actor)]
ReviewerActor = Annotated[str, Depends(reviewer_actor)]


def _require_roles(
    user: AuthenticatedUser,
    request: Request,
    allowed_roles: set[Role],
) -> AuthenticatedUser:
    if user.role in allowed_roles:
        return user
    try:
        with get_engine().begin() as connection:
            record_auth_event(connection, "access_denied", request.url.path, user=user)
    except (ConfigurationError, SQLAlchemyError) as error:
        raise api_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "authentication_unavailable",
            "authentication audit is unavailable",
        ) from error
    raise api_error(
        status.HTTP_403_FORBIDDEN,
        "authorization_forbidden",
        "your role cannot perform this action",
    )


def _authentication_required() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={
            "code": "authentication_required",
            "message": "sign in to access this resource",
        },
        headers={"WWW-Authenticate": "Session"},
    )
