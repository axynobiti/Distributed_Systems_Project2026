import argparse
import os
import requests


# Base URL of the UI service.
# The CLI talks to the UI service, not directly to the auth service.
UI_SERVICE_URL = os.getenv("UI_SERVICE_URL", "http://192.168.49.2:30080")
TOKEN_FILE = ".auth_token"


def save_token(token):
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


def auth_headers():
    token = load_token()

    if not token:
        print("No token found. Please login first.")
        return None

    return {
        "Authorization": f"Bearer {token}"
    }


def print_json_response(response):
    try:
        print(response.json())
    except ValueError:
        print("Service returned invalid JSON.")
        print(response.text)


def response_json(response):
    try:
        return response.json()
    except ValueError:
        return {
            "detail": response.text or "Service returned invalid JSON."
        }


def detail_text(detail):
    if isinstance(detail, dict):
        if "detail" in detail:
            return detail_text(detail["detail"])

        if "error" in detail:
            return str(detail["error"])

        return ", ".join(
            f"{key}: {value}"
            for key, value in detail.items()
        )

    return str(detail)


def print_error(action, result):
    detail = result.get("detail", result)
    print(f"{action} failed: {detail_text(detail)}")


def print_task_progress(progress):
    if not progress:
        return

    print(
        "Tasks: "
        f"{progress.get('completed', 0)}/{progress.get('total', 0)} completed, "
        f"{progress.get('running', 0)} running, "
        f"{progress.get('pending', 0)} pending, "
        f"{progress.get('failed', 0)} failed"
    )


def validate_file_path(path, label):
    if not os.path.isfile(path):
        print(f"{label} file not found: {path}")
        return False

    return True


