from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.auth_dependencies import require_admin

# Router that contains all administrator-only endpoints.
router = APIRouter()

# Request body model used when an admin creates a new user.
class UserCreate(BaseModel):
    username: str
    email: str
    role: str


@router.post("/admin/users")
def create_user(
    user: UserCreate,
    db: Session = Depends(get_db),
    admin_user: User = Depends(require_admin)
):
    """
    Create a new user account.

    This endpoint is protected by require_admin, so only authenticated
    users with the admin role can access it.

    New users are created without a password. Their password_hash is set
    to None, which means they must create their password on first login.
    """

    # Check that the username is not already used. 
    existing_user = db.query(User).filter(User.username == user.username).first()

    if existing_user:
        raise HTTPException(
            status_code=400,
            detail="Username already exists"
        )


    # Check that the email is not already used.
    existing_email = db.query(User).filter(User.email == user.email).first()

    if existing_email:
        raise HTTPException(
            status_code=400,
            detail="Email already exists"
        )


    # Only the supported roles can be assigned to a user.
    if user.role not in ["admin", "user"]:
        raise HTTPException(
            status_code=400,
            detail="Invalid role. Role must be either 'admin' or 'user'"
        )

    # Create the database user record.
    # password_hash is None because the user has not set a password yet.
    new_user = User(
        username=user.username,
        email=user.email,
        role=user.role,
        password_hash=None
    )

    # Store the new user in the database.
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    return {
        "success": True,
        "username": new_user.username,
        "email": new_user.email,
        "role": new_user.role,
        "password_status": "not_set"
    }


@router.delete("/admin/users/{username}")
def delete_user(
    username: str,
    db: Session = Depends(get_db),
    admin_user: User = Depends(require_admin)
):
    """
    Delete an existing user account.

    This endpoint is admin-only. An admin is not allowed to delete their
    own account while using it, to avoid locking themselves out.
    """

    # Find the user that should be deleted.
    user = db.query(User).filter(User.username == username).first()

    if not user:
        raise HTTPException(
            status_code=404,
            detail="User not found"
        )

    # Prevent the currently logged-in admin from deleting their own account.
    if user.username == admin_user.username:
        raise HTTPException(
            status_code=400,
            detail="Admin cannot delete their own account while logged in"
        )

    # Delete the user from the database.
    db.delete(user)
    db.commit()

    return {
        "deleted": True,
        "username": username
    }


@router.get("/admin/users")
def list_users(
    db: Session = Depends(get_db),
    admin_user: User = Depends(require_admin)
):
    """
    Return all users in the system.

    This endpoint is admin-only. It does not return password hashes.
    Instead, it only reports whether each user has already set a password.
    """

    # Load all users from the database.
    db_users = db.query(User).all()

    result = {}

    # Convert database User objects into a safe response format.
    for user in db_users:
        result[user.username] = {
            "email": user.email,
            "role": user.role,
            "password_set": user.password_hash is not None
        }

    return result