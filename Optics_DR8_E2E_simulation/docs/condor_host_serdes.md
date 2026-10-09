# Broadcom Condor as the host SerDes

This doc covers what MatX's internal material says about the Broadcom Condor 3nm 200G SerDes (on the Phytile BCM78005 IOD), how `dr8sim` models it as host TX and host RX, and what the model predicts for a 1.6T-DR8 link. The study was compiled on Oct 8 2026 from MatX Drive and Slack, then updated the same day with the Broadcom TH6 (BCM78914) datasheet and design guide, which use the same Condor SerDes. The Condor user guide (UG100) is encrypted and could not be read, so some parameters are assumptions; they are marked **[ASSUMED]** below and in [`CondorConfig`](../src/dr8sim/config.py).

## Results: 500 m, 4.0 dB channel loss, Condor at both host ends

The host PCB is calibrated to Celestica's MatX EM896K3 trace loss: 1.20 dB/in at 53.125 GHz, 120 mm long (the length is assumed). The C2M channel is 14.2 dB at Nyquist, including the assumed 5 dB Phytile package and 3.5 dB for the module side and connector.

| | Retimed DR8 | LRO DR8 (driver peaking 1.5 dB) |
|---|---|---|
| C2M IL @ 53.125 GHz | 14.17 dB | 14.17 dB |
| Condor host RX SNR (6-tap RX FFE) | 27.28 dB | 20.22 dB |
| Condor RX BER vs Broadcom criterion 1.5e-6 | 5e-24, PASS | 3.6e-6, FAIL (marginal); 8.6e-8 PASS with silicon-fit impairments |
| End-to-end pre-FEC BER | 1.0e-7 | 3.6e-6 |
| **Net optical link margin** | **+4.45 dB** | **+4.02 dB** |

To reproduce:

```bash
dr8sim run -c configs/condor_retimed_500m_4db.json -o output/condor_retimed_500m_4db
```

```bash
dr8sim run -c configs/condor_lro_500m_4db.json -o output/condor_lro_500m_4db
```

What the results show:

- With a retimed module, the Condor hops have huge margin. The optics set the link BER.
- With an LRO module, the optical power budget is fine (+4 dB). The binding constraint is Broadcom's own pass criterion: a Condor RX must reach about 1.5e-6 pre-FEC in IBIS-AMI simulation (TH6 DG104 §9.1.2), not just the 2.4e-4 KP4 limit. With the measured 6-tap Condor RX FFE, the LRO case sits right at that limit: it fails with the default (conservative) impairments and passes with impairments fitted toward silicon. See "Calibration against Condor silicon" below. Either way, the module's linear-driver peaking must be tuned to the host trace.

### LRO: driver peaking vs host trace length

These were run with the earlier 24-tap RX FFE assumption; the trend still holds.

Values are the Condor RX pre-FEC BER. `*` marks the ones that meet 1.5e-6.

| Host trace | 0 dB | 1.5 dB | 3 dB | 4.5 dB | 6 dB |
|---|---|---|---|---|---|
| 60 mm | 1.5e-6* | 2.5e-6 | 4.0e-6 | 1.2e-5 | 7.5e-6 |
| 120 mm | 2.7e-6 | 1.2e-6* | 2.1e-6 | 1.7e-6 | 7.1e-6 |
| 180 mm | 6.1e-5 | 2.4e-6 | 2.0e-6 | 1.8e-6 | 1.0e-6* |
| 250 mm | 6.4e-5 | 6.1e-5 | 3.1e-5 | 3.1e-5 | 2.3e-6 |

The best peaking rises with trace length. An LRO module needs programmable output equalization matched to each host channel, and long host traces (≥ 250 mm) don't meet the criterion even at 6 dB. These are 16k-symbol runs, so single cells carry roughly ±2× statistical spread; read the trend, not individual cells.

### LRO: sensitivity to the unknowns

These were also run with the earlier 24-tap RX FFE assumption.

Base case is the LRO preset with 3 dB driver peaking. Values are the Condor RX BER.

