"""Regression tests for bugs found in the Oct 2026 code audit."""

import dataclasses
import json
import math

import numpy as np
import pytest

from dr8sim import cli
from dr8sim import config
from dr8sim import electrical_channel as ec
from dr8sim import metrology
from dr8sim import module
from dr8sim import optical_channel as oc
from dr8sim import simulator


# ---- Electrical ----

def test_ffe_ref_tap_counts_precursor_taps():
  """ref_tap = number of pre-cursor taps; a pre-cursor channel needs them."""
  rng = np.random.default_rng(0)
  sym, _, _ = ec.generate_pam4_symbols(4096, rng)
  # Pre-cursor ISI: sample k also sees the next three (future) symbols.
  rx = (sym + 0.3 * np.roll(sym, -1) + 0.15 * np.roll(sym, -2)
        + 0.08 * np.roll(sym, -3))
  rx = rx + rng.normal(0, 0.01, len(sym))
  snr_pre = ec.equalize_ffe_dfe(rx, sym, num_ffe_taps=6, num_dfe_taps=0,
                                ref_tap=5)[3]
  snr_post = ec.equalize_ffe_dfe(rx, sym, num_ffe_taps=6, num_dfe_taps=0,
                                 ref_tap=0)[3]
  assert snr_pre > snr_post + 10
  # And the scoring function used for CTLE adaptation agrees.
  assert ec._mmse_snr_db(rx, sym, 6, 5, 0) > ec._mmse_snr_db(rx, sym, 6, 0, 0) + 10  # pylint: disable=protected-access


def test_equalizer_rejects_too_short_input():
  with pytest.raises(ValueError, match='at least'):
    ec.equalize_ffe_dfe(np.zeros(100), np.zeros(100), 15, 2)


def test_unbiased_equalizer_ser_matches_theory():
  rng = np.random.default_rng(1)
  sym, _, _ = ec.generate_pam4_symbols(200_000, rng)
  sigma = 0.12
  out = ec.equalize_ffe_dfe(sym + rng.normal(0, sigma, len(sym)), sym, 1, 0, 0)
  theory = 0.75 * math.erfc((1 / 3) / (math.sqrt(2) * sigma))
  assert out[4] == pytest.approx(theory, rel=0.08)


def test_tx_jitter_rms_matches_setting():
  fs = 106.25e9 * 16
  sym = np.tile([-1.0, 1.0], 2048)
  clean = ec.transmit_electrical_pam4(sym, 16, fs, 0.8, (1.0,), 12, 0.0, 65,
                                      np.random.default_rng(2))
  jit = ec.transmit_electrical_pam4(sym, 16, fs, 0.8, (1.0,), 12, 0.5, 65,
                                    np.random.default_rng(2))

  def crossings(w):
    i = np.nonzero(np.signbit(w[:-1]) != np.signbit(w[1:]))[0]
    return i + w[i] / (w[i] - w[i + 1])

  c0, c1 = crossings(clean), crossings(jit)
  assert len(c0) == len(c1)  # no spurious extra crossings
  rms_ps = np.std(c1 - c0) / fs * 1e12
  assert rms_ps == pytest.approx(0.5, rel=0.08)


# ---- Optical / metrology ----

@pytest.mark.parametrize('alpha', [-0.5, 0.0, 0.5])
def test_chirp_and_dispersion_follow_standard_response(alpha):
  """Small-signal |H| = |cos(theta) - alpha*sin(theta)| (alpha<0 helps, D>0)."""
  cfg = config.LinkSimulationConfig()
  fs = cfg.sample_rate_hz
  n = 1 << 14
  t = np.arange(n) / fs
  f_tone = fs * 300 / n
  m = 0.02
  intensity = 1 + m * np.cos(2 * np.pi * f_tone * t)
  field = np.sqrt(intensity) * np.exp(1j * 0.5 * alpha * np.log(intensity))
  fiber = config.FiberConfig(length_km=6.0, override_dispersion_ps_nm_km=4.0,
                             pmd_ps_per_sqrt_km=0.0)
  _, p, _, _, _ = oc.propagate_smf_fiber(field, 1310.0, fs, fiber)
  amp = 2 * np.abs(np.fft.rfft(p / np.mean(p)))[300] / n / m
  theta = math.pi * (1310e-9) ** 2 * (4.0 * 6.0 * 1e-3) * f_tone ** 2 / 299792458.0
  assert amp == pytest.approx(abs(math.cos(theta) - alpha * math.sin(theta)),
                              rel=0.02)


@pytest.mark.parametrize('er_int', [15.0, 22.0, 30.0])
def test_intrinsic_extinction_ratio_is_exact(er_int):
  mzm = config.MzmConfig(intrinsic_er_db=er_int, chirp_alpha=0.0,
                         insertion_loss_db=0.0, bias_error_deg=0.0)
  p = np.abs(oc._mzm_field_transfer(np.linspace(-4, 4, 4001), mzm)) ** 2  # pylint: disable=protected-access
  assert 10 * np.log10(p.max() / p.min()) == pytest.approx(er_int, abs=0.01)


@pytest.mark.parametrize('taps', [(0.0, 1.0, 0.0), (-0.07, 0.84, -0.09),
                                  (-0.15, 1.0, -0.15)])
