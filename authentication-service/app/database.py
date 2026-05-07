import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# Database connection URL for the authentication service.
# This local development URL matches:
# database name: auth_db
# user: auth_user
# password: auth_password
# host: localhost
#
# Docker/Kubernetes provides DATABASE_URL through the auth deployment.
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://auth_user:auth_password@localhost/auth_db"
)

# SQLAlchemy engine.
# The engine manages the connection pool and communicates with PostgreSQL.
engine = create_engine(DATABASE_URL)

# Session factory used to create database sessions.
# Each request gets its own session through get_db().
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)

# Base class used by SQLAlchemy models.
# All database models inherit from this Base.
Base = declarative_base()


def get_db():
    """
    Provide a database session to FastAPI endpoints.

    This function is used as a dependency with Depends(get_db).
    It opens a database session before the request and closes it
    after the request finishes.
    """

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
