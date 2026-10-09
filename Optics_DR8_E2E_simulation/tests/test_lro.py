"""Tests for the LRO (linear receive optics) architecture."""

import json

import numpy as np
import pytest

from dr8sim import config
from dr8sim import return_path
from dr8sim import simulator


def _cfg(arch):
  cfg = config.LinkSimulationConfig(num_symbols=2048, random_seed=5)
  cfg.architecture = arch
  cfg.fiber.length_km = 0.5
  return cfg


def test_unknown_architecture_is_rejected():
  with pytest.raises(ValueError, match='architecture'):
    config.apply_overrides(config.LinkSimulationConfig(), ['architecture=lpo'])


def test_lro_closes_the_link_with_a_penalty_vs_retimed():
  retimed = simulator.run_end_to_end_simulation(_cfg('retimed'), False)
  lro = simulator.run_end_to_end_simulation(_cfg('lro'), False)
  # Same TX direction and optics, so TX metrics match exactly.
  assert lro.tecq.tdecq_db == retimed.tecq.tdecq_db
  assert lro.end_to_end_ber < 2.4e-4
  # Linear RX: host SNR below the module DSP receiver on the same signal.
  assert (lro.client_to_host_m2c.post_eq_snr_db
          < lro.optical_line.orx_snr_db)
  assert lro.end_to_end_ber > retimed.end_to_end_ber
  assert simulator.summarize_result(lro)['architecture'] == 'lro'


def test_lro_report_labels_the_linear_path():
  res = simulator.run_end_to_end_simulation(_cfg('lro'), False)
  report = simulator.format_simulation_report(res)
  assert 'Module Architecture: LRO' in report
  assert 'Linear Driver' in report


def test_more_driver_noise_hurts_lro_host_snr():
  quiet = _cfg('lro')
  noisy = _cfg('lro')
  noisy.lro.driver_noise_mv_rms = 15.0
  a = simulator.run_end_to_end_simulation(quiet, False)
  b = simulator.run_end_to_end_simulation(noisy, False)
  assert (b.client_to_host_m2c.post_eq_snr_db
          < a.client_to_host_m2c.post_eq_snr_db)


def test_linear_driver_sets_output_swing():
  cfg = _cfg('lro')
  cfg.lro.driver_noise_mv_rms = 0.0
  cfg.lro.driver_peaking_db = 0.0
  rng = np.random.default_rng(0)
  levels = np.array([-1, -1 / 3, 1 / 3, 1])
  x = np.repeat(rng.choice(levels, 4096), cfg.samples_per_symbol) * 1e-3
  out = return_path.lro_linear_driver(x, cfg, rng)
  outer = np.percentile(np.abs(out), 99)
  assert outer == pytest.approx(0.5 * cfg.lro.driver_output_vppd, rel=0.1)


def test_total_channel_loss_override():
  cfg = _cfg('lro')
  cfg.fiber.total_channel_loss_db = 4.0
  res = simulator.run_end_to_end_simulation(cfg, False)
  assert res.optical_line.total_fiber_loss_db == 4.0
  json.dumps(simulator.summarize_result(res), allow_nan=False)


@pytest.mark.slow
def test_lro_link_budget_uses_fixed_channel_loss():
  cfg = _cfg('lro')
  cfg.fiber.total_channel_loss_db = 4.0
  res = simulator.run_end_to_end_simulation(cfg, True)
  bgt = res.link_budget
  assert bgt.total_channel_insertion_loss_db == 4.0
  assert bgt.fiber_attenuation_db + bgt.connector_loss_db == pytest.approx(4.0)
  assert bgt.host_m2c_concatenation_penalty_db > 0.0
