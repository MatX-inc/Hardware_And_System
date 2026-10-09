"""Tests for the RIN_OMA <-> laser RIN conversion."""

import numpy as np
import pytest

from dr8sim import config
from dr8sim import optical_channel
from dr8sim import simulator


def test_correction_values():
  assert config.rin_oma_correction_db(5.0) == pytest.approx(0.71, abs=0.01)
  # Higher ER -> OMA closer to 2x average -> RIN_OMA below laser RIN.
  assert config.rin_oma_correction_db(8.0) < 0
  assert config.rin_oma_correction_db(3.5) > config.rin_oma_correction_db(5.0)
  with pytest.raises(ValueError):
    config.rin_oma_correction_db(0.0)


def test_round_trip():
  rin = config.rin_oma_to_laser_rin(-136.0, 5.0)
  assert config.laser_rin_to_rin_oma(rin, 5.0) == pytest.approx(-136.0)


@pytest.mark.parametrize('er_db', [3.5, 5.0, 8.0])
def test_conversion_matches_square_wave_measurement(er_db):
  """Square-wave-modulate a RIN-carrying laser and measure RIN_OMA directly."""
  cfg = config.LinkSimulationConfig()
  cfg.laser.rin_db_hz = -140.0
  fs = cfg.sample_rate_hz
  n = 1 << 20
  field, _ = optical_channel.simulate_cw_laser(
      n, fs, cfg.laser, np.random.default_rng(3))
  er = 10 ** (er_db / 10)
  p_low, p_high = 2 / (er + 1), 2 * er / (er + 1)  # average power 1
  levels = np.where((np.arange(n) // 64) % 2 == 0, p_low, p_high)
  power = np.abs(field) ** 2 / np.mean(np.abs(field) ** 2) * levels
  var_low = np.var(power[levels == p_low])
  var_high = np.var(power[levels == p_high])
  # White noise per sample spans fs/2 (one-sided).
  rin_oma_measured = 10 * np.log10(
      0.5 * (var_low + var_high) / ((fs / 2) * (p_high - p_low) ** 2))
  assert rin_oma_measured == pytest.approx(
      config.laser_rin_to_rin_oma(-140.0, er_db), abs=0.1)


def test_rin_oma_input_drives_the_simulation():
  cfg = config.LinkSimulationConfig(num_symbols=1024)
  config.apply_overrides(cfg, ['laser.rin_oma_db_hz=-136'])
  expected = -136.0 - config.rin_oma_correction_db(5.0)
  assert cfg.effective_laser_rin_db_hz == pytest.approx(expected)
  res = simulator.run_end_to_end_simulation(cfg, False)
  summary = simulator.summarize_result(res)
  assert summary['laser_rin_db_hz'] == pytest.approx(expected)
  assert summary['rin_oma_db_hz'] == pytest.approx(-136.0)
  assert 'RIN_OMA -136.0 dB/Hz' in simulator.format_simulation_report(res)
  # Same noise as setting the equivalent laser RIN directly.
  direct = config.LinkSimulationConfig(num_symbols=1024)
  direct.laser.rin_db_hz = expected
  res2 = simulator.run_end_to_end_simulation(direct, False)
  assert res2.optical_line.orx_snr_db == pytest.approx(
      res.optical_line.orx_snr_db)


def test_rin_oma_needs_target_er():
  cfg = config.LinkSimulationConfig()
  with pytest.raises(ValueError, match='target_outer_er_db'):
    config.apply_overrides(
        cfg, ['mzm.target_outer_er_db=0', 'laser.rin_oma_db_hz=-136'])


def test_rin_oma_can_be_cleared():
  cfg = config.LinkSimulationConfig()
  config.apply_overrides(cfg, ['laser.rin_oma_db_hz=-136'])
  config.apply_overrides(cfg, ['laser.rin_oma_db_hz=none'])
  assert cfg.effective_laser_rin_db_hz == cfg.laser.rin_db_hz
