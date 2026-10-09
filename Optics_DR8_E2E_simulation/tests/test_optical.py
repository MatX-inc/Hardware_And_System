"""Tests for the laser, SiPh MZM, SMF fiber, and optical receiver models."""

import numpy as np
import pytest

from dr8sim import config
from dr8sim import electrical_channel
from dr8sim import metrology
from dr8sim import optical_channel


def _symbols(cfg):
  rng = np.random.default_rng(cfg.random_seed)
  tx_symbols, _, _ = electrical_channel.generate_pam4_symbols(
      cfg.num_symbols, rng
  )
  return tx_symbols, rng


def test_laser_mzm_and_6km_fiber_propagation():
  sim_cfg = config.LinkSimulationConfig(num_symbols=2048, random_seed=11)
  sim_cfg.laser.wavelength_error_nm = 4.5  # 1314.5 nm
  tx_symbols, rng = _symbols(sim_cfg)
  opt_res = optical_channel.simulate_optical_sublink(
      otx_input_symbols=tx_symbols,
      reference_tx_symbols=tx_symbols,
      sim_cfg=sim_cfg,
      rng=rng,
  )
  assert opt_res.tx_er_db == pytest.approx(5.0, abs=0.8)
  # 6 km fiber loss = 6 * 0.35 + 1.0 + 0.2 = 3.3 dB
  assert opt_res.total_fiber_loss_db == pytest.approx(3.3, abs=0.01)
  assert opt_res.tx_oma_outer_dbm - opt_res.rx_oma_outer_dbm == pytest.approx(
      3.3, abs=0.01
  )
  # Positive chromatic dispersion above the 1300 nm zero-dispersion point
  assert opt_res.chromatic_dispersion_ps_nm_km > 0.25
  assert opt_res.orx_snr_db > 18.0


def test_dispersion_sign_around_zero_dispersion_wavelength():
  fiber = config.FiberConfig(zero_dispersion_wavelength_nm=1310.0)
  assert optical_channel.compute_smf_dispersion_ps_nm_km(1300.0, fiber) < 0
  assert optical_channel.compute_smf_dispersion_ps_nm_km(
      1310.0, fiber
  ) == pytest.approx(0.0, abs=1e-9)
  assert optical_channel.compute_smf_dispersion_ps_nm_km(1320.0, fiber) > 0


def test_zero_length_fiber_has_no_loss():
  cfg = config.LinkSimulationConfig(num_symbols=1024)
  cfg.fiber.length_km = 0.0
  field = np.ones(1024 * cfg.samples_per_symbol, dtype=complex)
  _, _, _, total_cd, loss = optical_channel.propagate_smf_fiber(
      field, 1310.0, cfg.sample_rate_hz, cfg.fiber
  )
  assert total_cd == 0.0
  assert loss == 0.0


def test_drive_vpp_increases_with_target_er():
  lo = optical_channel.compute_drive_vpp_for_target_er(3.0, 4.0, 22.0)
  hi = optical_channel.compute_drive_vpp_for_target_er(6.0, 4.0, 22.0)
  assert hi > lo > 0


def test_tdecq_increases_with_large_dispersion():
  sim_cfg = config.LinkSimulationConfig(num_symbols=2048, random_seed=21)
  tx_symbols, rng = _symbols(sim_cfg)
  opt_0km = optical_channel.simulate_optical_sublink(
      otx_input_symbols=tx_symbols,
      reference_tx_symbols=tx_symbols,
      sim_cfg=sim_cfg,
      rng=rng,
  )
  tecq_res = metrology.calculate_tdecq(
      optical_power_mw=opt_0km.tx_optical_power_mw,
      tx_symbols=tx_symbols,
      sim_cfg=sim_cfg,
  )
  sim_cfg.fiber.override_dispersion_ps_nm_km = 3.5
  opt_high_cd = optical_channel.simulate_optical_sublink(
      otx_input_symbols=tx_symbols,
      reference_tx_symbols=tx_symbols,
      sim_cfg=sim_cfg,
      rng=np.random.default_rng(sim_cfg.random_seed),
  )
  tdecq_high_cd = metrology.calculate_tdecq(
      optical_power_mw=opt_high_cd.rx_optical_power_mw,
      tx_symbols=tx_symbols,
      sim_cfg=sim_cfg,
  )
  assert 0.3 < tecq_res.tdecq_db < 3.0
  assert tdecq_high_cd.tdecq_db > tecq_res.tdecq_db
