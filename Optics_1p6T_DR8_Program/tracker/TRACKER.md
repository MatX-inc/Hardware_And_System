# Program tracker snapshot

Snapshot of the live tracker, exported 2026-10-09. Edit the live tracker day to day; re-export here when something changes. The CSVs in this folder hold the same data.

## Status by workstream

| Workstream | Done | In progress | Waiting | Blocked / needs info | Not started |
| --- | ---: | ---: | ---: | ---: | ---: |
| WS1 Lab setup | 0 | 0 | 0 | 0 | 4 |
| WS2 DR8 optics sourcing | 0 | 2 | 3 | 0 | 2 |
| WS3 Switch vendor selection | 1 | 3 | 0 | 0 | 6 |
| WS4 Qualification | 0 | 1 | 0 | 0 | 2 |
| WS5 Test data analysis | 0 | 0 | 0 | 0 | 2 |
| WS6 1.6T DR8 simulation | 1 | 1 | 1 | 1 | 6 |

## WS1 Lab setup

Stand up a lab that can qualify 1.6T DR8 OSFP optics on TH6 switches.

| ID | Task | Status | Owner | Next step |
| --- | --- | --- | --- | --- |
| lab-01 | Lab equipment BOM | Not started | Yong | List 224G PAM4 BERT, sampling scope with optical head (TDECQ/OMA), power meters, VOAs, optical switch, MPO-16 test cords, OSFP host/module compliance boards, thermal forcing. |
| lab-02 | Facilities: power and liquid cooling for the switch | Not started | Yong | Arista 7060XE7-64PRS-RV3-L is DC/ORv3 and fully liquid-cooled (35 °C inlet, 5.5–6 LPM, BMQC/UQD). Confirm the lab can support it, or plan an air-cooled lab switch. |
| lab-03 | Fiber plant and loss emulation | Not started | Yong | MPO-16 cords plus VOAs to sweep channel loss across 2.92 dB (our connector loss), 3.0 dB (DR8) and 4.0 dB (DR8-2 / vendor TRO). |
| lab-04 | Test automation and logging | Not started | Yong | Script CMIS/DDM reads and pre-FEC BER / FEC histogram counters from the switch CLI into one log format. |

## WS2 DR8 optics sourcing

Pick the 1.6T DR8 source(s), LRO and/or FRO, with samples in the lab.

| ID | Task | Status | Owner | Next step |
| --- | --- | --- | --- | --- |
| opt-05 | Request LRO and FRO DR8 samples from a second source | Not started | Yong | Eoptolink sells LRO, FRO and LPO 1.6T lines. Request datasheets and samples. |
| opt-06 | Confirm single MPO-16 connector option | Not started | Yong | Our package uses one MPO-16; check each vendor's DR8 connector. |
| opt-04 | Amphenol technical follow-up: factory automation and BER test | In progress | Yong / Chico | Friday meeting. Ask for outgoing BER test conditions and data format. |
| opt-07 | Track standards: 802.3dj DR8 and OIF 224G RTLR | In progress | Yong | No published 224G RTLR spec yet, so LRO interop must be proven per part number with each host. |
| opt-01 | Amphenol: quote for 16 LRO and FRO DR8 units | Waiting on vendor | Amphenol | Chase quote (MP pricing from the Oct 7 call is on Drive). |
| opt-02 | Amphenol: 2 samples of each product | Waiting on vendor | Amphenol | Confirm ship date. Samples come with integrated heat sinks for air-cooled testing. |
| opt-03 | Amphenol: DVT report, component portfolio, firmware size | Waiting on vendor | Amphenol | Review DVT report when received. |

## WS3 Switch vendor selection

Primary: Arista 7060XE7-64PRS-RV3-L. Backup for lead-time risk: Nexthop NH-4220-F. Plus a lab test switch.

| ID | Task | Status | Owner | Next step |
| --- | --- | --- | --- | --- |
| sw-03 | Choose lab test switch | Not started | Yong | Candidates: Nexthop NH-4220-F (air-cooled, AC power, fits a normal lab) or Arista 'Banff' (air-cooled). Either avoids the liquid loop and ORv3 DC power the 64PRS-RV3-L needs. |
| sw-06 | Nexthop: liquid-cooled / ORv3 DC variant? | Not started | Yong | NH-4220-F is air-cooled and AC-powered. Ask whether a liquid-cooled ORv3 DC version is on the roadmap, or whether our network racks can take an air-cooled 2RU box. |
| sw-07 | Nexthop: third-party DR8 optics and LRO tuning | Not started | Yong | Their 1.6T optics are 2DR4 with 2×MPO-12. Confirm support and tuning access for third-party single-MPO-16 DR8 (Amphenol, Eoptolink, Coherent), including LRO host TX settings. |
| sw-08 | Resolve OSFP-RHS (Arista) vs OSFP-IHS (Nexthop) heat sink | Not started | Yong | Optics SKUs differ by heat sink type. Confirm each optics vendor offers both, and order qual samples for both switches. |
| sw-09 | Compare NOS access for qual automation | Not started | Yong | Arista EOS vs Nexthop NOS/SONiC: per-lane pre-FEC BER, FEC histograms, CMIS access, host SerDes tuning. |
| sw-10 | Arista HQ lab tour | Not started | Yong / Chico | Offered by Arista on Sep 1 for 'a couple of months' out. Ask to include 1.6T LRO/DR8 test results. |
| sw-01 | Arista 7060XE7-64PRS-RV3-L open questions | In progress | Yong | Confirm model name (64PS vs 64PRS), TH6-C = 200G SerDes, 512×200G on all ports, power with hotter optics, LRO support/tuning. |
| sw-04 | Arista: lead time, EFT/GA and eval units for 64PRS-RV3-L | In progress | Yong / Chico | Ask Srinidhi/Kevin for quoted lead time, EFT unit availability and confirm the roadmap name. No lead time found in email or Slack yet. |
| sw-05 | Nexthop: intro call, NDA, lead time, pricing, eval unit | In progress | Yong | An Nguyen replied Oct 9; schedule the call. Ask for NH-4220-F lead time, eval/loaner unit, pricing at our volume. |
| sw-02 | Pick a backup TH6 switch vendor | Done | Yong | Nexthop NH-4220-F chosen as backup for lead-time risk (pending decision). |

