import importlib.util
import inspect
import json
import os
import sys
import tempfile
import traceback
from dataclasses import dataclass
from typing import Any

import requests
from minio import Minio


def read_bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)

    if value is None:
        return default

    return value.lower() in ["1", "true", "yes", "on"]


def require_env(name: str) -> str:
    value = os.getenv(name)

    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")

    return value


@dataclass
class WorkerConfig:
    task_type: str
    job_id: str
    task_id: str
    task_index: str
    input_path: str
    user_code_path: str
    output_path: str
    minio_endpoint: str
    minio_bucket: str
    minio_access_key: str
    minio_secret_key: str
    minio_secure: bool
    manager_url: str
    worker_service_token: str
    pod_name: str

    @classmethod
    def from_env(cls):
        return cls(
            task_type=require_env("TASK_TYPE"),
            job_id=require_env("JOB_ID"),
            task_id=require_env("TASK_ID"),
            task_index=require_env("TASK_INDEX"),
            input_path=require_env("INPUT_PATH"),
            user_code_path=require_env("USER_CODE_PATH"),
            output_path=require_env("OUTPUT_PATH"),
            minio_endpoint=require_env("MINIO_ENDPOINT"),
            minio_bucket=require_env("MINIO_BUCKET"),
            minio_access_key=require_env("MINIO_ACCESS_KEY"),
            minio_secret_key=require_env("MINIO_SECRET_KEY"),
            minio_secure=read_bool_env("MINIO_SECURE"),
            manager_url=os.getenv("MANAGER_URL", ""),
            worker_service_token=os.getenv("WORKER_SERVICE_TOKEN", ""),
            pod_name=os.getenv("HOSTNAME", ""),
        )


def build_minio_client(config: WorkerConfig) -> Minio:
    return Minio(
        config.minio_endpoint,
        access_key=config.minio_access_key,
        secret_key=config.minio_secret_key,
        secure=config.minio_secure,
    )


def parse_minio_path(path: str, default_bucket: str):
    if path.startswith("s3://"):
        without_scheme = path[len("s3://"):]
        bucket, object_name = without_scheme.split("/", 1)
        return bucket, object_name

    return default_bucket, path.lstrip("/")


def download_object(minio_client: Minio, path: str, default_bucket: str) -> bytes:
    bucket, object_name = parse_minio_path(path, default_bucket)
    response = minio_client.get_object(bucket, object_name)

    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


def upload_object(
    minio_client: Minio,
    path: str,
    default_bucket: str,
    content: bytes,
    content_type: str = "application/json",
):
    bucket, object_name = parse_minio_path(path, default_bucket)

    with tempfile.SpooledTemporaryFile() as file_obj:
        file_obj.write(content)
        file_obj.seek(0)
        minio_client.put_object(
            bucket,
            object_name,
            file_obj,
            length=len(content),
            content_type=content_type,
        )


def load_user_module(code_bytes: bytes):
    with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as code_file:
        code_file.write(code_bytes)
        code_path = code_file.name

    module_name = f"user_code_{os.getpid()}"
    spec = importlib.util.spec_from_file_location(module_name, code_path)

    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load user code")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    return module


def get_user_function(module: Any, names: list[str]):
    for name in names:
        function = getattr(module, name, None)

        if callable(function):
            return function

    raise RuntimeError(f"User code must define one of: {', '.join(names)}")


def call_user_function(function, *args):
    signature = inspect.signature(function)
    parameter_count = len(signature.parameters)

    if parameter_count == 1:
        return function(args[-1])

    if parameter_count == 2:
        return function(args[0], args[1])

    raise RuntimeError(
        f"User function must accept 1 or 2 arguments, got {parameter_count}"
    )


def normalize_pairs(result):
    if result is None:
        return []

    if isinstance(result, dict):
        return list(result.items())

    if isinstance(result, tuple) and len(result) == 2:
        return [result]

    if isinstance(result, list):
        return result

    if inspect.isgenerator(result):
        return list(result)

    raise RuntimeError("Mapper/reducer output must be pairs, a dict, or a generator")


