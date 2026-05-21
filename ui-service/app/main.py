import os
import requests

from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from pydantic import BaseModel
from typing import Optional


AUTH_SERVICE_URL = os.getenv("AUTH_SERVICE_URL")
MANAGER_SERVICE_URL = os.getenv("MANAGER_SERVICE_URL")

app = FastAPI(title="UI Service")


# -------------------------
# Token helper
# -------------------------

def get_bearer_token(authorization: Optional[str]):
    if not authorization:
        raise HTTPException(
            status_code=401,
            detail="Missing Authorization header."
        )

    scheme, _, token = authorization.partition(" ")

    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=401,
            detail="Authorization header must be: Bearer <token>."
        )

    return token


# -------------------------
# Request models
# -------------------------

class LoginRequest(BaseModel):
    username: str
    password: Optional[str] = None


class SetInitialPasswordRequest(BaseModel):
    username: str
    password: str


class CreateUserRequest(BaseModel):
    username: str
    email: str
    role: str


# -------------------------
# Auth service commands
# -------------------------

@app.post("/login")
def login(request: LoginRequest):
    # Build the login data received from the CLI.
    data = {
        "username": request.username
    }

    # Add password only if the CLI provided one.
    if request.password:
        data["password"] = request.password

    # Forward the login request from UI Service to Auth Service.
    response = requests.post(
        f"{AUTH_SERVICE_URL}/auth/login",
        json=data
    )

    # Convert Auth Service response from JSON into a Python dictionary.
    result = response.json()

    # Auth says this is the user's first login and they must create a password.
    if result.get("requires_password_setup"):
        return {
            "requires_password_setup": True,
            "message": "First login detected. Use /set-initial-password to create a password.",
            "username": request.username
        }

    # Auth says the account has a password, but none was provided.
    if result.get("requires_password"):
        return {
            "requires_password": True,
            "message": "Password required.",
            "username": request.username
        }

    # Auth says login succeeded, so return the token to the CLI.
    if result.get("success"):
        return {
            "success": True,
            "message": "Login successful.",
            "username": result["username"],
            "role": result["role"],
            "access_token": result["access_token"],
            "token_type": result["token_type"]
        }

    # If Auth returned an error, forward that error back to the CLI.
    raise HTTPException(
        status_code=response.status_code,
        detail=result
    )


@app.post("/set-initial-password")
def set_initial_password(request: SetInitialPasswordRequest):
    # Forward the new password from UI Service to Auth Service.
    response = requests.post(
        f"{AUTH_SERVICE_URL}/auth/set-initial-password",
        json={
            "username": request.username,
            "password": request.password
        }
    )

    # Convert Auth Service response from JSON into a Python dictionary.
    result = response.json()

    # If password creation succeeded, return the new token to the CLI.
    if result.get("success"):
        return {
            "success": True,
            "message": "Password created successfully.",
            "username": result["username"],
            "role": result["role"],
            "access_token": result["access_token"],
            "token_type": result["token_type"]
        }

    # If Auth returned an error, forward that error back to the CLI.
    raise HTTPException(
        status_code=response.status_code,
        detail=result
    )


@app.post("/validate-token")
def validate_token(authorization: Optional[str] = Header(default=None)):
    # Read and extract the Bearer token from the Authorization header.
    token = get_bearer_token(authorization)

    # Send the extracted token to the Auth Service for validation.
    response = requests.post(
        f"{AUTH_SERVICE_URL}/auth/validate-token",
        json={
            "token": token
        }
    )

    # Convert Auth Service response from JSON into a Python dictionary.
    result = response.json()

    # If Auth says the token is valid, return user information to the CLI.
    if result.get("valid"):
        return {
            "valid": True,
            "message": "Token is valid.",
            "username": result["username"],
            "role": result["role"]
        }

    # If Auth says the token is invalid, return 401 Unauthorized.
    raise HTTPException(
        status_code=401,
        detail=result
    )


@app.post("/logout")
def logout():
    # UI Service is stateless, so it does not store login sessions.
    return {
        "success": True,

        # The CLI must delete .auth_token locally to log out.
        "message": "UI service is stateless. Delete the token on the client."
    }


@app.post("/admin/create-user")
def admin_create_user(
    request: CreateUserRequest,
    authorization: Optional[str] = Header(default=None)
):
    # Extract the Bearer token from the Authorization header.
    token = get_bearer_token(authorization)

    # Forward the create-user request to the Auth Service with the admin token.
    response = requests.post(
        f"{AUTH_SERVICE_URL}/admin/users",
        headers={
            "Authorization": f"Bearer {token}"
        },
        json={
            "username": request.username,
            "email": request.email,
            "role": request.role
        }
    )

    # Convert Auth Service response from JSON into a Python dictionary.
    result = response.json()

    # If Auth created the user successfully, return success to the CLI.
    if response.status_code == 200:
        return {
            "success": True,
            "message": "User created successfully.",
            "username": result["username"],
            "email": result["email"],
            "role": result["role"],
            "password_status": result["password_status"]
        }

    # Otherwise, forward Auth Service error back to the CLI.
    raise HTTPException(
        status_code=response.status_code,
        detail=result
    )


