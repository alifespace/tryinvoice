from fastapi import FastAPI

from app.api.account import router as account_router
from app.api.batches import router as batches_router
from app.api.documents import router as documents_router
from app.api.exports import router as exports_router
from app.api.health import router as health_router
from app.api.processing import router as processing_router
from app.api.templates import router as templates_router
from app.api.transactions import router as transactions_router


def create_app() -> FastAPI:
    application = FastAPI(title="TryInvoice API", version="0.1.0")
    application.include_router(health_router, prefix="/api/v1")
    application.include_router(templates_router, prefix="/api/v1")
    application.include_router(batches_router, prefix="/api/v1")
    application.include_router(documents_router, prefix="/api/v1")
    application.include_router(exports_router, prefix="/api/v1")
    application.include_router(processing_router, prefix="/api/v1")
    application.include_router(account_router, prefix="/api/v1")
    application.include_router(transactions_router, prefix="/api/v1")
    return application


app = create_app()
