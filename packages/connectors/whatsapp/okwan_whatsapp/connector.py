"""WhatsApp Cloud API connector — Okwan flagship connector #1.

Defined once via the Okwan SDK; REST endpoints, SQL projection, and
MCP tools are generated from this file. Do not add interfaces here.
"""
from __future__ import annotations

from okwan_core import (
    BearerTokenAuth,
    Connector,
    ConnectorContext,
    OpType,
    RateLimitProfile,
    register,
)

from .schemas import (
    ListTemplatesIn,
    Message,
    SendTemplateIn,
    SendTextIn,
    Template,
    TemplateList,
)

GRAPH_VERSION = "v21.0"

whatsapp = register(
    Connector(
        name="whatsapp",
        version="0.1.0",
        description=(
            "WhatsApp Cloud API: send text and template messages, "
            "manage message templates."
        ),
        base_url=f"https://graph.facebook.com/{GRAPH_VERSION}",
        auth=BearerTokenAuth(),
        rate_limit=RateLimitProfile(requests_per_second=20, burst=10),
        docs_url="https://developers.facebook.com/docs/whatsapp/cloud-api",
    )
)

messages = whatsapp.resource(
    "messages", schema=Message, description="Outbound WhatsApp messages"
)
templates = whatsapp.resource(
    "templates", schema=Template, description="Approved message templates"
)


@messages.operation(
    OpType.CREATE,
    input_model=SendTextIn,
    output_model=Message,
    name="send_text",
    description="Send a plain text WhatsApp message to one recipient.",
)
async def send_text(ctx: ConnectorContext, params: SendTextIn) -> Message:
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": params.to,
        "type": "text",
        "text": {"preview_url": params.preview_url, "body": params.body},
    }
    data = await ctx.client.post(f"/{params.phone_number_id}/messages", json=payload)
    return Message(message_id=data["messages"][0]["id"], to=params.to)


@messages.operation(
    OpType.CREATE,
    input_model=SendTemplateIn,
    output_model=Message,
    name="send_template",
    description=(
        "Send a pre-approved template message. Required for business-"
        "initiated conversations outside the 24-hour customer window."
    ),
)
async def send_template(ctx: ConnectorContext, params: SendTemplateIn) -> Message:
    components = []
    if params.body_parameters:
        components.append(
            {
                "type": "body",
                "parameters": [p.model_dump() for p in params.body_parameters],
            }
        )
    payload = {
        "messaging_product": "whatsapp",
        "to": params.to,
        "type": "template",
        "template": {
            "name": params.template_name,
            "language": {"code": params.language_code},
            "components": components,
        },
    }
    data = await ctx.client.post(f"/{params.phone_number_id}/messages", json=payload)
    return Message(message_id=data["messages"][0]["id"], to=params.to)


@templates.operation(
    OpType.LIST,
    input_model=ListTemplatesIn,
    output_model=TemplateList,
    description="List message templates for a WhatsApp Business Account.",
)
async def list_templates(ctx: ConnectorContext, params: ListTemplatesIn) -> TemplateList:
    data = await ctx.client.get(
        f"/{params.waba_id}/message_templates", params={"limit": params.limit}
    )
    items = [
        Template(
            id=t["id"],
            name=t["name"],
            status=t["status"],
            category=t.get("category", ""),
            language=t.get("language", ""),
        )
        for t in data.get("data", [])
    ]
    cursor = data.get("paging", {}).get("cursors", {}).get("after")
    return TemplateList(items=items, next_cursor=cursor)
