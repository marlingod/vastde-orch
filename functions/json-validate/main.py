"""VAST DataEngine function: validate uploaded JSON, tag the object.

Triggered by ElementCreated events on the raw-json source view. For each new
object, parses it as JSON and writes S3 tags describing what was found:
  valid           = "true" | "false"
  schema_kind     = "array" | "object" | "scalar" | "null"  (when valid)
  top_level_keys  = N        (when object) or len (when array)
  error           = short message (when invalid)

Reads VAST S3 via env-injected credentials (config.yaml or runtime).
"""

import json
import os
import urllib3

import boto3
from botocore.config import Config

urllib3.disable_warnings()  # lab cert is self-signed
_S3 = None  # populated in init()


def init(ctx):
    """Cold-start: build the S3 client once."""
    global _S3
    endpoint = os.environ.get("S3_ENDPOINT_URL") or os.environ.get("AWS_ENDPOINT_URL_S3")
    _S3 = boto3.client(
        "s3",
        endpoint_url=endpoint,
        verify=False,
        config=Config(signature_version="s3v4", retries={"max_attempts": 3}),
    )
    ctx.logger.info(f"init: S3 client built (endpoint={endpoint})")


def handler(ctx, event):
    """Per-event: read object, classify JSON, write tags, log + return."""
    data = _extract_event_data(event)
    bucket, key = _extract_bucket_key(data)
    if not (bucket and key):
        ctx.logger.error(f"event missing bucket/key: type={getattr(event, 'type', '?')} data={data}")
        return {"status": "error", "error": "no bucket/key in event"}

    obj = _S3.get_object(Bucket=bucket, Key=key)
    body = obj["Body"].read()

    tags = _classify(body)
    tagset = [{"Key": k, "Value": v} for k, v in tags.items()]
    _S3.put_object_tagging(Bucket=bucket, Key=key, Tagging={"TagSet": tagset})

    ctx.logger.info(f"processed s3://{bucket}/{key} tags={tags}")
    return {"status": "ok", "bucket": bucket, "key": key, "tags": tags}


def _classify(body):
    """Parse body as JSON, return tag set."""
    try:
        doc = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {"valid": "false", "error": type(exc).__name__ + ": " + str(exc)[:120]}
    if isinstance(doc, dict):
        return {"valid": "true", "schema_kind": "object", "top_level_keys": str(len(doc))}
    if isinstance(doc, list):
        return {"valid": "true", "schema_kind": "array", "top_level_keys": str(len(doc))}
    if doc is None:
        return {"valid": "true", "schema_kind": "null"}
    return {"valid": "true", "schema_kind": "scalar"}


def _extract_event_data(event):
    """CloudEvents: prefer .get_data(); fall back if it's a raw dict."""
    if hasattr(event, "get_data"):
        try:
            return event.get_data() or {}
        except Exception:
            pass
    return event if isinstance(event, dict) else {}


def _extract_bucket_key(data):
    """Find bucket/key in the event payload — handles three plausible shapes."""
    if not isinstance(data, dict):
        return None, None
    if "bucket" in data and "key" in data:
        return data["bucket"], data["key"]
    if "s3" in data:
        s3 = data["s3"] or {}
        return (s3.get("bucket", {}).get("name"), s3.get("object", {}).get("key"))
    if "detail" in data:
        d = data["detail"] or {}
        return d.get("bucket"), (d.get("object") or {}).get("key")
    return None, None
