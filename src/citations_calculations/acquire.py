"""Acquire local uploads or preserve a host tool's response without inventing connector access."""

import json
from decimal import Decimal
from pathlib import Path
from datetime import datetime

from .extract import extract, json_units, check_payload, public_uri, MAX_BYTES, MAX_UNITS
from .store import Store, EvidenceError, now


def timestamp(value):
    try:
        time = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError) as e:
        raise EvidenceError("Dates need an ISO timestamp with a timezone") from e
    if time.tzinfo is None:
        raise EvidenceError("Timestamp has no timezone")
    return time


def incomplete(value):
    if isinstance(value, dict):
        markers = {"next_cursor", "nextCursor", "next_page_token", "has_more", "hasMore", "truncated", "isTruncated"}
        return any(bool(v) for k, v in value.items() if k in markers) or any(incomplete(v) for v in value.values())
    if isinstance(value, list):
        return any(incomplete(v) for v in value)
    return False


def ingest(root, path, title=None, uri=None, format=None, completeness="complete", published_at=None):
    path = Path(path)
    if path.name.startswith(".env"):
        raise EvidenceError("Environment files are not evidence")
    if completeness not in ("complete", "partial", "unknown"):
        raise EvidenceError("Invalid completeness flag")
    if uri:
        public_uri(uri)
    if published_at:
        timestamp(published_at)
    raw = path.read_bytes()
    extracted = extract(raw, format or path.suffix.lower().lstrip("."))
    provenance = {
        "title": title or path.name,
        "origin": "web_capture" if uri else "uploaded_file",
        "uri": uri,
        "filename": path.name,
        "acquired_at": now(),
        "published_at": published_at,
        "completeness": completeness,
        "authenticity": "Local snapshot; origin and publication date supplied by the caller",
    }
    sid = Store(root).add(raw, extracted, provenance)
    return Store(root).search(sid)


def capture(root, path):
    """The raw envelope must be written from the observed tool response, never reconstructed from an answer."""
    raw = Path(path).read_bytes()
    if len(raw) > MAX_BYTES:
        raise EvidenceError("Tool capture exceeds the 20 MiB limit")
    envelope = json.loads(raw)
    check_payload(envelope)
    if (
        not isinstance(envelope, dict)
        or not isinstance(envelope.get("tool_name"), str)
        or not envelope.get("tool_name")
        or "tool_result" not in envelope
    ):
        raise EvidenceError("Tool capture requires tool_name and the unmodified tool_result")
    if (
        envelope.get("is_error")
        or envelope.get("isError")
        or (isinstance(envelope["tool_result"], dict) and envelope["tool_result"].get("isError"))
    ):
        raise EvidenceError("An errored tool response is not source evidence")
    completeness = envelope.get("completeness", "unknown")
    if completeness not in ("complete", "partial", "unknown"):
        raise EvidenceError("Invalid completeness flag")
    if incomplete(envelope):
        completeness = "partial"
    if envelope.get("retrieved_at"):
        timestamp(envelope["retrieved_at"])
    uri = envelope.get("source_url")
    if uri:
        public_uri(uri)
    extracted = {
        "format": "tool_capture",
        "units": json_units(json.loads(raw, parse_float=Decimal)["tool_result"], "/tool_result"),
        "warnings": ["This verifies the captured response, not the original service or a full document"],
    }
    if len(extracted["units"]) > MAX_UNITS:
        raise EvidenceError("Tool capture has too many evidence units")
    provenance = {
        "title": envelope.get("title") or envelope["tool_name"],
        "origin": "tool_capture",
        "uri": uri,
        "tool_name": envelope["tool_name"],
        "tool_args": envelope.get("tool_args", {}),
        "tool_call_id": envelope.get("tool_call_id"),
        "acquired_at": now(),
        "retrieved_at": envelope.get("retrieved_at"),
        "published_at": None,
        "completeness": completeness,
        "authenticity": "Caller-preserved tool response; no automatic interception or provider attestation",
    }
    sid = Store(root).add(raw, extracted, provenance)
    return Store(root).search(sid)
