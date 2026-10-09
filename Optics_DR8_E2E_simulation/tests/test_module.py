"""Tests for the 8-lane DR8 module simulation."""

import json

from dr8sim import config
from dr8sim import module


def _module_cfg():
  cfg = config.ModuleConfig()
  cfg.lane.num_symbols = 1024
  return cfg


def test_lane_configs_have_distinct_seeds_and_overrides():
  cfg = _module_cfg()
  cfg.lane_overrides = {'5': {'laser.cw_power_dbm': 7.0}}
  lanes = module.build_lane_configs(cfg)
  assert len(lanes) == 8
  assert len({c.random_seed for c, _ in lanes}) == 8
  assert lanes[5][0].laser.cw_power_dbm == 7.0
  assert lanes[5][1] == {'laser.cw_power_dbm': 7.0}
  assert lanes[4][0].laser.cw_power_dbm == cfg.lane.laser.cw_power_dbm


def test_monte_carlo_spread_is_reproducible_and_varies_lanes():
  cfg = _module_cfg()
  cfg.monte_carlo = True
  a = module.build_lane_configs(cfg)
  b = module.build_lane_configs(cfg)
  powers = [c.laser.cw_power_dbm for c, _ in a]
  assert powers == [c.laser.cw_power_dbm for c, _ in b]
  assert len(set(powers)) == 8


def test_bad_lane_override_index_is_rejected():
  cfg = _module_cfg()
  cfg.lane_overrides = {'8': {'laser.cw_power_dbm': 7.0}}
  try:
    module.build_lane_configs(cfg)
  except ValueError as e:
    assert '0..7' in str(e)
  else:
    raise AssertionError('expected ValueError')


def test_run_module_flags_a_degraded_lane():
  cfg = _module_cfg()
  # Starve lane 2 of laser power so it fails the BER limit.
  cfg.lane_overrides = {'2': {'laser.cw_power_dbm': -6.0}}
  result = module.run_module(cfg)
  assert len(result.lanes) == 8
  assert not result.lanes[2].passed
  assert result.worst('end_to_end_ber').lane == 2
  assert not result.passed
  others = [lane for lane in result.lanes if lane.lane != 2]
  assert all(lane.passed for lane in others)

  report = module.format_module_report(result)
  assert 'MODULE: FAIL (7/8 lanes pass)' in report
  json.dumps(module.summarize_module(result), allow_nan=False)
