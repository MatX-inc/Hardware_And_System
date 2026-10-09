# WS3: Switch vendor selection

Arista is the primary network switch; Nexthop is the backup in case Arista's lead time slips. Tasks and status: [../tracker/TRACKER.md](../tracker/TRACKER.md#ws3-switch-vendor-selection). Decision: [dec-002](../decisions.md#dec-002-arista-primary-nexthop-backup).

## Comparison

| | Arista DCS-7060XE7-64PRS-RV3-L (primary) | Nexthop NH-4220-F (backup) |
| --- | --- | --- |
| Chip | TH6 (102.4T, 267 MB buffer) | TH6 (102.4T, 267 MB buffer) |
| Ports | 64 × 1.6T OSFP-RHS | 64 × 1.6T OSFP-IHS, up to 512 × 200G |
| Size and cooling | 2OU, ORv3, fully liquid-cooled | 2RU, air-cooled, front-to-back |
| Power | DC; ~3 kW with 25 W optics; up to 40 W per port | AC 200–240 V, 1+1 5.2 kW PSUs; 2993 W typical (LRO, 50% traffic), 3720 W max (FRO, 100%) |
| Optics support | DSP and LRO listed (no LPO) | Built for LPO/LRO; Nexthop sells FRO and LRO 2DR4 |
| Software | Arista EOS | Nexthop NOS (SONiC), community SONiC, FBOSS |
| Availability | Roadmap: EFT Q4 2026, GA Q1 2027 (Arista, under NDA) | Shipping to hyperscalers since March 2026 |
| Lead time | Not yet quoted in writing | Not yet quoted |

Both use the same Broadcom Condor SerDes, so dr8sim LRO results and most optics qualification data apply to either switch.

## Open questions

Arista:

- Lead time, EFT unit availability, and confirmation that this box is the 2OU liquid-cooled roadmap platform
- 512 × 200G on all ports; LRO host tuning access
- 1.6T LPO/LRO test results promised in September; HQ lab tour

Nexthop:

- Lead time, evaluation unit and pricing (intro with An Nguyen on Oct 8)
- Liquid-cooled or ORv3 DC variant on the roadmap
- Support and tuning access for third-party single-MPO-16 DR8 optics

Both:

- OSFP-RHS vs OSFP-IHS changes which optics part numbers we order
- Access to per-lane pre-FEC BER, FEC histograms and CMIS from the NOS for test automation

## Sources

- [Nexthop platforms](https://nexthop.ai/platforms/)
- [NH-4220 datasheet](https://nexthop.ai/downloads/hardware/2603/NH-4220-Product-Datasheet-D005.pdf)
- [Nexthop optics and cables](https://nexthop.ai/optics-and-cables/)
- Arista TH6 overview and next-gen iCPO/XPO decks (Aug 2026, NDA): Google Drive
