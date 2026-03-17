"""FastAPI application for desk API."""

from fastapi import FastAPI

from app.routes import workstations

app = FastAPI(title="Desk API", description="HTTP API for EC2 workstations")

app.include_router(workstations.router, prefix="/api")
