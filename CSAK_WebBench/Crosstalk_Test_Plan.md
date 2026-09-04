# CSAK Crosstalk A/B Test Plan

**Board:** Broadcom CSAK APD0601 (Triple test chip), Condor3 200G octals, 212.5 Gb/s PAM4 per lane
**Tooling:** CSAK Web Bench (`CSAK_EVB/webui/`) → `crosstalk.py`, aapl 4.1.6 over AACS
**Written:** 2026-09-04

## 1. Objective

Quantify how much the other seven lanes of an octal pair degrade one *victim* lane's
pre-FEC BER. The victim is measured twice under identical settings:

| Phase | Victim lane | Other 7 lanes (both octals) | Purpose |
|---|---|---|---|
| A · isolated | running, PRBS31, link-trained | parked: `serdes -lane-dp-reset 1` (RX+TX datapath in reset, TX quiet) | baseline with no data-dependent crosstalk |
| B · all-on | running | released: `serdes -lane-dp-reset 0`, all 16 lanes PMD-locked | victim under full aggressor activity |

The difference in error counts, mean BER, SNR and eye margin between A and B is the
crosstalk impact.

## 2. Preconditions

1. Board LIVE in the dashboard (AACS reachable), DUT powered, firmware D003_07 uploaded on
   both octals of the pair, Core-init done (dashboard `/api/core` reports `ready`).
2. Both octals lane-initialised at least once so every lane is trained (all 16 lanes green
   on the board map).
3. Nothing else talking to the board: the test drives everything through the Web Bench
   API, which serialises AACS access. Don't start sweeps or manual `aapl` commands during
   a run.

## 3. Parameters (user-controlled)

| Parameter | UI field | Default | Range |
|---|---|---|---|
| Victim octal | Crosstalk tab → Victim octal | :8 | any usable octal with a partner (or loopback) |
| Victim lane | Lane | 0 | 0–7 |
| Rounds per phase | Rounds | 3 | 1–50 |
| Dwell per round | Dwell (s) | 10 | 1–600 |
| Screenshots | checkbox | on | headless Chrome required |

Rule of thumb: at 212.5 Gb/s one 10 s round is 2.1e12 bits, so zero errors only proves
BER < 4.7e-13. To *resolve* BER in the e-13 range use ≥ 60 s dwells or ≥ 10 rounds.

## 4. Procedure (automated by `crosstalk.py`)

1. **Preflight** — health LIVE; core state `ready` on both octals; snapshot every lane's
   stopped / TX-output / lock state (restored at the end).
2. **Phase A setup** — `dp-reset 1` on lanes ≠ victim on octal A and on partner B;
   `dp-reset 0` on the victim; wait until the victim reports PMD lock at both ends
   (timeout 60 s). Snapshot lane table (SNR, eye). Screenshot dashboard (`?octal=A`).
3. **Phase A rounds** — for r = 1..N: `serdes -ber -error-reset -ber-dwell T -a A.L`, then
   the same on `B.L`. Record errors, bits, BER, bound flag per round per end.
4. **Phase B setup** — `dp-reset 0` on all 8 lanes of A and B; wait for 16/16 PMD lock
   (timeout 60 s; a miss is recorded as a warning, not a failure). Snapshot + screenshot.
5. **Phase B rounds** — for r = 1..N: `serdes -ber -error-reset -ber-dwell T -a A.*`
   (full octal, victim row extracted, other lanes kept for context), then on `B.*`.
6. **Restore** — lanes (other than the victim) that were stopped before the test are stopped again; the victim is left running.
7. **Report** — `results.json`, `rounds.csv`, `report.pdf` (setup, method, per-round
   table, summary with verdict, two dashboard screenshots, all-lane snapshots, notes)
   written to `CSAK_EVB/crosstalk_results/<timestamp>_<octal>_L<lane>/`.

## 5. Pass / verdict logic (per end of the link)

| Isolated errors | All-on errors | Verdict |
|---|---|---|
| both < 10 (and not 0 → ≥5) | | too few errors to judge — increase dwell or rounds |
| 0 | 0 | no measurable crosstalk at this dwell — lengthen dwell |
| 0 | > 0 | crosstalk-induced degradation |
| > 0 | ratio > 2 | significant crosstalk impact |
| > 0 | 1.2 < ratio ≤ 2 | mild impact |
| > 0 | 0.8 ≤ ratio ≤ 1.2 | within noise |
| > 0 | ratio < 0.8 | no penalty; isolated result statistics-limited |

SNR delta (all-on − isolated) and eye U/M/L are reported alongside as corroboration.

## 6. Running it

**From the dashboard:** right-hand card → **Crosstalk** tab → pick octal / lane / rounds /
dwell → *Run crosstalk test*. The progress bar, phase, and a live per-round error table
update every 2 s; when done, links to the PDF/CSV/JSON/PNGs appear and the run is listed
under *Previous runs*. Other controls stay usable but should be left alone during a run.

**From a shell** (same code path):
```sh
cd CSAK_EVB/webui
.venv/bin/python crosstalk.py --octal :8 --lane 0 --rounds 3 --dwell 10
# add --no-screenshots on a machine without Chrome; --url if the server is elsewhere
```

## 7. Limitations

- Datapath reset silences aggressor *data*; the lanes stay powered, so supply-noise
  coupling from idle lanes is not removed. For a power-off baseline the lanes would have
  to be powered down, which also drops the octal PLL load — not part of this test.
- Pre-FEC (raw PRBS checker) only. Post-FEC counts would need the FEC block enabled.
- Link training is left enabled, so lanes re-train when released in Phase B; small
  equalisation differences between phases are expected and visible in the SNR/eye rows.
- Dashboard screenshots need `/Applications/Google Chrome.app`; without it the report
  simply omits section 5.
