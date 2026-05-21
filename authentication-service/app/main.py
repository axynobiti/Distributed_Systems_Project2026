from fastapi import FastAPI

from app.database import engine
from app.models import Base
from app.admin_routes import router as admin_router
from app.auth_routes import router as auth_router

# Create the FastAPI application.
app = FastAPI(title="Authentication Service")

# Ensure the authentication database tables exist before the service handles requests.
Base.metadata.create_all(bind=engine)

# Register the admin endpoints
app.include_router(admin_router)

# Register the authentication endpoints
app.include_router(auth_router)