"""IEEE 802.3 Metrology: TDECQ, TECQ, R_LM, Receiver Sensitivity, & Link Budget.

Implements:
  - PAM4 Eye Linearity (R_LM) and sub-eye height/width measurements
  - IEEE 802.3 Clause 121/171 Reference Receiver TDECQ and TECQ calculation:
      * 4th-order Bessel-Thomson reference optical receiver filter (0.5 * Baud)
      * 5-tap T-spaced MMSE reference Feed-Forward Equalizer (FFE)
      * Noise enhancement factor C_eq = sqrt(sum(c_k^2))
      * Dual vertical histogram slices at exact +/- 0.05 UI around symbol center
      * Exact Gaussian tail SER solver at target SER = 4.8e-4
  - Receiver Sensitivity (OMA_outer and P_avg in dBm) waterfall sweep and
    interpolation at Pre-FEC BER = 2.4e-4 (KP4 FEC) and 1.0e-3
  - Complete 1.6T-DR8 SMF-28 Link Power Budget and Margin Breakdown
"""

from __future__ import annotations

import copy
import dataclasses
from typing import List

import numpy as np
from scipy import optimize
from scipy import special

from dr8sim import config
from dr8sim import electrical_channel
from dr8sim import host_serdes
from dr8sim import optical_channel
from dr8sim import return_path


@dataclasses.dataclass
class TdecqResult:
  """IEEE 802.3 Reference Receiver TDECQ / TECQ measurement results.

  Attributes:
    tdecq_db: Transmitter and Dispersion Eye Closure Quaternary (dB). inf
      when the eye misses the target SER even with no added noise.
    reference_ffe_taps: 5-tap T-spaced reference equalizer weights (sum = 1).
    noise_enhancement_factor: Equalizer noise enhancement C_eq = sqrt(sum c_k^2).
    ideal_noise_sigma_mw: Ideal allowable RMS noise sigma_ideal (mW).
    tolerable_noise_left_mw: Allowable RMS noise at -0.05 UI slice (mW).
    tolerable_noise_right_mw: Allowable RMS noise at +0.05 UI slice (mW).
    effective_sigma_mw: Limiting noise sigma_G / C_eq (mW).
    oma_outer_mw: Measured outer OMA (P3 - P0) in mW.
    avg_power_mw: Measured average optical power in mW.
    er_db: Measured Extinction Ratio 10*log10(P3 / P0) in dB.
    rlm: PAM4 Eye Linearity metric R_LM (dimensionless, 1.0 is ideal).
    sub_eye_openings_mw: Tuple of (lower, middle, upper) sub-eye openings (mW).
    thresholds_mw: 3 PAM4 reference slicing thresholds [P_th1, P_th2, P_th3]
      (mW).
    ref_equalized_waveform_mw: Oversampled waveform after reference 5-tap FFE
      for eye diagram plotting (mW).
  """

  tdecq_db: float
  reference_ffe_taps: np.ndarray
  noise_enhancement_factor: float
  ideal_noise_sigma_mw: float
  tolerable_noise_left_mw: float
  tolerable_noise_right_mw: float
  effective_sigma_mw: float
  oma_outer_mw: float
  avg_power_mw: float
  er_db: float
  rlm: float
  sub_eye_openings_mw: tuple[float, float, float]
  thresholds_mw: np.ndarray
  ref_equalized_waveform_mw: np.ndarray
  optimal_sample_phase: int = 8


@dataclasses.dataclass
class SensitivitySweepPoint:
  """Single operating point in an optical receiver sensitivity waterfall sweep.

  Attributes:
    voa_attenuation_db: Additional VOA attenuation applied to fiber path (dB).
    rx_avg_power_dbm: Average received optical power at PIN PD (dBm).
    rx_oma_outer_dbm: Received outer OMA at PIN PD (dBm).
    orx_snr_db: Media RX (ORX) post-FFE/DFE SNR (dB).
    orx_ber: Media RX (ORX) Pre-FEC Bit Error Ratio.
    host_rx_ber: End-to-end Host RX Bit Error Ratio after M2C PCB trace.
  """

  voa_attenuation_db: float
  rx_avg_power_dbm: float
  rx_oma_outer_dbm: float
  orx_snr_db: float
  orx_ber: float
  host_rx_ber: float


