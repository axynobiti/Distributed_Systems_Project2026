import argparse
import os
import requests


# Base URL of the UI service.
# The CLI talks to the UI service, not directly to the auth service.
UI_SERVICE_URL = os.getenv("UI_SERVICE_URL")
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

    # Build the login data from the terminal arguments.
    data = {
        "username": args.username
    }

    # Add password only if the user provided one.
    if args.password:
        data["password"] = args.password

    # Send username/password to the UI service.
    response = requests.post(
        f"{UI_SERVICE_URL}/login",
        json=data
    )

    # Convert the UI service response from JSON into a Python dictionary.
    result = response.json()

    # If this is the user's first login, ask them to create a password.
    if result.get("requires_password_setup"):
        print("First login detected.")
        new_password = input("Create password: ")

        # Send the new password to the UI service.
        setup_response = requests.post(
            f"{UI_SERVICE_URL}/set-initial-password",
            json={
                "username": args.username,
                "password": new_password
            }
        )

        # Convert the password setup response into a Python dictionary.
        setup_result = setup_response.json()

        # If password setup worked, save the returned token.
        if setup_result.get("success"):
            save_token(setup_result["access_token"])
            print("Password created successfully.")
            print("Logged in as:", setup_result["username"])
            print("Role:", setup_result["role"])

        # Otherwise, print the setup error.
        else:
            print("Password setup failed:")
            print(setup_result)

        return

    # If the account already has a password but none was provided, ask for it.
    if result.get("requires_password"):
        print("Password required.")
        print("Use:")
        print(f"python cli.py login --username {args.username} --password <password>")
        return

    # If login succeeded, save the returned token locally.
    if result.get("success"):
        save_token(result["access_token"])
        print("Login successful.")
        print("Logged in as:", result["username"])
        print("Role:", result["role"])

    # Otherwise, print the login error.
    else:
        print("Login failed:")
        print(result)


def admin_create_user(args):
    """
    Create a new user account through the UI service.
    """

    # Build Authorization header using the saved token from .auth_token.
    headers = auth_headers()

    # Stop if no token exists, because this is an admin-only request.
    if not headers:
        return

    # Send the new user data to the UI service, together with the admin token.
    response = requests.post(
        f"{UI_SERVICE_URL}/admin/create-user",
        headers=headers,  # Proves who is making the request.
        json={            # Data of the user we want to create.
            "username": args.username,
            "email": args.email,
            "role": args.role
        }
    )

    # Convert the UI service response from JSON into a Python dictionary.
    result = response.json()

    # If HTTP succeeded and the response says success=true, print the created user.
    if response.status_code == 200 and result.get("success"):
        print("User created successfully.")
        print("Username:", result["username"])
        print("Email:", result["email"])
        print("Role:", result["role"])
        print("Password status:", result["password_status"])

    # Otherwise, print the error returned by the UI/Auth service.
    else:
        print("Failed to create user:")
        print(result)


def admin_list_users(args):
    """
    List all users through the UI service.
    """

    # Build Authorization header using the saved token from .auth_token.
    headers = auth_headers()

    # Stop if no token exists, because listing users is admin-only.
    if not headers:
        return

    # Ask the UI service for the list of users, using the admin token.
    response = requests.get(
        f"{UI_SERVICE_URL}/admin/list-users",
        headers=headers
    )

    # Convert the UI service response from JSON into a Python dictionary.
    result = response.json()

    # If the HTTP request succeeded, print the users.
    if response.status_code == 200:
        print("Users:")

        # Extract the users dictionary from the response.
        users = result["users"]

        # Print each user's username, email, role, and password status.
        for username, info in users.items():
            print(
                f"- {username} | email: {info['email']} | "
                f"role: {info['role']} | password_set: {info['password_set']}"
            )

    # Otherwise, print the error returned by the UI/Auth service.
    else:
        print("Failed to list users:")
        print(result)


def admin_delete_user(args):
    """
    Delete a user account through the UI service.
    """

    # Build Authorization header using the saved token from .auth_token.
    headers = auth_headers()

    # Stop if no token exists, because deleting users is admin-only.
    if not headers:
        return

    # Send a DELETE request to the UI service for the selected username.
    response = requests.delete(
        f"{UI_SERVICE_URL}/admin/delete-user/{args.username}",
        headers=headers  # Proves who is making the delete request.
    )

    # Convert the UI service response from JSON into a Python dictionary.
    result = response.json()

    # If HTTP succeeded and the response says success=true, print confirmation.
    if response.status_code == 200 and result.get("success"):
        print("User deleted successfully.")
        print("Username:", result["username"])

    # Otherwise, print the error returned by the UI/Auth service.
    else:
        print("Failed to delete user:")
        print(result)


