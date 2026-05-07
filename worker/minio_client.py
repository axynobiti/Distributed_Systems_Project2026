import os

from minio import Minio


MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "minio:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin123")
MINIO_BUCKET = os.getenv("MINIO_BUCKET", "mapreduce")


def get_client():
    return Minio(
        MINIO_ENDPOINT,
        access_key=MINIO_ACCESS_KEY,
        secret_key=MINIO_SECRET_KEY,
        secure=False,
    )


def ensure_bucket(bucket_name=MINIO_BUCKET):
    client = get_client()

    if not client.bucket_exists(bucket_name):
        client.make_bucket(bucket_name)


def download_file(object_name, local_path, bucket_name=MINIO_BUCKET):
    ensure_bucket(bucket_name)

    client = get_client()
    client.fget_object(bucket_name, object_name, local_path)


def upload_file(local_path, object_name, bucket_name=MINIO_BUCKET):
    ensure_bucket(bucket_name)

    client = get_client()
    client.fput_object(bucket_name, object_name, local_path)
