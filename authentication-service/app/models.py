from sqlalchemy import Column, String, DateTime, Enum
from sqlalchemy.sql import func

from app.database import Base


class User(Base):
    """
    Database model for users of the authentication service.

    Each row in this table represents one user account.
    Users can have either the "admin" role or the "user" role.
    """

    # Name of the database table.
    __tablename__ = "users"

    # Unique username used as the primary identifier for each user.
    username = Column(String, primary_key=True, unique=True, nullable=False)

    # Unique email address associated with the user account.
    email = Column(String, unique=True, nullable=False)

    # User role used for authorization.
    # Admin users can access admin-only endpoints.
    role = Column(Enum("admin", "user", name="user_role"), nullable=False)

    # Hashed password.
    # This is nullable because newly created users do not have a password yet.
    # They set it during their first login.
    password_hash = Column(String, nullable=True)

    # Timestamp automatically set by the database when the user is created.
    created_at = Column(DateTime, server_default=func.now())