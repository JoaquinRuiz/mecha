"""Health endpoint."""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from mecha import __version__

router = APIRouter()


class Health(BaseModel):
    status: Literal["ok"]
    version: str


@router.get("/health")
async def health() -> Health:
    return Health(status="ok", version=__version__)