def validate_token(args):
    """
    Ask the UI service whether the saved token is still valid.
    """

    # Build Authorization header using the saved token from .auth_token.
    headers = auth_headers()

    # Stop if no token exists.
    if not headers:
        return

    # Send the saved token to the UI service for validation.
    response = requests.post(
        f"{UI_SERVICE_URL}/validate-token",
        headers=headers  # Contains Authorization: Bearer <token>.
    )

    # Convert the UI service response from JSON into a Python dictionary.
    result = response.json()

    # If HTTP succeeded and the response says valid=true, print token info.
    if response.status_code == 200 and result.get("valid"):
        print("Token is valid.")
        print("Username:", result["username"])
        print("Role:", result["role"])

    # Otherwise, print the validation error.
    else:
        print("Token is invalid:")
        print(result)


def logout(args):
    """
    Log out by deleting the locally saved token.
    """

    # Delete the local .auth_token file if it exists.
    if delete_token():
        print("Logged out.")

    # If there was no token file, there was no active local login.
    else:
        print("No active login found.")


def submit_job(args):
    """
    Submit a MapReduce job through the UI service.
    """

    # Build Authorization header using the saved token from .auth_token.
    headers = auth_headers()

    # Stop if no token exists, because job submission requires login.
    if not headers:
        return

    # Check that the input file exists before uploading.
    if not validate_file_path(args.input, "Input"):
        return

    # Check that the mapper file exists before uploading.
    if not validate_file_path(args.mapper, "Mapper"):
        return

    # Check that the reducer file exists before uploading.
    if not validate_file_path(args.reducer, "Reducer"):
        return

    # Open input, mapper, and reducer files in binary mode for upload.
    with open(args.input, "rb") as input_file, \
            open(args.mapper, "rb") as mapper_file, \
            open(args.reducer, "rb") as reducer_file:

        # Build the multipart file upload body expected by the UI service.
        files = {
            "input_file": (
                os.path.basename(args.input),  # Send only the filename, not the full path.
                input_file,
                "application/octet-stream"
            ),
            "mapper_file": (
                os.path.basename(args.mapper),  # Mapper source filename.
                mapper_file,
                "application/octet-stream"
            ),
            "reducer_file": (
                os.path.basename(args.reducer),  # Reducer source filename.
                reducer_file,
                "application/octet-stream"
            )
        }

        # Send the job files and token to the UI service.
        response = requests.post(
            f"{UI_SERVICE_URL}/jobs",
            headers=headers,  # Proves which user is submitting the job.
            files=files       # Contains input, mapper, and reducer files.
        )

    # Convert the UI service response into a Python dictionary.
    result = response_json(response)

    # If HTTP succeeded and the response says success=true, print job info.
    if response.status_code == 200 and result.get("success"):
        print("Job submitted successfully.")
        print("Job ID:", result["job_id"])
        print("Status:", result["status"])
        print("Mappers:", result["num_mappers"])
        print("Reducers:", result["num_reducers"])
        print_task_progress(result.get("task_progress"))

    # Otherwise, print a clean job submission error.
    else:
        print_error("Job submission", result)


def list_jobs(args):
    """
    List MapReduce jobs visible to the current user.
    """

    # Build Authorization header using the saved token from .auth_token.
    headers = auth_headers()

    # Stop if no token exists, because listing jobs requires login.
    if not headers:
        return

    # Ask the UI service for the jobs visible to the current user.
    response = requests.get(
        f"{UI_SERVICE_URL}/jobs",
        headers=headers  # Proves which user is asking for jobs.
    )

    # Convert the UI service response into a Python object.
    result = response_json(response)

    # If the HTTP request failed, print the error and stop.
    if response.status_code != 200:
        print_error("List jobs", result)
        return

    # If the returned job list is empty, tell the user.
    if not result:
        print("No jobs found.")
        return

    # Print all jobs returned by the Manager through the UI service.
    print("Jobs:")

    # Print one line for each job.
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

    # Build Authorization header using the saved token from .auth_token.
    headers = auth_headers()

    # Stop if no token exists, because viewing job status requires login.
    if not headers:
        return

    # Ask the UI service for details about the selected job.
    response = requests.get(
        f"{UI_SERVICE_URL}/jobs/{args.job_id}",
        headers=headers  # Proves which user is asking for this job.
    )

    # Convert the UI service response into a Python object.
    result = response_json(response)

    # If the HTTP request failed, print the error and stop.
    if response.status_code != 200:
        print_error("Get job status", result)
        return

    # Print the main job information.
    print("Job ID:", result["job_id"])
    print("Status:", result["status"])
    print("Owner:", result["username"])
    print("Mappers:", result["num_mappers"])
    print("Reducers:", result["num_reducers"])

    # Print output path if the job already has one.
    if result.get("output_path"):
        print("Output:", result["output_path"])

    # Print error message if the job failed.
    if result.get("error_message"):
        print("Error:", result["error_message"])

    # Print summary of completed/running/pending/failed tasks.
    print_task_progress(result.get("task_progress"))

    # Get detailed task list, or use an empty list if missing.
    tasks = result.get("tasks") or []

    # If task details exist, print them one by one.
    if tasks:
        print("Task details:")

        # Print each map/reduce task with status and attempt count.
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

    # Build Authorization header using the saved token from .auth_token.
    headers = auth_headers()

    # Stop if no token exists, because retrieving results requires login.
    if not headers:
        return

    # Ask the UI service for the final result content of the selected job.
    response = requests.get(
        f"{UI_SERVICE_URL}/jobs/{args.job_id}/result/content",
        headers=headers  # Proves which user is asking for this result.
    )

    # Convert the UI service response into a Python object.
    result = response_json(response)

    # If the HTTP request failed, print the error and stop.
    if response.status_code != 200:
        print_error("Retrieve result", result)
        return

    # Extract the actual result text from the response.
    content = result.get("content")

    # If there is no content field, say the result is empty.
    if content is None:
        print("Result is empty.")
        return

    # Print the result exactly, avoiding an extra newline if one already exists.
    print(content, end="" if content.endswith("\n") else "\n")