def test_tx_er_and_oma_are_long_run_and_hit_target(taps):
  cfg = config.LinkSimulationConfig(num_symbols=2048)
  cfg.mzm = dataclasses.replace(cfg.mzm, line_tx_fir_taps=taps,
                                bias_error_deg=0.0)
  r = simulator.run_end_to_end_simulation(cfg, False)
  assert r.optical_line.tx_er_db == pytest.approx(5.0, abs=0.05)


def test_tdecq_is_invariant_to_time_shift():
  cfg = config.LinkSimulationConfig(num_symbols=2048)
  rng = np.random.default_rng(3)
  sym, _, _ = ec.generate_pam4_symbols(2048, rng)
  p = np.repeat(1.0 + 0.5 * sym, 16)
  vals = [metrology.calculate_tdecq(np.roll(p, k), sym, cfg).tdecq_db
          for k in range(16)]
  assert all(math.isfinite(v) for v in vals)
  assert max(vals) - min(vals) < 0.05


def test_interpolation_brackets_into_zero_error_points():
  p = metrology._interpolate_power_at_target_ber(  # pylint: disable=protected-access
      np.array([-12.0, -11.0, -10.0, -9.0, -8.0]),
      np.array([1e-2, 2e-3, 5e-4, 0.0, 0.0]), 2.4e-4)
  assert -10.0 < p < -9.0


@pytest.mark.slow
def test_link_budget_items_add_up_to_net_margin():
  cfg = config.LinkSimulationConfig(num_symbols=2048)
  cfg.fiber.length_km = 0.5
  b = simulator.run_end_to_end_simulation(cfg, True).link_budget
  parts = (b.total_power_budget_oma_db - b.total_channel_insertion_loss_db
           - b.tdecq_allocation_db - b.host_m2c_concatenation_penalty_db
           - b.mpi_and_dgd_penalty_db)
  assert parts == pytest.approx(b.net_link_margin_db, abs=1e-9)


# ---- Orchestration ----

def test_c2m_errors_are_not_double_counted():
  """When C2M errors dominate, the reported BER tracks the counted BER."""
  cfg = config.LinkSimulationConfig(num_symbols=8192)
  cfg.fiber.length_km = 0.5
  cfg.host_channel.pcb_trace_length_mm = 240.0
  r = simulator.run_end_to_end_simulation(cfg, False)
  c2m = r.host_to_client_c2m.ber
  assert c2m > 1e-4  # this setup is C2M-limited
  assert r.end_to_end_ber < 1.5 * c2m + r.optical_line.orx_ber + r.client_to_host_m2c.ber
  assert r.optical_line.orx_ber < 0.1 * c2m  # ORX no longer re-counts C2M errors


def test_spec_check_handles_closed_eye():
  metrics = {'tdecq_db': None, 'tx_er_db': 5.0, 'tx_rlm': 0.95,
             'end_to_end_ber': 1e-9}
  failures = module.check_spec(metrics, config.SpecLimits())
  assert any('TDECQ' in f for f in failures)


def test_zero_padded_lane_key_is_applied():
  cfg = config.ModuleConfig()
  cfg.lane_overrides = {'03': {'mzm.insertion_loss_db': 12.0}}
  lanes = module.build_lane_configs(cfg)
  assert lanes[3][0].mzm.insertion_loss_db == 12.0
  cfg.lane_overrides = {'3': {}, '03': {}}
  with pytest.raises(ValueError, match='more than one'):
    module.build_lane_configs(cfg)


def test_er_spread_skipped_in_drive_voltage_mode():
  cfg = config.ModuleConfig(monte_carlo=True)
  cfg.lane.mzm.target_outer_er_db = 0.0
  for lane_cfg, _ in module.build_lane_configs(cfg):
    assert lane_cfg.mzm.target_outer_er_db == 0.0


def test_sweeping_num_symbols_is_not_capped():
  rows = simulator.sweep_parameter(config.LinkSimulationConfig(),
                                   'num_symbols', [4096, 16384])
  assert rows[0]['orx_snr_db'] != rows[1]['orx_snr_db']


@pytest.mark.parametrize('override', [
    'laser.rin_db_hz=null', 'host_channel.tx_fir_taps=0.1',
    'receiver.orx_ffe_taps=21.9'])
def test_bad_overrides_are_rejected(override):
  with pytest.raises(ValueError):
    config.apply_overrides(config.LinkSimulationConfig(), [override])


def test_plot_flags_require_output_dir():
  with pytest.raises(SystemExit) as e:
    cli.main(['run', '--num-symbols', '1024', '--no-budget',
              '--no-reach-sweep', '--c2m-plot'])
  assert e.value.code == 2


def test_closed_eye_run_writes_strict_json(tmp_path):
  rc = cli.main(['run', '--num-symbols', '2048', '--no-budget',
                 '--wavelength-error-nm', '12', '--reach-km', '6', '10',
                 '-o', str(tmp_path)])
  assert rc in (0, 1)
  json.loads((tmp_path / 'simulation_summary.json').read_text(),
             parse_constant=lambda c: pytest.fail(f'non-strict JSON {c}'))
