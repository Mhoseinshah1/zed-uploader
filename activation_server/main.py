"""Activation server FastAPI app (standalone, optional).

Run:  uvicorn activation_server.main:app --port 8100
DB :  ACTIVATION_DB (default ./activation.db)
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import BaseModel

from activation_server.store import ActivationStore

DEFAULT_DB = "activation.db"


class ActivateIn(BaseModel):
    key: str
    fingerprint: str


def create_app(db_path: str | None = None) -> FastAPI:
    path = db_path or os.environ.get("ACTIVATION_DB", DEFAULT_DB)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store = ActivationStore(path)
        await store.init()
        app.state.store = store
        try:
            yield
        finally:
            await store.close()

    app = FastAPI(title="ZedUploader Activation Server", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.post("/activate")
    async def activate(body: ActivateIn) -> dict:
        return await app.state.store.activate(body.key.strip(), body.fingerprint.strip())

    @app.post("/check")
    async def check(body: ActivateIn) -> dict:
        return await app.state.store.check(body.key.strip(), body.fingerprint.strip())

    return app


app = create_app()
