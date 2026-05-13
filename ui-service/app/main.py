import os
import requests

from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from pydantic import BaseModel
from typing import Optional


AUTH_SERVICE_URL = os.getenv("AUTH_SERVICE_URL", "http://127.0.0.1:8000")
MANAGER_SERVICE_URL = os.getenv("MANAGER_SERVICE_URL", "http://127.0.0.1:8002")

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
    data = {
        "username": request.username
    }

    if request.password:
        data["password"] = request.password

    response = requests.post(
        f"{AUTH_SERVICE_URL}/auth/login",
        json=data
    )

    result = response.json()

    if result.get("requires_password_setup"):
        return {
            "requires_password_setup": True,
            "message": "First login detected. Use /set-initial-password to create a password.",
            "username": request.username
        }

    if result.get("requires_password"):
        return {
            "requires_password": True,
            "message": "Password required.",
            "username": request.username
        }

    if result.get("success"):
        return {
            "success": True,
            "message": "Login successful.",
            "username": result["username"],
            "role": result["role"],
            "access_token": result["access_token"],
            "token_type": result["token_type"]
        }

    raise HTTPException(
        status_code=response.status_code,
        detail=result
    )


@app.post("/set-initial-password")
def set_initial_password(request: SetInitialPasswordRequest):
    response = requests.post(
        f"{AUTH_SERVICE_URL}/auth/set-initial-password",
        json={
            "username": request.username,
            "password": request.password
        }
    )

    result = response.json()

    if result.get("success"):
        return {
            "success": True,
            "message": "Password created successfully.",
            "username": result["username"],
            "role": result["role"],
            "access_token": result["access_token"],
            "token_type": result["token_type"]
        }

    raise HTTPException(
        status_code=response.status_code,
        detail=result
    )


@app.post("/validate-token")
def validate_token(authorization: Optional[str] = Header(default=None)):
    token = get_bearer_token(authorization)
    response = requests.post(
        f"{AUTH_SERVICE_URL}/auth/validate-token",
        json={
            "token": token
        }
    )

    result = response.json()

    if result.get("valid"):
        return {
            "valid": True,
            "message": "Token is valid.",
            "username": result["username"],
            "role": result["role"]
        }

    raise HTTPException(
        status_code=401,
        detail=result
    )


@app.post("/logout")
def logout():
    return {
        "success": True,
        "message": "UI service is stateless. Delete the token on the client."
    }


@app.post("/admin/create-user")
def admin_create_user(
    request: CreateUserRequest,
    authorization: Optional[str] = Header(default=None)
):
    token = get_bearer_token(authorization)
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

    result = response.json()

    if response.status_code == 200:
        return {
            "success": True,
            "message": "User created successfully.",
            "username": result["username"],
            "email": result["email"],
            "role": result["role"],
            "password_status": result["password_status"]
        }

    raise HTTPException(
        status_code=response.status_code,
        detail=result
    )


@app.get("/admin/list-users")
def admin_list_users(authorization: Optional[str] = Header(default=None)):
    token = get_bearer_token(authorization)
    response = requests.get(
        f"{AUTH_SERVICE_URL}/admin/users",
        headers={
            "Authorization": f"Bearer {token}"
        }
    )

    result = response.json()

    if response.status_code == 200:
        return {
            "users": result
        }

    raise HTTPException(
        status_code=response.status_code,
        detail=result
    )


@app.delete("/admin/delete-user/{username}")
def admin_delete_user(
    username: str,
    authorization: Optional[str] = Header(default=None)
):
    token = get_bearer_token(authorization)
    response = requests.delete(
        f"{AUTH_SERVICE_URL}/admin/users/{username}",
        headers={
            "Authorization": f"Bearer {token}"
        }
    )

    result = response.json()

    if response.status_code == 200:
        return {
            "success": True,
            "message": "User deleted successfully.",
            "username": result["username"]
        }

    raise HTTPException(
        status_code=response.status_code,
        detail=result
    )


# -------------------------
# Manager service commands
# -------------------------

def proxy_manager_response(response):
    try:
        result = response.json()
    except ValueError:
        raise HTTPException(
            status_code=502,
            detail="Manager Service returned invalid JSON."
        )

    if 200 <= response.status_code < 300:
        return result

    raise HTTPException(
        status_code=response.status_code,
        detail=result
    )


def manager_auth_headers(token: str):
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
    token = get_bearer_token(authorization)
    input_file.file.seek(0)
    mapper_file.file.seek(0)
    reducer_file.file.seek(0)

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
        response = requests.post(
            f"{MANAGER_SERVICE_URL}/jobs",
            headers=manager_auth_headers(token),
            files=files
        )
    except requests.RequestException:
        raise HTTPException(
            status_code=503,
            detail="Manager Service unavailable."
        )

    return proxy_manager_response(response)


@app.get("/jobs")
def list_jobs(authorization: Optional[str] = Header(default=None)):
    token = get_bearer_token(authorization)

    try:
        response = requests.get(
            f"{MANAGER_SERVICE_URL}/jobs",
            headers=manager_auth_headers(token)
        )
    except requests.RequestException:
        raise HTTPException(
            status_code=503,
            detail="Manager Service unavailable."
        )

    return proxy_manager_response(response)


@app.get("/jobs/{job_id}")
def get_job_status(
    job_id: str,
    authorization: Optional[str] = Header(default=None)
):
    token = get_bearer_token(authorization)

    try:
        response = requests.get(
            f"{MANAGER_SERVICE_URL}/jobs/{job_id}",
            headers=manager_auth_headers(token)
        )
    except requests.RequestException:
        raise HTTPException(
            status_code=503,
            detail="Manager Service unavailable."
        )

    return proxy_manager_response(response)


@app.get("/jobs/{job_id}/result/content")
def get_job_result(
    job_id: str,
    authorization: Optional[str] = Header(default=None)
):
    token = get_bearer_token(authorization)

    try:
        response = requests.get(
            f"{MANAGER_SERVICE_URL}/jobs/{job_id}/result/content",
            headers=manager_auth_headers(token)
        )
    except requests.RequestException:
        raise HTTPException(
            status_code=503,
            detail="Manager Service unavailable."
        )

    return proxy_manager_response(response)
