from fastapi import APIRouter

from app.api.routes import (
    applications,
    companies,
    dashboard,
    discovery,
    ingestion,
    jobs,
    opportunities,
    profile,
    resumes,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(jobs.router)
api_router.include_router(ingestion.router)
api_router.include_router(dashboard.router)
api_router.include_router(companies.router)
api_router.include_router(discovery.router)
api_router.include_router(profile.router)
api_router.include_router(opportunities.router)
api_router.include_router(resumes.router)
api_router.include_router(applications.router)
