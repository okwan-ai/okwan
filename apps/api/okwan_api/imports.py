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

**Mappings.** An upload names its mapping or a saved one
(`/import-layouts/{name}`), and a preview reads a file without storing
it: its columns, its first rows, and a generic starting mapping per kind
from the column names alone. There are no processor presets until real
dealer files arrive (§9 2026-11-07).

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
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from okwan_files import (
    KINDS,
    MAX_FILE_BYTES,
    CardNumberFound,
    FileRefused,
    Mapping,
    MappingError,
    describe,
    prepare,
    read,
)
from okwan_files.guard import is_card_column, last4
from okwan_files.mapping import suggest
from okwan_vault import DuplicateImport, ImportFile
from pydantic import BaseModel, Field, ValidationError

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
    mapping: Mapping | None = Field(None, description="How the file's columns fill the kind's fields")
    layout: str | None = Field(None, description="Or the name of a saved mapping")


class PreviewIn(BaseModel):
    filename: str = Field(min_length=1, max_length=200)
    content_base64: str = Field(min_length=1)
    header_row: int = Field(1, ge=1, le=50, description="The row holding the column names")


#: Rows a preview shows.
SAMPLE_ROWS = 10
_LAYOUT_NAME = r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,59}$"


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
        caller's subtree, with a mapping or a saved one by name. Refused
        whole if any cell holds a card number."""
        await _guard(actor, tenant_id)
        if not imports_open():
            raise HTTPException(503, "file imports are not open yet")
        mapping = await _mapping_for(tenant_id, body)
        enforce(request, (IMPORT_TENANT, tenant_id))
        data = _decode(body.content_base64)
        try:
            prepared = prepare(data, body.filename, mapping)
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
                tenant_id, kind=mapping.kind, filename=body.filename.strip(),
                format=prepared.format, layout=mapping.name,
                mapping=mapping.model_dump(mode="json"), sha256=prepared.sha256,
                rows=n.rows, skipped=n.skipped, card_columns=n.card_columns,
                first_date=prepared.first_date, last_date=prepared.last_date,
            )
        except DuplicateImport as exc:
            raise HTTPException(409, {"message": "This file is already imported.",
                                      "existing_id": exc.existing_id}) from None
        return public(record)

    @router.post("/{tenant_id}/imports/preview")
    async def preview(
        tenant_id: str, body: PreviewIn, request: Request, actor=Depends(admin_actor)
    ) -> dict[str, Any]:
        """Read a file without storing anything: its columns, its first rows,
        and a mapping to start from for each kind. The same card guard as an
        upload; a card column shows its last four digits at most."""
        await _guard(actor, tenant_id)
        if not imports_open():
            raise HTTPException(503, "file imports are not open yet")
        enforce(request, (IMPORT_TENANT, tenant_id))
        data = _decode(body.content_base64)
        try:
            sheet = read(data, body.filename, header_row=body.header_row)
        except CardNumberFound as exc:
            raise HTTPException(422, str(exc)) from None
        except FileRefused as exc:
            raise HTTPException(400, str(exc)) from None
        finally:
            del data
        cards = [j for j, h in enumerate(sheet.header) if h and is_card_column(h)]

        def shown(row: list[str]) -> list[str]:
            out = row[: len(sheet.header)]
            for j in cards:
                digits = last4(out[j]) if out[j] else None
                out[j] = f"•••• {digits}" if digits else ""
            return out

        return {
            "format": sheet.format, "header_row": body.header_row, "header": sheet.header,
            "rows": len(sheet.rows), "sample": [shown(list(r)) for r in sheet.rows[:SAMPLE_ROWS]],
            "card_columns": [sheet.header[j] for j in cards],
            "fields": {k: describe(k) for k in KINDS},
            "suggested": {k: suggest(k, sheet.header) for k in KINDS},
        }

    @router.get("/{tenant_id}/import-layouts")
    async def list_layouts(tenant_id: str, actor=Depends(admin_actor)) -> dict[str, Any]:
        """A tenant's saved mappings, by name."""
        await _guard(actor, tenant_id)
        return {"layouts": [_layout(x) for x in await get_store().list_layouts(tenant_id)]}

    @router.put("/{tenant_id}/import-layouts/{name}")
    async def save_layout(
        tenant_id: str, name: str, body: Mapping, actor=Depends(admin_actor)
    ) -> dict[str, Any]:
        """Save a mapping under a name, replacing one of the same name. The
        name in the path is the one recorded on every row it produces."""
        await _guard(actor, tenant_id)
        if not re.fullmatch(_LAYOUT_NAME, name):
            raise HTTPException(400, "a mapping's name is 1 to 60 letters, digits, spaces, . _ or -")
        mapping = body.model_copy(update={"name": name})
        try:
            saved = await get_store().save_layout(tenant_id, name, mapping.model_dump(mode="json"))
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        return _layout(saved)

    @router.delete("/{tenant_id}/import-layouts/{name}", status_code=204)
    async def delete_layout(tenant_id: str, name: str, actor=Depends(admin_actor)) -> None:
        await _guard(actor, tenant_id)
        if not await get_store().delete_layout(tenant_id, name):
            raise HTTPException(404, f"no saved mapping named {name}")

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


def _layout(x: dict) -> dict[str, Any]:
    return {"name": x["name"], "kind": x["kind"], "mapping": x["mapping"],
            "created_at": x["created_at"].isoformat(), "updated_at": x["updated_at"].isoformat()}


async def _mapping_for(tenant_id: str, body: ImportIn) -> Mapping:
    """The upload's own mapping, or its saved one by name; exactly one."""
    if (body.mapping is None) == (body.layout is None):
        raise HTTPException(400, "send a mapping or the name of a saved one (layout), not both")
    if body.mapping is not None:
        return body.mapping
    saved = await get_store().get_layout(tenant_id, body.layout)
    if saved is None:
        raise HTTPException(404, f"no saved mapping named {body.layout}")
    try:
        return Mapping.model_validate({**saved["mapping"], "name": saved["name"]})
    except ValidationError:
        raise HTTPException(400, f"the saved mapping {body.layout} is no longer valid; save it again") from None


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