def encode_pairs_jsonl(pairs) -> bytes:
    lines = []

    for pair in pairs:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise RuntimeError(f"Invalid output pair: {pair!r}")

        lines.append(json.dumps([pair[0], pair[1]]))

    return ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")


def run_map_task(input_bytes: bytes, code_bytes: bytes) -> bytes:
    module = load_user_module(code_bytes)
    mapper = get_user_function(module, ["map", "mapper", "map_func"])
    input_text = input_bytes.decode("utf-8")
    output_pairs = []

    for line_number, line in enumerate(input_text.splitlines()):
        result = call_user_function(mapper, line_number, line)
        output_pairs.extend(normalize_pairs(result))

    return encode_pairs_jsonl(output_pairs)


def parse_reduce_input(input_bytes: bytes):
    input_text = input_bytes.decode("utf-8").strip()

    if not input_text:
        return {}

    try:
        parsed = json.loads(input_text)
    except json.JSONDecodeError:
        parsed = [
            json.loads(line)
            for line in input_text.splitlines()
            if line.strip()
        ]

    if isinstance(parsed, dict):
        return parsed

    grouped = {}

    for pair in parsed:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise RuntimeError(f"Invalid reduce input pair: {pair!r}")

        key, value = pair
        grouped.setdefault(key, []).append(value)

    return grouped


def run_reduce_task(input_bytes: bytes, code_bytes: bytes) -> bytes:
    module = load_user_module(code_bytes)
    reducer = get_user_function(module, ["reduce", "reducer", "reduce_func"])
    grouped_values = parse_reduce_input(input_bytes)
    output_pairs = []

    for key, values in grouped_values.items():
        result = call_user_function(reducer, key, values)
        output_pairs.extend(normalize_pairs(result))

    return encode_pairs_jsonl(output_pairs)


def manager_headers(config: WorkerConfig):
    if not config.worker_service_token:
        return {}

    return {
        "Authorization": f"Bearer {config.worker_service_token}"
    }


def call_manager(config: WorkerConfig, action: str, payload: dict):
    if not config.manager_url:
        return

    if not config.worker_service_token:
        print("WORKER_SERVICE_TOKEN is not set; skipping Manager callback")
        return

    url = (
        f"{config.manager_url.rstrip('/')}/jobs/{config.job_id}"
        f"/tasks/{config.task_id}/{action}"
    )

    response = requests.post(
        url,
        json=payload,
        headers=manager_headers(config),
        timeout=10,
    )
    response.raise_for_status()


def report_start(config: WorkerConfig):
    call_manager(
        config,
        "start",
        {
            "kubernetes_pod_name": config.pod_name or None,
        },
    )


def report_complete(config: WorkerConfig):
    call_manager(
        config,
        "complete",
        {
            "output_path": config.output_path,
        },
    )


def report_fail(config: WorkerConfig, error_message: str):
    try:
        call_manager(
            config,
            "fail",
            {
                "error_message": error_message,
            },
        )
    except Exception as callback_error:
        print(f"Could not report task failure to Manager: {callback_error}")


def run_worker():
    config = WorkerConfig.from_env()
    minio_client = build_minio_client(config)

    try:
        report_start(config)

        input_bytes = download_object(
            minio_client,
            config.input_path,
            config.minio_bucket,
        )
        code_bytes = download_object(
            minio_client,
            config.user_code_path,
            config.minio_bucket,
        )

        if config.task_type == "map":
            output_bytes = run_map_task(input_bytes, code_bytes)
        elif config.task_type == "reduce":
            output_bytes = run_reduce_task(input_bytes, code_bytes)
        else:
            raise RuntimeError(f"Unsupported TASK_TYPE: {config.task_type}")

        upload_object(
            minio_client,
            config.output_path,
            config.minio_bucket,
            output_bytes,
        )
        report_complete(config)
    except Exception as exc:
        error_message = f"{type(exc).__name__}: {exc}"
        report_fail(config, error_message)
        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(run_worker())
