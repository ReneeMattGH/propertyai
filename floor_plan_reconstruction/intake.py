"""Crestion Labs intake bridge.

Receives signed project submissions from the client portal and stores the
submitted floor plan on this machine. The portal's storage links expire after
an hour, so a submission is only durable once its files have been copied here.

Endpoints:
  POST /intake/requests                     receive a signed portal submission
  GET  /intake/requests                     list submissions, newest first
  GET  /intake/requests/{id}                one submission with its stored files
  GET  /intake/requests/{id}/files/{fid}    download a stored file
  POST /intake/requests/{id}/build          start a reconstruction from a stored plan
  POST /intake/requests/{id}/ignore         retire a submission without building it
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional
from urllib.parse import urlsplit
from urllib.request import Request as UrlRequest, urlopen

from fastapi import APIRouter, BackgroundTasks, Body, HTTPException, Request
from fastapi.responses import FileResponse

MAX_FILE_BYTES = 15 * 1024 * 1024
DOWNLOAD_TIMEOUT_S = 12
ALLOWED_HOST_SUFFIX = ".supabase.co"
PLAN_EXTENSIONS = {".png", ".jpg", ".jpeg", ".pdf"}
TERMINAL_BUILD_STATUSES = {"ignored"}

_LOCK = threading.RLock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _secret() -> str:
    return os.environ.get("PRODUCTION_BACKEND_SECRET", "").strip()


def _verify_signature(raw: bytes, signature: Optional[str]) -> None:
    secret = _secret()
    if not secret:
        raise HTTPException(
            status_code=503,
            detail="Intake is not configured. Set PRODUCTION_BACKEND_SECRET on the engine.",
        )
    if not signature or not signature.startswith("sha256="):
        raise HTTPException(status_code=401, detail="Submission is not signed.")
    expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature[len("sha256="):].strip().lower()):
        raise HTTPException(status_code=403, detail="Submission signature is not valid.")


def _read_store(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _write_store(path: Path, store: Dict[str, Any]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(store, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def _configured_hosts() -> List[str]:
    configured = os.environ.get("INTAKE_ALLOWED_HOSTS", "")
    return [host.strip().lower() for host in configured.split(",") if host.strip()]


def _check_url_host(url: str) -> None:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    configured = _configured_hosts()
    allowed = configured or [ALLOWED_HOST_SUFFIX]
    if not any(host == entry or host.endswith(entry.lstrip(".")) for entry in allowed):
        raise ValueError("File links must point at an allowed storage host.")
    # Plain HTTP is only tolerated for a host named exactly in the allow list,
    # which is how a local engine and a local file server are tested together.
    if parts.scheme == "https" or host in configured:
        return
    raise ValueError("File links must use HTTPS.")


def _download(url: str, target: Path) -> Dict[str, Any]:
    _check_url_host(url)
    digest = hashlib.sha256()
    written = 0
    request = UrlRequest(url, headers={"User-Agent": "crestion-labs-intake/1"})
    with urlopen(request, timeout=DOWNLOAD_TIMEOUT_S) as response:
        with open(target, "wb") as handle:
            for chunk in iter(lambda: response.read(1024 * 256), b""):
                written += len(chunk)
                if written > MAX_FILE_BYTES:
                    raise ValueError("File is larger than the 15 MB reconstruction limit.")
                digest.update(chunk)
                handle.write(chunk)
    if written == 0:
        raise ValueError("Stored file is empty.")
    return {"size_bytes": written, "sha256": digest.hexdigest()}


def build_intake_router(
    *,
    data_dir: Path,
    start_build: Callable[..., Awaitable[Dict[str, Any]]],
    valid_styles: tuple,
) -> APIRouter:
    intake_dir = data_dir / "intake"
    store_path = data_dir / "requests.json"
    router = APIRouter()

    def request_dir(request_id: str) -> Path:
        directory = intake_dir / request_id
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def file_view(request_id: str, entry: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "file_id": entry["file_id"],
            "name": entry["name"],
            "category": entry["category"],
            "kind": entry["kind"],
            "mime_type": entry["mime_type"],
            "size_bytes": entry["size_bytes"],
            "stored": entry["stored"],
            "stored_path": entry.get("stored_path"),
            "sha256": entry.get("sha256"),
            "error": entry.get("error"),
            "url": f"/intake/requests/{request_id}/files/{entry['file_id']}",
        }

    def summary(record: Dict[str, Any]) -> Dict[str, Any]:
        files = record.get("files", [])
        plans = [entry for entry in files if entry.get("kind") == "plan"]
        return {
            "request_id": record["request_id"],
            "project_id": record["project_id"],
            "received_at": record["received_at"],
            "updated_at": record["updated_at"],
            "status": record["status"],
            "project_name": record["project"].get("name", ""),
            "client": {
                "email": record["client"].get("email", ""),
                "name": " ".join(
                    part
                    for part in (
                        record["client"].get("firstName", ""),
                        record["client"].get("lastName", ""),
                    )
                    if part
                ).strip(),
            },
            "company_name": (record.get("company") or {}).get("name", ""),
            "plan_count": len(plans),
            "plans_ready": any(entry.get("stored") for entry in plans),
            "file_count": len(files),
            "build": record.get("build"),
            "issues": record.get("issues", []),
        }

    @router.post("/intake/requests")
    async def receive_request(background_tasks: BackgroundTasks, request: Request):
        raw = await request.body()
        _verify_signature(raw, request.headers.get("x-crestion-signature"))
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="Submission body is not JSON.") from exc

        project = payload.get("project") or {}
        project_id = str(project.get("id") or payload.get("requestId") or "").strip()
        if not project_id:
            raise HTTPException(status_code=400, detail="Submission has no project identifier.")

        with _LOCK:
            store = _read_store(store_path)
            for record in store.values():
                if record.get("project_id") == project_id and record.get("status") not in TERMINAL_BUILD_STATUSES:
                    return {"ok": True, "deduplicated": True, "request": summary(record)}

        style = str(project.get("furnishingStyle") or "").strip().lower()
        if style not in valid_styles:
            style = "modern"

        request_id = str(uuid.uuid4())
        target_dir = request_dir(request_id)
        files: List[Dict[str, Any]] = []
        issues: List[str] = []
        for incoming in payload.get("files") or []:
            name = str(incoming.get("name") or "unnamed")
            extension = Path(name).suffix.lower()
            entry = {
                "file_id": str(uuid.uuid4()),
                "name": name,
                "category": str(incoming.get("category") or "other"),
                "kind": str(incoming.get("kind") or "file"),
                "mime_type": str(incoming.get("mimeType") or ""),
                "size_bytes": int(incoming.get("sizeBytes") or 0),
                "declared_sha256": str(incoming.get("sha256") or ""),
                "stored": False,
                "stored_path": None,
                "sha256": None,
                "error": None,
            }
            url = str(incoming.get("signedUrl") or "")
            if not url:
                entry["error"] = "No storage link was supplied."
                issues.append(f"{name}: no storage link was supplied.")
            else:
                stored_name = f"{entry['file_id']}{extension or '.bin'}"
                target = target_dir / stored_name
                try:
                    result = _download(url, target)
                    entry["stored"] = True
                    entry["stored_path"] = str(target)
                    entry["sha256"] = result["sha256"]
                    entry["size_bytes"] = result["size_bytes"]
                except Exception as exc:
                    target.unlink(missing_ok=True)
                    entry["error"] = str(exc)
                    issues.append(f"{name}: {exc}")
            if entry["kind"] == "plan" and entry["stored"] and extension not in PLAN_EXTENSIONS:
                issues.append(f"{name}: plan files must be PNG, JPG or PDF.")
            files.append(entry)

        if not any(entry["kind"] == "plan" and entry["stored"] for entry in files):
            issues.append("No floor plan could be stored, so this submission cannot be built yet.")

        now = _now()
        record = {
            "request_id": request_id,
            "project_id": project_id,
            "portal": payload.get("portal", "crestionlabs"),
            "version": payload.get("version"),
            "submitted_at": payload.get("submittedAt"),
            "received_at": now,
            "updated_at": now,
            "status": "new",
            "client": payload.get("client") or {},
            "company": payload.get("company"),
            "project": project,
            "files": files,
            "issues": issues,
            "build": None,
        }
        with _LOCK:
            store = _read_store(store_path)
            store[request_id] = record
            _write_store(store_path, store)
        return {"ok": True, "deduplicated": False, "request": summary(record)}

    @router.get("/intake/requests")
    async def list_requests(status: Optional[str] = None, limit: int = 100):
        with _LOCK:
            store = _read_store(store_path)
        records = sorted(store.values(), key=lambda item: item.get("received_at", ""), reverse=True)
        if status:
            records = [record for record in records if record.get("status") == status]
        counts: Dict[str, int] = {}
        for record in store.values():
            counts[record.get("status", "new")] = counts.get(record.get("status", "new"), 0) + 1
        return {
            "total": len(store),
            "counts": counts,
            "requests": [summary(record) for record in records[: max(1, min(limit, 200))]],
        }

    @router.get("/intake/requests/{request_id}")
    async def get_request(request_id: str):
        with _LOCK:
            record = _read_store(store_path).get(request_id)
        if not record:
            raise HTTPException(status_code=404, detail="Submission not found.")
        detail = summary(record)
        detail["files"] = [file_view(request_id, entry) for entry in record.get("files", [])]
        detail["project"] = record.get("project") or {}
        detail["company"] = record.get("company")
        detail["client"] = record.get("client") or {}
        detail["submitted_at"] = record.get("submitted_at")
        return detail

    @router.get("/intake/requests/{request_id}/files/{file_id}")
    async def get_file(request_id: str, file_id: str):
        if "/" in request_id or ".." in request_id or "/" in file_id or ".." in file_id:
            raise HTTPException(status_code=400, detail="Invalid file reference.")
        with _LOCK:
            record = _read_store(store_path).get(request_id)
        if not record:
            raise HTTPException(status_code=404, detail="Submission not found.")
        entry = next(
            (item for item in record.get("files", []) if item["file_id"] == file_id),
            None,
        )
        if not entry or not entry.get("stored") or not entry.get("stored_path"):
            raise HTTPException(status_code=404, detail="That file was not stored.")
        path = Path(entry["stored_path"])
        if not path.is_file():
            raise HTTPException(status_code=410, detail="The stored copy of that file is gone.")
        return FileResponse(path, filename=entry["name"], media_type=entry["mime_type"] or None)

    @router.post("/intake/requests/{request_id}/build")
    async def build_request(
        request_id: str,
        background_tasks: BackgroundTasks,
        body: Dict[str, Any] = Body(default={}),
    ):
        with _LOCK:
            store = _read_store(store_path)
            record = store.get(request_id)
        if not record:
            raise HTTPException(status_code=404, detail="Submission not found.")
        if record.get("status") == "building" and (record.get("build") or {}).get("job_id"):
            raise HTTPException(
                status_code=409,
                detail="This submission already has a reconstruction running.",
            )

        project = record.get("project") or {}
        plan = None
        for entry in record.get("files", []):
            if entry.get("kind") != "plan" or not entry.get("stored"):
                continue
            stored_path = Path(str(entry.get("stored_path") or ""))
            if stored_path.is_file() and stored_path.suffix.lower() in PLAN_EXTENSIONS:
                plan = entry
                break
        if not plan:
            raise HTTPException(
                status_code=422,
                detail="No stored floor plan is available. Re-submit the project from the portal.",
            )

        def chosen(key: str, fallback: Any) -> Any:
            value = body.get(key)
            return fallback if value in (None, "") else value

        style = _validated_pick(body.get("style"), project.get("furnishingStyle"), valid_styles)
        num_floors = max(1, min(5, int(chosen("num_floors", 1) or 1)))
        known_width = _dimension(chosen("known_width", project.get("planWidthMeters")))
        known_length = _dimension(chosen("known_length", project.get("planLengthMeters")))
        path = Path(plan["stored_path"])
        handle = open(path, "rb")
        try:
            job = await start_build(
                background_tasks,
                file_handle=handle,
                filename=plan["name"],
                project_name=str(project.get("name") or record["project_id"])[:120],
                style=style,
                num_floors=num_floors,
                known_width=known_width,
                known_length=known_length,
                property_id=record["project_id"],
            )
        finally:
            handle.close()

        with _LOCK:
            store = _read_store(store_path)
            current = store.get(request_id)
            if current:
                current["status"] = "building"
                current["updated_at"] = _now()
                current["build"] = {
                    "job_id": job.get("job_id") or job.get("id"),
                    "style": style,
                    "num_floors": num_floors,
                    "known_width_meters": known_width,
                    "known_length_meters": known_length,
                    "scale_verified": bool(known_width and known_length),
                    "started_at": _now(),
                }
                store[request_id] = current
                _write_store(store_path, store)
        return {"ok": True, "request": summary(current or record), "job": job}

    @router.post("/intake/requests/{request_id}/ignore")
    async def ignore_request(request_id: str):
        with _LOCK:
            store = _read_store(store_path)
            record = store.get(request_id)
            if not record:
                raise HTTPException(status_code=404, detail="Submission not found.")
            record["status"] = "ignored"
            record["updated_at"] = _now()
            store[request_id] = record
            _write_store(store_path, store)
        return {"ok": True, "request": summary(record)}

    return router


def _validated_pick(explicit: Any, fallback: Any, valid_styles: tuple) -> str:
    for candidate in (explicit, fallback):
        value = str(candidate or "").strip().lower()
        if value in valid_styles:
            return value
    return "modern"


def _dimension(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < 2 or number > 100:
        return None
    return number
