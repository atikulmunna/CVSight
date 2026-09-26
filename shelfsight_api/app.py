from __future__ import annotations

from typing import Literal

from fastapi import Depends, FastAPI, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.middleware.base import RequestResponseEndpoint

from shelfsight_api.analytics_api import router as analytics_router
from shelfsight_api.annotation_api import router as annotation_router
from shelfsight_api.auth import (
    get_current_user,
    require_annotation_user,
    require_owner,
    require_reviewer,
)
from shelfsight_api.auth_api import router as auth_router
from shelfsight_api.database import database_is_ready
from shelfsight_api.dataset_api import router as dataset_router
from shelfsight_api.export_api import router as export_router
from shelfsight_api.image_api import router as image_router
from shelfsight_api.job_api import router as job_router
from shelfsight_api.labeled_import_api import router as labeled_import_router
from shelfsight_api.model_registry_api import router as model_registry_router
from shelfsight_api.prelabel_api import router as prelabel_router
from shelfsight_api.propagation_api import router as propagation_router
from shelfsight_api.recognition_api import router as recognition_router
from shelfsight_api.review_api import router as review_router
from shelfsight_api.sku_api import router as sku_router


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: Literal["shelfsight-api"]


class DatabaseHealthResponse(BaseModel):
    status: Literal["ok", "unavailable"]
    service: Literal["postgresql"]


app = FastAPI(title="ShelfSight API", version="0.1.0")
app.include_router(auth_router)
app.include_router(analytics_router, dependencies=[Depends(require_owner)])
app.include_router(dataset_router, dependencies=[Depends(get_current_user)])
app.include_router(image_router, dependencies=[Depends(get_current_user)])
app.include_router(annotation_router, dependencies=[Depends(get_current_user)])
app.include_router(sku_router, dependencies=[Depends(get_current_user)])
app.include_router(export_router, dependencies=[Depends(require_owner)])
app.include_router(job_router, dependencies=[Depends(require_owner)])
app.include_router(model_registry_router, dependencies=[Depends(require_owner)])
app.include_router(prelabel_router, dependencies=[Depends(require_owner)])
app.include_router(labeled_import_router, dependencies=[Depends(require_owner)])
app.include_router(propagation_router, dependencies=[Depends(require_annotation_user)])
app.include_router(recognition_router, dependencies=[Depends(require_annotation_user)])
app.include_router(review_router, dependencies=[Depends(require_reviewer)])


@app.middleware("http")
async def add_api_security_headers(
    request: Request,
    call_next: RequestResponseEndpoint,
) -> Response:
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.exception_handler(RequestValidationError)
async def sanitized_validation_error(
    request: Request,
    error: RequestValidationError,
) -> JSONResponse:
    del request
    errors = [
        {
            "type": item["type"],
            "loc": item["loc"],
            "msg": item["msg"],
        }
        for item in error.errors()
    ]
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={"detail": errors},
    )


@app.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", service="shelfsight-api")


@app.get(
    "/api/health/database",
    response_model=DatabaseHealthResponse,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": DatabaseHealthResponse}},
)
def database_health(response: Response) -> DatabaseHealthResponse:
    if database_is_ready():
        return DatabaseHealthResponse(status="ok", service="postgresql")
    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return DatabaseHealthResponse(status="unavailable", service="postgresql")
