# Decision log

Newest first. Status is one of proposed, decided or revisit.

| ID | Date | Decision | Status |
| --- | --- | --- | --- |
| dec-002 | 2026-10-09 | Arista 7060XE7-64PRS-RV3-L primary; Nexthop NH-4220-F backup for lead time | Proposed |
| dec-001 | 2026-10-09 | Fully retimed (FRO) DR8 is the baseline; LRO qualified per part number | Proposed |

## dec-002: Arista primary, Nexthop backup

**Options:** Arista 7060XE7-64PRS-RV3-L (liquid-cooled, ORv3 DC), Nexthop NH-4220-F (air-cooled, AC), other TH6 ODM boxes.

**Rationale:** Arista has the deepest engagement (network design work, optics roadmap, LRO support) and a liquid-cooled ORv3 box that fits our racks. It is a roadmap product, with EFT in Q4 2026 and GA in Q1 2027 (Arista, under NDA). Nexthop's TH6 box already ships to hyperscalers, is built for LRO and runs SONiC. Its gaps are air cooling and AC power only, the OSFP-IHS heat sink, and 2DR4 (2 × MPO-12) own-brand optics. Both switches use the same TH6 Condor SerDes, so optics qualification data largely carries across.

## dec-001: FRO baseline, LRO per part number

**Options:** FRO (full DSP), LRO (TRO / half-retimed), LPO.

**Rationale:** No 224G RTLR spec is published, so LRO interoperability depends on each host SerDes. dr8sim (500 m, 4 dB, Condor host) shows retimed passing with large margin. LRO is marginal against Broadcom's 1.5e-6 pre-FEC criterion at the host RX and needs driver peaking matched to the host trace. LRO saves about 8 W per module if it qualifies.