## WS4 Qualification

Run the optic × switch qualification matrix to a pass/fail decision.

| ID | Task | Status | Owner | Next step |
| --- | --- | --- | --- | --- |
| qual-02 | Define test matrix | Not started | Yong | Optic (vendor × LRO/FRO) × switch × port group × temperature × channel loss. |
| qual-03 | Define pass/fail criteria | Not started | Yong | Pre-FEC BER limit, FEC codeword histogram tail, link flaps, TDECQ/OMA margin, power and case temperature. |
| qual-01 | Write qualification test plan | In progress | Yong | Review the draft test plan; set pass/fail limits from 802.3dj. |

## WS5 Test data analysis

Repeatable analysis and reports for every qual run.

| ID | Task | Status | Owner | Next step |
| --- | --- | --- | --- | --- |
| data-01 | Define data format and log schema | Not started | Yong | One schema for scope, BERT and switch counter data. |
| data-02 | Build reusable analysis skill | Not started | Yong / Claude | After first dataset: save parsing, plots and pass/fail checks as a skill. |

## WS6 1.6T DR8 simulation

dr8sim: end-to-end 1.6T DR8 link model (host Condor SerDes → optics → host), backing the LRO vs FRO choice.

| ID | Task | Status | Owner | Next step |
| --- | --- | --- | --- | --- |
| sim-02 | Thermal: 17 W LRO vs 25 W FRO at 70 °C case | Not started | Yong | Amphenol's thermal sims use these powers. Check against our liquid-cooled riding heat sink design. |
| sim-05 | Get CSAK loopback S-parameters | Not started | Yong | OSFP 0.5 m and 1.5 m loopbacks plus the 13–14 in trace loopback; run the bench on them and fit CondorConfig. |
| sim-07 | Measure Condor with a real DR8 module on CSAK | Not started | Yong | Plug retimed and LRO DR8 samples into the CSAK OSFP cage in fiber loopback; read Condor PF/FFE/DFE, SNR, BER. Best calibration point. Needs Amphenol samples. |
| sim-08 | Re-run LRO peaking vs host trace and sensitivity tables | Not started | Yong | Those tables predate the Oct 8 audit and the 6-tap FFE update; re-run to confirm the trend (≥250 mm traces fail). |
| sim-09 | Model the TH6 switch host channel for LRO | Not started | Yong | TH6 uses the same Condor SerDes, so dr8sim applies to the switch side. Add the Arista switch host trace (roadmap lists <26 dB PCB for the 2OU liquid-cooled box) and pick the worst ports for the qual matrix. |
| sim-10 | Set dr8sim spec limits to the 802.3dj revision | Not started | Yong | Default SpecLimits are placeholders; align with the same limits used in the qual test plan. |
| sim-03 | Link budget: LRO vs retimed with Condor host (500 m, 4 dB) | In progress | Yong | Retimed: +4.18 dB margin, Condor RX BER ~1e-16 (pass). LRO: +3.94 dB margin, but Condor RX BER 2.0e-6 with default impairments (fails the 1.5e-6 TH6 DG104 criterion) and 3.4e-8 with silicon-fit impairments (pass). Verdict depends on calibration. |
| sim-06 | Get Phytile / TH6 package and IOD-to-OSFP channel models | Waiting on vendor | Broadcom | MatX asked Broadcom for Phytile S-parameters; no answer recorded. Also need BCM78914 package IL file from Broadcom ESP. |
| sim-04 | Calibrate Condor RX model against CSAK silicon | Blocked | Yong | Model is 2–3 decades pessimistic at 45–55 dB. Fix peaking-filter shapes and RX jitter units (UI vs ps). Needs CSAK channel S-parameters or the Broadcom AMI model. |
| sim-01 | Build dr8sim end-to-end link simulator | Done | Yong | Done. 8 lanes × 200G PAM4, retimed and LRO modes, Condor host model, 8-lane Monte Carlo. Code audit fixes Oct 8; test suite passing. |