@dataclasses.dataclass
class SensitivitySweepResult:
  """Complete Receiver Sensitivity waterfall curve and interpolated thresholds.

  Attributes:
    points: List of evaluated SensitivitySweepPoint points.
    sensitivity_oma_dbm_at_kp4: Interpolated RX outer OMA (dBm) at BER = 2.4e-4.
    sensitivity_pavg_dbm_at_kp4: Interpolated RX average power (dBm) at
      BER = 2.4e-4.
    sensitivity_oma_dbm_at_1e3: Interpolated RX outer OMA (dBm) at BER = 1.0e-3.
    sensitivity_pavg_dbm_at_1e3: Interpolated RX average power (dBm) at
      BER = 1.0e-3.
    e2e_sensitivity_oma_dbm_at_kp4: End-to-end (Host RX) outer OMA sensitivity
      at BER = 2.4e-4 (dBm).
  """

  points: List[SensitivitySweepPoint]
  sensitivity_oma_dbm_at_kp4: float
  sensitivity_pavg_dbm_at_kp4: float
  sensitivity_oma_dbm_at_1e3: float
  sensitivity_pavg_dbm_at_1e3: float
  e2e_sensitivity_oma_dbm_at_kp4: float


@dataclasses.dataclass
class LinkBudgetReport:
  """Complete 1.6T-DR8 (8x200G) Optical Link Power Budget & Margin Report.

  Attributes:
    fiber_length_km: Fiber distance (km).
    operating_wavelength_nm: Actual operating wavelength (nm).
    chromatic_dispersion_ps_nm: Cumulative dispersion D(lambda)*L (ps/nm).
    tx_avg_power_dbm: Launched TX average optical power (dBm).
    tx_oma_outer_dbm: Launched TX outer OMA (dBm).
    tx_er_db: Launched TX outer Extinction Ratio (dB).
    tx_rlm: Launched TX PAM4 eye linearity R_LM.
    tecq_db: Back-to-back (0 km) Transmitter Eye Closure Quaternary (dB).
    tdecq_db: TDECQ at the simulated fiber length (dB).
    dispersion_penalty_tdecq_db: Chromatic dispersion eye closure penalty
      TDECQ - TECQ (dB).
    fiber_attenuation_db: Pure fiber propagation loss alpha * L (dB).
    connector_loss_db: MPO connector insertion loss (dB).
    splice_and_aging_loss_db: Splice and cable margin loss (dB).
    total_channel_insertion_loss_db: Total passive optical channel loss (dB).
    rx_avg_power_dbm: Received average optical power at the simulated fiber
      length (dBm).
    rx_oma_outer_dbm: Received outer OMA at the simulated fiber length (dBm).
    rx_sensitivity_oma_btb_dbm: Back-to-back (0 km) RX OMA sensitivity at
      BER = 2.4e-4 (dBm).
    rx_sensitivity_oma_link_dbm: Link-length RX OMA sensitivity at
      BER = 2.4e-4 (dBm).
    rx_sensitivity_pavg_link_dbm: Link-length RX average power sensitivity at
      BER = 2.4e-4 (dBm).
    total_power_budget_oma_db: Total available OMA link budget
      TX_OMA - RX_Sens_BTB (dB).
    tdecq_allocation_db: Measured dispersion / eye-closure penalty: link-length
      ORX OMA sensitivity minus back-to-back sensitivity (dB, may be < 0).
    mpi_and_dgd_penalty_db: Multi-path interference allocation (dB). PMD/DGD
      is simulated directly, so it is not allocated separately.
    host_m2c_concatenation_penalty_db: OMA sensitivity penalty of the full
      receive direction to the host RX versus the module DSP receiver alone
      (dB). For LRO this is the linear-receive penalty.
    net_link_margin_db: Unallocated positive link margin at the simulated
      fiber length (dB).
  """

  fiber_length_km: float
  operating_wavelength_nm: float
  chromatic_dispersion_ps_nm: float
  tx_avg_power_dbm: float
  tx_oma_outer_dbm: float
  tx_er_db: float
  tx_rlm: float
  tecq_db: float
  tdecq_db: float
  dispersion_penalty_tdecq_db: float
  fiber_attenuation_db: float
  connector_loss_db: float
  splice_and_aging_loss_db: float
  total_channel_insertion_loss_db: float
  rx_avg_power_dbm: float
  rx_oma_outer_dbm: float
  rx_sensitivity_oma_btb_dbm: float
  rx_sensitivity_oma_link_dbm: float
  rx_sensitivity_pavg_link_dbm: float
  total_power_budget_oma_db: float
  tdecq_allocation_db: float
  mpi_and_dgd_penalty_db: float
  host_m2c_concatenation_penalty_db: float
  net_link_margin_db: float


