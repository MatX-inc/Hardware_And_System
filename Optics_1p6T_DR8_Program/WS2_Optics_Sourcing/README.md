# WS2: 1.6T DR8 optics sourcing

Goal: pick the 1.6T DR8 source(s), LRO and/or FRO, with samples in the lab. Tasks and status: [../tracker/TRACKER.md](../tracker/TRACKER.md#ws2-dr8-optics-sourcing). Vendor table: [../tracker/optics_vendors.csv](../tracker/optics_vendors.csv).

## Candidates

| Vendor | Versions | Notes | Status |
| --- | --- | --- | --- |
| Amphenol | LRO, FRO (also LPO) | Broadcom DSP with integrated drivers. Thermal sims use 17 W LRO / 25 W FRO at 70 °C case. Outgoing BER test on every unit. | Samples requested (2 of each) |
| Eoptolink | LRO, FRO, LPO | Not contacted yet | Long list |
| Coherent | LPO, LRO, DSP | Contacted Oct 7 via Paragon (local rep); datasheets received | Contacted |

## Requirements to check with every vendor

- Single MPO-16 connector (our package uses one MPO-16)
- Heat sink: OSFP-RHS for Arista, OSFP-IHS for Nexthop
- LRO linear-driver peaking range, and how it is set per host channel
- CMIS firmware version and update size

Quotes, pricing and vendor datasheets stay on Google Drive.
