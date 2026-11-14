# Sample import files

One week of a café's card business in October 2026, as its processor and
its bank would export it. Use them to try the importer, to demo a check,
or as the shape to ask a dealer's processor export to match. Every name,
merchant ID, card ending and amount is made up; the card columns are
masked to the last four digits, as a real export must be before upload.

`tests/test_sample_files.py` imports each file with the mapping below and
checks the findings listed here, so this page stays true.

| File | Kind | What it holds |
|---|---|---|
| `processor-batches-2026-10.csv` | Settlement batches | Nine daily batches, 5 to 13 October, one merchant ID |
| `processor-transactions-2026-10-09.csv` | Card transactions | Friday 9 October: four sales and a refund |
| `bank-statement-2026-10.csv` | Bank statement | The operating account's deposits, a fee and a mobile deposit |
| `bank-statement-2026-10.ofx` | Bank statement | The same statement as OFX, as a bank's download gives it |

## Importing them

On a merchant's **Files** tab, choose **Import a file**, say what the file
is, choose it, and match the columns. Okwan suggests a match from the
column names alone; the ones marked *picked* are chosen by hand from the
column list. Currency is USD for all three.

**Processor batches**

| Field | Column |
|---|---|
| Batch ID | `Batch ID` |
| Closed on | `Close Date` *(picked)* |
| Gross | `Gross Sales` |
| Fees | `Fees` |
| Chargebacks | `Chargebacks` |
| Net | `Net Deposit` |
| Merchant ID (MID) | `MID` |

**Card transactions**

| Field | Column |
|---|---|
| Transacted on | `Transaction Date` *(picked)* |
| Amount | `Amount` |
| Card, last 4 digits | `Card` *(picked: a masked card column can only fill this field)* |
| Card brand | `Card Brand` |
| Authorization code | `Auth Code` |
| Transaction ID | `Transaction ID` |
| Type | `Type` |
| Fee | `Fee` |

**Bank statement (CSV)**

| Field | Column |
|---|---|
| Posted on | `Date` *(picked)* |
| Description | `Description` |
| Amount | `Amount` |
| Bank transaction id | `Reference` *(picked)* |

The OFX statement needs no mapping: OFX names its own columns.

Save each mapping by name when you import, and the next file from the
same processor or bank uses it in one step.

## What the checks find

With the batches and the statement imported, **Processor batches vs bank
deposits** (`batches_deposits`) reports:

- **Five deposits matched**, each to the batches it paid:
  - B-1005 to B-1008, each paid the next business day.
  - B-1009 to B-1012 (Friday, Saturday, Sunday and Monday 12 October,
    Columbus Day, a bank holiday) paid as **one** deposit of 6,138.22 on
    Tuesday 13 October.
- **B-1013, never paid.** Its net of 944.06, after a 45.00 chargeback, is
  not on the statement.
- **The mobile deposit of 312.40 on 16 October has no batch.** It is
  money in that the processor does not explain.
- The 35.00 monthly fee is money out, so it is not read as a deposit.

In the dashboard this is the merchant's **Files** tab, under **Batches
against deposits**: once both files are imported, **Check these files**
runs it and the result stays there, the batch that was not deposited
first, with its amount and the day it closed, then each deposit and the
batches it paid.

The card transactions are the shape **Clover card payments vs processor
settlement** (`clover_payments_settlements`) reads. It matches each card
sale by amount and day, and uses the last four digits and the
authorization code to tell the two 12.50 sales apart. The refund row is
money out, so that check does not read it.
