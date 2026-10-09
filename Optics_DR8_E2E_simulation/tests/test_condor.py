"""Tests for the Broadcom Condor host SerDes model."""

import dataclasses
import pathlib

import numpy as np
import pytest

from dr8sim import config
from dr8sim import electrical_channel
from dr8sim import host_serdes
from dr8sim import simulator

_CONFIGS = pathlib.Path(__file__).resolve().parents[1] / 'configs'


def _cfg(arch='retimed'):
  """Condor preset for `arch` (calibrated host PCB), shortened for speed."""
  cfg = config.LinkSimulationConfig()
  config.load_json(str(_CONFIGS / f'condor_{arch}_500m_4db.json'), cfg)
  cfg.num_symbols = 2048
  cfg.random_seed = 3
  return cfg


@pytest.mark.parametrize('codes, match', [
    ([0, 0, 0, 169, 0, 0], 'main'),
    ([0, -4, 0, 160, 0, 0], 'pre2'),     # pre2 is positive-only
    ([-8, 16, -40, 100, -10, 0], '168'),  # sum(|codes|) > 168
    ([0, 0, 168], '6 codes'),
])
def test_condor_tx_code_limits(codes, match):
  with pytest.raises(ValueError, match=match):
    config.apply_overrides(config.LinkSimulationConfig(),
                           [f'condor.tx_ffe_codes={codes}'])


def test_condor_swing_scales_with_code_sum():
  condor = config.CondorConfig(tx_ffe_codes=(0, 0, -24, 120, 0, 0),
                               tx_amp_vppd=0.9)
  tx = host_serdes.condor_tx_settings(condor)
  assert tx['tx_vppd'] == pytest.approx(0.9 * 144 / 168)


def test_condor_only_replaces_host_ends():
  cfg = _cfg()
  c2m = host_serdes.c2m_configs(cfg)
  m2c = host_serdes.m2c_configs(cfg)
  base = cfg.host_channel
  # Host TX is Condor; module client RX is unchanged.
  assert c2m.tx.tx_fir_taps == tuple(float(c) for c in cfg.condor.tx_ffe_codes)
  assert c2m.rx == base
  # Module client TX is unchanged; host RX is Condor.
  assert m2c.tx == base
  assert m2c.rx.rx_ffe_taps == cfg.condor.rx_ffe_taps
  # Phytile package loss added to the host channel.
  assert c2m.channel.package_connector_loss_db_at_nyquist == pytest.approx(
      base.package_connector_loss_db_at_nyquist
      + cfg.condor.package_loss_db_at_nyquist)


def test_generic_host_is_unchanged_by_condor_settings():
  a = config.LinkSimulationConfig(num_symbols=2048)
  b = dataclasses.replace(a, condor=config.CondorConfig(tx_amp_vppd=1.1))
  ra = simulator.summarize_result(simulator.run_end_to_end_simulation(a, False))
  rb = simulator.summarize_result(simulator.run_end_to_end_simulation(b, False))
  assert ra == rb


def test_ctle_adapts_and_fixed_codes_are_honored():
  cfg = _cfg()
  res = simulator.run_end_to_end_simulation(cfg, False)
  codes = res.client_to_host_m2c.ctle_codes
  assert len(codes) == 3
  assert all(0 <= c <= m for c, m in zip(codes, cfg.condor.rx_ctle_max_codes))
  cfg.condor.rx_ctle_codes = (5, 5, 5)
  fixed = simulator.run_end_to_end_simulation(cfg, False)
  assert fixed.client_to_host_m2c.ctle_codes == (5.0, 5.0, 5.0)


def test_multistage_ctle_boost_matches_codes():
  f = np.array([0.0, 1e12])
  h = electrical_channel.multistage_ctle_response(
      f, (23,), (23,), (30.0,), (10.0,))
  assert abs(h[0]) == pytest.approx(1.0)
  assert 20 * np.log10(abs(h[1])) == pytest.approx(10.0, abs=0.1)


@pytest.mark.parametrize('arch', ['retimed', 'lro'])
def test_condor_links_close(arch):
  res = simulator.run_end_to_end_simulation(_cfg(arch), False)
  assert res.end_to_end_ber < 2.4e-4
  report = simulator.format_simulation_report(res)
  assert 'Broadcom Condor' in report and 'Condor Host RX' in report


def test_condor_lro_is_worse_than_condor_retimed():
  r = simulator.run_end_to_end_simulation(_cfg('retimed'), False)
  l = simulator.run_end_to_end_simulation(_cfg('lro'), False)
  assert l.end_to_end_ber > r.end_to_end_ber


def test_lro_driver_peaking_changes_the_host_signal():
  from dr8sim import return_path
  cfg = _cfg('lro')
  rng = np.random.default_rng(0)
  x = np.repeat(rng.choice([-1.0, 1.0], 2048), cfg.samples_per_symbol)
  outs = []
  for peak in (1.5, 6.0):
    cfg.lro.driver_peaking_db = peak
    cfg.lro.driver_noise_mv_rms = 0.0
    outs.append(return_path.lro_linear_driver(x, cfg, np.random.default_rng(1)))
  assert not np.allclose(outs[0], outs[1], atol=1e-3)
