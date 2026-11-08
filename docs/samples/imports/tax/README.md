# Sample sales tax returns

Three monthly Wyoming returns, as a merchant would type them from the
filed returns or export them from a filing service (Avalara Returns'
liability worksheet, a state's filing confirmation). Each row is one
return: one jurisdiction, one period. Every figure and confirmation number
is made up.

`tests/test_avalara.py` imports the file with the mapping below and checks
the findings listed here against AvaTax-shaped transactions.

## Importing it

On a merchant's **Files** tab, choose **Import a file**, pick **Tax
returns**, and match the columns. Currency is USD.

| Field | Column |
|---|---|
| State or jurisdiction | `State` (as AvaTax names it: `WY`) |
| Period start | `Period start` |
| Period end | `Period end` |
| Tax reported | `Tax collected` |
| Taxable sales | `Taxable sales` |
| Adjustments | `Prior period adjustments` |
| Vendor discount | `Vendor discount` |
| Paid | `Amount paid` |
| Filed on | `Filed on` |
| Return or confirmation number | `Confirmation` |

## What the check finds

**Sales tax: recorded vs filed** (`avatax_collected_filed`) compares each
return with every document AvaTax **committed** for the same state between
the return's first and last day. It reads sales invoices and return
invoices (refunds), never a quote that was saved and left. With these
returns and the test's AvaTax documents:

- **September** reported 40.00, and AvaTax committed 40.00 over three
  sales: **agrees**.
- **August** reported 25.00 against 23.00 committed. The return carries a
  2.00 correction for July, which **explains** the gap.
- **October** reported 31.50, but AvaTax committed 36.50 less a 2.00
  refund (34.50): **under-reported by 3.00**.
- **A Colorado sale** has no Colorado return imported: a real miss.

How a sale with no matching return reads depends on where it falls:

- **In a gap between two returns** (a month whose return was not imported):
  a finding.
- **After the latest return** for its state: waiting, because that period's
  return cannot have been filed yet.
- **Before the earliest return:** waiting, because it predates the imports.
- **In a state with no return at all:** a finding.

A return for a month with no sales sums to zero. A $0.00 return then
agrees, while a return reporting tax with nothing committed behind it is a
finding.