def login(args):
    """
    Log in a user through the UI service.

    The UI service talks to the auth service and returns the token.
    The CLI stores that token locally for later requests.
    """

    data = {
        "username": args.username
    }

    if args.password:
        data["password"] = args.password

    response = requests.post(
        f"{UI_SERVICE_URL}/login",
        json=data
    )

    result = response.json()

    if result.get("requires_password_setup"):
        print("First login detected.")
        new_password = input("Create password: ")

        setup_response = requests.post(
            f"{UI_SERVICE_URL}/set-initial-password",
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

    if result.get("requires_password"):
        print("Password required.")
        print("Use:")
        print(f"python cli.py login --username {args.username} --password <password>")
        return

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
    Create a new user account through the UI service.
    """

    headers = auth_headers()

    if not headers:
        return

    response = requests.post(
        f"{UI_SERVICE_URL}/admin/create-user",
        headers=headers,
        json={
            "username": args.username,
            "email": args.email,
            "role": args.role
        }
    )

    result = response.json()

    if response.status_code == 200 and result.get("success"):
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
    List all users through the UI service.
    """

    headers = auth_headers()

    if not headers:
        return

    response = requests.get(
        f"{UI_SERVICE_URL}/admin/list-users",
        headers=headers
    )

    result = response.json()

    if response.status_code == 200:
        print("Users:")

        users = result["users"]

        for username, info in users.items():
            print(
                f"- {username} | email: {info['email']} | "
                f"role: {info['role']} | password_set: {info['password_set']}"
            )
    else:
        print("Failed to list users:")
        print(result)


def admin_delete_user(args):
    """
    Delete a user account through the UI service.
    """

    headers = auth_headers()

    if not headers:
        return

    response = requests.delete(
        f"{UI_SERVICE_URL}/admin/delete-user/{args.username}",
        headers=headers
    )

    result = response.json()

    if response.status_code == 200 and result.get("success"):
        print("User deleted successfully.")
        print("Username:", result["username"])
    else:
        print("Failed to delete user:")
        print(result)


def validate_token(args):
    """
    Ask the UI service whether the saved token is still valid.
    """

    headers = auth_headers()

    if not headers:
        return

    response = requests.post(
        f"{UI_SERVICE_URL}/validate-token",
        headers=headers
    )

    result = response.json()

    if response.status_code == 200 and result.get("valid"):
        print("Token is valid.")
        print("Username:", result["username"])
        print("Role:", result["role"])
    else:
        print("Token is invalid:")
        print(result)


def logout(args):
    """
    Log out by deleting the locally saved token.
    """

    if delete_token():
        print("Logged out.")
    else:
        print("No active login found.")


def submit_job(args):
    """
    Submit a MapReduce job through the UI service.
    """

    headers = auth_headers()

    if not headers:
        return

    if not validate_file_path(args.input, "Input"):
        return

    if not validate_file_path(args.mapper, "Mapper"):
        return

    if not validate_file_path(args.reducer, "Reducer"):
        return

    with open(args.input, "rb") as input_file, \
            open(args.mapper, "rb") as mapper_file, \
            open(args.reducer, "rb") as reducer_file:
        files = {
            "input_file": (
                os.path.basename(args.input),
                input_file,
                "application/octet-stream"
            ),
            "mapper_file": (
                os.path.basename(args.mapper),
                mapper_file,
                "application/octet-stream"
            ),
            "reducer_file": (
                os.path.basename(args.reducer),
                reducer_file,
                "application/octet-stream"
            )
        }

        response = requests.post(
            f"{UI_SERVICE_URL}/jobs",
            headers=headers,
            files=files
        )

    result = response_json(response)

    if response.status_code == 200 and result.get("success"):
        print("Job submitted successfully.")
        print("Job ID:", result["job_id"])
        print("Status:", result["status"])
        print("Mappers:", result["num_mappers"])
        print("Reducers:", result["num_reducers"])
        print_task_progress(result.get("task_progress"))
    else:
        print_error("Job submission", result)


def list_jobs(args):
    """
    List MapReduce jobs visible to the current user.
    """

    headers = auth_headers()

    if not headers:
        return

    response = requests.get(
        f"{UI_SERVICE_URL}/jobs",
        headers=headers
    )

    result = response_json(response)

    if response.status_code != 200:
        print_error("List jobs", result)
        return

    if not result:
        print("No jobs found.")
        return

    print("Jobs:")

    for job in result:
        print(
            f"- Job {job['job_id']} | "
            f"status: {job['status']} | "
            f"mappers: {job['num_mappers']} | "
            f"reducers: {job['num_reducers']}"
        )


def get_job_status(args):
    """
    Get the status/details of a MapReduce job.
    """

    headers = auth_headers()

    if not headers:
        return

    response = requests.get(
        f"{UI_SERVICE_URL}/jobs/{args.job_id}",
        headers=headers
    )

    result = response_json(response)

    if response.status_code != 200:
        print_error("Get job status", result)
        return

    print("Job ID:", result["job_id"])
    print("Status:", result["status"])
    print("Owner:", result["username"])
    print("Mappers:", result["num_mappers"])
    print("Reducers:", result["num_reducers"])

    if result.get("output_path"):
        print("Output:", result["output_path"])

    if result.get("error_message"):
        print("Error:", result["error_message"])

    print_task_progress(result.get("task_progress"))

    tasks = result.get("tasks") or []

    if tasks:
        print("Task details:")

        for task in tasks:
            print(
                f"- {task['task_type']} {task['task_index']} | "
                f"status: {task['status']} | "
                f"attempts: {task['attempt_count']}"
            )


def get_job_result(args):
    """
    Get the result of a completed MapReduce job.
    """

    headers = auth_headers()

    if not headers:
        return

    response = requests.get(
        f"{UI_SERVICE_URL}/jobs/{args.job_id}/result/content",
        headers=headers
    )

    result = response_json(response)

    if response.status_code != 200:
        print_error("Retrieve result", result)
        return

    content = result.get("content")

    if content is None:
        print("Result is empty.")
        return

    print(content, end="" if content.endswith("\n") else "\n")


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

    # jobs command group
    jobs_parser = subparsers.add_parser("jobs")
    jobs_subparsers = jobs_parser.add_subparsers(dest="jobs_command")

    # jobs submit command
    submit_job_parser = jobs_subparsers.add_parser("submit")
    submit_job_parser.add_argument("--input", required=True)
    submit_job_parser.add_argument("--mapper", required=True)
    submit_job_parser.add_argument("--reducer", required=True)
    submit_job_parser.set_defaults(func=submit_job)

    # jobs list command
    list_jobs_parser = jobs_subparsers.add_parser("list")
    list_jobs_parser.set_defaults(func=list_jobs)

    # jobs view command
    job_status_parser = jobs_subparsers.add_parser("view")
    job_status_parser.add_argument("--job-id", required=True)
    job_status_parser.set_defaults(func=get_job_status)

    # jobs retrieve result command
    retrieve_parser = jobs_subparsers.add_parser("retrieve")
    retrieve_subparsers = retrieve_parser.add_subparsers(dest="retrieve_command")

    result_parser = retrieve_subparsers.add_parser("result")
    result_parser.add_argument("--job-id", required=True)
    result_parser.set_defaults(func=get_job_result)

    args = parser.parse_args()

    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
