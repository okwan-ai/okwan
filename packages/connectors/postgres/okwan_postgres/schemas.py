"""Postgres/Neon connector schemas — read-path canonical models."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Column(BaseModel):
    name: str
    data_type: str
    is_nullable: bool
    default: str | None = None


class Table(BaseModel):
    schema_name: str = Field(alias="schema", description="Postgres schema, e.g. public")
    name: str
    model_config = {"populate_by_name": True}


class TableSchema(BaseModel):
    schema_name: str = Field(alias="schema")
    name: str
    columns: list[Column]
    model_config = {"populate_by_name": True}


class ListTablesIn(BaseModel):
    schema_name: str = Field(
        default="public", alias="schema", description="Schema to list tables from"
    )
    model_config = {"populate_by_name": True}


class TableList(BaseModel):
    items: list[Table]


class GetSchemaIn(BaseModel):
    table: str = Field(description="Table name")
    schema_name: str = Field(default="public", alias="schema")
    model_config = {"populate_by_name": True}


class SearchRowsIn(BaseModel):
    table: str = Field(description="Table to read from")
    schema_name: str = Field(default="public", alias="schema")
    columns: list[str] = Field(
        default_factory=list, description="Columns to return; empty = all"
    )
    equals: dict[str, Any] = Field(
        default_factory=dict,
        description="Equality filters, e.g. {\"country\": \"GH\"}",
    )
    limit: int = Field(default=100, ge=1, le=1000)
    offset: int = Field(default=0, ge=0)
    model_config = {"populate_by_name": True}


class QueryIn(BaseModel):
    sql: str = Field(
        description=(
            "A single read-only SQL statement (SELECT or WITH). "
            "Writes are rejected by a read-only transaction."
        )
    )
    limit: int = Field(default=500, ge=1, le=5000, description="Row cap on results")


class RowSet(BaseModel):
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int
