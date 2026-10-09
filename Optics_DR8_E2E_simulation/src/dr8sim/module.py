"""Full 8-lane 1.6T-DR8 module simulation.

A DR8 module carries eight independent 200G PAM4 lanes over eight parallel
fibers at the same nominal O-band wavelength. Each lane here is a complete
end-to-end single-lane simulation (`simulator.run_end_to_end_simulation`)
with its own data/noise seed, optional per-lane parameter overrides, and
optional Monte Carlo lane-to-lane spread. Lane results are checked against
`config.SpecLimits` and rolled up into a module verdict.
"""

from __future__ import annotations

import concurrent.futures
import copy
import dataclasses
import math
from typing import Any, Dict, List, Tuple

import numpy as np

from dr8sim import config
from dr8sim import simulator


@dataclasses.dataclass
class LaneResult:
  """Outcome of one lane of a DR8 module run.

  Attributes:
    lane: Lane index (0-based).
    applied: Dotted config keys this lane changed relative to the base config,
      mapped to the value used.
    metrics: Headline metrics from `simulator.summarize_result`.
    failures: Human-readable spec violations; empty if the lane passes.
  """

  lane: int
  applied: Dict[str, Any]
  metrics: Dict[str, Any]
  failures: List[str]

  @property
  def passed(self) -> bool:
    return not self.failures


@dataclasses.dataclass
class ModuleResult:
  """Roll-up of all lanes of a DR8 module run.

  Attributes:
    lanes: Per-lane results, ordered by lane index.
    spec: Limits the lanes were checked against.
    fiber_length_km: Base fiber length of the run (km).
    aggregate_tbps: Nominal module throughput (Tbps).
    architecture: Module class of the base config ('retimed' or 'lro').
    host_serdes: Host SerDes model of the base config.
  """

  lanes: List[LaneResult]
  spec: config.SpecLimits
  fiber_length_km: float
  aggregate_tbps: float
  architecture: str = 'retimed'
  host_serdes: str = 'generic'

  @property
  def passed(self) -> bool:
    return all(lane.passed for lane in self.lanes)

  def worst(self, metric: str, highest: bool = True) -> LaneResult:
    """Returns the lane with the highest (or lowest) value of `metric`."""
    candidates = [l for l in self.lanes if l.metrics.get(metric) is not None]
    if not candidates:
      raise ValueError(f'No lane reported {metric}')
    pick = max if highest else min
    return pick(candidates, key=lambda l: l.metrics[metric])


def build_lane_configs(
    module_cfg: config.ModuleConfig,
) -> List[Tuple[config.LinkSimulationConfig, Dict[str, Any]]]:
  """Builds the effective per-lane configs for a module run.

  Order of application per lane: base config, unique seed, Monte Carlo spread
  (if enabled), then explicit `lane_overrides`.

  Returns:
    List of (lane_config, applied_changes), one per lane.
  """
  base = module_cfg.lane
  num_lanes = base.num_lanes
  overrides: Dict[int, Dict[str, Any]] = {}
  for key, values in module_cfg.lane_overrides.items():
    key_s = str(key).strip()
    if not key_s.isdigit() or int(key_s) >= num_lanes:
      raise ValueError(
          f'lane_overrides key {key!r} is not a lane index in 0..{num_lanes - 1}'
      )
    lane_idx = int(key_s)  # normalizes '03' -> 3
    if lane_idx in overrides:
      raise ValueError(f'lane {lane_idx} has more than one lane_overrides key')
    overrides[lane_idx] = values

  spread_rng = np.random.default_rng([base.random_seed, 0xD8])
  lanes = []
  for lane in range(num_lanes):
    cfg = copy.deepcopy(base)
    cfg.random_seed = base.random_seed + lane * module_cfg.lane_seed_stride
    applied: Dict[str, Any] = {}

    if module_cfg.monte_carlo:
      for attr, key in config.LANE_SPREAD_KEYS.items():
        sigma = getattr(module_cfg.spread, attr)
        # Always draw, so changing one sigma does not reshuffle the others.
        delta = float(spread_rng.standard_normal()) * sigma
        if key == 'mzm.target_outer_er_db' and cfg.mzm.target_outer_er_db <= 0:
          delta = 0.0  # drive-voltage mode: no target ER to perturb
        if key == 'fiber.connector_loss_db' and (
            cfg.fiber.total_channel_loss_db is not None):
          delta = 0.0  # fixed channel loss: connector loss is not used
        if delta:
          value = float(config.get_by_path(cfg, key)) + delta
          if key in ('fiber.connector_loss_db', 'mzm.insertion_loss_db',
                     'receiver.tia_irnd_pa_per_sqrt_hz'):
            value = max(value, 0.0)
          config.set_by_path(cfg, key, value)
          applied[key] = round(value, 4)

    for key, value in overrides.get(lane, {}).items():
      config.set_by_path(cfg, key, value)
      applied[key] = config.get_by_path(cfg, key)

    lanes.append((cfg, applied))
  return lanes


