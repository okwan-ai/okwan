"""Request metering and plan limits.

Counted per request, which is the unit a customer can predict without
knowing how the platform works. A federated query touching four
connectors is one request even though it costs us four upstream calls —
that asymmetry is ours to manage, not the customer's to reason about,
and §5's pricing principle is predictability over precision.

Attribution is to the tenant that made the call; billing rolls up to the
root, because an ISV's merchants are not customers.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

#: Requests per calendar month. Free is deliberately usable — the point is
#: for an ISV to reach a real integration before deciding, not to hit a
#: wall during evaluation.
PLANS: dict[str, int] = {
    "free": 5_000,
    "pro": 100_000,
    "team": 1_000_000,
    "enterprise": 0,  # 0 means unmetered
}

DEFAULT_PLAN = "free"


class PlanRefused(ValueError):
    """A plan change the vault will not make, carrying the reason.

    A ValueError, so an unknown plan name fails the way it always has.
    """


#: Why a child tenant takes no plan: usage bills to the root, and the
#: 402 gate reads only the root's plan.
NOT_ROOT_PLAN = "billing rolls up to the root, so a child's plan is never read"


def hour_bucket(when: datetime | None = None) -> datetime:
    now = when or datetime.now(UTC)
    return now.replace(minute=0, second=0, microsecond=0)


def month_start(when: datetime | None = None) -> datetime:
    now = when or datetime.now(UTC)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


@dataclass(frozen=True, slots=True)
class Quota:
    plan: str
    limit: int
    used: int

    @property
    def unmetered(self) -> bool:
        return self.limit == 0

    @property
    def remaining(self) -> int | None:
        return None if self.unmetered else max(0, self.limit - self.used)

    @property
    def exceeded(self) -> bool:
        return not self.unmetered and self.used >= self.limit


async def billing_root(store, tenant_id: str) -> str:
    """The account that pays for this tenant's usage.

    An ISV's merchants roll up to the ISV. A tenant with no parent pays
    for itself.
    """
    from .authz import MAX_DEPTH

    current = tenant_id
    for _ in range(MAX_DEPTH):
        tenant = await store.get_tenant(current)
        if tenant is None or tenant.parent_id is None:
            return current
        current = tenant.parent_id
    return current


async def quota(store, tenant_id: str, now: datetime | None = None) -> Quota:
    """Month-to-date usage against the paying account's plan.

    The one computation behind the 402 gate and the operator's
    `plan show`, so the two cannot disagree. An ISV's merchants roll up:
    the subtree shares one allowance, read from the root's plan.
    """
    root = await billing_root(store, tenant_id)
    plan, limit = await store.get_plan(root)
    used = await store.usage_since(root, month_start(now))
    return Quota(plan=plan, limit=limit, used=used)


#: How long an unpaid checkout session's row is kept. Stripe ends an unpaid
#: session a day after it opens and retries a webhook for three days; after
#: a week no event can arrive for one.
UNPAID_CHECKOUT_KEPT = timedelta(days=7)
#: The most checkout rows read for one account, paid and unpaid each.
CHECKOUTS_LISTED = 500

#: A dispute's states at Stripe in which the bank is waiting for the seller,
#: and those in which it is over (Stripe's documentation, the Dispute
#: object's `status`, read 2026-11-13).
DISPUTE_NEEDS = frozenset({"needs_response", "warning_needs_response", "appeal_needs_response"})
DISPUTE_ENDED = frozenset({"won", "lost", "warning_closed", "prevented"})
#: How long the right to mail about a dispute is held while the mail is
#: being sent. A send that never reports back (the process stopped, the
#: database went away) leaves the right held; after this long the next
#: round takes it and mails. Well over the mail provider's timeout.
DISPUTE_NOTICE_HELD = timedelta(minutes=15)


def dispute_stage(status: str) -> str:
    """Where a dispute stands: decided, an inquiry before a dispute, an
    appeal after one, or open. A state Stripe adds later reads as open."""
    if status in DISPUTE_ENDED:
        return "ended"
    return "inquiry" if status.startswith("warning_") else "appeal" if status.startswith("appeal_") else "open"


def dispute_heard(status: str, okwan: bool) -> str:
    """What the operator has heard once mailed about a dispute as it now
    stands: how it ended, or the stage it is at, and with it whether its
    charge was a known Okwan payment. A dispute is mailed again whenever
    this differs from what they were last told, so a decision that changes
    and a dispute matched to its account after its first mail are both
    news. `PostgresStore` says the same in SQL (`_HEARD`)."""
    stage = dispute_stage(status)
    return status if stage == "ended" else stage + ("" if okwan else ":unmatched")
#: How long a payment is kept while no Okwan account is known for it. The
#: Stripe account takes payments for other products too; theirs are heard
#: of and, never matched, forgotten after this.
UNMATCHED_PAYMENT_KEPT = timedelta(days=7)
