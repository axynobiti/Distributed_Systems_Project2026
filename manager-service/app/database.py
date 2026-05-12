import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# Database connection URL for the Manager Service.
# Defaults to the local development/testing database.
#
# Expected PostgreSQL setup:
# database name: manager_db
# user: manager_user
# password: manager_password
# host: localhost
#
# Docker/Kubernetes can override this with the DATABASE_URL environment variable.
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://manager_user:manager_password@localhost/manager_db"
)

# SQLAlchemy engine used to communicate with PostgreSQL.
engine = create_engine(DATABASE_URL)

# Session factory.
# Each API request will get its own database session through get_db().
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)

# Base class for SQLAlchemy models.
Base = declarative_base()


def get_db():
    """
    Provide a database session to FastAPI endpoints.

    The session is opened before handling the request and closed after
    the request finishes.
    """

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