| Change | BER |
|---|---|
| Baseline: FFE 24t (3 pre) + DFE 1t | 7.3e-6 |
| FFE 12t + DFE 1t | 4.0e-6 |
| FFE 24t + DFE 4t | 2.5e-6 |
| ADC ENOB 6.5 → 5.5 | 1.2e-5 |
| LRO driver noise 1.5 → 3 mVrms | 8.0e-6 |
| Optical channel loss 3 dB / 5 dB | 7.2e-6 / 1.2e-5 |
| No RX FFE (AMI-style PF + VGA + DFE only) | ~1e-1 (collapses) |

The last row is a model limitation, not a Condor prediction. With only the assumed peaking filters and a DFE, the model can't cancel pre-cursor ISI. The real Condor works at 45 dB (measured), and the silicon has an RX FFE. RX FFE/DFE behaviour must be calibrated against Broadcom's AMI model before the LRO numbers can be trusted to better than a few dB.

- Condor TX FFE presets on C2M make little end-to-end difference, because the optics dominate.

## Calibration against Condor silicon (CSAK bench data in GitHub)

`MatX-inc/Hardware_And_System/CSAK_WebBench` holds the Condor test-bench tooling for Broadcom's CSAK APD0601 board (Triple test chip, Condor3 octals, firmware D003_07, 212.5 Gb/s). Its test fixtures include a full lane dump from a link-trained octal (`display_lane_9.txt`, octal `:9` = "PHD2 1 m + UPXR flyover") and a BER run (`ber_output.txt`).

What the silicon shows, per lane:

| Item | Value | Used in the model |
|---|---|---|
| RX FFE | **6 taps: RXFFE(n3, n2, n1, m, p1, p2)**, e.g. (138, −110, 23, 324, 49, 146) | Yes: `rx_ffe_taps = 6`, `rx_ffe_pre_taps = 3` (was 24 assumed) |
| DFE | DFE(1,2) = (x, 0) in PAM4 ER mode (`P4E`); ER mode uses an "ECD" block | 1 DFE tap kept as an ECD stand-in |
| Peaking filters PF(M, L, H) | Mid ≈ 15–18, low ≈ 21–23, high ≈ 11–13 (ranges 0–30 / 0–30 / 0–23) | Reference only; dB per code still unknown |
| VGA | 47–54 | n/a |
| Link-trained TX FIR | pre1 −34..−38, main 130..134, other taps 0 (Σ = 168) | Used for the calibration runs |
| Condor SNR readout | 21.4–22.5 dB | Definition unknown, so not compared directly |
| BER (10 s dwell, PRBS31) | 4e-13 to 9e-12 on the `:9` and `:f` octals | Calibration target |
| Other CSAK channels | OSFP 0.5 m and 1.5 m loopbacks, a 13–14 in trace loopback, a pad loopback | Good future calibration points |

### Model vs silicon, Condor TX → channel → Condor RX

Script: `scripts/calibrate_condor_csak.py`. Measured points: ~1e-10 at 45 dB, 1e-9..1e-7 at 50 dB, ~1e-6 at 55 dB.

| Model setting | 30 dB | 45 dB | 50 dB | 55 dB |
|---|---|---|---|---|
| Default impairments, 6-tap FFE | 1.1e-7 | 2.9e-2 | 9.9e-2 | 1.8e-1 |
| Silicon-fit impairments (below) | 2.7e-16 | 1.9e-4 | 9.4e-3 | 6.7e-2 |
| No noise or jitter, ENOB 10 | ~0 | 8.4e-11 | | 3.1e-9 |

After the audit fixes, the model is even more pessimistic at high loss than before: correct TX jitter and in-band RX noise both add impairment. PF1 pins at its maximum code on every long channel, which points to the assumed peaking-filter shapes (zeros and dB per code) being too weak. That's the main item to calibrate. The equalizer structure itself can reach the measured 45 dB result, but only with almost no impairments. So the impairment assumptions are too pessimistic. The biggest single item is RX clock jitter: the AMI `Rx_Clock_PDF "-0.037 0.037 0.01"` has no unit. Read as UI it costs about 2.5 dB at 30 dB of loss. Read as ps, like the TX jitter entry, it's negligible, and the silicon data favors ps. Even the best fit stays 2–3 decades pessimistic at 45–55 dB. Neither a cable-like nor a PCB-like channel shape fixes that: the real PF stages are stronger or better shaped than the assumed ones. This can't be pinned down further without the CSAK channel S-parameters or the AMI model.

### What it means for the optics cases (500 m, 4 dB)

