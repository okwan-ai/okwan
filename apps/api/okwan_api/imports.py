"""Uploading settlement reports and bank statements (Tier 1 importer).

A tenant uploads a CSV, XLSX or OFX file with a column mapping, and its
rows become one of three canonical kinds (okwan_files). Everything about
the file is decided before anything is stored: it is read in memory,
searched for card numbers (a file holding one is refused whole), mapped,
and only then are the mapped fields of each row written. The bytes are
dropped when the request ends, and are never logged.

Routes take an API key or a dashboard session, like the rest of the
tenant routes: anything outside the caller's subtree answers 404. Upload
is administration of Okwan's own store, not a read of a rail, so it is
not metered and never reaches MCP; agents read imported rows through the
query and reconciliation surfaces.

**Open only where the privacy policy says so.** Outside development an
upload answers 503 until OKWAN_IMPORTS_OPEN=true: storing customer files
is a new kind of data, and the policy has to describe it first (the
importer's step 6). Listing and deleting stay open, so nothing stored is
ever out of the customer's reach.

**Retention.** A file and its rows expire 13 months after upload. Reads
skip an expired file, an upload prunes the tenant's expired files, and
`sweeper()` deletes every tenant's once an hour.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from okwan_files import (
    KINDS,
    MAX_FILE_BYTES,
    CardNumberFound,
    FileRefused,
    Mapping,
    MappingError,
    prepare,
)
from okwan_vault import DuplicateImport, ImportFile
from pydantic import BaseModel, Field

from .admin import _guard
from .auth import admin_actor, get_store
from .ratelimit import IMPORT_TENANT, enforce

logger = logging.getLogger("okwan.imports")

SWEEP_SECONDS = 3600


def imports_open() -> bool:
    if os.environ.get("OKWAN_ENV", "dev") == "dev":
        return True
    return os.environ.get("OKWAN_IMPORTS_OPEN", "").strip().lower() == "true"


class ImportIn(BaseModel):
    filename: str = Field(min_length=1, max_length=200, description="The file's name as uploaded")
    content_base64: str = Field(min_length=1, description="The file's bytes, base64-encoded")
    mapping: Mapping


def public(f: ImportFile) -> dict[str, Any]:
    return {
        "id": f.id, "kind": f.kind, "filename": f.filename, "format": f.format,
        "layout": f.layout, "rows": f.row_count, "skipped": f.skipped,
        "skipped_rows": f.skipped_rows, "card_columns": f.card_columns,
        "first_date": f.first_date.isoformat() if f.first_date else None,
        "last_date": f.last_date.isoformat() if f.last_date else None,
        "created_at": f.created_at.isoformat(), "expires_at": f.expires_at.isoformat(),
        "mapping": f.mapping,
    }


def _decode(content: str) -> bytes:
    if len(content) > (MAX_FILE_BYTES * 4) // 3 + 8:
        raise HTTPException(413, f"the file is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB")
    try:
        return base64.b64decode(content, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(400, "content_base64 is not valid base64") from None


def build_router() -> APIRouter:
    router = APIRouter(prefix="/v1/tenants", tags=["imports"])

    @router.post("/{tenant_id}/imports", status_code=201)
    async def upload(
        tenant_id: str, body: ImportIn, request: Request, actor=Depends(admin_actor)
    ) -> dict[str, Any]:
        """Import a settlement report or bank statement for a tenant in the
        caller's subtree. Refused whole if any cell holds a card number."""
        await _guard(actor, tenant_id)
        if not imports_open():
            raise HTTPException(503, "file imports are not open yet")
        enforce(request, (IMPORT_TENANT, tenant_id))
        data = _decode(body.content_base64)
        try:
            prepared = prepare(data, body.filename, body.mapping)
        except CardNumberFound as exc:
            raise HTTPException(422, str(exc)) from None
        except FileRefused as exc:
            raise HTTPException(400, str(exc)) from None
        except MappingError as exc:
            raise HTTPException(400, {"message": "The mapping does not fit this file.",
                                      "problems": exc.problems}) from None
        finally:
            del data
        n = prepared.normalized
        try:
            record = await get_store().add_import(
                tenant_id, kind=body.mapping.kind, filename=body.filename.strip(),
                format=prepared.format, layout=body.mapping.name,
                mapping=body.mapping.model_dump(mode="json"), sha256=prepared.sha256,
                rows=n.rows, skipped=n.skipped, card_columns=n.card_columns,
                first_date=prepared.first_date, last_date=prepared.last_date,
            )
        except DuplicateImport as exc:
            raise HTTPException(409, {"message": "This file is already imported.",
                                      "existing_id": exc.existing_id}) from None
        return public(record)

    @router.get("/{tenant_id}/imports")
    async def list_imports(
        tenant_id: str, kind: str | None = Query(None), actor=Depends(admin_actor)
    ) -> dict[str, Any]:
        """A tenant's imported files, newest first. Expired files are gone."""
        await _guard(actor, tenant_id)
        if kind is not None and kind not in KINDS:
            raise HTTPException(400, f"kind must be one of {', '.join(KINDS)}")
        files = await get_store().list_imports(tenant_id, kind)
        return {"imports": [public(f) for f in files]}

    @router.get("/{tenant_id}/imports/{file_id}")
    async def get_import(
        tenant_id: str, file_id: str, actor=Depends(admin_actor)
    ) -> dict[str, Any]:
        await _guard(actor, tenant_id)
        found = await get_store().get_import(tenant_id, file_id)
        if found is None:
            raise HTTPException(404, f"no such import: {file_id}")
        return public(found)

    @router.delete("/{tenant_id}/imports/{file_id}", status_code=204)
    async def delete_import(tenant_id: str, file_id: str, actor=Depends(admin_actor)) -> None:
        """Delete a file and every row it produced, at once."""
        await _guard(actor, tenant_id)
        if not await get_store().delete_import(tenant_id, file_id):
            raise HTTPException(404, f"no such import: {file_id}")

    return router


async def sweeper(interval: float = SWEEP_SECONDS) -> None:
    """Delete every tenant's expired imports now, then once an interval.

    Reads already skip expired files, so a missed sweep delays deletion
    and never serves expired rows. A failure is logged and retried on the
    next round; it never stops the API.
    """
    while True:
        try:
            gone = await get_store().sweep_imports()
            if gone:
                logger.info("import sweep deleted %d expired files", gone)
        except Exception:
            logger.warning("import sweep failed", exc_info=True)
        await asyncio.sleep(interval)
