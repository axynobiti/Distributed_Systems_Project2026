from datetime import datetime, timedelta, timezone
from typing import Optional
import os

import jwt
from jwt.exceptions import InvalidTokenError
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User

# Router that contains all authentication-related endpoints.
router = APIRouter()

# Password hashing context.
# bcrypt is used to securely hash and verify user passwords.
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# JWT configuration.
# AUTH_SECRET_KEY can be provided from the environment.
SECRET_KEY = os.getenv("AUTH_SECRET_KEY")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 5


# Request body model for login.
# Password is optional because first-time users may not have a password yet.
class LoginRequest(BaseModel):
    username: str
    password: Optional[str] = None


# Request body model used when a user sets their password for the first time.
class SetInitialPasswordRequest(BaseModel):
    username: str
    password: str


# Request body model used by other services to validate a JWT token.
class TokenRequest(BaseModel):
    token: str


def create_access_token(data: dict):
    """
    Create a signed JWT access token.

    The token stores the provided data, such as username and role,
    and adds an expiration timestamp. The token is then signed using
    the secret key so it can be verified later.
    """

    # Copy the input data so the original dictionary is not modified.
    to_encode = data.copy()

    # Calculate when the token should expire.
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=ACCESS_TOKEN_EXPIRE_MINUTES
    )

    # Add the expiration time to the JWT payload.
    to_encode.update({"exp": expire})

    # Encode and sign the JWT.
    encoded_jwt = jwt.encode(
        to_encode,
        SECRET_KEY,
        algorithm=ALGORITHM
    )

    return encoded_jwt


@router.post("/auth/login")
def login(credentials: LoginRequest, db: Session = Depends(get_db)):
    """
    Authenticate a user and return an access token.

    If the user exists but has no password yet, the endpoint tells the
    client that initial password setup is required.

    If the password is correct, a JWT token is created and returned.
    """

    # Look up the user by username.
    user = db.query(User).filter(User.username == credentials.username).first()

    if not user:
        return {
            "success": False,
            "error": "User not found"
        }

    # If password_hash is None, this is the user's first login.
    if user.password_hash is None:
        return {
            "success": False,
            "requires_password_setup": True,
            "message": "First login detected. Please create a password."
        }

    # If the user already has a password, one must be provided.
    if credentials.password is None:
        return {
            "success": False,
            "requires_password": True,
            "message": "Password required."
        }

    # Verify the provided password against the stored password hash.
    if not pwd_context.verify(credentials.password, user.password_hash):
        return {
            "success": False,
            "error": "Incorrect password"
        }

    # Create a token containing the username and role.
    access_token = create_access_token(
        data={
            "sub": user.username,
            "role": user.role
        }
    )

    return {
        "success": True,
        "username": user.username,
        "role": user.role,
        "access_token": access_token,
        "token_type": "bearer"
    }


@router.post("/auth/set-initial-password")
def set_initial_password(
    request: SetInitialPasswordRequest,
    db: Session = Depends(get_db)
):
    """
    Set the password for a user who does not have one yet.

    This endpoint is used during first login. After the password is
    stored, the user immediately receives a JWT token.
    """

    # Find the user who is setting their first password.
    user = db.query(User).filter(User.username == request.username).first()

    if not user:
        raise HTTPException(
            status_code=404,
            detail="User not found"
        )

    # Do not allow overwriting an existing password through this endpoint.
    if user.password_hash is not None:
        raise HTTPException(
            status_code=400,
            detail="Password has already been set"
        )

    # Hash the new password before storing it.
    hashed_password = pwd_context.hash(request.password)

    user.password_hash = hashed_password

    # Save the password hash in the database.
    db.commit()
    db.refresh(user)

    # Create a token so the user is logged in immediately after setup.
    access_token = create_access_token(
        data={
            "sub": user.username,
            "role": user.role
        }
    )

    return {
        "success": True,
        "username": user.username,
        "role": user.role,
        "access_token": access_token,
        "token_type": "bearer"
    }


@router.post("/auth/validate-token")
def validate_token(token_request: TokenRequest, db: Session = Depends(get_db)):
    """
    Validate a JWT token and return the associated user information.

    This endpoint is intended for other services, such as the Manager
    service, so they can check whether a request comes from an
    authenticated user.
    """

    token = token_request.token

    try:
        # Decode the token and verify its signature and expiration time.
        payload = jwt.decode(
            token,
            SECRET_KEY,
            algorithms=[ALGORITHM]
        )

        # The username is stored in the JWT subject field.
        username = payload.get("sub")

        if username is None:
            return {
                "valid": False,
                "error": "Token does not contain username"
            }

    except InvalidTokenError:
        return {
            "valid": False,
            "error": "Invalid or expired token"
        }

    # Make sure the user from the token still exists in the database.
    user = db.query(User).filter(User.username == username).first()

    if not user:
        return {
            "valid": False,
            "error": "User no longer exists"
        }

    return {
        "valid": True,
        "username": user.username,
        "role": user.role
    }