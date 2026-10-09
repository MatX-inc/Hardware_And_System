# 1.6T DR8 optics qualification program

Qualifying 1.6T DR8 OSFP optics (LRO and FRO) on TH6 network switches for the MatX One scale-out network. Owner: Yong Zeng.

## Workstreams

| WS | Folder | Goal |
| --- | --- | --- |
| WS1 | [WS1_Lab_Setup](WS1_Lab_Setup/) | Stand up a lab that can qualify 1.6T DR8 OSFP optics on TH6 switches |
| WS2 | [WS2_Optics_Sourcing](WS2_Optics_Sourcing/) | Pick the 1.6T DR8 source(s), LRO and/or FRO, with samples in the lab |
| WS3 | [WS3_Switch_Selection](WS3_Switch_Selection/) | Arista 7060XE7-64PRS-RV3-L primary, Nexthop NH-4220-F backup, plus a lab switch |
| WS4 | [WS4_Qualification](WS4_Qualification/) | Run the optic × switch qualification matrix to a pass/fail decision |
| WS5 | [WS5_Data_Analysis](WS5_Data_Analysis/) | Repeatable analysis and reports for every qualification run |
| WS6 | [WS6_Simulation](WS6_Simulation/) | dr8sim link and thermal simulation backing the LRO vs FRO choice |

## Status and decisions

- [tracker/TRACKER.md](tracker/TRACKER.md): every task by workstream, with status, owner and next step. The CSVs in [tracker/](tracker/) hold the same data plus the optics and switch vendor tables.
- [decisions.md](decisions.md): decision log.

The tracker is a snapshot. Day-to-day edits happen in the live tracker page and the test plan doc on claude.ai; both are re-exported here when they change.

## What is not in this repo

Vendor NDA decks, datasheets, quotes and pricing stay on Google Drive. Folders and files reference them by name.
