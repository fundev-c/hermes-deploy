---
name: sales-drive-workflow
description: Save sales files to the drive and sum revenue_usd by region.
tags:
  - sales
  - shared-drive
  - excel
  - aggregation
---

# Sales Drive Workflow

## Trigger
An email arrives with a sales-data attachment (CSV or .xlsx) and the user asks to save it to the shared drive and compute revenue totals by region.

## Steps

1. Save the attachment to the shared drive:
   ```json
   { "name": "drive_save_attachment", "arguments": { "source": "<cached-path>", "name": "<filename>" } }
   ```
   - `source` is the cached document path from the user message.
   - `name` is the desired filename on the drive (preserve original name).

2. Read the saved file back from the drive:
   ```json
   { "name": "drive_read", "arguments": { "path": "<filename>" } }
   ```
   - The `drive_read` tool handles both CSV and .xlsx; for Excel it returns each sheet as CSV text.

3. Parse the returned content:
   - Split on `## Sheet:` to isolate each worksheet.
   - Find the header line containing `region` and `revenue_usd`.
   - Sum `revenue_usd` values grouped by `region`.

4. Report the region totals back to the user.

## Conventions
- Region column may be named `region` or `Region`; revenue column is `revenue_usd`.
- If the file already exists on the drive, pass `on_exists: "rename"` to avoid overwriting.
- Always confirm the save succeeded (non-error tool result) before reading back.

## Example Output
The largest region is **AMER** with a total revenue of **$X,XXX,XXX**.

| Region | Total Revenue |
|--------|---------------|
| AMER   | $X,XXX,XXX    |
| EMEA   | $X,XXX,XXX    |
| APAC   | $X,XXX,XXX    |