@app.get("/admin/list-users")
def admin_list_users(authorization: Optional[str] = Header(default=None)):
    # Extract the Bearer token from the Authorization header.
    token = get_bearer_token(authorization)

    # Forward the list-users request to the Auth Service with the admin token.
    response = requests.get(
        f"{AUTH_SERVICE_URL}/admin/users",
        headers={
            "Authorization": f"Bearer {token}"
        }
    )

    # Convert Auth Service response from JSON into a Python dictionary.
    result = response.json()

    # If Auth returned users successfully, wrap them and return to the CLI.
    if response.status_code == 200:
        return {
            "users": result
        }

    # Otherwise, forward Auth Service error back to the CLI.
    raise HTTPException(
        status_code=response.status_code,
        detail=result
    )


@app.delete("/admin/delete-user/{username}")
def admin_delete_user(
    username: str,
    authorization: Optional[str] = Header(default=None)
):
    # Extract the Bearer token from the Authorization header.
    token = get_bearer_token(authorization)

    # Forward the delete-user request to the Auth Service with the admin token.
    response = requests.delete(
        f"{AUTH_SERVICE_URL}/admin/users/{username}",
        headers={
            "Authorization": f"Bearer {token}"
        }
    )

    # Convert Auth Service response from JSON into a Python dictionary.
    result = response.json()

    # If Auth deleted the user successfully, return success to the CLI.
    if response.status_code == 200:
        return {
            "success": True,
            "message": "User deleted successfully.",
            "username": result["username"]
        }

    # Otherwise, forward Auth Service error back to the CLI.
    raise HTTPException(
        status_code=response.status_code,
        detail=result
    )


# -------------------------
# Manager service commands
# -------------------------

def proxy_manager_response(response):
    # Try to convert the Manager Service response into JSON.
    try:
        result = response.json()

    # If Manager did not return JSON, report a bad backend response.
    except ValueError:
        raise HTTPException(
            status_code=502,
            detail="Manager Service returned invalid JSON."
        )

    # If Manager returned success, pass its JSON response back to the CLI.
    if 200 <= response.status_code < 300:
        return result

    # If Manager returned an error, forward that error to the CLI.
    raise HTTPException(
        status_code=response.status_code,
        detail=result
    )


def manager_auth_headers(token: str):
    # Build Authorization header to forward the user's token to Manager Service.
    return {
        "Authorization": f"Bearer {token}"
    }


@app.post("/jobs")
def submit_job(
    input_file: UploadFile = File(...),
    mapper_file: UploadFile = File(...),
    reducer_file: UploadFile = File(...),
    authorization: Optional[str] = Header(default=None)
):
    # Extract the Bearer token sent by the CLI.
    token = get_bearer_token(authorization)

    # Reset uploaded file streams before forwarding them.
    input_file.file.seek(0)
    mapper_file.file.seek(0)
    reducer_file.file.seek(0)

    # Build the multipart file upload body for the Manager Service.
    files = {
        "input_file": (
            input_file.filename,
            input_file.file,
            input_file.content_type or "application/octet-stream"
        ),
        "mapper_file": (
            mapper_file.filename,
            mapper_file.file,
            mapper_file.content_type or "application/octet-stream"
        ),
        "reducer_file": (
            reducer_file.filename,
            reducer_file.file,
            reducer_file.content_type or "application/octet-stream"
        )
    }

    try:
        # Forward the job files and user token to the Manager Service.
        response = requests.post(
            f"{MANAGER_SERVICE_URL}/jobs",
            headers=manager_auth_headers(token),
            files=files
        )

    # If Manager cannot be reached, return service unavailable.
    except requests.RequestException:
        raise HTTPException(
            status_code=503,
            detail="Manager Service unavailable."
        )

    # Return Manager's response, or forward its error.
    return proxy_manager_response(response)


@app.get("/jobs")
def list_jobs(authorization: Optional[str] = Header(default=None)):
    # Extract the Bearer token sent by the CLI.
    token = get_bearer_token(authorization)

    try:
        # Forward the list-jobs request and user token to the Manager Service.
        response = requests.get(
            f"{MANAGER_SERVICE_URL}/jobs",
            headers=manager_auth_headers(token)
        )

    # If Manager cannot be reached, return service unavailable.
    except requests.RequestException:
        raise HTTPException(
            status_code=503,
            detail="Manager Service unavailable."
        )

    # Return Manager's response, or forward its error.
    return proxy_manager_response(response)


@app.get("/jobs/{job_id}")
def get_job_status(
    job_id: str,
    authorization: Optional[str] = Header(default=None)
):
    # Extract the Bearer token sent by the CLI.
    token = get_bearer_token(authorization)

    try:
        # Forward the job-status request and user token to the Manager Service.
        response = requests.get(
            f"{MANAGER_SERVICE_URL}/jobs/{job_id}",
            headers=manager_auth_headers(token)
        )

    # If Manager cannot be reached, return service unavailable.
    except requests.RequestException:
        raise HTTPException(
            status_code=503,
            detail="Manager Service unavailable."
        )

    # Return Manager's response, or forward its error.
    return proxy_manager_response(response)


@app.get("/jobs/{job_id}/result/content")
def get_job_result(
    job_id: str,
    authorization: Optional[str] = Header(default=None)
):
    # Extract the Bearer token sent by the CLI.
    token = get_bearer_token(authorization)

    try:
        # Forward the result-content request and user token to the Manager Service.
        response = requests.get(
            f"{MANAGER_SERVICE_URL}/jobs/{job_id}/result/content",
            headers=manager_auth_headers(token)
        )

    # If Manager cannot be reached, return service unavailable.
    except requests.RequestException:
        raise HTTPException(
            status_code=503,
            detail="Manager Service unavailable."
        )

    # Return Manager's response, or forward its error.
    return proxy_manager_response(response)