def compute_pam4_rlm(level_powers: np.ndarray) -> float:
  """Calculates IEEE 802.3 Clause 121/171 PAM4 Eye Linearity (R_LM)."""
  p0, p1, p2, p3 = [float(x) for x in level_powers]
  v_avg = 0.5 * (p0 + p3)
  denom0 = p0 - v_avg
  denom3 = p3 - v_avg
  if abs(denom0) < 1e-15 or abs(denom3) < 1e-15:
    return 0.0
  es1 = (p1 - v_avg) / denom0
  es2 = (p2 - v_avg) / denom3
  rlm = min(3.0 * es1, 3.0 * es2, 2.0 - 3.0 * es1, 2.0 - 3.0 * es2)
  return float(np.clip(rlm, 0.0, 1.0))


def _qfunc(x: np.ndarray | float) -> np.ndarray | float:
  """Standard Gaussian tail probability Q(x) = 0.5 * erfc(x / sqrt(2))."""
  return 0.5 * special.erfc(np.asarray(x) / np.sqrt(2.0))


def _qfunc_inv(p: float) -> float:
  """Inverse of the standard Gaussian Q-function Q^-1(p)."""
  return float(np.sqrt(2.0) * special.erfinv(1.0 - 2.0 * p))


def _pam4_slice_ser_given_sigma(
    samples_mw: np.ndarray,
    tx_symbols: np.ndarray,
    thresholds_mw: np.ndarray,
    sigma_mw: float,
) -> float:
  """Computes the expected PAM4 Symbol Error Ratio for a vertical UI slice.

  Evaluates the directional threshold-crossing probability for each sample
  given its transmitted PAM4 level in {-1, -1/3, +1/3, +1}.

  Args:
    samples_mw: 1D array of equalized optical power samples at a fixed UI phase
      (mW).
    tx_symbols: Ground-truth transmitted PAM4 symbols for those samples.
    thresholds_mw: 3-element array [P_th1, P_th2, P_th3] (mW).
    sigma_mw: Candidate additive Gaussian noise standard deviation (mW).

  Returns:
    Expected Symbol Error Ratio (SER) in (0, 1).
  """
  if sigma_mw <= 1e-15:
    return 0.0
  pth1, pth2, pth3 = thresholds_mw
  m0 = tx_symbols < -0.66
  m1 = (tx_symbols >= -0.66) & (tx_symbols < 0.0)
  m2 = (tx_symbols >= 0.0) & (tx_symbols < 0.66)
  m3 = tx_symbols >= 0.66

  ser_sum = 0.0
  if np.any(m0):
    ser_sum += float(np.sum(_qfunc((pth1 - samples_mw[m0]) / sigma_mw)))
  if np.any(m1):
    ser_sum += float(
        np.sum(
            _qfunc((samples_mw[m1] - pth1) / sigma_mw)
            + _qfunc((pth2 - samples_mw[m1]) / sigma_mw)
        )
    )
  if np.any(m2):
    ser_sum += float(
        np.sum(
            _qfunc((samples_mw[m2] - pth2) / sigma_mw)
            + _qfunc((pth3 - samples_mw[m2]) / sigma_mw)
        )
    )
  if np.any(m3):
    ser_sum += float(np.sum(_qfunc((samples_mw[m3] - pth3) / sigma_mw)))

  return ser_sum / max(len(samples_mw), 1)


def _sample_at_fractional_phase(
    waveform: np.ndarray, sps: int, fractional_phase: float, n_sym: int
) -> np.ndarray:
  """Symbol-spaced samples at time k*sps + fractional_phase (linear interp).

  `fractional_phase` may be negative or >= sps; samples then come from the
  previous / next symbol period, so symbol k always means the same UI.
  """
  base = int(np.floor(fractional_phase))
  frac = fractional_phase - base

  def _at(i: int) -> np.ndarray:
    q, r = divmod(i, sps)
    return np.roll(waveform[r::sps][:n_sym], -q)

  return (1.0 - frac) * _at(base) + frac * _at(base + 1)


def _eye_center_phase(
    waveform: np.ndarray, sps: int, coarse_phase: int
) -> float:
  """Fractional eye-center time (samples) from the mid-level crossings.

  Takes the circular mean of the mid-level crossing times modulo one UI and
  places the center half a UI away, choosing the representation closest to
  `coarse_phase` so the symbol alignment found by correlation is kept.
  """
  x = waveform - np.mean(waveform)
  idx = np.nonzero(np.signbit(x[:-1]) != np.signbit(x[1:]))[0]
  if len(idx) < 16:
    return float(coarse_phase)
  t = idx + x[idx] / (x[idx] - x[idx + 1])  # linear-interpolated crossings
  ang = 2.0 * np.pi * (t % sps) / sps
  cross = (np.angle(np.mean(np.exp(1j * ang))) / (2.0 * np.pi) * sps) % sps
  center = (cross + 0.5 * sps) % sps
  candidates = center + sps * np.array([-1.0, 0.0, 1.0])
  return float(candidates[np.argmin(np.abs(candidates - coarse_phase))])


