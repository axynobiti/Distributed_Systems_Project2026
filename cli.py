import argparse
import os
import requests

# Base URL of the authentication service.
# For local testing, the FastAPI service runs on localhost port 8000.
AUTH_SERVICE_URL = os.getenv("AUTH_SERVICE_URL", "http://127.0.0.1:8000")

# Local file used to store the current user's JWT token.
# This CLI supports one active logged-in user at a time.
TOKEN_FILE = ".auth_token"


def save_token(token):
    """
    Save the JWT token locally.

    The token is reused by later CLI commands so the user does not need
    to enter their password for every request.
    """

    with open(TOKEN_FILE, "w") as file:
        file.write(token)


def load_token():
    """
    Load the saved JWT token.

    Returns None if no user is currently logged in.
    """

    if not os.path.exists(TOKEN_FILE):
        return None

    with open(TOKEN_FILE, "r") as file:
        return file.read().strip()


def login(args):
    """
    Log in a user through the authentication service.

    If the user has no password yet, the CLI asks them to create one
    and then stores the returned access token.
    """

    # Build the login request body.
    # Password is optional because first-time users do not have one yet.
    data = {
        "username": args.username
    }

    if args.password:
        data["password"] = args.password

    # Send login request to the authentication service.
    response = requests.post(
        f"{AUTH_SERVICE_URL}/auth/login",
        json=data
    )

    result = response.json()

    # First login case: the user exists but has no password yet.
    if result.get("requires_password_setup"):
        print("First login detected.")
        new_password = input("Create password: ")

        # Send the new password to the authentication service.
        setup_response = requests.post(
            f"{AUTH_SERVICE_URL}/auth/set-initial-password",
            json={
                "username": args.username,
                "password": new_password
            }
        )

        setup_result = setup_response.json()

        if setup_result.get("success"):
            save_token(setup_result["access_token"])
            print("Password created successfully.")
            print("Logged in as:", setup_result["username"])
            print("Role:", setup_result["role"])
        else:
            print("Password setup failed:")
            print(setup_result)

        return

    # Existing users must provide a password.
    if result.get("requires_password"):
        print("Password required.")
        print("Use:")
        print(f"python cli.py login --username {args.username} --password <password>")
        return

    # Successful login: save the token for future commands.
    if result.get("success"):
        save_token(result["access_token"])
        print("Login successful.")
        print("Logged in as:", result["username"])
        print("Role:", result["role"])
    else:
        print("Login failed:")
        print(result)


def admin_create_user(args):
    """
    Create a new user account.

    This command requires the currently logged-in user to be an admin.
    The saved token is sent to the authentication service as a Bearer token.
    """

    token = load_token()

    if not token:
        print("No token found. Please login as admin first.")
        return

    response = requests.post(
        f"{AUTH_SERVICE_URL}/admin/users",
        headers={
            "Authorization": f"Bearer {token}"
        },
        json={
            "username": args.username,
            "email": args.email,
            "role": args.role
        }
    )

    result = response.json()

    if response.status_code == 200:
        print("User created successfully.")
        print("Username:", result["username"])
        print("Email:", result["email"])
        print("Role:", result["role"])
        print("Password status:", result["password_status"])
    else:
        print("Failed to create user:")
        print(result)


def admin_list_users(args):
    """
    List all users in the authentication database.

    This command is admin-only and requires a valid admin token.
    """

    token = load_token()

    if not token:
        print("No token found. Please login as admin first.")
        return

    response = requests.get(
        f"{AUTH_SERVICE_URL}/admin/users",
        headers={
            "Authorization": f"Bearer {token}"
        }
    )

    result = response.json()

    if response.status_code == 200:
        print("Users:")
        for username, info in result.items():
            print(
                f"- {username} | email: {info['email']} | "
                f"role: {info['role']} | password_set: {info['password_set']}"
            )
    else:
        print("Failed to list users:")
        print(result)     


def admin_delete_user(args):
    """
    Delete a user account.

    This command is admin-only and requires a valid admin token.
    """

    token = load_token()

    if not token:
        print("No token found. Please login as admin first.")
        return

    response = requests.delete(
        f"{AUTH_SERVICE_URL}/admin/users/{args.username}",
        headers={
            "Authorization": f"Bearer {token}"
        }
    )

    result = response.json()

    if response.status_code == 200:
        print("User deleted successfully.")
        print("Username:", result["username"])
    else:
        print("Failed to delete user:")
        print(result)


def validate_token(args):
    """
    Ask the authentication service whether the saved token is still valid.
    """

    token = load_token()

    if not token:
        print("No token found. Please login first.")
        return

    response = requests.post(
        f"{AUTH_SERVICE_URL}/auth/validate-token",
        json={
            "token": token
        }
    )

    result = response.json()

    if result.get("valid"):
        print("Token is valid.")
        print("Username:", result["username"])
        print("Role:", result["role"])
    else:
        print("Token is invalid:")
        print(result)


def logout(args):
    """
    Log out the current CLI user.

    This only deletes the locally saved token. It does not delete the user
    from the database and does not invalidate the token on the server.
    """
     
    if os.path.exists(TOKEN_FILE):
        os.remove(TOKEN_FILE)
        print("Logged out.")
    else:
        print("No active login found.")


def main():
    """
    Configure the CLI commands and route each command to its handler function.
    """

    parser = argparse.ArgumentParser(
        description="MapReduce System CLI"
    )

    subparsers = parser.add_subparsers(dest="command")

    # login command
    login_parser = subparsers.add_parser("login")
    login_parser.add_argument("--username", required=True)
    login_parser.add_argument("--password", required=False)
    login_parser.set_defaults(func=login)

    # validate-token command
    validate_parser = subparsers.add_parser("validate-token")
    validate_parser.set_defaults(func=validate_token)

    # admin command group
    admin_parser = subparsers.add_parser("admin")
    admin_subparsers = admin_parser.add_subparsers(dest="admin_command")

    # admin create-user command
    create_user_parser = admin_subparsers.add_parser("create-user")
    create_user_parser.add_argument("--username", required=True)
    create_user_parser.add_argument("--email", required=True)
    create_user_parser.add_argument("--role", required=True, choices=["user", "admin"])
    create_user_parser.set_defaults(func=admin_create_user)

    # admin list-users command
    list_users_parser = admin_subparsers.add_parser("list-users")
    list_users_parser.set_defaults(func=admin_list_users)

    # admin delete-user command
    delete_user_parser = admin_subparsers.add_parser("delete-user")
    delete_user_parser.add_argument("--username", required=True)
    delete_user_parser.set_defaults(func=admin_delete_user)

    # logout command
    logout_parser = subparsers.add_parser("logout")
    logout_parser.set_defaults(func=logout)

    args = parser.parse_args()

    # Execute the function connected to the selected command.
    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()