def check_spec(metrics: Dict[str, Any], spec: config.SpecLimits) -> List[str]:
  """Returns the list of spec violations for one lane's metrics."""
  failures = []
  # summarize_result maps inf/NaN to None (e.g. TDECQ of a closed eye).
  tdecq, er = metrics.get('tdecq_db'), metrics.get('tx_er_db')
  rlm, ber = metrics.get('tx_rlm'), metrics.get('end_to_end_ber')
  if tdecq is None:
    failures.append('TDECQ N/A (eye closed)')
  elif tdecq > spec.tdecq_max_db:
    failures.append(f'TDECQ {tdecq:.2f} dB > {spec.tdecq_max_db:g} dB')
  if er is None or er < spec.er_min_db:
    failures.append(f'ER {_cell(er, ".2f")} dB < {spec.er_min_db:g} dB')
  if rlm is None or rlm < spec.rlm_min:
    failures.append(f'R_LM {_cell(rlm, ".3f")} < {spec.rlm_min:g}')
  if ber is None or ber > spec.pre_fec_ber_max:
    failures.append(f'BER {_cell(ber, ".2e")} > {spec.pre_fec_ber_max:.1e}')
  if 'net_link_margin_db' in metrics:
    margin = metrics['net_link_margin_db']
    if margin is None:
      failures.append('link margin unknown (sensitivity not bracketed)')
    elif margin < spec.link_margin_min_db:
      failures.append(
          f'margin {margin:+.2f} dB < {spec.link_margin_min_db:+g} dB'
      )
  return failures


def _simulate_lane(
    args: Tuple[int, config.LinkSimulationConfig, Dict[str, Any], bool,
                config.SpecLimits],
) -> LaneResult:
  """Runs one lane. Module-level so it can be pickled for process pools."""
  lane, cfg, applied, run_budget, spec = args
  res = simulator.run_end_to_end_simulation(
      cfg, run_sensitivity_and_budget=run_budget
  )
  metrics = simulator.summarize_result(res)
  return LaneResult(
      lane=lane,
      applied=applied,
      metrics=metrics,
      failures=check_spec(metrics, spec),
  )


def run_module(
    module_cfg: config.ModuleConfig | None = None,
    run_budget: bool = False,
    jobs: int = 1,
) -> ModuleResult:
  """Simulates all lanes of a 1.6T-DR8 module.

  Args:
    module_cfg: Module configuration. Defaults to `config.ModuleConfig()`.
    run_budget: Also run per-lane sensitivity sweeps and link budget (slower,
      roughly 10x per lane).
    jobs: Worker processes. 1 runs lanes sequentially in-process.

  Returns:
    ModuleResult with per-lane metrics and spec verdicts.
  """
  if module_cfg is None:
    module_cfg = config.ModuleConfig()
  tasks = [
      (lane, cfg, applied, run_budget, module_cfg.spec)
      for lane, (cfg, applied) in enumerate(build_lane_configs(module_cfg))
  ]
  if jobs > 1:
    with concurrent.futures.ProcessPoolExecutor(max_workers=jobs) as pool:
      lanes = list(pool.map(_simulate_lane, tasks))
  else:
    lanes = [_simulate_lane(t) for t in tasks]

  return ModuleResult(
      lanes=lanes,
      spec=module_cfg.spec,
      fiber_length_km=module_cfg.lane.fiber.length_km,
      aggregate_tbps=module_cfg.lane.aggregate_bit_rate_tbps,
      architecture=module_cfg.lane.architecture,
      host_serdes=module_cfg.lane.host_serdes,
  )