def calculate_tdecq(
    optical_power_mw: np.ndarray,
    tx_symbols: np.ndarray,
    sim_cfg: config.LinkSimulationConfig,
    num_ref_taps: int = 5,
    target_ser: float = 4.8e-4,
) -> TdecqResult:
  """Calculates IEEE 802.3 Clause 121/171 TDECQ (or TECQ at 0 km).

  Args:
    optical_power_mw: Optical power waveform (mW) of length `num_symbols * sps`.
    tx_symbols: Transmitted normalized PAM4 symbols in {-1, -1/3, +1/3, +1}.
    sim_cfg: Simulation configuration.
    num_ref_taps: Number of T-spaced taps in reference FFE (5 per IEEE 802.3).
    target_ser: Target Symbol Error Ratio (4.8e-4 per IEEE 802.3).

  Returns:
    TdecqResult with TDECQ (dB), reference taps, C_eq, R_LM, and eye metrics.
  """
  sps = sim_cfg.samples_per_symbol
  fs = sim_cfg.sample_rate_hz
  n_sym = len(tx_symbols)

  # 1. Reference 4th-order Bessel-Thomson optical receiver filter (0.5 * Baud)
  ref_bw_ghz = 0.5 * sim_cfg.baud_rate_gbaud
  filtered_mw = electrical_channel.apply_bessel_lowpass(
      optical_power_mw, cutoff_ghz=ref_bw_ghz, sample_rate_hz=fs, order=4
  )

  # 2. Optimal sampling phase and integer lag alignment
  best_phase, best_lag, _ = electrical_channel.find_optimal_sampling_phase(
      waveform=filtered_mw, tx_symbols=tx_symbols, sps=sps
  )
  aligned_mw = np.roll(filtered_mw, -best_lag * sps)
  avg_power_mw = float(np.mean(aligned_mw))
  # Eye center at fractional-sample resolution (mid-level crossings + 0.5 UI),
  # so TDECQ does not depend on the sample grid.
  center = _eye_center_phase(aligned_mw, sps, best_phase)

  # 3. Solve for 5-tap T-spaced MMSE reference equalizer on AC-coupled signal
  ref_tap = num_ref_taps // 2
  center_raw_mw = _sample_at_fractional_phase(aligned_mw, sps, center, n_sym)
  ac_center = center_raw_mw - avg_power_mw
  padded_ac = np.pad(
      ac_center, (ref_tap, num_ref_taps - 1 - ref_tap), mode='edge'
  )

  toeplitz_mat = np.zeros((n_sym, num_ref_taps), dtype=np.float64)
  for k in range(n_sym):
    toeplitz_mat[k, :] = padded_ac[
        k + num_ref_taps - 1 : k - 1 if k > 0 else None : -1
    ][:num_ref_taps]

  # Regularization balances ISI suppression and noise enhancement C_eq
  reg = 2e-3 * np.trace(toeplitz_mat.T @ toeplitz_mat) / num_ref_taps
  taps = np.linalg.solve(
      toeplitz_mat.T @ toeplitz_mat + reg * np.eye(num_ref_taps),
      toeplitz_mat.T @ tx_symbols,
  )
  # Normalize reference FFE taps to unit DC sum per IEEE 802.3 Clause 121.8.5
  tap_sum = float(np.sum(taps))
  if abs(tap_sum) > 1e-9:
    taps = taps / tap_sum

  # Noise enhancement factor C_eq = sqrt(sum(c_k^2))
  c_eq = float(np.sqrt(np.sum(taps**2)))

  # Apply T-spaced reference FFE across the entire oversampled waveform
  upsampled_taps = np.zeros((num_ref_taps - 1) * sps + 1, dtype=np.float64)
  upsampled_taps[::sps] = taps
  pad_left = ref_tap * sps
  pad_right = (num_ref_taps - 1 - ref_tap) * sps
  padded_wave = np.pad(
      aligned_mw - avg_power_mw, (pad_left, pad_right), mode='wrap'
  )
  eq_wave_mw = (
      np.convolve(padded_wave, upsampled_taps, mode='valid')[: len(aligned_mw)]
      + avg_power_mw
  )

  # 4. Measure mean PAM4 optical power levels [P0, P1, P2, P3] on the unit-DC
  # equalized waveform (equivalent to long runs of identical symbols per IEEE)
  trim = max(32, num_ref_taps * 4)
  center_eq_mw = _sample_at_fractional_phase(
      eq_wave_mw, sps, center, n_sym)[trim : n_sym - trim]
  tx_trimmed = tx_symbols[trim : n_sym - trim]

  level_powers_mw = np.zeros(4, dtype=np.float64)
  eq_levels = []
  for idx, lvl in enumerate(electrical_channel.PAM4_LEVELS):
    mask = np.isclose(tx_trimmed, lvl, atol=0.05)
    lvl_samples = center_eq_mw[mask] if np.any(mask) else center_eq_mw
    level_powers_mw[idx] = float(np.mean(lvl_samples))
    eq_levels.append(lvl_samples)

  p0_mw = max(float(level_powers_mw[0]), 1e-9)
  p3_mw = max(float(level_powers_mw[3]), p0_mw + 1e-6)
  oma_outer_mw = p3_mw - p0_mw
  er_db = float(10.0 * np.log10(p3_mw / p0_mw))
  rlm = compute_pam4_rlm(level_powers_mw)

  p_mid_mw = 0.5 * (p0_mw + p3_mw)
  thresholds_mw = np.array(
      [
          p_mid_mw - oma_outer_mw / 3.0,
          p_mid_mw,
          p_mid_mw + oma_outer_mw / 3.0,
      ],
      dtype=np.float64,
  )

  sub_eyes = (
      float(np.percentile(eq_levels[1], 15) - np.percentile(eq_levels[0], 85)),
      float(np.percentile(eq_levels[2], 15) - np.percentile(eq_levels[1], 85)),
      float(np.percentile(eq_levels[3], 15) - np.percentile(eq_levels[2], 85)),
  )

  # 5. Extract exact +/- 0.05 UI vertical slices around optimal sampling phase
  delta_samples = 0.05 * sps
  left_slice_mw = _sample_at_fractional_phase(
      eq_wave_mw, sps, center - delta_samples, n_sym
  )[trim : n_sym - trim]
  right_slice_mw = _sample_at_fractional_phase(
      eq_wave_mw, sps, center + delta_samples, n_sym
  )[trim : n_sym - trim]

  # 6. Ideal reference noise sigma_ideal for an un-impaired PAM4 signal
  q_inv = _qfunc_inv(target_ser / 1.5)
  sigma_ideal_mw = oma_outer_mw / (6.0 * q_inv)

  def _solve_slice_sigma(slice_samples: np.ndarray) -> float:
    """Solves for additive noise sigma_G yielding SER = target_ser."""
    low = 1e-6 * sigma_ideal_mw
    high = 2.0 * sigma_ideal_mw
    f_low = _pam4_slice_ser_given_sigma(
        slice_samples, tx_trimmed, thresholds_mw, low
    )
    if f_low >= target_ser:
      # Eye already misses the target SER with no added noise: closed.
      return 0.0
    f_high = _pam4_slice_ser_given_sigma(
        slice_samples, tx_trimmed, thresholds_mw, high
    )
    if f_high <= target_ser:
      return high
    return float(
        optimize.brentq(
            lambda s: _pam4_slice_ser_given_sigma(
                slice_samples, tx_trimmed, thresholds_mw, s
            )
            - target_ser,
            low,
            high,
            xtol=1e-6 * sigma_ideal_mw,
        )
    )

  sigma_left_mw = _solve_slice_sigma(left_slice_mw)
  sigma_right_mw = _solve_slice_sigma(right_slice_mw)
  sigma_g_mw = min(sigma_left_mw, sigma_right_mw)
  effective_sigma_mw = sigma_g_mw / max(c_eq, 1e-9)

  if effective_sigma_mw <= 0.0:
    tdecq_db = float('inf')  # Closed eye: no noise budget left at all.
  else:
    tdecq_db = float(
        10.0 * np.log10(max(sigma_ideal_mw / effective_sigma_mw, 1.0))
    )

  return TdecqResult(
      tdecq_db=tdecq_db,
      reference_ffe_taps=taps,
      noise_enhancement_factor=c_eq,
      ideal_noise_sigma_mw=sigma_ideal_mw,
      tolerable_noise_left_mw=sigma_left_mw,
      tolerable_noise_right_mw=sigma_right_mw,
      effective_sigma_mw=effective_sigma_mw,
      oma_outer_mw=oma_outer_mw,
      avg_power_mw=avg_power_mw,
      er_db=er_db,
      rlm=rlm,
      sub_eye_openings_mw=sub_eyes,
      thresholds_mw=thresholds_mw,
      ref_equalized_waveform_mw=eq_wave_mw,
      optimal_sample_phase=int(round(center)) % sps,
  )


