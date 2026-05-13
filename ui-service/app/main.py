import os
import requests

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional


AUTH_SERVICE_URL = os.getenv("AUTH_SERVICE_URL", "http://127.0.0.1:8000")

TOKEN_FILE = ".auth_token"

app = FastAPI(title="UI Service")


# -------------------------
# Token helper functions
# -------------------------

def save_token(token: str):
    with open(TOKEN_FILE, "w") as file:
        file.write(token)


def load_token():
    if not os.path.exists(TOKEN_FILE):
        return None

    with open(TOKEN_FILE, "r") as file:
        return file.read().strip()


def delete_token():
    if os.path.exists(TOKEN_FILE):
        os.remove(TOKEN_FILE)
        return True

    return False


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
        save_token(result["access_token"])

        return {
            "success": True,
            "message": "Login successful.",
            "username": result["username"],
            "role": result["role"]
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
        save_token(result["access_token"])

        return {
            "success": True,
            "message": "Password created successfully.",
            "username": result["username"],
            "role": result["role"]
        }

    raise HTTPException(
        status_code=response.status_code,
        detail=result
    )


@app.post("/validate-token")
def validate_token():
    token = load_token()

    if not token:
        raise HTTPException(
            status_code=401,
            detail="No token found. Please login first."
        )

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
    deleted = delete_token()

    if deleted:
        return {
            "success": True,
            "message": "Logged out."
        }

    return {
        "success": False,
        "message": "No active login found."
    }


@app.post("/admin/create-user")
def admin_create_user(request: CreateUserRequest):
    token = load_token()

    if not token:
        raise HTTPException(
            status_code=401,
            detail="No token found. Please login as admin first."
        )

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
def admin_list_users():
    token = load_token()

    if not token:
        raise HTTPException(
            status_code=401,
            detail="No token found. Please login as admin first."
        )

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
def admin_delete_user(username: str):
    token = load_token()

    if not token:
        raise HTTPException(
            status_code=401,
            detail="No token found. Please login as admin first."
        )

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
# Empty placeholders for later services
# -------------------------

@app.post("/jobs")
def submit_job():
    return {
        "message": "submit-job is not implemented yet."
    }


@app.get("/jobs/{job_id}")
def get_job_status(job_id: str):
    return {
        "message": "job-status is not implemented yet.",
        "job_id": job_id
    }


@app.get("/jobs/{job_id}/result")
def get_job_result(job_id: str):
    return {
        "message": "job-result is not implemented yet.",
        "job_id": job_id
    }