| Case | Condor RX BER | vs 1.5e-6 |
|---|---|---|
| Retimed, any impairment set | 1e-16 or better | PASS |
| LRO, default impairments (`condor_lro_500m_4db.json`) | 3.6e-6 | FAIL (marginal) |
| LRO, silicon-fit impairments (`condor_lro_500m_4db_silicon_fit.json`) | 8.6e-8 | PASS |

Silicon-fit impairments are: RX jitter read as ps (0.0011 UI RJ, 0.0079 UI DJ pp), TX SNDR 38 dB, RX noise 0.064 mV/√GHz, ENOB 7.

Retimed is robust. LRO with Condor hinges on Condor's real noise and jitter. The silicon evidence leans toward pass, but the model can't settle it.

### Next calibration step

The CSAK board has OSFP cages, and the CSAK Web Bench can read Condor's adapted PF/FFE/DFE, SNR and BER per lane. Plugging a real retimed or LRO DR8 module into an OSFP cage in fiber loopback would give direct Condor-with-optics data, the best possible calibration. Short of that:

1. Get S-parameters for the CSAK OSFP 0.5 m / 1.5 m loopbacks and the 13–14 in trace loopback.
2. Run the bench on those channels.
3. Fit `CondorConfig` to the results.

> [!NOTE]
> On Oct 8 2026 a code audit fixed several simulator bugs: the RX FFE pre-/post-cursor direction (it mattered for Condor's 6-tap FFE), Condor CTLE adaptation, TX jitter (previously about half strength), chirp sign, MZM intrinsic ER, TX OMA/ER measurement, TDECQ sampling, and end-to-end BER double counting. The results, calibration and LRO verdict tables were re-run after the fixes. The peaking and sensitivity tables were run before them, so treat them as trends only.
>
> On Oct 8 2026 the electrical RX input noise was fixed to be band-limited at the specified density, the IEEE COM convention. Before that it was spread over the whole simulated band, which put about 10 dB too little of it in band. The result tables above were re-run with the fix. Calibration and sensitivity tables marked as run before it may shift by a few tenths of a dB.

## What the internal material says

Confidence: **DS** = Broadcom datasheet or spec, **AMI** = Broadcom IBIS-AMI model documentation, **MEAS** = lab data, **SLACK** = a statement in Slack.

### TX

| Parameter | Value | Source |
|---|---|---|
| FFE | 6 taps: pre3, pre2, pre1, main, post1, post2 | PM8x200 spec v19.0 §2.2.2 (DS); AMI sheet |
| Code ranges | pre3 −8..0, **pre2 0..+16**, pre1 −40..0, **main 0..168 (default 168)**, post1 −64..0, post2 −16..+16 | AMI sheet |
| Code scale | Σ\|codes\| ≤ 168, where 168 is full-scale drive | AMI ADS deck slide 5 |
| Broadcom guidance | Post EQ is unnecessary unless RX PF3 adapts to max. Tune pre1/pre2 to minimize pre-cursor ISI. | AMI ADS deck |
| Amplitude | amp_ctrl 0.68 / **0.9** / 1.1 V (AMI); datasheet range 800–1000 mVppd | AMI sheet; BCM78005 DS Table 35 |
| Jitter | Tx_Jitter DJ ±0.125, RJ 0.09 (units not stated; modeled as ps); DCD 0.09 ps | AMI sheet |
| Output R / AC coupling | 66/82/98 Ω differential; AC-couple once per link | DS Table 35; Broadcom PCB integration guide rev8 |
| Link training | Clause 136/93/72 link training; TXEQ requests use die temperature | PM8x200 spec |

### RX

| Parameter | Value | Source |
|---|---|---|
| Architecture | ADC/DSP | APD D2D & SerDes update, Jan 2026, slide 9 |
| Peaking filters | PF1 low-freq 0–30, PF2 mid 0–30, PF3 high 0–23; default auto. dB per code not given. | AMI sheet |
| VGA | 0–64, default 25, auto | AMI sheet |
| FFE / DFE | Both present (`aapl condor -get-rxffe`, `-get-dfe`); tap counts unknown | Slack, Jul 25 2026 ([link](https://matx-talk.slack.com/archives/C0BPBM3FCRZ/p1785015608639823)) |
| Modes | PAM4_200G_ER (default) / NR; ER mode uses an "ECD" block | AMI sheet |
| RX clock jitter | DJ ±0.037 UI, RJ 0.01 UI | AMI sheet |
| Reach | 45 dB bump-to-bump, guaranteed across process corners | Phytile interface doc §2.2; Slack, Jul 28 2026 |
| BER spec | 1e-15 with FEC across all conditions | PM8x200 spec |
| Adaptation | Tracks die temperature (polled every ~5–10 ms, ±5 °C accuracy) | DS §7.5 |
| FEC | RS544, RS272 LL, RS544 LL; no inner FEC (no Cl.177) | DS Table 9 |
| Refclk | 312.5 MHz for 212.5 Gb/s; 625 MHz only for 224G | PCB guide Table 7; DS Table 48 |

### Measured: Broadcom Triple test chip (same Condor IP) on the CSAK board

| Channel IL @ 53.1 GHz | BER |
|---|---|
| 28–30 dB | ~8e-14 (test floor) |
| 41–47 dB | 1e-13 to 9e-9 |
| ~45 dB | ~1e-10 |
| 50–53 dB | 1e-9 to 3e-5 |
| ~55 dB | ~1e-6 |

- IL rises about 1.7 dB from 32 °C to 70 °C.
- OSFP paths measured only 19–28 dB SRR.
- MatX capped electrical channels at 50 dB on Oct 8 2026. Sims at 45/50/55 dB found weaker TX FFE and higher amp_ctrl help.

### TH6 (BCM78914) documents: same Condor SerDes

These come from the TH6 folder on Drive (shared Oct 8 2026): the DS109 datasheet (Mar 13 2026), the DG104 hardware design guide (Apr 2 2026), and the reference-board stackup.

| Item | Value | Source |
|---|---|---|
| AMI pass criterion | PAM4 IBIS-AMI is a pre-FEC model. Use reported BER, not eye masks. Require pre-FEC BER ≈ **1.5e-6** (≈ 1e-15 post-FEC). | DG104 §9.1.2 |
| AMI model RX path | AFE (separate S-parameter) + RX EQ = PF, VGA, DFE. TX path = AFE + TX pre-emphasis. | DG104 §9.1 |
| Package loss | Large package: RX/TX package IL "might be higher than 4 dB" in some channels. Worst-case IL and crosstalk package models are on Broadcom's ESP. Per-pin IL file: `BCM78914B0_Package_Condor_IL_53GHz_final` (not on MatX Drive). | DG104 Ch.9, §7.2 |
| 200G PCB rules | Design to IEEE COM. Diff impedance 93 Ω ±10%. P/N phase skew < ±0.5 ps on the PCB and < ±1.0 ps die-to-connector. Mode conversion SCD21 < −21.6 dB (PCB) / < −15.5 dB (package + PCB) at Nyquist. No physical intra-pair length matching at 200G. AMI simulation is mandatory. | DG104 §7.1 |
| Minimum loss slope (100G guidance) | Bump-to-bump loss at 0.75 fb must be ≥ 10 dB higher than at 0.25 fb (Condor RX), so very short or flat channels are a problem. Not stated for 200G in DG104. | DG104 §7.2 |
| AC coupling | RX has on-die AC caps that can't be bypassed. External caps are needed only if RX input exceeds 0.96 V (input Vcm ~0.66 V max). Use 100 nF 0201 if needed. | DG104 Ch.2 |
| Temperature tracking | ~5 °C per minute | DG104 §2.2.3 |
| DC specs | RX Vdiff 500–900 mVppd, Rdiff 80/95/112 Ω, Cac 0.5/0.6 pF. TX Vdiff 800–1000 mVppd, Vcm 400/450/500 mV, Rdiff 66/82/98 Ω. | DS109 Table 35 (same as Phytile DS) |
| Refclk | 312.5 MHz, ±25 ppm, ≤ 0.035 ps rms (4 MHz HPF, 12 kHz–20 MHz) | DS109 Tables 48–49 |
| Return-loss modes | LR / MR / VSR, per lane | DS109 §3.2.1 |
| Reference board stackup (BCM978914B0K) | 50 layers. L1–24 **Megtron 8 (N)**, HVLP3 copper, Dk ≈ 2.79–2.91, used for 200G routing. L25–50 R-1755V. | Stackup xlsx (image) |

DS109 Chapter 6 has the same stale "CEI-56G / CDAUI-8" compliance text as the Phytile datasheet, so ignore it.

### Host channel at MatX

The Celestica reference channel (to PHD2, not OSFP) is 12.1 dB at 53 GHz, excluding the package, and is marked "needs updating for MatX". Phytile package loss and the IOD-to-OSFP-cage channel are **not available**. MatX asked Broadcom for Phytile S-parameters; there is no recorded answer.

### Condor with optics

- **Broadcom:** marketing lists C2M, LPO, CPO and LRO among Condor interconnects.
- **MatX:** statements conflict. Yongming, Apr 14 2026: "plan to use LRO and could test LPO". Yvonne, May 28 2026: "not currently planning on LPO … would need a retimer".
- **Missing:** no Broadcom host-TX settings for LRO were found, and no Condor + DR8 module interop or plugfest data.

## How dr8sim models Condor

Set `host_serdes: "condor"` (CLI `--condor`). Condor replaces only the two host ASIC ends. The module's client SerDes keep `host_channel`. The mapping lives in [`host_serdes.py`](../src/dr8sim/host_serdes.py).

| Model element | Implementation | Value | Basis |
|---|---|---|---|
| TX FFE | Codes → T-spaced FIR; swing = amp × Σ\|codes\| / 168 | AMI default codes | AMI |
| TX amplitude | | 0.9 V | AMI |
| TX jitter | RJ Gaussian + dual-Dirac DJ | 0.09 ps rms, 0.25 ps pp | AMI (units assumed ps) |
| TX DAC / SNDR / BW | | 8 bit / 33 dB / 70 GHz | ASSUMED |
| Phytile package | Added to the host channel at both host ends | 5.0 dB @ 53 GHz | ASSUMED (TH6 DG104: >4 dB on some channels) |
| Host PCB (Condor presets) | Skin 0.08 dB/in/√GHz + dielectric 0.01163 dB/in/GHz | 1.20 dB/in @ 53.125 GHz | Celestica EM896K3; length 120 mm ASSUMED |
| Pass criterion | Condor RX BER checked in the report | 1.5e-6 | TH6 DG104 §9.1.2 |
| RX front end | 4th-order Bessel; input noise | 70 GHz; 0.128 mV/√GHz | ASSUMED; COM η0 = 1.64e-8 V²/GHz |
| PF1/PF2/PF3 | Three first-order peaking stages; codes auto-adapted by grid search for best post-EQ SNR (or fixed via `condor.rx_ctle_codes`) | Zeros 1.5 / 10 / 30 GHz; max boost 4 / 8 / 10 dB | Code ranges AMI; zeros and dB ASSUMED |
| VGA | Implicit (ADC full-scale and equalizer normalization) | | |
| ADC | | 6.5 ENOB | ASSUMED |
| RX DSP | FFE + DFE | 6-tap FFE (3 pre) + 1-tap DFE (ECD stand-in) | MEASURED (CSAK lane dump) |
| RX clock jitter | RJ + dual-Dirac DJ at the sampler | 0.01 UI rms, 0.074 UI pp | AMI |

Not modeled: ER/NR mode differences, reflections and crosstalk (the measured SRR of 19–28 dB matters on real OSFP paths), temperature drift, link training, and FEC-level effects.

## Gaps and next steps

0. **DG105.** `78914-DG105 (TH6 HW Design Guide)` (Jun 2026) did not open with the folder password, so DG104 (Apr 2026) was used. Get the DG105 password: it may add 200G-specific loss-slope or channel rules.

1. **UG100.** Decrypt or obtain a readable copy. It should give PF dB per code, RX FFE/DFE tap counts, ADC resolution, and the optical/LRO modes.
2. **Calibrate against the AMI model.** Run Broadcom's AMI model (`BRCM_AMI_Condor_3nm_v2.1.zip`, 26 MB, on Drive) in ADS on the MatX C2M channel. Fit `CondorConfig` so this model's post-EQ SNR matches the AMI result.
3. **Phytile package S-parameters** from Broadcom, to replace the 5 dB assumption. Also ask for the Phytile equivalent of the TH6 `Package_Condor_IL_53GHz` sheet.
4. **MatX IOD → OSFP host channel.** The front-panel routing doc was not found. Replace the 120 mm generic trace with the real channel (S-parameters, or the loss per inch from the Celestica proposal).
5. **802.3dj Annex 176D** (200GAUI-1 C2M) host and module specs. The D3.0 PDF exceeded the connector's download limit.
6. **LRO decision.** The LRO case is marginal with these assumptions. It needs Broadcom confirmation of 200G LRO support, and module vendor linear-driver specs to replace the `lro.*` assumptions.

## Sources

Drive:

- [BCM78005 DS200 (unencrypted Mar copy)](https://drive.google.com/file/d/1B5cvDswLzhWTdCeqtdcc29daNZ5xN6S5/view)
- [Condor_3nm_AMI_v2_CHAR_DESCRIPTION.xlsx](https://drive.google.com/file/d/1XbTl6zJsGkvLR4phfnHakcBgLbUYTWpG/view)
- [Condor_3nm_AMI_v2_sim_in_ADS.pdf](https://drive.google.com/file/d/1vBfryUeWGGlSo_adGm3I2-cALQHlhIHH/view)
- [PM8x200_APD_Spec_v19.0](https://drive.google.com/file/d/1e3P7ar7_CHPvYXi0A0BZwT68EHMqBuKq/view)
- [Broadcom APD 3nm PCB integration guide rev8](https://drive.google.com/file/d/1coqObVuVM808Pzqp15FL-Hcu6rxk4wCf/view)
- [APD D2D & SerDes update, Jan 2026](https://drive.google.com/file/d/1sDb_OQdnn6ZzwuK-En4va9o371Eq6dNy/view)
- [Phytile 12.8T interface doc](https://drive.google.com/file/d/1Fud3G_sDB1SWJxzafcSQz5kxeXTNSqO6/view)
- [CSAK_NPC_BER_Results.xlsx](https://drive.google.com/file/d/1gweoCsPFnGGsOzc4H6ikm1jOeYys51nW/view)
- [Triple CSAK device screening](https://drive.google.com/file/d/10xCep2TF2ITsDYo3ezEKxq1hS3IA-0qr/view)
- [Summary CSAK Triple OSFP](https://drive.google.com/file/d/1o4ix5iWbFmfNUDJF1BhePiN0bskKsh5u/view)
- [Summary CSAK Triple 2.5D high-loss](https://drive.google.com/file/d/1PS8naZZ7lAhGYljEWCdkcko9NImLquhd/view)
- [Celestica Technical Proposal V1.1](https://drive.google.com/file/d/1xyp9RGyXEv_Vi8ksfvg_Gi7oKtkDPQG1/view)
- [SIPI weekly sync, Oct 8 2026](https://docs.google.com/document/d/1sR-P7Z1I-z26hI5QO92_Fsxr-BKYIoqZQbIM3CohQpI/edit)

TH6 folder ([Drive folder](https://drive.google.com/drive/folders/1gyw01oyFcMscBz12wziuuDNoRXkKoqF5)), decrypted locally with the folder password:

- [78914-DS109](https://drive.google.com/file/d/1sbmH5BFW8gqI4ZFesdxkBkeDnKJ8IDLX/view), read in full
- [78914-DG104](https://drive.google.com/file/d/1zO5hanuesC6jpQCULI70bPBZqUpfQ1EU/view), read in full
- [BCM978914B0K PCB stackup](https://drive.google.com/file/d/1vz5I1SWbsm8yFNYCWjcGmw4uDoByjUlN/view)
- Not opened: [78914-DG105](https://drive.google.com/file/d/1gOFFRGxrHB1tiJtw1JhePpVjSdG3NJng/view) (different password). Also skipped: the BOM, schematic and Allegro zips, and SwitchCLI UG103 (not SerDes-relevant, or over the download limit).

Not readable:

- TSC-Condor-UG100 (encrypted): [copy 1](https://drive.google.com/file/d/1UqKW7iw2O-Hln7dpfUsVV9sx-yO03L-V/view), [copy 2](https://drive.google.com/file/d/1KJA-jZzs23wXQzGB_f_0CnWxKDiij0OW/view)
- IEEE P802.3dj D3.0 (too large to download)
- Condor AMI zip and TH6 package S-parameter zips (too large to download)

Slack: #boards-and-systems-hardware (Jul 25–28 and Apr 14 2026), #system-architecture (May 28–29 2026), #sipi-optic.