def _interpolate_power_at_target_ber(
    powers_dbm: np.ndarray,
    bers: np.ndarray,
    target_ber: float,
) -> float:
  """Interpolates optical power (dBm) at `target_ber` on a log10(BER) waterfall.

  Returns NaN when the swept BERs do not straddle `target_ber`, rather than
  extrapolating to the edge of the sweep.
  """
  # Zero-error points are valid "better than" evidence: floor them instead of
  # dropping them, so a waterfall that crosses into zero errors still brackets.
  bers = np.maximum(np.asarray(bers, dtype=float), 1e-30)
  valid = bers < 0.25
  if np.sum(valid) < 2:
    return float('nan')
  if not np.min(bers[valid]) <= target_ber <= np.max(bers[valid]):
    return float('nan')
  p_val = powers_dbm[valid]
  log_b = np.log10(bers[valid])
  target_log_b = np.log10(target_ber)
  order = np.argsort(p_val)
  p_sorted = p_val[order]
  log_b_sorted = log_b[order]
  log_b_mono = np.minimum.accumulate(log_b_sorted)
  return float(np.interp(-target_log_b, -log_b_mono, p_sorted))


def sweep_receiver_sensitivity(
    sim_cfg: config.LinkSimulationConfig,
    voa_attenuations_db: np.ndarray | None = None,
    include_host_m2c: bool = True,
    max_extensions: int = 4,
) -> SensitivitySweepResult:
  """Sweeps VOA attenuation to measure Receiver Sensitivity (OMA and P_avg).

  Args:
    sim_cfg: Link configuration; the fiber length sets the dispersion.
    voa_attenuations_db: Initial VOA attenuations (dB). Defaults to 2-13 dB.
    include_host_m2c: Also run the M2C host segment for end-to-end BER.
    max_extensions: Times the sweep may add higher attenuations when no point
      is worse than the pre-FEC BER target, or lower (possibly negative)
      attenuations when no point is better than it.

  Returns:
    SensitivitySweepResult. Sensitivities are NaN if the waterfall never
    crosses the corresponding BER.
  """
  if voa_attenuations_db is None:
    voa_attenuations_db = np.linspace(2.0, 13.0, 12)

  rng = np.random.default_rng(sim_cfg.random_seed)
  tx_symbols, _, tx_bits = electrical_channel.generate_pam4_symbols(
      sim_cfg.num_symbols, rng
  )

  # Run Stage 1 (Host TX -> C2M PCB -> Client RX) once
  c2m = host_serdes.c2m_configs(sim_cfg)
  c2m_res = electrical_channel.simulate_electrical_segment(
      tx_symbols=tx_symbols,
      tx_bits=tx_bits,
      sim_cfg=sim_cfg,
      host_cfg=c2m.tx,
      rng=rng,
      rx_cfg=c2m.rx,
      channel_cfg=c2m.channel,
  )
  retimed_tx = sim_cfg.host_channel.retimed_forwarding
  otx_in = (
      c2m_res.sliced_symbols
      if retimed_tx
      else np.clip(c2m_res.equalized_symbols, -1.1, 1.1)
  )
  # Symbols actually launched on the line: each hop counts its own errors.
  line_symbols = c2m_res.sliced_symbols if retimed_tx else tx_symbols
  line_bits = (electrical_channel.slice_pam4_symbols(line_symbols)[2]
               if retimed_tx else tx_bits)

  trim_bits = 128
  points: List[SensitivitySweepPoint] = []

  def _run_point(atten_db: float) -> None:
    point_rng = np.random.default_rng(sim_cfg.random_seed + 101)
    opt_res = optical_channel.simulate_optical_sublink(
        otx_input_symbols=otx_in,
        reference_tx_symbols=line_symbols,
        sim_cfg=sim_cfg,
        rng=point_rng,
        additional_attenuation_db=float(atten_db),
    )

    if include_host_m2c:
      m2c_res, downstream_ber = return_path.simulate_receive_direction(
          opt_res=opt_res,
          ref_symbols=line_symbols,
          ref_bits=line_bits,
          sim_cfg=sim_cfg,
          rng=point_rng,
      )
      e2e_bit_err = float(
          np.mean(
              m2c_res.rx_bits[trim_bits:-trim_bits]
              != tx_bits[trim_bits:-trim_bits]
          )
      )
      # Retimed: hops are independent, so the floor adds them. Soft
      # forwarding: downstream already includes the C2M errors.
      floor = c2m_res.ber + downstream_ber if retimed_tx else downstream_ber
      e2e_ber = max(e2e_bit_err, floor)
    else:
      e2e_ber = opt_res.orx_ber

    points.append(
        SensitivitySweepPoint(
            voa_attenuation_db=float(atten_db),
            rx_avg_power_dbm=opt_res.rx_avg_power_dbm,
            rx_oma_outer_dbm=opt_res.rx_oma_outer_dbm,
            orx_snr_db=opt_res.orx_snr_db,
            orx_ber=opt_res.orx_ber,
            host_rx_ber=e2e_ber,
        )
    )

  for atten_db in voa_attenuations_db:
    _run_point(float(atten_db))

  # Extend the VOA range until the waterfall crosses the pre-FEC target, so a
  # strong transmitter or weak receiver does not leave sensitivity unbracketed.
  target = sim_cfg.receiver.target_pre_fec_ber
  step_db = 3.0
  for _ in range(max_extensions):
    # Both the ORX and the end-to-end curves need a point above the target.
    if (max(p.orx_ber for p in points) > target
        and max(p.host_rx_ber for p in points) > target):
      break
    last_db = max(p.voa_attenuation_db for p in points)
    for k in range(1, 4):
      _run_point(last_db + k * step_db)
  # Likewise extend toward lower attenuation (negative = less loss than the
  # configured channel) if even the strongest point misses the target, e.g.
  # an LRO path that only closes near the nominal received power.
  for _ in range(max_extensions):
    # ...and a point below it.
    if (min(p.orx_ber for p in points) < target
        and min(p.host_rx_ber for p in points) < target):
      break
    first_db = min(p.voa_attenuation_db for p in points)
    for k in range(1, 4):
      _run_point(first_db - k * step_db)
  points.sort(key=lambda p: p.voa_attenuation_db)

  oma_arr = np.array([p.rx_oma_outer_dbm for p in points], dtype=np.float64)
  pavg_arr = np.array([p.rx_avg_power_dbm for p in points], dtype=np.float64)
  orx_ber_arr = np.array([p.orx_ber for p in points], dtype=np.float64)
  e2e_ber_arr = np.array([p.host_rx_ber for p in points], dtype=np.float64)

  sens_oma_kp4 = _interpolate_power_at_target_ber(
      oma_arr, orx_ber_arr, sim_cfg.receiver.target_pre_fec_ber
  )
  sens_pavg_kp4 = _interpolate_power_at_target_ber(
      pavg_arr, orx_ber_arr, sim_cfg.receiver.target_pre_fec_ber
  )
  sens_oma_1e3 = _interpolate_power_at_target_ber(oma_arr, orx_ber_arr, 1.0e-3)
  sens_pavg_1e3 = _interpolate_power_at_target_ber(
      pavg_arr, orx_ber_arr, 1.0e-3
  )
  e2e_sens_oma_kp4 = _interpolate_power_at_target_ber(
      oma_arr, e2e_ber_arr, sim_cfg.receiver.target_pre_fec_ber
  )

  return SensitivitySweepResult(
      points=points,
      sensitivity_oma_dbm_at_kp4=sens_oma_kp4,
      sensitivity_pavg_dbm_at_kp4=sens_pavg_kp4,
      sensitivity_oma_dbm_at_1e3=sens_oma_1e3,
      sensitivity_pavg_dbm_at_1e3=sens_pavg_1e3,
      e2e_sensitivity_oma_dbm_at_kp4=e2e_sens_oma_kp4,
  )


