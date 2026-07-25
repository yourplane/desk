"""FastAPI application for desk API."""

from fastapi import FastAPI

from app.routes import amis, costs, router_infra, saved_commands, web_routes, workstations

app = FastAPI(title="Desk API", description="HTTP API for EC2 workstations")

app.include_router(workstations.router, prefix="/api")
app.include_router(router_infra.router, prefix="/api")
app.include_router(amis.router, prefix="/api")
app.include_router(costs.router, prefix="/api")
app.include_router(saved_commands.router, prefix="/api")
app.include_router(web_routes.router, prefix="/api")
