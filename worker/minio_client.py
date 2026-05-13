import os

from minio import Minio


MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "minio:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin123")
MINIO_BUCKET = os.getenv("MINIO_BUCKET", "mapreduce")
MINIO_SECURE = os.getenv("MINIO_SECURE", "false").lower() == "true"


def get_client():
    return Minio(
        MINIO_ENDPOINT,
        access_key=MINIO_ACCESS_KEY,
        secret_key=MINIO_SECRET_KEY,
        secure=MINIO_SECURE,
    )


def parse_minio_path(path):
    if path.startswith("s3://"):
        without_scheme = path[len("s3://"):]
        bucket_name, object_name = without_scheme.split("/", 1)
        return bucket_name, object_name

    return MINIO_BUCKET, path.lstrip("/")


def ensure_bucket(bucket_name=MINIO_BUCKET):
    client = get_client()

    if not client.bucket_exists(bucket_name):
        client.make_bucket(bucket_name)


def download_file(path, local_path):
    bucket_name, object_name = parse_minio_path(path)
    ensure_bucket(bucket_name)

    client = get_client()
    client.fget_object(bucket_name, object_name, local_path)


def upload_file(local_path, path):
    bucket_name, object_name = parse_minio_path(path)
    ensure_bucket(bucket_name)

    client = get_client()
    client.fput_object(bucket_name, object_name, local_path)
