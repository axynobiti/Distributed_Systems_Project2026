import os
import subprocess

from minio_client import download_file, upload_file


def require_env(name):
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def main():
    job_id = require_env("JOB_ID")
    task_id = require_env("TASK_ID")
    task_type = require_env("TASK_TYPE")

    input_path = require_env("INPUT_PATH")
    code_path = require_env("CODE_PATH")
    output_path = require_env("OUTPUT_PATH")

    print("Starting worker")
    print(f"JOB_ID={job_id}")
    print(f"TASK_ID={task_id}")
    print(f"TASK_TYPE={task_type}")

    local_input = "/tmp/input.txt"
    local_code = "/tmp/job_code.py"
    local_output = "/tmp/output.txt"

    print(f"Downloading input: {input_path}")
    download_file(input_path, local_input)

    print(f"Downloading code: {code_path}")
    download_file(code_path, local_code)

    print("Executing task code")

    subprocess.run(
        [
            "python",
            local_code,
            "--input",
            local_input,
            "--output",
            local_output,
            "--task-type",
            task_type,
        ],
        check=True,
    )

    print(f"Uploading output: {output_path}")
    upload_file(local_output, output_path)

    print("Worker finished successfully")


if __name__ == "__main__":
    main()