def compute_link_budget(
    sim_cfg: config.LinkSimulationConfig,
    opt_result: optical_channel.OpticalSubLinkResult,
    tx_symbols: np.ndarray,
    sens_btb: SensitivitySweepResult | None = None,
    sens_link: SensitivitySweepResult | None = None,
) -> LinkBudgetReport:
  """Computes the full 1.6T-DR8 Link Power Budget, TDECQ, & Net Margin."""
  tecq_res = calculate_tdecq(
      optical_power_mw=opt_result.tx_optical_power_mw,
      tx_symbols=tx_symbols,
      sim_cfg=sim_cfg,
      target_ser=sim_cfg.receiver.target_tdecq_ser,
  )

  tdecq_res = calculate_tdecq(
      optical_power_mw=opt_result.rx_optical_power_mw,
      tx_symbols=tx_symbols,
      sim_cfg=sim_cfg,
      target_ser=sim_cfg.receiver.target_tdecq_ser,
  )
  cd_penalty_tdecq_db = tdecq_res.tdecq_db - tecq_res.tdecq_db  # info only

  if sens_btb is None:
    cfg_btb = copy.deepcopy(sim_cfg)
    cfg_btb.fiber.length_km = 0.0
    cfg_btb.fiber.total_channel_loss_db = None
    sens_btb = sweep_receiver_sensitivity(cfg_btb)
  if sens_link is None:
    sens_link = sweep_receiver_sensitivity(sim_cfg)

  fiber_atten_db = (
      sim_cfg.fiber.attenuation_db_per_km * sim_cfg.fiber.length_km
  )
  if sim_cfg.fiber.total_channel_loss_db is not None:
    # Fixed channel loss: attribute fiber attenuation first, the rest to
    # connectors/other.
    total_channel_loss_db = sim_cfg.fiber.total_channel_loss_db
    fiber_atten_db = min(fiber_atten_db, total_channel_loss_db)
    conn_loss_db = total_channel_loss_db - fiber_atten_db
    splice_loss_db = 0.0
  elif sim_cfg.fiber.length_km <= 0.0:
    # Back-to-back: propagate_smf_fiber applies no channel loss at 0 km.
    fiber_atten_db = conn_loss_db = splice_loss_db = 0.0
    total_channel_loss_db = 0.0
  else:
    conn_loss_db = sim_cfg.fiber.connector_loss_db
    splice_loss_db = sim_cfg.fiber.splice_and_margin_loss_db
    total_channel_loss_db = fiber_atten_db + conn_loss_db + splice_loss_db

  # Exact decomposition (no clamping; NaN propagates when a sensitivity is
  # not bracketed):
  #   net = budget - channel loss - CD/eye penalty - receive-path penalty - MPI
  # where budget = TX OMA - BTB ORX sensitivity. PMD is simulated in the
  # link-length sweep, so only MPI is a separate allocation.
  total_power_budget_oma_db = (
      opt_result.tx_oma_outer_dbm - sens_btb.sensitivity_oma_dbm_at_kp4
  )
  tdecq_alloc_db = (
      sens_link.sensitivity_oma_dbm_at_kp4 - sens_btb.sensitivity_oma_dbm_at_kp4
  )
  mpi_and_dgd_db = sim_cfg.fiber.mpi_penalty_db
  m2c_penalty_db = (
      sens_link.e2e_sensitivity_oma_dbm_at_kp4
      - sens_link.sensitivity_oma_dbm_at_kp4
  )
  net_margin_db = (
      total_power_budget_oma_db
      - total_channel_loss_db
      - tdecq_alloc_db
      - m2c_penalty_db
      - mpi_and_dgd_db
  )

  return LinkBudgetReport(
      fiber_length_km=sim_cfg.fiber.length_km,
      operating_wavelength_nm=opt_result.effective_wavelength_nm,
      chromatic_dispersion_ps_nm=opt_result.total_dispersion_ps_nm,
      tx_avg_power_dbm=opt_result.tx_avg_power_dbm,
      tx_oma_outer_dbm=opt_result.tx_oma_outer_dbm,
      tx_er_db=opt_result.tx_er_db,
      tx_rlm=tecq_res.rlm,
      tecq_db=tecq_res.tdecq_db,
      tdecq_db=tdecq_res.tdecq_db,
      dispersion_penalty_tdecq_db=cd_penalty_tdecq_db,
      fiber_attenuation_db=fiber_atten_db,
      connector_loss_db=conn_loss_db,
      splice_and_aging_loss_db=splice_loss_db,
      total_channel_insertion_loss_db=total_channel_loss_db,
      rx_avg_power_dbm=opt_result.rx_avg_power_dbm,
      rx_oma_outer_dbm=opt_result.rx_oma_outer_dbm,
      rx_sensitivity_oma_btb_dbm=sens_btb.sensitivity_oma_dbm_at_kp4,
      rx_sensitivity_oma_link_dbm=sens_link.sensitivity_oma_dbm_at_kp4,
      rx_sensitivity_pavg_link_dbm=sens_link.sensitivity_pavg_dbm_at_kp4,
      total_power_budget_oma_db=total_power_budget_oma_db,
      tdecq_allocation_db=tdecq_alloc_db,
      mpi_and_dgd_penalty_db=mpi_and_dgd_db,
      host_m2c_concatenation_penalty_db=m2c_penalty_db,
      net_link_margin_db=net_margin_db,
  )