def _cell(value: Any, fmt: str) -> str:
  if value is None or (isinstance(value, float) and math.isnan(value)):
    return 'N/A'
  return format(value, fmt)


def format_module_report(result: ModuleResult) -> str:
  """Formats a per-lane table and module verdict."""
  has_margin = any('net_link_margin_db' in l.metrics for l in result.lanes)
  header = (
      f'{"Lane":>4} | {"WL (nm)":>8} | {"TX OMA":>7} | {"ER":>5} | '
      f'{"R_LM":>5} | {"TDECQ":>5} | {"RX OMA":>7} | {"ORX SNR":>7} | '
      f'{"E2E BER":>9}'
  )
  if has_margin:
    header += f' | {"Margin":>7}'
  header += ' | Result'

  lines = [
      '=' * len(header),
      f' 1.6T-DR8 {result.architecture.upper()} MODULE REPORT'
      f'{" (Condor host)" if result.host_serdes == "condor" else ""}: '
      f'{len(result.lanes)} lanes, {result.aggregate_tbps:.1f} Tbps, '
      f'{result.fiber_length_km:g} km SMF-28',
      '=' * len(header),
      header,
      '-' * len(header),
  ]
  for lane in result.lanes:
    m = lane.metrics
    row = (
        f'{lane.lane:>4} | {_cell(m["effective_wavelength_nm"], "8.2f")} | '
        f'{_cell(m["tx_oma_outer_dbm"], "+7.2f")} | '
        f'{_cell(m["tx_er_db"], "5.2f")} | {_cell(m["tx_rlm"], "5.3f")} | '
        f'{_cell(m["tdecq_db"], "5.2f")} | '
        f'{_cell(m["rx_oma_outer_dbm"], "+7.2f")} | '
        f'{_cell(m["orx_snr_db"], "7.2f")} | '
        f'{_cell(m["end_to_end_ber"], "9.2e")}'
    )
    if has_margin:
      row += f' | {_cell(m.get("net_link_margin_db"), "+7.2f")}'
    row += ' | ' + ('PASS' if lane.passed else 'FAIL')
    lines.append(row)

  lines.append('-' * len(header))
  worst_tdecq = result.worst('tdecq_db')
  worst_ber = result.worst('end_to_end_ber')
  lines.append(
      f'Worst TDECQ: lane {worst_tdecq.lane} '
      f'({worst_tdecq.metrics["tdecq_db"]:.2f} dB, limit '
      f'{result.spec.tdecq_max_db:g} dB)'
  )
  lines.append(
      f'Worst BER:   lane {worst_ber.lane} '
      f'({worst_ber.metrics["end_to_end_ber"]:.2e}, limit '
      f'{result.spec.pre_fec_ber_max:.1e})'
  )
  if has_margin:
    try:
      worst_margin = result.worst('net_link_margin_db', highest=False)
      lines.append(
          f'Min margin:  lane {worst_margin.lane} '
          f'({worst_margin.metrics["net_link_margin_db"]:+.2f} dB)'
      )
    except ValueError:
      lines.append('Min margin:  N/A (no lane bracketed sensitivity)')

  for lane in result.lanes:
    if lane.applied:
      changes = ', '.join(f'{k}={v}' for k, v in lane.applied.items())
      lines.append(f'  lane {lane.lane} changes: {changes}')
    for failure in lane.failures:
      lines.append(f'  lane {lane.lane} FAIL: {failure}')

  lines.append(
      f'MODULE: {"PASS" if result.passed else "FAIL"} '
      f'({sum(l.passed for l in result.lanes)}/{len(result.lanes)} lanes pass)'
  )
  lines.append('=' * len(header))
  return '\n'.join(lines)


def summarize_module(result: ModuleResult) -> Dict[str, Any]:
  """Returns a JSON-ready dict of the module verdict and per-lane metrics."""
  return {
      'passed': result.passed,
      'architecture': result.architecture,
      'host_serdes': result.host_serdes,
      'fiber_length_km': result.fiber_length_km,
      'aggregate_tbps': result.aggregate_tbps,
      'spec': config.to_dict(result.spec),
      'lanes': [
          {
              'lane': lane.lane,
              'passed': lane.passed,
              'failures': lane.failures,
              'applied': lane.applied,
              'metrics': lane.metrics,
          }
          for lane in result.lanes
      ],
  }
