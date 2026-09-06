import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.database import Base, engine
from app.routers import auth, notices, appeals, payments, billing
from app.models import notice, user, billing as billing_models  # noqa: F401 (ensures models are registered before create_all)

app = FastAPI(title="AI-Powered Vehicle Penalty Management Platform")

# Allow a separately-hosted frontend (e.g. a React dev server) to call this
# API during development. Not needed for the bundled static UI below, since
# that's served same-origin, but harmless to leave on.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Creates tables on startup if they don't exist yet.
# Fine for MVP development; switch to Alembic migrations once schema stabilises.
Base.metadata.create_all(bind=engine)

app.include_router(auth.router)
app.include_router(notices.router)
app.include_router(appeals.router)
app.include_router(payments.router)
app.include_router(billing.router)


@app.get("/health")
def health_check():
    return {"status": "ok"}


STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


@app.get("/", include_in_schema=False)
def serve_frontend():
    """Serves the bundled demo UI (a single self-contained HTML file) at
    the API's own root, so there's no separate frontend server or CORS
    setup needed for local development/demoing."""
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


# Serves app/static/vendor/* (currently: a vendored copy of jsPDF, used by
# the "download appeal as PDF" button) at /static/vendor/*. Vendored
# locally rather than pulled from a CDN on every page load, so that
# feature — and the demo generally — doesn't depend on a third-party
# script host being reachable/unblocked on whatever network the demo runs
# on.
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
