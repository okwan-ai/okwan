# Owner and legal items

Flagged here, not drafted. Each needs the owner, and where marked,
counsel. Engineering can supply facts for any of them (what is stored,
where, for how long: [data-inventory.md](data-inventory.md)) but does not
decide them.

| Item | Who | Why it is needed before a contract |
|---|---|---|
| Privacy policy and terms of service | Owner, counsel | Signup collects addresses and the vault holds merchants' payment credentials; neither document exists and the site links neither (`OKWAN_PROJECT.md` §10 item 27) |
| Data processing agreement for customers | Owner, counsel | ISVs will put their merchants' data through Okwan and will ask what Okwan may do with it |
| Anthropic as a subprocessor of support conversations | Owner, counsel | The support assistant sends a customer's messages and a read-only view of their account (secrets removed) to Anthropic's API. The privacy policy and any DPA must name it, and say that answers come from an AI assistant with a person behind it |
| Support conversation retention | Owner, counsel | Kept 30 days after the last message (`SUPPORT_KEPT_DAYS`); tickets are not purged. The policy decides both |
| Subprocessor list | Owner, counsel | Render, Neon, Vercel, Resend, Anthropic and GitHub each touch some data ([dependencies.md](dependencies.md), "Services"); which count as subprocessors, and in which regions they hold data, is a legal reading |
| Which privacy and data-protection laws apply | Counsel | Customers in the US, and merchants and founders with ties to Ghana and other markets; the answer shapes retention, deletion and breach notice |
| Breach notification duties and the decision process | Owner, counsel | The runbook stops at "the owner decides, with counsel" (`docs/ops/RUNBOOK.md`, "Suspected cross-tenant exposure"). Who is told, by when, and by whom must be settled before it is needed |
| Retention and deletion policy | Owner, counsel | Engineering will build the purge and offboarding (known gap G5) to whatever the policy says |
| Whether the 2026-11-03 query hole requires any notice | Owner, counsel | The facts: exposure window from the query layer's launch to the fix's deploy; production has had no customers; the check of who ran queries is `OKWAN_PROJECT.md` §10 item 29 |
| PCI DSS scope statement | Owner, a qualified assessor | Okwan stores no card numbers and receives none from the rails it reads; an assessor, not engineering, confirms what that means for scope |
| Response targets and support terms | Owner | Written as proposals in the runbook; they bind only once a contract states them |
| A second person behind the support assistant, and a support address | Owner | First response is the assistant; everything it escalates reaches one person today. Runbook, "Who answers" and "Owner decisions" |
| Penetration test and SOC 2: whether, when, which firm | Owner | This pack is written for that reviewer |
| Cyber and professional liability insurance | Owner, broker | Commonly asked for in an ISV's vendor review |
| Use of third parties' logos and marks on the site and dashboard | Owner, counsel | Already flagged in `OKWAN_PROJECT.md` §10 item 25 |

Immigration matters are out of scope for this pack and are handled
separately.
