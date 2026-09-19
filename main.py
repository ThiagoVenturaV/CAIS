"""Compatibility entrypoint: `uvicorn main:app` keeps working."""

from backend.app.main import app

__all__ = ["app"]
