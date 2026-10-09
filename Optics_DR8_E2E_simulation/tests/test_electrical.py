"""Tests for the electrical (C2M / M2C) channel and SerDes DSP models."""

import numpy as np

from dr8sim import config
from dr8sim import electrical_channel


def test_pam4_gray_coding_round_trip(rng):
  symbols, indices, bits = electrical_channel.generate_pam4_symbols(1024, rng)
  sliced_sym, sliced_idx, recovered_bits = (
      electrical_channel.slice_pam4_symbols(symbols)
  )
  np.testing.assert_allclose(symbols, sliced_sym)
  np.testing.assert_array_equal(indices, sliced_idx)
  np.testing.assert_array_equal(bits, recovered_bits)


def test_gray_code_adjacent_levels_differ_by_one_bit():
  bits = electrical_channel._INDEX_TO_GRAY_BITS  # pylint: disable=protected-access
  for i in range(3):
    assert np.sum(bits[i] != bits[i + 1]) == 1


def test_pcb_trace_and_electrical_equalization():
  sim_cfg = config.LinkSimulationConfig(num_symbols=2048, random_seed=7)
  rng = np.random.default_rng(sim_cfg.random_seed)
  tx_symbols, _, tx_bits = electrical_channel.generate_pam4_symbols(
      sim_cfg.num_symbols, rng
  )
  res = electrical_channel.simulate_electrical_segment(
      tx_symbols=tx_symbols,
      tx_bits=tx_bits,
      sim_cfg=sim_cfg,
      host_cfg=sim_cfg.host_channel,
      rng=rng,
  )
  # 120 mm C2M PCB trace + package has ~12-18 dB insertion loss at 53.125 GHz
  assert 10.0 < res.pcb_loss_nyquist_db < 25.0
  # Adaptive CTLE + 15-tap FFE + 2-tap DFE should recover SNR > 19 dB
  assert res.post_eq_snr_db > 19.0
  assert res.ber < 2.4e-4


def test_longer_pcb_trace_has_more_loss():
  cfg = config.LinkSimulationConfig()
  x = np.zeros(4096)
  x[0] = 1.0
  kwargs = dict(
      sample_rate_hz=cfg.sample_rate_hz,
      nyquist_ghz=cfg.nyquist_freq_ghz,
      dielectric_loss_db_per_inch_ghz=0.038,
      skin_loss_db_per_inch_sqrt_ghz=0.18,
      package_loss_db_at_nyquist=3.5,
  )
  _, short_db, _, _ = electrical_channel.apply_pcb_trace_channel(
      x, trace_length_mm=50.0, **kwargs
  )
  _, long_db, _, _ = electrical_channel.apply_pcb_trace_channel(
      x, trace_length_mm=200.0, **kwargs
  )
  assert long_db > short_db
