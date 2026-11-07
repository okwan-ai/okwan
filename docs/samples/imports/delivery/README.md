# Sample delivery platform reports

Two weeks of a café's DoorDash orders and one week of Uber Eats, as each
platform's merchant portal exports them, and the bank statement their
payouts land on. Every name, id and amount is made up.

The column names follow each platform's documented report fields. Exact
headers vary by platform, region and report version, and DoorDash renamed
several columns in 2025. The importer maps by column, so a renamed or
reordered column is a different mapping, never a code change. Real exports
from a dealer's restaurants will replace these as the reference.

`tests/test_delivery.py` imports each file with the mapping below and
checks the findings listed here.

| File | Kind | What it holds |
|---|---|---|
| `doordash-transactions-2026-10.csv` | Delivery orders | Eleven lines: nine orders, a credit and a marketing charge |
| `doordash-payouts-2026-10.csv` | Delivery payouts | Two weekly payouts, the second with a Fast Pay fee |
| `uber-eats-payment-details-2026-10.csv` | Delivery orders | Three orders, one with a refund |
| `uber-eats-payouts-2026-10.csv` | Delivery payouts | One weekly payout |
| `bank-statement-delivery-2026-10.csv` | Bank statement | Two platform deposits that match, one that doesn't, and a card processor's deposit |

## Importing them

On a merchant's **Files** tab, choose **Import a file**, pick the kind, and
match the columns. Currency is USD throughout.

**DoorDash transactions (Delivery orders)**

| Field | Column |
|---|---|
| Ordered on | `Timestamp local date` |
| Type | `Transaction type` |
| Transaction ID | `Transaction ID` |
| Order ID | `DoorDash order ID` |
| POS order reference | `Merchant delivery ID` |
| Store ID | `Store ID` |
| Status | `Final order status` |
| Subtotal | `Subtotal` |
| Tax | `Subtotal tax passed to merchant` |
| Commission | `Commission` |
| Processing fee | `Payment processing fee` |
| Marketing and promotions | `Marketing fees` |
| Error charges | `Error charges` |
| Adjustments | `Adjustments` |
| Net | `Net total` |
| Payout date | `Payout date` |
| Payout ID | `Payout ID` |

DoorDash writes its deductions as negative numbers. Okwan stores every
deduction as a positive amount, whatever sign the file uses, so the totals
add up by kind of charge. If a report has no net column, leave Net
unmapped and Okwan derives it from the parts.

**DoorDash payouts (Delivery payouts)**: Payout ID → `Payout ID`, Paid on →
`Payout date`, Net → `Net payout`, Instant payout fee → `Fast Pay fee`, and
the period, sales and deduction columns by name.

**Uber Eats payment details (Delivery orders)**: Order ID → `Order ID`,
Ordered on → `Order Date`, Subtotal → `Sales (excl. tax)`, Tax →
`Tax on Sales`, Commission → `Marketplace Fee`, Refunds → `Refunds`, Net →
`Total Payout`, Payout ID → `Payout Reference`, Payout date → `Payout Date`.

**Uber Eats payouts (Delivery payouts)**: Payout ID → `Payout Reference`,
Paid on → `Payout Date`, Net → `Total Payout`.

**Bank statement**: as in the card-processor samples, one folder up.

## What the checks find

**Delivery payouts vs their order lines** (`delivery_payout_lines`):

- **DoorDash DD-PO-1012** (127.28) is exactly its seven lines.
- **DoorDash DD-PO-1019** (61.87) is 1.99 short of its three lines
  (63.86). The payout's Fast Pay fee explains the gap.
- **Uber Eats UE-PAY-1012** (71.17) is exactly its three orders.
- The order placed on 19 October has no payout yet, so it is waiting, not
  a finding.

**Delivery payouts vs bank deposits** (`delivery_payouts_deposits`):

- DD-PO-1012 arrived on its payout date.
- Uber Eats paid on Monday 12 October, Columbus Day. The bank posted it
  the next business day.
- **DD-PO-1019 never reached the bank.**
- **A DoorDash deposit of 58.40 on 27 October has no payout** in the
  imported reports.
- The Square deposit is not a delivery platform's, so this check does not
  read it.

**Where the money went.** Each line keeps commission, processing fee,
marketing, error charges and refunds apart. It also carries
`withheld_minor` (what the platform kept from the sale) and
`commission_rate_bps` (commission as a share of the subtotal). The sample
shows what that surfaces:

- One DoorDash order took **25%** commission while the others took 15%.
- A missing-item **error charge** of 12.50.
- A cancelled order charged back in full.

Ask it in SQL:

```sql
-- What each platform kept, by kind of charge (the layout names the report)
SELECT layout, sum(commission_minor), sum(error_charges_minor), sum(refunds_minor), sum(withheld_minor)
FROM files.delivery_lines GROUP BY layout;

-- DoorDash orders charged more than a 15% plan allows (rates round, so allow a point)
SELECT order_id, commission_rate_bps FROM files.delivery_lines
WHERE layout = 'DoorDash transactions' AND commission_rate_bps > 1600;
```