def main():
    """
    Configure the CLI commands and route each command to its handler function.
    """

    # Create the main CLI parser.
    parser = argparse.ArgumentParser(
        description="MapReduce System CLI"
    )

    # Create top-level commands like login, admin, logout, jobs.
    subparsers = parser.add_subparsers(dest="command")

    # Define: python3 cli.py login
    login_parser = subparsers.add_parser("login")
    login_parser.add_argument("--username", required=True)
    login_parser.add_argument("--password", required=False)
    login_parser.set_defaults(func=login)

    # Define: python3 cli.py validate-token
    validate_parser = subparsers.add_parser("validate-token")
    validate_parser.set_defaults(func=validate_token)

    # Define: python3 cli.py admin ...
    admin_parser = subparsers.add_parser("admin")
    admin_subparsers = admin_parser.add_subparsers(dest="admin_command")

    # Define: python3 cli.py admin create-user
    create_user_parser = admin_subparsers.add_parser("create-user")
    create_user_parser.add_argument("--username", required=True)
    create_user_parser.add_argument("--email", required=True)
    create_user_parser.add_argument("--role", required=True, choices=["user", "admin"])
    create_user_parser.set_defaults(func=admin_create_user)

    # Define: python3 cli.py admin list-users
    list_users_parser = admin_subparsers.add_parser("list-users")
    list_users_parser.set_defaults(func=admin_list_users)

    # Define: python3 cli.py admin delete-user
    delete_user_parser = admin_subparsers.add_parser("delete-user")
    delete_user_parser.add_argument("--username", required=True)
    delete_user_parser.set_defaults(func=admin_delete_user)

    # Define: python3 cli.py logout
    logout_parser = subparsers.add_parser("logout")
    logout_parser.set_defaults(func=logout)

    # Define: python3 cli.py jobs ...
    jobs_parser = subparsers.add_parser("jobs")
    jobs_subparsers = jobs_parser.add_subparsers(dest="jobs_command")

    # Define: python3 cli.py jobs submit
    submit_job_parser = jobs_subparsers.add_parser("submit")
    submit_job_parser.add_argument("--input", required=True)
    submit_job_parser.add_argument("--mapper", required=True)
    submit_job_parser.add_argument("--reducer", required=True)
    submit_job_parser.set_defaults(func=submit_job)

    # Define: python3 cli.py jobs list
    list_jobs_parser = jobs_subparsers.add_parser("list")
    list_jobs_parser.set_defaults(func=list_jobs)

    # Define: python3 cli.py jobs view
    job_status_parser = jobs_subparsers.add_parser("view")
    job_status_parser.add_argument("--job-id", required=True)
    job_status_parser.set_defaults(func=get_job_status)

    # Define: python3 cli.py jobs retrieve ...
    retrieve_parser = jobs_subparsers.add_parser("retrieve")
    retrieve_subparsers = retrieve_parser.add_subparsers(dest="retrieve_command")

    # Define: python3 cli.py jobs retrieve result
    result_parser = retrieve_subparsers.add_parser("result")
    result_parser.add_argument("--job-id", required=True)
    result_parser.set_defaults(func=get_job_result)

    # Parse the actual command typed by the user.
    args = parser.parse_args()

    # If the command has a handler function, call it.
    if hasattr(args, "func"):
        args.func(args)

    # If no valid command was given, print help.
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
