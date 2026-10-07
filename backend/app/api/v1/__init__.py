"""/api/v1 라우터 묶음."""

from fastapi import APIRouter

from app.api.v1 import health, parse_query, route_positions, snapshot, stations

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(stations.router)
api_router.include_router(snapshot.router)
api_router.include_router(parse_query.router)
api_router.include_router(route_positions.router)
