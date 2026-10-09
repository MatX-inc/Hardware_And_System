# WS6: 1.6T DR8 simulation

The simulator code lives in [../../Optics_DR8_E2E_simulation](../../Optics_DR8_E2E_simulation/) (dr8sim). Condor host SerDes study, calibration and open items: [condor_host_serdes.md](../../Optics_DR8_E2E_simulation/docs/condor_host_serdes.md). Tasks and status: [../tracker/TRACKER.md](../tracker/TRACKER.md#ws6-16t-dr8-simulation).

## Current results: 500 m, 4.0 dB channel loss, Condor host at both ends

| | Retimed (FRO) | LRO |
| --- | --- | --- |
| Net optical link margin | +4.18 dB | +3.94 dB |
| Condor RX pre-FEC BER vs 1.5e-6 (TH6 DG104 §9.1.2) | ~1e-16, pass | 2.0e-6 default impairments (fail); 3.4e-8 silicon-fit impairments (pass) |

Reproduce with `dr8sim run -c configs/condor_retimed_500m_4db.json`, `condor_lro_500m_4db.json` and `condor_lro_500m_4db_silicon_fit.json`.

The LRO verdict depends on calibrating the Condor RX model, which is still 2–3 decades pessimistic against CSAK silicon at 45–55 dB. TH6 uses the same Condor SerDes, so these results also apply to the switch side of WS4.
