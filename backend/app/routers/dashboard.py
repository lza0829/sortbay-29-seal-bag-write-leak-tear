from __future__ import annotations

from fastapi import APIRouter

from app.db import db
from app.services.projections import overview_data

router = APIRouter(tags=["总览"])


@router.get("/总览")
@router.get("/overview")
@router.get("/dashboard")
def overview():
    with db() as conn:
        return overview_data(conn)
