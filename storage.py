from __future__ import annotations

import os
import mimetypes
import secrets
from pathlib import Path
from dataclasses import dataclass

LOCAL_ROOT = Path(os.getenv('LOCAL_STORAGE_ROOT', Path(__file__).resolve().parents[1] / 'data' / 'uploads'))
STORAGE_PROVIDER = os.getenv('OBJECT_STORAGE_PROVIDER', 'local').lower()
MAX_UPLOAD_MB = int(os.getenv('MAX_UPLOAD_MB', '25'))

@dataclass(frozen=True)
class StoredFile:
    storage_key: str
    original_name: str
    content_type: str
    size_bytes: int
    public_url: str | None = None


def _safe_ext(name: str) -> str:
    ext = Path(name).suffix.lower()
    return ext if len(ext) <= 12 else ''


def store_local(data: bytes, original_name: str, content_type: str | None) -> StoredFile:
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise ValueError(f'File exceeds {MAX_UPLOAD_MB} MB limit')
    LOCAL_ROOT.mkdir(parents=True, exist_ok=True)
    key = f"{secrets.token_hex(16)}{_safe_ext(original_name)}"
    target = LOCAL_ROOT / key
    target.write_bytes(data)
    return StoredFile(key, original_name, content_type or mimetypes.guess_type(original_name)[0] or 'application/octet-stream', len(data), f'/api/v1/files/{key}')


def store(data: bytes, original_name: str, content_type: str | None) -> StoredFile:
    if STORAGE_PROVIDER == 'local':
        return store_local(data, original_name, content_type)
    if STORAGE_PROVIDER == 's3':
        try:
            import boto3
        except ImportError as exc:
            raise RuntimeError('boto3 is required when OBJECT_STORAGE_PROVIDER=s3') from exc
        bucket = os.environ['OBJECT_STORAGE_BUCKET']
        client = boto3.client(
            's3',
            endpoint_url=os.getenv('OBJECT_STORAGE_ENDPOINT') or None,
            aws_access_key_id=os.getenv('OBJECT_STORAGE_ACCESS_KEY'),
            aws_secret_access_key=os.getenv('OBJECT_STORAGE_SECRET_KEY'),
            region_name=os.getenv('OBJECT_STORAGE_REGION') or None,
        )
        key = f"uploads/{secrets.token_hex(16)}{_safe_ext(original_name)}"
        data_len = len(data)
        if data_len > MAX_UPLOAD_MB * 1024 * 1024:
            raise ValueError(f'File exceeds {MAX_UPLOAD_MB} MB limit')
        client.put_object(Bucket=bucket, Key=key, Body=data, ContentType=content_type or 'application/octet-stream')
        return StoredFile(key, original_name, content_type or 'application/octet-stream', data_len, None)
    raise RuntimeError(f'Unknown OBJECT_STORAGE_PROVIDER: {STORAGE_PROVIDER}')


def signed_url(storage_key: str, expires_seconds: int = 900) -> str | None:
    if STORAGE_PROVIDER == 'local':
        return f'/api/v1/files/{storage_key}'
    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError('boto3 is required when OBJECT_STORAGE_PROVIDER=s3') from exc
    bucket = os.environ['OBJECT_STORAGE_BUCKET']
    client = boto3.client(
        's3',
        endpoint_url=os.getenv('OBJECT_STORAGE_ENDPOINT') or None,
        aws_access_key_id=os.getenv('OBJECT_STORAGE_ACCESS_KEY'),
        aws_secret_access_key=os.getenv('OBJECT_STORAGE_SECRET_KEY'),
        region_name=os.getenv('OBJECT_STORAGE_REGION') or None,
    )
    return client.generate_presigned_url('get_object', Params={'Bucket': bucket, 'Key': storage_key}, ExpiresIn=expires_seconds)
