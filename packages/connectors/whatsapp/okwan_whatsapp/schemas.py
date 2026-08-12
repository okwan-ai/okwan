"""WhatsApp Cloud API schemas — canonical models for the connector.

These models are the single source of truth: REST request/response
bodies, future SQL table projections, and MCP tool input schemas are
all generated from them.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


# ── canonical record schemas ────────────────────────────────────────

class Message(BaseModel):
    """One outbound message acknowledgment from the Cloud API."""

    message_id: str = Field(description="WhatsApp message ID (wamid)")
    to: str = Field(description="Recipient phone number in E.164 format")
    status: str = Field(default="accepted", description="Send status")


class Template(BaseModel):
    """A pre-approved message template."""

    id: str
    name: str
    status: str = Field(description="APPROVED, PENDING, or REJECTED")
    category: str = Field(description="MARKETING, UTILITY, or AUTHENTICATION")
    language: str


# ── operation inputs / outputs ──────────────────────────────────────

class SendTextIn(BaseModel):
    phone_number_id: str = Field(description="Sender phone number ID from Meta")
    to: str = Field(description="Recipient phone number in E.164 format, e.g. +14155551234")
    body: str = Field(max_length=4096, description="Message text")
    preview_url: bool = Field(default=False, description="Render URL previews")


class TemplateParameter(BaseModel):
    type: str = Field(default="text", description="Parameter type: text, currency, date_time")
    text: str = Field(description="Substitution value for {{n}} placeholders")


class SendTemplateIn(BaseModel):
    phone_number_id: str = Field(description="Sender phone number ID from Meta")
    to: str = Field(description="Recipient phone number in E.164 format")
    template_name: str = Field(description="Approved template name")
    language_code: str = Field(default="en_US", description="Template language, e.g. en_US")
    body_parameters: list[TemplateParameter] = Field(
        default_factory=list, description="Ordered values for template body placeholders"
    )


class ListTemplatesIn(BaseModel):
    waba_id: str = Field(description="WhatsApp Business Account ID")
    limit: int = Field(default=25, ge=1, le=100)


class TemplateList(BaseModel):
    items: list[Template]
    next_cursor: str | None = None
