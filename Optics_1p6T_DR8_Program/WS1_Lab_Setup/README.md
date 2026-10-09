# WS1: Lab setup

Goal: stand up a lab that can qualify 1.6T DR8 OSFP optics on TH6 switches. Tasks and status: [../tracker/TRACKER.md](../tracker/TRACKER.md#ws1-lab-setup).

## Planned equipment

| Area | Equipment | Purpose |
| --- | --- | --- |
| Electrical | 224G PAM4 BERT, OSFP host and module compliance boards | Module electrical input/output, LRO receive margin |
| Optical | Sampling scope with optical head, optical power meters | TDECQ, OMA, extinction ratio, per-lane power |
| Channel | MPO-16 test cords, VOAs, optical switch | Sweep channel loss 2.92 / 3.0 / 4.0 dB; route lanes |
| Thermal | Thermal forcing or chamber, case thermocouples | Case temperature corners up to 70 °C |
| Switch bench | TH6 switch with its power and cooling | System BER, FEC, link stability on real ports |
| Automation | Scripts for CMIS/DDM and switch counters | One log format for every run |

## Facilities note

The Arista 7060XE7-64PRS-RV3-L needs ORv3 DC power and a liquid loop (35 °C inlet, 5.5–6 LPM, BMQC/UQD). An air-cooled, AC-powered TH6 box such as the Nexthop NH-4220-F (2 × 5.2 kW AC PSUs, 3720 W max) is the simpler lab switch.

Put the BOM, lab layout and calibration records in this folder as they are made.
