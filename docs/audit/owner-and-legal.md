# Owner and legal items

Each needs the owner, and where marked, counsel. On 2026-11-04 the owner
chose to write the privacy policy and terms without counsel
(`OKWAN_PROJECT.md` §9); those rows say what was decided. Engineering can supply facts for any of them (what is stored,
where, for how long: [data-inventory.md](data-inventory.md)) but does not
decide them.

| Item | Who | Why it is needed before a contract |
|---|---|---|
| Privacy policy and terms of service | Owner | **Published 2026-11-04** at `okwan.ai/privacy` and `okwan.ai/terms` (`apps/web/app/privacy/page.tsx`, `apps/web/app/terms/page.tsx`), written from this pack, and linked from the site footer and the signup form. They describe what the system does today; a change to what is stored changes them |
| Data processing agreement for customers | Owner | Processor terms are in the Terms, "How we handle data for you". A signed DPA is offered on request |
| Anthropic as a subprocessor of support conversations | Owner | **Named** in the privacy policy, with what is sent (secrets removed) and that a person reviews what the assistant escalates |
| Support conversation retention | Owner | **Decided:** conversations 30 days after the last message; tickets and replies for the life of the account, deleted with it |
| Subprocessor list | Owner | **Published** in the privacy policy with regions: Render (Oregon), Neon (Ohio), Vercel and Google Fonts (global), Resend (Virginia), Anthropic (US). GitHub receives no customer data |
| Which privacy and data-protection laws apply | Counsel | Customers in the US, and merchants and founders with ties to Ghana and other markets; the answer shapes retention, deletion and breach notice |
| Breach notification duties and the decision process | Owner, counsel | The runbook stops at "the owner decides, with counsel" (`docs/ops/RUNBOOK.md`, "Suspected cross-tenant exposure"). Who is told, by when, and by whom must be settled before it is needed |
| Retention and deletion policy | Owner | **Decided** in the privacy policy. Deletion on request within 30 days: `python -m okwan_vault tenant delete` (runbook, "Delete an account on request"). An automatic purge of expired rows is still gap G5 |
| Whether the 2026-11-03 query hole requires any notice | Owner, counsel | The facts: exposure window from the query layer's launch to the fix's deploy; production has had no customers; the check of who ran queries is `OKWAN_PROJECT.md` §10 item 29 |
| PCI DSS scope statement | Owner, a qualified assessor | Okwan stores no card numbers and receives none from the rails it reads; an assessor, not engineering, confirms what that means for scope |
| Response targets and support terms | Owner | Written as proposals in the runbook; they bind only once a contract states them |
| A second person behind the support assistant, and a support address | Owner | First response is the assistant; everything it escalates reaches one person today. Runbook, "Who answers" and "Owner decisions" |
| Penetration test and SOC 2: whether, when, which firm | Owner | This pack is written for that reviewer |
| Cyber and professional liability insurance | Owner, broker | Commonly asked for in an ISV's vendor review |
| Use of third parties' logos and marks on the site and dashboard | Owner, counsel | Already flagged in `OKWAN_PROJECT.md` §10 item 25 |
