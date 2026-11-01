"""Builds the FastAPI app: security, error shape, routers and the static frontend."""

import mimetypes
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

from jobscout.pipeline import build_app_pipeline
from jobscout.server import routes_config, routes_jobs, routes_runs, routes_setup
from jobscout.server.errors import install_error_handlers
from jobscout.server.runner import PipelineFactory, RunManager
from jobscout.server.security import LocalOnlyMiddleware
from jobscout.server.workspace import Workspace

# Windows takes file types from the registry, where .js is sometimes text/plain; browsers refuse
# module scripts served that way.
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")

STATIC_DIR = Path(__file__).parent / "static"
BUILD_HINT = (
    "<!doctype html><title>AI Job Scout</title>"
    "<p>The app's interface isn't built yet. Developers: run npm run build in frontend/.</p>"
)

ROUTERS = [routes_config.router, routes_runs.router, routes_jobs.router, routes_setup.router]


def create_app(
    *, static_dir: Path | None = None, sample: bool = False, pipeline_factory: PipelineFactory = build_app_pipeline
) -> FastAPI:
    workspace = Workspace(sample)
    runs = RunManager(workspace, pipeline_factory)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        runs.close()  # cancel and join the run thread before the database goes away
        workspace.close()

    app = FastAPI(title="AI Job Scout", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.workspace = workspace
    app.state.runs = runs
    app.state.credit = routes_runs.CreditCache()
    app.add_middleware(LocalOnlyMiddleware)
    install_error_handlers(app)
    for router in ROUTERS:
        app.include_router(router)
    _serve_frontend(app, static_dir or STATIC_DIR)

    return app


def _serve_frontend(app: FastAPI, static_dir: Path) -> None:
    """Files from `static_dir`; any other page address gets index.html, so the app's own routes work on reload."""
    root = static_dir.resolve()

    @app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def frontend(path: str):
        if path == "api" or path.startswith("api/"):
            raise HTTPException(404, "Not found.")
        candidate = (root / path).resolve()
        if candidate.is_file() and candidate.is_relative_to(root):
            return FileResponse(candidate)
        index = root / "index.html"
        return FileResponse(index) if index.is_file() else HTMLResponse(BUILD_HINT)
