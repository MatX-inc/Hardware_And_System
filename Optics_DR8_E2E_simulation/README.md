# dr8sim: 1.6T-DR8 optical link simulator

Time-domain, end-to-end simulation of a 1.6T-DR8 transceiver link: 8 lanes ×
200 Gb/s PAM4 at 106.25 GBaud over O-band SMF-28. Each lane is modeled from
host ASIC TX to remote host ASIC RX. Outputs include IEEE 802.3-style TDECQ/TECQ,
R_LM, extinction ratio, receiver-sensitivity waterfalls, and a link power
budget.

```
Host TX ─ C2M PCB ─ Client RX (CTLE/ADC/FFE+DFE) ─ retimed or LPO forwarding
  ─ Line TX DSP (FIR, arcsin pre-distortion, DAC, driver) ─ CW laser (RIN, linewidth, λ error)
  ─ SiPh push-pull MZM (Vπ, ER, bias error, EO BW, chirp) ─ SMF-28 (CD, PMD, loss)
  ─ PIN PD + TIA (thermal/shot/RIN, overload) ─ ADC ─ ORX FFE+DFE
  ─ Client TX ─ M2C PCB ─ Host RX
```

## Module architectures

Choose one with `--architecture` (or `--set architecture=...`):

| Architecture | Transmit (host → fiber) | Receive (fiber → host) |
|---|---|---|
| `retimed` (default) | Module DSP retimes | Module DSP (ADC + FFE/DFE) retimes, then re-drives the M2C trace |
| `lro` | Module DSP retimes | Linear: PD → TIA → linear driver → M2C trace. The host SerDes RX equalizes fiber + TIA + driver + trace together. |

LRO receive-path parameters (driver swing, bandwidth, peaking, noise,
saturation, and host RX FFE/DFE taps) live in `LroConfig` under `lro.*`. In LRO
runs the report still shows the module DSP receiver, labelled "Reference DSP
RX". That receiver isn't in the LRO signal path. It's there so you can read
the linear-receive penalty directly.

Laser RIN can be given either as laser RIN relative to average power
(`--rin-db-hz`, `laser.rin_db_hz`) or as an IEEE-style RIN_OMA
(`--rin-oma-db-hz`, `laser.rin_oma_db_hz`). RIN_OMA is converted at the MZM
target outer ER:

  RIN_OMA = RIN + 10·log10((1 + ER²) / (2·(ER − 1)²))

That's +0.71 dB at 5 dB ER. Spec RIN_xOMA values include a reflection
condition, while the model has none, so the whole value is treated as
intrinsic laser RIN.

`--channel-loss-db` (`fiber.total_channel_loss_db`) fixes the total passive
channel loss, for example to a spec channel such as 3.0 dB (DR8) or 4.0 dB
(DR8-2). Chromatic dispersion still follows `fiber.length_km`.

```bash
dr8sim run --lro --fiber-length-km 0.5 --channel-loss-db 4 -o output/lro
```

## Host SerDes: Broadcom Condor

`--condor` (or `--set host_serdes=condor`) models the Broadcom Condor 3nm
SerDes on the Phytile IOD as the **host TX** (into the module) and the
**host RX** (out of the module). The module's own client SerDes stay as they
were. The model includes:

- TX FFE in Condor's integer codes, `condor.tx_ffe_codes`, ordered
  (pre3, pre2, pre1, main, post1, post2) with sum(|codes|) ≤ 168
- amplitude 0.68 / 0.9 / 1.1 V
- RX with three auto-adapted peaking filters (PF1/PF2/PF3), an ADC, and an
  FFE + DFE
- AMI jitter values
- Phytile package loss

Combine it with either module architecture:

```bash
dr8sim run -c configs/condor_retimed_500m_4db.json
```

```bash
dr8sim run -c configs/condor_lro_500m_4db.json
```

Several parameters are assumptions, because Broadcom's UG100 couldn't be read.
[docs/condor_host_serdes.md](docs/condor_host_serdes.md) has the internal
sources, which values are measured and which are assumed, results, and open
items.

## Install

Requires Python 3.9 or newer.

```bash
python3 -m venv .venv
```

```bash
.venv/bin/pip install --upgrade pip
```

```bash
.venv/bin/pip install -e '.[dev]'
```

The pip upgrade matters on Python 3.9: its bundled pip can't do editable
installs from `pyproject.toml`.

This installs the `dr8sim` command. `python -m dr8sim` also works.

## Usage

### Single lane, full report

```bash
dr8sim run -o output/run
```

This prints the stage-by-stage report, the link budget, and a 0–6 km reach
table. It writes `simulation_report.txt`, `simulation_summary.json`,
`config.json` (the exact config used), and a 6-panel dashboard PNG/PDF.
Add `--no-budget` to skip the sensitivity sweeps (much faster).

Exit codes:

- 0: pass.
- 1: fail. Either the net margin is negative, or, with `--no-budget`, the
  end-to-end BER is above `receiver.target_pre_fec_ber`.
- 3: margin unknown, because the sensitivity sweep never reached the target
  BER.

The reach sweep and the sensitivity sweeps cap runs at 8192 symbols.

### All 8 lanes

```bash
dr8sim module -o output/module
```

Each lane gets its own data/noise seed. Options:

- `--lane-set 3:laser.cw_power_dbm=8.0` overrides one lane. Repeatable.
- `--monte-carlo` applies Gaussian lane-to-lane spread to laser power,
  wavelength, ER, bias, MZM IL, connector loss, and TIA noise. Tune it with
  `--spread-set wavelength_sigma_nm=2`.
- `--spec-set tdecq_max_db=3.0` changes a pass/fail limit.
- `--budget` adds per-lane sensitivity sweeps and link margin.
- `-j 8` runs lanes in parallel.

Each lane is checked against TDECQ, ER, R_LM, pre-FEC BER, and (with
`--budget`) margin limits. The exit code is 1 if any lane fails.

> [!WARNING]
> The default `SpecLimits` values are placeholders in the 802.3dj 200G/lane DR
> range. Check them against the spec revision you design to.

### Sweep any parameter

```bash
dr8sim sweep laser.wavelength_error_nm --range -6 9 6 -o output/sweeps
```

```bash
dr8sim sweep receiver.tia_irnd_pa_per_sqrt_hz --values 12 16 20 --metrics tdecq_db orx_snr_db e2e_ber
```

Any field can be swept. With `-o`, it writes a CSV and a plot.

### Configuration

Every parameter lives in the dataclasses in
[src/dr8sim/config.py](src/dr8sim/config.py). There are three ways to change
them, applied in this order:

1. A JSON file with `-c FILE`. A file only needs the keys it changes. A module
   config wraps the lane config under `"lane"`.
2. Shortcut flags: `--fiber-length-km`, `--wavelength-error-nm`,
   `--target-er-db`, `--rin-db-hz`, `--pcb-trace-length-mm`, `--num-symbols`,
   `--seed`, `--unretimed`.
3. `--set dotted.key=value`, for example `--set mzm.eo_bw_ghz=50`.

To get a full, editable config:

```bash
dr8sim config -o my_config.json
```

Unknown keys are rejected, so typos don't pass silently.

The presets in [configs/](configs/) are:

| File | What it is |
|---|---|
| `worst_case_6km.json` | 6 km at +4.5 nm laser error and λ0 = 1300 nm. This is what the original `main.py` ran by default. |
| `dr8_2km.json` | 2 km reach |
| `lpo_unretimed.json` | Soft forwarding (module DSP equalizes but doesn't slice) at 2 km. **Not** a true LPO model |
| `condor_retimed_500m_4db.json` | Condor host TX/RX + retimed module, 500 m, 4.0 dB |
| `condor_lro_500m_4db.json` | Condor host TX/RX + LRO module, 500 m, 4.0 dB |
| `condor_lro_500m_4db_silicon_fit.json` | Condor LRO with impairments fitted toward Condor silicon data |
| `lro_500m_4db.json` | LRO module, 500 m, 4.0 dB total channel loss |
| `module_monte_carlo.json` | 8-lane Monte Carlo with spread, a degraded lane 7, and spec limits |

### Python API

```python
from dr8sim import LinkSimulationConfig, ModuleConfig, run_end_to_end_simulation, run_module, sweep_parameter

cfg = LinkSimulationConfig()
cfg.fiber.length_km = 2.0
result = run_end_to_end_simulation(cfg, run_sensitivity_and_budget=True)
print(result.tdecq.tdecq_db, result.link_budget.net_link_margin_db)

rows = sweep_parameter(cfg, "mzm.target_outer_er_db", [4.0, 4.5, 5.0, 5.5])

mod = ModuleConfig(monte_carlo=True)
if __name__ == "__main__":  # needed for jobs > 1 (process pool) on macOS
    print(run_module(mod, jobs=8).passed)
```

## Tests

```bash
.venv/bin/pytest
```

The full suite takes a few seconds. `-m "not slow"` skips the link-budget
test.

## Layout

```
src/dr8sim/
  config.py              dataclasses, JSON load/save, dotted-key overrides
  electrical_channel.py  PAM4, TX FIR/DAC/RJ, PCB channel, CTLE, ADC, FFE+DFE
  optical_channel.py     laser, SiPh MZM, SMF-28, PD/TIA, ORX DSP
  metrology.py           TDECQ/TECQ, R_LM, sensitivity sweep, link budget
  simulator.py           single-lane pipeline, sweeps, report, JSON summary
  return_path.py         receive direction: retimed ORX DSP or LRO linear driver
  host_serdes.py         per-end SerDes configs (generic or Broadcom Condor host)
  module.py              8-lane DR8 module, Monte Carlo, spec checks
  plotting.py            dashboard, per-lane, and sweep plots
  cli.py                 `dr8sim` command
configs/                 preset JSON configs
docs/FUNCTION_GUIDE.md   model and math reference for each function
docs/condor_host_serdes.md  Condor study: sources, parameters, results
tests/                   pytest suite
```

## Model notes

- One simulated lane uses 16384 symbols at 16 samples/symbol by default.
  Sweeps cap runs at 8192 symbols unless you pass `--full-length`. When few
  errors are counted, BER falls back to a Gaussian-tail estimate from the
  measured SNR, so very small BERs are extrapolations.
- Receiver sensitivity extends the VOA range until the BER waterfall crosses
  the target. If it never crosses, sensitivity is reported as N/A and is never
  extrapolated.
- DR8 lanes share one nominal wavelength on parallel fibers, so there is no
  inter-lane optical crosstalk model. Lane-to-lane differences come only from
  seeds, overrides, and Monte Carlo spread.
