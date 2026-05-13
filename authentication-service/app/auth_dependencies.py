import jwt
from jwt.exceptions import InvalidTokenError
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.auth_routes import SECRET_KEY, ALGORITHM

# HTTPBearer tells FastAPI to expect an Authorization header
# in the form: Authorization: Bearer <token>
security = HTTPBearer()


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
):
    """
    Extract and validate the JWT token from the request.

    If the token is valid, the function returns the corresponding user
    from the database. If the token is missing, invalid, expired, or refers
    to a deleted user, the request is rejected.
    """

    # Extract the raw token from the Authorization header.
    token = credentials.credentials

    try:
        # Decode the JWT and verify its signature and expiration time.
        payload = jwt.decode(
            token,
            SECRET_KEY,
            algorithms=[ALGORITHM]
        )

        # The username is stored in the JWT subject field.
        username = payload.get("sub")

        if username is None:
            raise HTTPException(
                status_code=401,
                detail="Token does not contain username"
            )

    except InvalidTokenError:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired token"
        )
    
    # Load the user from the database to make sure the account still exists.
    user = db.query(User).filter(User.username == username).first()

    if not user:
        raise HTTPException(
            status_code=401,
            detail="User no longer exists"
        )

    return user


def require_admin(current_user: User = Depends(get_current_user)):
    """
    Require the current authenticated user to have the admin role.

    This dependency is used to protect admin-only endpoints.
    """

    # Only users with the admin role are allowed to continue.
    if current_user.role != "admin":
        raise HTTPException(
            status_code=403,
            detail="Admin privileges required"
        )

    return current_user