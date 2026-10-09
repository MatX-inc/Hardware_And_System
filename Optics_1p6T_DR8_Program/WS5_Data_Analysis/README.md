# WS5: Test data analysis

Goal: repeatable analysis and reports for every qualification run. Tasks and status: [../tracker/TRACKER.md](../tracker/TRACKER.md#ws5-test-data-analysis).

## Planned layout

```
runs/<run_id>/
  run_sheet.json   run ID, date, optics vendor/part/serial, LRO or FRO, firmware,
                   switch and port, channel loss, case temperature, operator
  raw/             scope and BERT exports, switch counter logs, CMIS/DDM snapshots
  report.md        per-lane tables, BER vs loss and vs temperature, pass/fail
analysis/          one reusable parser and report script used for every run
```

Large raw captures belong on Drive or GCS; keep the run sheet, summary data and report here.
