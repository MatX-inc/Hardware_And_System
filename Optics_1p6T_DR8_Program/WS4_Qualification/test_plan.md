# 1.6T DR8 Qualification Test Plan

Snapshot exported 2026-10-09 from the live test plan doc. Author: Yong Zeng.

## Scope and goals

This plan qualifies 1.6T DR8 OSFP optics, in both LRO (linear receive, retimed transmit) and FRO (fully retimed) versions, on TH6 network switches for the MatX One scale-out network.

- **Decide** whether LRO is usable on our target switch, or whether we stay on FRO as the standards-safe baseline.
- **Qualify** at least two optics sources on the selected switch across temperature and channel loss.
- **Produce** a repeatable data set and report per run, so vendors and part numbers can be compared over time.

Out of scope for this phase: XPO, CPO and LPO optics, and compute-tray (Condor host) testing.

## Devices under test

Two optics sources and one target switch are in play today; the tracker's vendor tables hold the full long list.

| Device | Vendor / model | Versions | Key figures | Status |
| --- | --- | --- | --- | --- |
| Optics | Amphenol 1.6T DR8 OSFP | LRO, FRO | Broadcom DSP; 17 W LRO / 25 W FRO in thermal sims at 70 °C case | 2 samples each requested |
| Optics | Eoptolink 1.6T DR8 | LRO, FRO | Not yet received | Long list |
| Switch | Arista DCS-7060XE7-64PRS-RV3-L | TH6, 64 × 1.6T OSFP-RHS | DSP and LRO listed; up to 40 W per port; 2OU, liquid-cooled | Primary target switch |
| Switch | Nexthop NH-4220-F | TH6, 64 × 1.6T OSFP-IHS | Built for LPO/LRO; 2RU, air-cooled, AC; 2993 W typical / 3720 W max | Backup for lead time; lab switch candidate |

## Lab setup and equipment

The lab needs module-level optical test gear and a switch-level system test bench; the target switch also needs DC power and a liquid loop.

| Area | Equipment | Purpose |
| --- | --- | --- |
| Electrical | 224G PAM4 BERT, OSFP host and module compliance boards | Module electrical input/output, LRO receive margin |
| Optical | Sampling scope with optical head, optical power meters | TDECQ, OMA, extinction ratio, per-lane power |
| Channel | MPO-16 test cords, VOAs, optical switch | Sweep channel loss 2.92 / 3.0 / 4.0 dB; route lanes |
| Thermal | Thermal forcing or chamber, case thermocouples | Case temperature corners up to 70 °C |
| Switch bench | TH6 switch, ORv3 DC power, liquid loop (35 °C inlet, 5.5–6 LPM) | System BER, FEC, link stability on real ports |
| Automation | Scripts for CMIS/DDM and switch counters | One log format for every run |

## Test matrix

Each run is one combination of the factors below; the full matrix is 2 sources × 2 versions × 3 losses × 3 temperatures = 36 combinations per switch, before port groups.

| Factor | Levels |
| --- | --- |
| Optics source | Amphenol, Eoptolink (more as samples arrive) |
| Optics version | LRO, FRO |
| Channel loss | 2.92 dB (MatX connector loss), 3.0 dB (DR8 max), 4.0 dB (DR8-2 / vendor TRO) |
| Case temperature | 25 °C, 55 °C, 70 °C |
| Switch port group | Shortest and longest host traces on the switch (to be identified) |
| Port mode | 1 × 1.6T and 8 × 200G breakout |

Sample size per combination: at least 2 modules, minimum 8 lanes each.

## Test cases and procedures

Module-level tests run first on compliance boards; system tests on the switch follow only for modules that pass.

1. **Incoming inspection**: CMIS identity, firmware version, DDM readings at idle.
2. **Optical transmitter**: TDECQ, OMA, extinction ratio and average power per lane, at each temperature.
3. **Receiver sensitivity**: pre-FEC BER versus received OMA per lane; for LRO, repeat across host channel loss.
4. **System link bring-up**: link up time and stability on the switch in 1.6T and 8 × 200G modes.
5. **System BER soak**: pre-FEC BER and FEC codeword histogram over a long soak at each loss and temperature corner.
6. **Stress**: hot plug, power cycle, and temperature ramps, counting link flaps and errors.
7. **Power and thermal**: module power and case temperature on the switch at full traffic.

## Pass/fail criteria

Limits below are placeholders to be set against IEEE 802.3dj and vendor datasheets before the first run.

| Metric | Pass condition | Limit |
| --- | --- | --- |
| Pre-FEC BER | Below limit on every lane, every corner | 2.4e-4 link (KP4); 1.5e-6 at the TH6/Condor host RX (DG104 §9.1.2) |
| FEC codeword histogram | No uncorrectable codewords; tail bins below margin | TBD |
| Link stability | Zero link flaps during soak and temperature ramps | 0 |
| TDECQ / OMA | Within DR8 transmitter spec with margin | TBD |
| Module power | At or below datasheet at 70 °C case | LRO ~17 W, FRO ~25 W (vendor) |
| Case temperature | Below module max at full traffic on the switch | 70 °C |

## Data collection and analysis

Every run produces one folder with raw data plus a run sheet, analyzed by the same script so results stay comparable.

- **Run sheet**: run ID, date, optics vendor/part/serial, LRO or FRO, firmware, switch and port, channel loss, case temperature, operator.
- **Raw data**: scope and BERT exports, switch counter logs (pre-FEC BER, FEC histogram, link events), CMIS/DDM snapshots.
- **Analysis**: one reusable script, later saved as a Claude skill, that parses a run folder and outputs per-lane tables, BER vs loss and BER vs temperature plots, and pass/fail against the criteria above.
- **Report**: one page per run, with results logged in the program tracker.

## Open questions and risks

The biggest risk is LRO interoperability: no 224G RTLR spec is published, so LRO margin depends on each host SerDes and must be measured per part number.

TH6 uses the same Broadcom Condor SerDes modeled in dr8sim, so the simulator's LRO results apply directly to the switch side. The lab results will in turn calibrate the model.

- [ ] Can the lab support the liquid-cooled, DC-powered Arista switch, or do we start on an air-cooled TH6 box?
- [ ] Does the Arista 64PRS-RV3-L support 8 × 200G breakout on all ports, and what LRO host tuning does it allow?
- [ ] Pre-FEC BER and TDECQ limits to adopt from 802.3dj.
- [ ] Which switch ports have the longest host traces?
- [ ] Do both optics vendors offer a single MPO-16 connector?
- [ ] dr8sim (500 m, 4 dB, Condor host) shows LRO marginal at the host RX (2.0e-6 default, 3.4e-8 silicon-fit vs 1.5e-6). Which switch ports and driver peaking settings should the LRO runs target?
