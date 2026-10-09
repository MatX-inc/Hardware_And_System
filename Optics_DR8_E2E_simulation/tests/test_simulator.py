"""Tests for the single-lane end-to-end simulator, sweeps, and report."""

import copy
import json

import pytest

from dr8sim import simulator


@pytest.mark.slow
def test_full_end_to_end_simulation_and_link_budget(small_cfg):
  res = simulator.run_end_to_end_simulation(
      sim_cfg=small_cfg, run_sensitivity_and_budget=True
  )
  assert res.link_budget is not None
  assert res.end_to_end_ber < 2.4e-4
  assert res.link_budget.net_link_margin_db > 0.0


def test_run_is_reproducible_for_a_seed(small_cfg):
  a = simulator.run_end_to_end_simulation(small_cfg, False)
  b = simulator.run_end_to_end_simulation(copy.deepcopy(small_cfg), False)
  assert simulator.summarize_result(a) == simulator.summarize_result(b)


def test_report_uses_configured_fiber_length(small_cfg):
  small_cfg.fiber.length_km = 2.0
  res = simulator.run_end_to_end_simulation(small_cfg, False)
  report = simulator.format_simulation_report(res)
  assert 'TDECQ @ 2 km' in report
  assert '6 km' not in report


def test_summary_is_strict_json(small_cfg):
  res = simulator.run_end_to_end_simulation(small_cfg, False)
  summary = simulator.summarize_result(res)
  json.dumps(summary, allow_nan=False)
  assert summary['fiber_length_km'] == small_cfg.fiber.length_km


def test_sweep_parameter_records_each_value(small_cfg):
  rows = simulator.sweep_parameter(
      small_cfg, 'laser.rin_db_hz', [-150.0, -130.0]
  )
  assert [r['value'] for r in rows] == [-150.0, -130.0]
  assert set(simulator.SWEEP_METRICS) <= set(rows[0])
  # Much worse RIN must not improve the receiver SNR.
  assert rows[1]['orx_snr_db'] < rows[0]['orx_snr_db']


def test_sweep_parameter_rejects_unknown_key(small_cfg):
  with pytest.raises(ValueError):
    simulator.sweep_parameter(small_cfg, 'laser.nope', [1.0])


def test_reach_sweep_dispersion_grows_with_length(small_cfg):
  rows = simulator.sweep_fiber_reach(small_cfg, [0.0, 3.0, 6.0])
  assert [r['length_km'] for r in rows] == [0.0, 3.0, 6.0]
  cds = [abs(r['dispersion_ps_nm']) for r in rows]
  assert cds[0] == 0.0 and cds[1] < cds[2]
