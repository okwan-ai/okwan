"""Who may act on whom.

A parent may administer its descendants; a child may not see its parent
or its siblings. That is the whole boundary, and it is one function so
there is a single place to audit rather than a check repeated at every
call site.

Depth is bounded rather than unlimited. A cycle is impossible given the
insert path, but an unbounded walk over adversarial data is a hang, and
two levels — platform, then merchant — is the shape the product actually
has.
"""
from __future__ import annotations

MAX_DEPTH = 8


async def _tenant(store, tenant_id: str):
    """Read a tenant. Store is async throughout."""
    return await store.get_tenant(tenant_id)


class Forbidden(Exception):
    """The acting tenant may not administer the target."""


async def ancestors(store, tenant_id: str) -> list[str]:
    """Tenant ids from the given tenant up to its root, exclusive of self."""
    out: list[str] = []
    current = tenant_id
    for _ in range(MAX_DEPTH):
        tenant = await _tenant(store, current)
        if tenant is None or tenant.parent_id is None:
            break
        out.append(tenant.parent_id)
        current = tenant.parent_id
    return out


async def may_administer(store, actor_id: str, target_id: str) -> bool:
    """True when actor is the target, or an ancestor of it.

    Sibling access is not a special case that has to be excluded — a
    sibling is simply not on the target's ancestor chain.
    """
    if actor_id == target_id:
        return True
    return actor_id in await ancestors(store, target_id)


async def require_administer(store, actor_id: str, target_id: str) -> None:
    if not await may_administer(store, actor_id, target_id):
        raise Forbidden(
            f"{actor_id} may not administer {target_id} — "
            "a tenant may act on itself and its descendants only"
        )
