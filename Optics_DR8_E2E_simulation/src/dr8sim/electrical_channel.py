"""Electrical channel and SerDes DSP models for 1.6T-DR8 (200G/lane PAM4).

Implements:
  - Gray-coded 200 Gbps PAM4 (106.25 GBaud) symbol/bit generation and slicing
  - Electrical TX FIR pre-emphasis, DAC quantization, random jitter (RJ), and
    analog bandwidth filtering
  - Causal PCB trace + IC package + OSFP connector insertion loss channel
    (skin effect + dielectric loss tangent + Hilbert minimum-phase dispersion)
  - Continuous-Time Linear Equalizer (CTLE)
  - ADC front-end bandwidth and ENOB quantization noise
  - Clock-and-Data Recovery (CDR) optimal sampling phase & cross-correlation
    alignment
  - Adaptive MMSE T-spaced FFE + decision-directed DFE
  - End-to-end Stage 1 (Host TX -> PCB -> Optics Client RX) and Stage 7
    (Optics Client TX -> PCB -> Host RX) electrical link pipelines, with the
    receive half reusable for any driven waveform (e.g. an LRO linear driver)
"""

from __future__ import annotations

import dataclasses
from typing import Tuple

import numpy as np
from scipy import signal
from scipy import special

from dr8sim import config


# Normalized PAM4 nominal levels {-3, -1, +1, +3} scaled to {-1, -1/3, +1/3, +1}
PAM4_LEVELS = np.array([-1.0, -1.0 / 3.0, 1.0 / 3.0, 1.0], dtype=np.float64)
PAM4_THRESHOLDS = np.array([-2.0 / 3.0, 0.0, 2.0 / 3.0], dtype=np.float64)

# IEEE 802.3 Gray code mapping:
# Index 0 (-1.0)   <-> bits (0, 0)
# Index 1 (-1/3)   <-> bits (0, 1)
# Index 2 (+1/3)   <-> bits (1, 1)
# Index 3 (+1.0)   <-> bits (1, 0)
_INDEX_TO_GRAY_BITS = np.array(
    [[0, 0], [0, 1], [1, 1], [1, 0]], dtype=np.uint8
)
_BITS_TO_INDEX = {
    (0, 0): 0,
    (0, 1): 1,
    (1, 1): 2,
    (1, 0): 3,
}


@dataclasses.dataclass
class ElectricalLinkResult:
  """Simulation outputs for a Host-to-Optics (C2M) or Optics-to-Host (M2C) link.

  Attributes:
    tx_symbols: Transmitted normalized PAM4 symbols in {-1, -1/3, +1/3, +1}.
    tx_bits: Transmitted Gray-coded bits of shape (2 * num_symbols,).
    tx_waveform_v: Electrical TX differential voltage waveform (V).
    pcb_rx_waveform_v: Waveform after PCB trace + package + connector loss (V).
    ctle_waveform_v: Waveform after RX CTLE peaking filter (V).
    adc_samples: Symbol-spaced or oversampled ADC output after quantization.
    equalized_symbols: Post-FFE/DFE equalized symbol decisions (continuous).
    sliced_symbols: Hard-sliced PAM4 symbols in {-1, -1/3, +1/3, +1}.
    rx_bits: Recovered Gray-coded bits of shape (2 * num_symbols,).
    ffe_taps: Converged T-spaced FFE tap weights.
    dfe_taps: Converged T-spaced DFE feedback tap weights.
    pcb_loss_nyquist_db: Total PCB + package insertion loss at 53.125 GHz (dB).
    post_eq_snr_db: Post-equalizer decision-point SNR (dB).
    ser: Measured Symbol Error Ratio.
    ber: Measured Bit Error Ratio (counting + analytical Gaussian tail backup).
    optimal_sample_phase: Optimal CDR sampling phase index within [0, sps - 1].
    ctle_codes: Multi-stage CTLE codes used (adapted or fixed); empty for the
      single-stage CTLE.
    rx_input_waveform_v: RX input after added noise and the RX AFE filter,
      before the CTLE (V).
  """

  tx_symbols: np.ndarray
  tx_bits: np.ndarray
  tx_waveform_v: np.ndarray
  pcb_rx_waveform_v: np.ndarray
  ctle_waveform_v: np.ndarray
  adc_samples: np.ndarray
  equalized_symbols: np.ndarray
  sliced_symbols: np.ndarray
  rx_bits: np.ndarray
  ffe_taps: np.ndarray
  dfe_taps: np.ndarray
  pcb_loss_nyquist_db: float
  post_eq_snr_db: float
  ser: float
  ber: float
  optimal_sample_phase: int
  ctle_codes: Tuple[float, ...] = ()
  rx_input_waveform_v: np.ndarray | None = None


def generate_pam4_symbols(
    num_symbols: int, rng: np.random.Generator
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
  """Generates random bits and maps them to Gray-coded PAM4 symbols.

  Args:
    num_symbols: Number of PAM4 symbols to generate.
    rng: NumPy random generator instance.

  Returns:
    Tuple of (symbols, level_indices, bits):
      - symbols: float64 array of shape (num_symbols,) in {-1, -1/3, +1/3, +1}.
      - level_indices: int64 array of shape (num_symbols,) in {0, 1, 2, 3}.
      - bits: uint8 array of shape (2 * num_symbols,) in {0, 1}.
  """
  bits = rng.integers(0, 2, size=(num_symbols, 2), dtype=np.uint8)
  # Gray decoding from bit pairs (b0, b1) -> index in {0, 1, 2, 3}:
  # (0,0)->0, (0,1)->1, (1,1)->2, (1,0)->3
  b0 = bits[:, 0].astype(np.int64)
  b1 = bits[:, 1].astype(np.int64)
  level_indices = np.where(b0 == 0, b1, 3 - b1)
  symbols = PAM4_LEVELS[level_indices]
  return symbols, level_indices, bits.reshape(-1)


def slice_pam4_symbols(
    samples: np.ndarray,
    thresholds: np.ndarray | None = None,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
  """Slices continuous equalized PAM4 samples into discrete symbols and bits.

  Args:
    samples: 1D float array of equalized symbol-rate samples.
    thresholds: Optional 3-element decision threshold array. Defaults to
      [-2/3, 0, +2/3].

  Returns:
    Tuple of (sliced_symbols, level_indices, bits).
  """
  if thresholds is None:
    thresholds = PAM4_THRESHOLDS
  level_indices = np.digitize(samples, thresholds)
  sliced_symbols = PAM4_LEVELS[level_indices]
  bits = _INDEX_TO_GRAY_BITS[level_indices].reshape(-1)
  return sliced_symbols, level_indices, bits


def quantize_signal(
    waveform: np.ndarray,
    enob: float,
    full_scale_pp: float | None = None,
    rng: np.random.Generator | None = None,
) -> np.ndarray:
  """Models DAC/ADC quantization and effective-number-of-bits (ENOB) noise.

  Args:
    waveform: Input 1D signal array.
    enob: Effective Number of Bits (ENOB).
    full_scale_pp: Peak-to-peak full-scale range. If None, uses 2.2 * max|x|.
    rng: Optional NumPy random generator. If provided, models ENOB distortion
      and thermal aperture jitter via additive white quantization noise matched
      to the ideal quantization step of an `enob`-bit converter.

  Returns:
    Quantized signal array of the same shape as `waveform`.
  """
  if full_scale_pp is None or full_scale_pp <= 0:
    peak = float(np.max(np.abs(waveform)))
    full_scale_pp = max(2.05 * peak, 1e-12)
  half_fs = 0.5 * full_scale_pp
  clipped = np.clip(waveform, -half_fs, half_fs)
  num_levels = 2.0**enob
  lsb = full_scale_pp / num_levels
  if rng is not None:
    # Effective quantization + distortion noise variance = lsb^2 / 12
    noise_std = lsb / np.sqrt(12.0)
    return np.clip(
        clipped + rng.normal(0.0, noise_std, size=waveform.shape),
        -half_fs,
        half_fs,
    )
  return np.round((clipped + half_fs) / lsb) * lsb - half_fs


def apply_bessel_lowpass(
    waveform: np.ndarray,
    cutoff_ghz: float,
    sample_rate_hz: float,
    order: int = 4,
) -> np.ndarray:
  """Applies a causal-phase-compensated Nth-order Bessel-Thomson lowpass filter.

  In the frequency domain, matches the magnitude and linear group delay (zero
  bulk delay after centering) of an analog Bessel-Thomson filter whose 3-dB
  bandwidth is `cutoff_ghz`.

  Args:
    waveform: 1D real or complex time-domain signal.
    cutoff_ghz: 3-dB cutoff frequency in GHz.
    sample_rate_hz: Waveform sample rate in Hz.
    order: Filter order (4 for standard IEEE 802.3 reference receiver / TIA).

  Returns:
    Filtered waveform of the same shape and dtype.
  """
  if cutoff_ghz <= 0:
    return waveform.copy()
  n = len(waveform)
  freqs_hz = np.fft.fftfreq(n, d=1.0 / sample_rate_hz)
  # Generate analog Bessel-Thomson polynomial normalized to 3-dB at 1 rad/s
  b, a = signal.bessel(order, 1.0, btype='low', analog=True, norm='mag')
  s = 1j * (freqs_hz / (cutoff_ghz * 1e9))
  h = np.polyval(b, s) / np.polyval(a, s)
  # Remove bulk linear phase slope at DC so the pulse peak stays aligned
  df = sample_rate_hz / n
  if n > 4:
    phase_step = np.angle(h[1] / h[0])
    tau_bulk = -phase_step / (2.0 * np.pi * df)
    h = h * np.exp(1j * 2.0 * np.pi * freqs_hz * tau_bulk)
  spectrum = np.fft.fft(waveform) * h
  result = np.fft.ifft(spectrum)
  return np.real(result) if np.isrealobj(waveform) else result


def apply_pcb_trace_channel(
    waveform: np.ndarray,
    sample_rate_hz: float,
    nyquist_ghz: float,
    trace_length_mm: float,
    dielectric_loss_db_per_inch_ghz: float,
    skin_loss_db_per_inch_sqrt_ghz: float,
    package_loss_db_at_nyquist: float,
) -> Tuple[np.ndarray, float, np.ndarray, np.ndarray]:
  """Applies causal frequency-dependent PCB trace + package insertion loss.

  Models conductor skin-effect loss (~sqrt(f)), dielectric loss tangent (~f),
  and BGA package / OSFP connector loss, reconstructing the minimum-phase
  causal dispersion phase via the discrete Hilbert transform.

  Args:
    waveform: Input electrical TX voltage waveform (V).
    sample_rate_hz: Waveform sample rate (Hz).
    nyquist_ghz: Nyquist frequency (baud_rate / 2) in GHz.
    trace_length_mm: PCB trace length in mm.
    dielectric_loss_db_per_inch_ghz: Dielectric loss factor (dB/inch/GHz).
    skin_loss_db_per_inch_sqrt_ghz: Skin-effect loss factor (dB/inch/sqrt(GHz)).
    package_loss_db_at_nyquist: Package + connector insertion loss at Nyquist
      (dB).

  Returns:
    Tuple of (output_waveform, total_loss_at_nyquist_db, freqs_ghz, il_db).
  """
  n = len(waveform)
  freqs_hz = np.fft.fftfreq(n, d=1.0 / sample_rate_hz)
  f_ghz = np.abs(freqs_hz) / 1e9
  length_inches = trace_length_mm / 25.4

  il_skin_db = skin_loss_db_per_inch_sqrt_ghz * length_inches * np.sqrt(f_ghz)
  il_diel_db = dielectric_loss_db_per_inch_ghz * length_inches * f_ghz
  il_pkg_db = package_loss_db_at_nyquist * np.sqrt(
      f_ghz / max(nyquist_ghz, 1e-6)
  )
  total_il_db = il_skin_db + il_diel_db + il_pkg_db

  nyq_skin = (
      skin_loss_db_per_inch_sqrt_ghz * length_inches * np.sqrt(nyquist_ghz)
  )
  nyq_diel = dielectric_loss_db_per_inch_ghz * length_inches * nyquist_ghz
  loss_at_nyquist_db = float(nyq_skin + nyq_diel + package_loss_db_at_nyquist)

  # Minimum-phase causal transfer function via Hilbert transform of log|H(f)|
  mag = 10.0 ** (-total_il_db / 20.0)
  log_mag = np.log(np.maximum(mag, 1e-12))
  # Analytic signal in cepstral domain for minimum-phase reconstruction
  cepstrum = np.fft.ifft(log_mag)
  window = np.zeros(n, dtype=np.float64)
  window[0] = 1.0
  if n % 2 == 0:
    window[1 : n // 2] = 2.0
    window[n // 2] = 1.0
  else:
    window[1 : (n + 1) // 2] = 2.0
  min_phase_tf = np.exp(np.fft.fft(cepstrum * window))

  # Remove bulk linear group delay so the main cursor stays near index 0
  df = sample_rate_hz / n
  if n > 8:
    k_ref = max(1, int(round((nyquist_ghz * 0.25e9) / df)))
    tau_bulk = -np.unwrap(np.angle(min_phase_tf[: k_ref + 1]))[-1] / (
        2.0 * np.pi * k_ref * df
    )
    min_phase_tf = min_phase_tf * np.exp(1j * 2.0 * np.pi * freqs_hz * tau_bulk)

  out_waveform = np.real(np.fft.ifft(np.fft.fft(waveform) * min_phase_tf))
  pos_mask = freqs_hz >= 0
  return out_waveform, loss_at_nyquist_db, f_ghz[pos_mask], total_il_db[pos_mask]


def apply_ctle(
    waveform: np.ndarray,
    sample_rate_hz: float,
    dc_gain_db: float,
    peaking_gain_db: float,
    zero_ghz: float,
    pole1_ghz: float,
    pole2_ghz: float,
) -> np.ndarray:
  """Applies a 1-zero, 2-pole Continuous-Time Linear Equalizer (CTLE).

  Args:
    waveform: Input electrical voltage waveform (V).
    sample_rate_hz: Waveform sample rate (Hz).
    dc_gain_db: Relative DC gain compared to peak gain (dB, typically negative).
    peaking_gain_db: Peak gain boost near Nyquist (dB).
    zero_ghz: Zero frequency f_z (GHz).
    pole1_ghz: First pole frequency f_p1 (GHz).
    pole2_ghz: Second pole frequency f_p2 (GHz).

  Returns:
    Equalized voltage waveform after CTLE.
  """
  n = len(waveform)
  freqs_hz = np.fft.fftfreq(n, d=1.0 / sample_rate_hz)
  s = 1j * (freqs_hz / 1e9)

  # Adjust effective zero frequency so the peak-to-DC ratio matches -dc_gain_db
  target_boost_ratio = 10.0 ** (abs(dc_gain_db) / 20.0)
  fz_eff = min(zero_ghz, pole1_ghz / max(target_boost_ratio, 1.0))

  h_raw = ((s + fz_eff) / fz_eff) / (
      ((s + pole1_ghz) / pole1_ghz) * ((s + pole2_ghz) / pole2_ghz)
  )
  peak_mag = float(np.max(np.abs(h_raw)))
  # Scale so the peak magnitude equals 10^(peaking_gain_db / 20) * DC_attenuation
  # which provides passive/active high-frequency peaking relative to DC
  dc_linear = 10.0 ** ((peaking_gain_db + dc_gain_db) / 20.0)
  h_ctle = h_raw * dc_linear
  if peak_mag > 0:
    # Ensure peak doesn't exceed 10^(peaking_gain_db / 20)
    h_ctle = h_raw * (10.0 ** (peaking_gain_db / 20.0)) / peak_mag

  # Remove bulk delay at DC
  df = sample_rate_hz / n
  if n > 4:
    tau_bulk = -np.angle(h_ctle[1] / h_ctle[0]) / (2.0 * np.pi * df)
    h_ctle = h_ctle * np.exp(1j * 2.0 * np.pi * freqs_hz * tau_bulk)

  return np.real(np.fft.ifft(np.fft.fft(waveform) * h_ctle))


def transmit_electrical_pam4(
    symbols: np.ndarray,
    sps: int,
    sample_rate_hz: float,
    vppd: float,
    fir_taps: Tuple[float, ...],
    dac_enob: float,
    rj_rms_ps: float,
    tx_bw_ghz: float,
    rng: np.random.Generator,
    dj_pp_ps: float = 0.0,
    snr_db: float = 0.0,
) -> np.ndarray:
  """Generates a continuous-time electrical PAM4 TX waveform with impairments.

  Includes T-spaced TX FIR pre-emphasis, DAC quantization, NRZ symbol hold with
  super-Gaussian transition edges, random timing jitter (RJ), and analog front-
  end bandwidth filtering.

  Args:
    symbols: Input PAM4 symbols in {-1, -1/3, +1/3, +1}.
    sps: Oversampling ratio (samples per symbol).
    sample_rate_hz: Continuous-time sample rate (Hz).
    vppd: Differential peak-to-peak voltage swing (V).
    fir_taps: T-spaced TX FIR tap weights (e.g., [pre, main, post]).
    dac_enob: Effective number of bits of TX DAC.
    rj_rms_ps: Random timing jitter RMS in picoseconds.
    tx_bw_ghz: TX analog 3-dB bandwidth in GHz.
    rng: NumPy random generator.
    dj_pp_ps: Dual-Dirac deterministic jitter, peak-to-peak (ps). 0 = off.
    snr_db: TX SNDR (dB) applied as white noise at the DAC output. 0 = off.

  Returns:
    1D float64 array of length `len(symbols) * sps` in Volts.
  """
  taps = np.asarray(fir_taps, dtype=np.float64)
  l1_norm = np.sum(np.abs(taps))
  if l1_norm > 0:
    taps = taps / l1_norm

  # Convolve symbols with T-spaced FIR pre-emphasis and align main cursor
  main_idx = int(np.argmax(np.abs(taps)))
  pre_emp_full = np.convolve(symbols, taps, mode='full')
  pre_emp = pre_emp_full[main_idx : main_idx + len(symbols)]

  # Scale to peak-to-peak differential voltage [-vppd/2, +vppd/2]
  tx_sym_v = pre_emp * (0.5 * vppd)
  tx_sym_v = quantize_signal(tx_sym_v, enob=dac_enob, full_scale_pp=vppd, rng=rng)
  if snr_db > 0:
    noise_std = float(np.std(tx_sym_v)) * 10.0 ** (-snr_db / 20.0)
    tx_sym_v = tx_sym_v + rng.normal(0.0, noise_std, size=tx_sym_v.shape)

  # Upsample with zero-order hold
  waveform = np.repeat(tx_sym_v, sps)

  # Apply analog TX bandwidth filter first so transitions are smooth and
  # differentiable before adding timing jitter
  waveform = apply_bessel_lowpass(
      waveform, cutoff_ghz=tx_bw_ghz, sample_rate_hz=sample_rate_hz, order=4
  )

  # Add random timing jitter (RJ): x(t + dt_j(t)) ~= x(t) + dx/dt * dt_j(t)
  if rj_rms_ps > 0 or dj_pp_ps > 0:
    dt_s = 1.0 / sample_rate_hz
    d_waveform_dt = np.gradient(waveform, dt_s)
    # Generate symbol-rate random jitter and interpolate smoothly
    jitter_sym_s = np.zeros(len(symbols))
    if rj_rms_ps > 0:
      jitter_sym_s += rng.normal(0.0, rj_rms_ps * 1e-12, size=len(symbols))
    if dj_pp_ps > 0:
      signs = rng.choice((-1.0, 1.0), size=len(symbols))
      jitter_sym_s += signs * 0.5 * dj_pp_ps * 1e-12
    # Each symbol's timing offset must apply to the whole transition that
    # starts that symbol, so center the per-symbol value on the edge.
    jitter_t = np.roll(np.repeat(jitter_sym_s, sps), sps // 2)
    waveform = waveform + d_waveform_dt * jitter_t

  return waveform


def find_optimal_sampling_phase(
    waveform: np.ndarray,
    tx_symbols: np.ndarray,
    sps: int,
) -> Tuple[int, int, float]:
  """Finds optimal sampling phase and integer symbol delay via cross-correlation.

  Args:
    waveform: Received oversampled waveform of length `num_symbols * sps`.
    tx_symbols: Reference transmitted PAM4 symbols of length `num_symbols`.
    sps: Samples per symbol.

  Returns:
    Tuple of (best_phase, best_symbol_lag, polarity_sign):
      - best_phase: Integer sample phase in [0, sps - 1].
      - best_symbol_lag: Integer symbol delay between TX and RX.
      - polarity_sign: +1.0 or -1.0.
  """
  num_sym = min(len(tx_symbols), len(waveform) // sps)
  ref = tx_symbols[:num_sym] - np.mean(tx_symbols[:num_sym])
  best_corr = -1.0
  best_phase = sps // 2
  best_lag = 0
  best_sign = 1.0
  max_lag = min(16, num_sym // 8)

  for phase in range(sps):
    sub = waveform[phase : num_sym * sps : sps]
    sub = sub - np.mean(sub)
    for lag in range(-max_lag, max_lag + 1):
      if lag >= 0:
        x = ref[: num_sym - lag]
        y = sub[lag:num_sym]
      else:
        x = ref[-lag:num_sym]
        y = sub[: num_sym + lag]
      denom = float(np.linalg.norm(x) * np.linalg.norm(y))
      if denom <= 1e-15:
        continue
      corr = float(np.dot(x, y)) / denom
      if abs(corr) > best_corr:
        best_corr = abs(corr)
        best_phase = phase
        best_lag = lag
        best_sign = 1.0 if corr >= 0 else -1.0

  return best_phase, best_lag, best_sign


def align_symbol_sequence(
    samples: np.ndarray, lag: int, polarity: float = 1.0
) -> np.ndarray:
  """Shifts a symbol-spaced array by `lag` symbols and corrects polarity."""
  out = np.roll(samples * polarity, -lag)
  return out


def equalize_ffe_dfe(
    rx_samples: np.ndarray,
    tx_symbols: np.ndarray,
    num_ffe_taps: int = 15,
    num_dfe_taps: int = 2,
    ref_tap: int | None = None,
    training_fraction: float = 0.35,
    reg_lambda: float = 1e-5,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, float, float, float]:
  """Applies an adaptive MMSE T-spaced FFE + Decision-Feedback Equalizer (DFE).

  Solves the joint FFE + DFE Wiener-Hopf normal equations over the initial
  training window, then performs true decision-directed DFE slicing over the
  entire sequence to capture realistic DFE error propagation.

  Args:
    rx_samples: Symbol-spaced ADC samples aligned to TX symbol timing.
    tx_symbols: Ground-truth transmitted PAM4 symbols in {-1, -1/3, +1/3, +1}.
    num_ffe_taps: Number of T-spaced FFE taps.
    num_dfe_taps: Number of post-cursor feedback DFE taps.
    ref_tap: Main cursor index within the FFE. Defaults to `num_ffe_taps // 2`.
    training_fraction: Fraction of symbols used for MMSE tap estimation.
    reg_lambda: Diagonal Tikhonov regularization for tap solver stability.

  Returns:
    Tuple of (eq_samples, ffe_taps, dfe_taps, snr_db, ser, ber).
  """
  n = min(len(rx_samples), len(tx_symbols))
  x = rx_samples[:n].astype(np.float64)
  x = x - np.mean(x)
  std_x = float(np.std(x))
  if std_x > 1e-12:
    x = x * (np.std(tx_symbols[:n]) / std_x)

  if ref_tap is None:
    ref_tap = num_ffe_taps // 2
  min_len = num_ffe_taps + num_dfe_taps + 128
  if n < min_len:
    raise ValueError(
        f'equalize_ffe_dfe needs at least {min_len} symbols, got {n}'
    )

  # ref_tap = number of pre-cursor taps (taps on future samples). Tap j of the
  # FFE multiplies x[k + ref_tap - j], so the main cursor sits at index ref_tap.
  pad_pre = num_ffe_taps - 1 - ref_tap
  pad_post = ref_tap
  x_padded = np.pad(x, (pad_pre, pad_post), mode='edge')

  eval_tail = max(16, num_ffe_taps)
  n_train = min(max(256, int(n * training_fraction)), n - eval_tail - 64)
  start_idx = max(num_dfe_taps, ref_tap)
  rows = n_train - start_idx

  # Build Toeplitz FFE + DFE regression matrix over training symbols
  a_mat = np.zeros((rows, num_ffe_taps + num_dfe_taps), dtype=np.float64)
  b_vec = tx_symbols[start_idx:n_train]

  for r in range(rows):
    k = start_idx + r
    # FFE taps: x[k + ref_tap - j] for j = 0 .. num_ffe_taps - 1
    a_mat[r, :num_ffe_taps] = x_padded[k + num_ffe_taps - 1 : k - 1 if k > 0 else None : -1][:num_ffe_taps]
    for d in range(num_dfe_taps):
      a_mat[r, num_ffe_taps + d] = -tx_symbols[k - 1 - d]

  # Solve regularized least-squares normal equations (A^T A + lambda I) w = A^T b
  ata = a_mat.T @ a_mat + reg_lambda * rows * np.eye(num_ffe_taps + num_dfe_taps)
  atb = a_mat.T @ b_vec
  weights = np.linalg.solve(ata, atb)
  # MMSE taps are biased: the output is the symbol scaled by g < 1 plus noise.
  # Remove the bias so the fixed slicer thresholds sit mid-way between levels.
  y_train = a_mat @ weights
  gain = float(np.dot(y_train, b_vec) / max(np.dot(b_vec, b_vec), 1e-15))
  if gain > 0.1:  # Skip when the FFE window can't see the main cursor at all.
    weights = weights / gain
  ffe_taps = weights[:num_ffe_taps]
  dfe_taps = weights[num_ffe_taps:]

  # Compute FFE output across all symbols using fast convolution
  ffe_out = np.convolve(x_padded, ffe_taps, mode='valid')[:n]

  # Run decision-directed DFE loop across all symbols
  eq_samples = np.copy(ffe_out)
  decisions = np.zeros(n, dtype=np.float64)
  if num_dfe_taps > 0:
    for k in range(n):
      fb = 0.0
      for d in range(num_dfe_taps):
        if k - 1 - d >= 0:
          fb += dfe_taps[d] * decisions[k - 1 - d]
      val = ffe_out[k] - fb
      eq_samples[k] = val
      # Hard slice into {-1, -1/3, +1/3, +1}
      if val < -2.0 / 3.0:
        decisions[k] = -1.0
      elif val < 0.0:
        decisions[k] = -1.0 / 3.0
      elif val < 2.0 / 3.0:
        decisions[k] = 1.0 / 3.0
      else:
        decisions[k] = 1.0

  # Evaluate metrics only after the training window to avoid overfitting
  eval_start = n_train
  eval_end = n - eval_tail
  eq_eval = eq_samples[eval_start:eval_end]
  tx_eval = tx_symbols[eval_start:eval_end]

  err_vec = eq_eval - tx_eval
  sig_power = float(np.mean(tx_eval**2))
  noise_power = max(float(np.mean(err_vec**2)), 1e-15)
  snr_db = 10.0 * np.log10(sig_power / noise_power)

  _, tx_idx, tx_bits = slice_pam4_symbols(tx_eval)
  _, rx_idx, rx_bits = slice_pam4_symbols(eq_eval)
  empirical_ser = float(np.mean(tx_idx != rx_idx))
  empirical_ber = float(np.mean(tx_bits != rx_bits))

  # Combine empirical counting with an analytical Gaussian tail estimate when
  # zero or very few errors occur in a finite-length simulation. Each level
  # uses its own mean and sigma (optical links have level-dependent noise,
  # e.g. RIN and shot noise grow with power).
  analytical_ser = pam4_gaussian_ser(eq_eval, tx_eval)
  analytical_ber = 0.5 * analytical_ser  # Gray code: 1 bit per symbol error

  if np.sum(tx_idx != rx_idx) >= 10:
    ser = empirical_ser
    ber = empirical_ber
  else:
    ser = float(max(empirical_ser, analytical_ser))
    ber = float(max(empirical_ber, analytical_ber))

  return eq_samples, ffe_taps, dfe_taps, snr_db, ser, ber


def band_limited_noise(
    n: int,
    sample_rate_hz: float,
    psd_v_per_sqrt_ghz: float,
    bandwidth_ghz: float,
    rng: np.random.Generator,
) -> np.ndarray:
  """White Gaussian noise of a given one-sided PSD, brick-wall limited.

  The result has PSD `psd_v_per_sqrt_ghz`^2 (V^2/GHz, one-sided) from DC to
  `bandwidth_ghz` and nothing above, so its RMS is
  psd * sqrt(bandwidth_ghz) for any sample rate.
  """
  if psd_v_per_sqrt_ghz <= 0:
    return np.zeros(n)
  std_full = psd_v_per_sqrt_ghz * np.sqrt(0.5 * sample_rate_hz / 1e9)
  noise = rng.normal(0.0, std_full, size=n)
  spectrum = np.fft.rfft(noise)
  freqs_ghz = np.fft.rfftfreq(n, d=1.0 / sample_rate_hz) / 1e9
  spectrum[freqs_ghz > bandwidth_ghz] = 0.0
  return np.fft.irfft(spectrum, n=n)


def pam4_gaussian_ser(samples: np.ndarray, tx_symbols: np.ndarray) -> float:
  """Gaussian-tail PAM4 SER using each level's own mean and sigma.

  Assumes the slicer thresholds at -2/3, 0, +2/3 and equiprobable levels.
  """
  thresholds = PAM4_THRESHOLDS
  ser = 0.0
  count = 0
  for idx, level in enumerate(PAM4_LEVELS):
    mask = np.isclose(tx_symbols, level)
    if not np.any(mask):
      continue
    vals = samples[mask]
    mu = float(np.mean(vals))
    sigma = max(float(np.std(vals)), 1e-12)
    p = 0.0
    if idx > 0:
      p += 0.5 * special.erfc((mu - thresholds[idx - 1]) / (np.sqrt(2) * sigma))
    if idx < 3:
      p += 0.5 * special.erfc((thresholds[idx] - mu) / (np.sqrt(2) * sigma))
    ser += p * mask.sum()
    count += mask.sum()
  return float(ser / max(count, 1))


def choose_sampling_phase(
    waveform: np.ndarray,
    ref_symbols: np.ndarray,
    sps: int,
    num_ffe_taps: int,
    ref_tap: int,
    num_dfe_taps: int,
    max_symbols: int = 4096,
) -> Tuple[int, int, float]:
  """CDR: locks delay/polarity by correlation, then picks the sampling phase
  that maximizes post-FFE/DFE SNR (where a real adaptive CDR settles).

  The phase scan spans one UI centered on the correlation peak and carries the
  symbol delay across the phase wrap, so alignment stays consistent.

  Returns:
    (phase, symbol_lag, polarity) for `waveform[phase::sps]` aligned with
    `align_symbol_sequence(..., lag, polarity)`.
  """
  phase0, lag0, polarity = find_optimal_sampling_phase(
      waveform, ref_symbols, sps
  )
  n = min(len(ref_symbols), max_symbols, len(waveform) // sps)
  if n < num_ffe_taps + num_dfe_taps + 128:
    return phase0, lag0, polarity
  ref = ref_symbols[:n]
  best = (-np.inf, phase0, lag0)
  for d in range(-(sps // 2), sps - sps // 2):
    phase, lag = phase0 + d, lag0
    if phase >= sps:
      phase, lag = phase - sps, lag + 1
    elif phase < 0:
      phase, lag = phase + sps, lag - 1
    samples = align_symbol_sequence(waveform[phase::sps], lag, polarity)[:n]
    score = _mmse_snr_db(samples, ref, num_ffe_taps, ref_tap, num_dfe_taps)
    if score > best[0]:
      best = (score, phase, lag)
  return best[1], best[2], polarity


def simulate_electrical_segment(
    tx_symbols: np.ndarray,
    tx_bits: np.ndarray,
    sim_cfg: config.LinkSimulationConfig,
    host_cfg: config.HostChannelConfig,
    rng: np.random.Generator,
    rx_cfg: config.HostChannelConfig | None = None,
    channel_cfg: config.HostChannelConfig | None = None,
) -> ElectricalLinkResult:
  """Simulates a complete 200G PAM4 electrical C2M or M2C PCB trace segment.

  Covers:
    TX FIR -> TX DAC -> TX BW + RJ -> PCB Trace + Package Channel ->
    RX Thermal Noise + CTLE -> RX ADC -> CDR Sampling -> Adaptive FFE + DFE.

  Args:
    tx_symbols: Input PAM4 symbol array of shape (num_symbols,).
    tx_bits: Input Gray-coded bit array of shape (2 * num_symbols,).
    sim_cfg: Top-level simulation parameters (baud rate, sps, sample rate).
    host_cfg: Electrical config for the TX end (tx_* fields). Also used for
      the channel and RX when `channel_cfg` / `rx_cfg` are not given.
    rng: NumPy random generator.
    rx_cfg: Electrical config for the RX end (rx_* and ctle_* fields).
    channel_cfg: Electrical config for the PCB/package channel.

  Returns:
    ElectricalLinkResult containing waveforms, equalized symbols, taps, and BER.
  """
  sps = sim_cfg.samples_per_symbol
  fs = sim_cfg.sample_rate_hz

  # 1. Electrical TX waveform generation
  tx_waveform_v = transmit_electrical_pam4(
      symbols=tx_symbols,
      sps=sps,
      sample_rate_hz=fs,
      vppd=host_cfg.tx_vppd,
      fir_taps=host_cfg.tx_fir_taps,
      dac_enob=float(host_cfg.tx_dac_bits) - 1.2,
      rj_rms_ps=host_cfg.tx_rj_rms_ps,
      tx_bw_ghz=host_cfg.tx_bw_ghz,
      rng=rng,
      dj_pp_ps=host_cfg.tx_dj_pp_ps,
      snr_db=host_cfg.tx_snr_db,
  )

  return receive_electrical_waveform(
      tx_waveform_v=tx_waveform_v,
      ref_symbols=tx_symbols,
      tx_bits=tx_bits,
      sim_cfg=sim_cfg,
      host_cfg=rx_cfg if rx_cfg is not None else host_cfg,
      rng=rng,
      channel_cfg=channel_cfg if channel_cfg is not None else host_cfg,
  )


def multistage_ctle_response(
    freqs_hz: np.ndarray,
    codes: Tuple[float, ...],
    max_codes: Tuple[float, ...],
    zeros_ghz: Tuple[float, ...],
    max_boost_db: Tuple[float, ...],
) -> np.ndarray:
  """Frequency response of cascaded first-order peaking stages.

  Each stage is (1 + jf/fz) / (1 + jf/fp) with fp = fz * 10^(boost/20), so it
  has unit DC gain and `boost` dB of high-frequency gain. A stage's boost is
  `max_boost_db * code / max_code`.
  """
  h = np.ones(len(freqs_hz), dtype=complex)
  jf_ghz = 1j * freqs_hz / 1e9
  for code, max_code, fz, max_db in zip(codes, max_codes, zeros_ghz,
                                        max_boost_db):
    boost_db = max_db * float(code) / max(float(max_code), 1.0)
    if boost_db <= 0:
      continue
    fp = fz * 10.0 ** (boost_db / 20.0)
    h *= (1.0 + jf_ghz / fz) / (1.0 + jf_ghz / fp)
  return h


def _mmse_snr_db(
    samples: np.ndarray,
    ref_symbols: np.ndarray,
    num_ffe_taps: int,
    ref_tap: int,
    num_dfe_taps: int,
) -> float:
  """Fast least-squares FFE (+ ideal-feedback DFE) SNR, used for adaptation."""
  n = len(samples)
  x = samples - np.mean(samples)
  x = x / max(float(np.std(x)), 1e-15)
  pad = np.pad(x, (num_ffe_taps - 1 - ref_tap, ref_tap), mode='edge')
  windows = np.lib.stride_tricks.sliding_window_view(pad, num_ffe_taps)[:n]
  cols = [windows]
  for d in range(1, num_dfe_taps + 1):
    cols.append(np.roll(ref_symbols, d)[:, None])
  a = np.hstack(cols)[num_ffe_taps:-num_ffe_taps]
  b = ref_symbols[num_ffe_taps:-num_ffe_taps]
  w, *_ = np.linalg.lstsq(a, b, rcond=None)
  mse = float(np.mean((a @ w - b) ** 2))
  return 10.0 * np.log10(float(np.mean(b**2)) / max(mse, 1e-15))


def adapt_multistage_ctle(
    waveform: np.ndarray,
    ref_symbols: np.ndarray,
    sim_cfg: config.LinkSimulationConfig,
    host_cfg: config.HostChannelConfig,
    num_ffe_taps: int,
    ref_tap: int,
    num_dfe_taps: int,
    max_adapt_symbols: int = 4096,
) -> Tuple[float, ...]:
  """Picks multi-stage CTLE codes that maximize post-FFE/DFE SNR.

  Mimics a SerDes 'auto' peaking-filter adaptation: a coarse grid over all
  stages, then one finer coordinate pass per stage. Runs on the first
  `max_adapt_symbols` symbols with CDR lag fixed from an initial lock.
  """
  sps = sim_cfg.samples_per_symbol
  n_sym = min(len(ref_symbols), max_adapt_symbols)
  seg = waveform[: n_sym * sps]
  ref = ref_symbols[:n_sym]
  freqs = np.fft.fftfreq(len(seg), d=1.0 / sim_cfg.sample_rate_hz)
  spectrum = np.fft.fft(seg)
  max_codes = host_cfg.rx_ctle_stage_max_codes
  stage_args = (max_codes, host_cfg.rx_ctle_stage_zeros_ghz,
                host_cfg.rx_ctle_stage_max_boost_db)

  def _filtered(codes):
    h = multistage_ctle_response(freqs, codes, *stage_args)
    return np.real(np.fft.ifft(spectrum * h))

  mid = tuple(0.5 * m for m in max_codes)
  phase0, lag0, polarity = find_optimal_sampling_phase(_filtered(mid), ref, sps)
  # Fixed-seed impairment draws so every candidate is scored on equal terms.
  imp_rng = np.random.default_rng(12345)
  rj = imp_rng.normal(0.0, 1.0, n_sym)
  dj = imp_rng.choice((-1.0, 1.0), n_sym)
  adc_noise_seed = 54321

  cache = {}

  def _score(codes):
    codes = tuple(float(round(c)) for c in codes)
    if codes not in cache:
      wave = _filtered(codes)
      slope_wave = np.gradient(wave)
      best = -np.inf
      # Score over a full UI around the lock point, including the impairments
      # added after the CTLE (sampling jitter and ADC noise), so peaking that
      # only helps a noiseless sampler is not rewarded.
      for d in range(-(sps // 2), sps - sps // 2):
        phase, lag = phase0 + d, lag0
        if phase >= sps:
          phase, lag = phase - sps, lag + 1
        elif phase < 0:
          phase, lag = phase + sps, lag - 1
        samples = align_symbol_sequence(wave[phase::sps], lag, polarity)[:n_sym]
        if host_cfg.rx_sample_rj_ui > 0 or host_cfg.rx_sample_dj_pp_ui > 0:
          slope = align_symbol_sequence(
              slope_wave[phase::sps], lag, polarity)[:n_sym]
          jit = (host_cfg.rx_sample_rj_ui * rj
                 + 0.5 * host_cfg.rx_sample_dj_pp_ui * dj)
          samples = samples + slope * jit * sps
        samples = quantize_signal(samples, host_cfg.rx_adc_enob,
                                  rng=np.random.default_rng(adc_noise_seed))
        best = max(best, _mmse_snr_db(samples, ref, num_ffe_taps, ref_tap,
                                      num_dfe_taps))
      cache[codes] = best
    return cache[codes]

  grids = [np.linspace(0, m, 4) for m in max_codes]
  best_codes = max(
      (tuple(c) for c in np.array(np.meshgrid(*grids)).T.reshape(-1, len(grids))),
      key=_score,
  )
  best_codes = list(best_codes)
  for stage, max_code in enumerate(max_codes):
    for code in range(0, int(max_code) + 1, 2):
      trial = list(best_codes)
      trial[stage] = code
      if _score(trial) > _score(best_codes):
        best_codes = trial
  return tuple(float(round(c)) for c in best_codes)


def receive_electrical_waveform(
    tx_waveform_v: np.ndarray,
    ref_symbols: np.ndarray,
    tx_bits: np.ndarray,
    sim_cfg: config.LinkSimulationConfig,
    host_cfg: config.HostChannelConfig,
    rng: np.random.Generator,
    num_ffe_taps: int | None = None,
    num_dfe_taps: int | None = None,
    channel_cfg: config.HostChannelConfig | None = None,
) -> ElectricalLinkResult:
  """Sends a driven waveform over the PCB trace into a SerDes receiver.

  Covers: PCB Trace + Package Channel -> RX Thermal Noise -> RX AFE BW ->
  CTLE (single or multi-stage adaptive) -> CDR Sampling (+ clock jitter) ->
  RX ADC -> Adaptive FFE + DFE.

  Args:
    tx_waveform_v: Oversampled voltage waveform driven into the trace (V).
    ref_symbols: PAM4 symbols the receiver should recover, used for CDR
      alignment, equalizer training, and error counting.
    tx_bits: Gray-coded bits for `ref_symbols`, stored in the result.
    sim_cfg: Top-level simulation parameters (baud rate, sps, sample rate).
    host_cfg: Electrical config of the receiving SerDes (rx_*, ctle_*).
    rng: NumPy random generator.
    num_ffe_taps: FFE taps; defaults to host_cfg.rx_ffe_taps.
    num_dfe_taps: DFE taps; defaults to host_cfg.rx_dfe_taps.
    channel_cfg: Electrical config holding the PCB/package channel; defaults
      to host_cfg.

  Returns:
    ElectricalLinkResult containing waveforms, equalized symbols, taps, and BER.
  """
  sps = sim_cfg.samples_per_symbol
  fs = sim_cfg.sample_rate_hz
  tx_symbols = ref_symbols
  if channel_cfg is None:
    channel_cfg = host_cfg
  if num_ffe_taps is None:
    num_ffe_taps = host_cfg.rx_ffe_taps
  if num_dfe_taps is None:
    num_dfe_taps = host_cfg.rx_dfe_taps
  ref_tap = host_cfg.rx_ffe_ref_tap if host_cfg.rx_ffe_ref_tap >= 0 else None

  # 2. PCB trace + package + connector channel
  pcb_rx_v, pcb_loss_nyq_db, _, _ = apply_pcb_trace_channel(
      waveform=tx_waveform_v,
      sample_rate_hz=fs,
      nyquist_ghz=sim_cfg.nyquist_freq_ghz,
      trace_length_mm=channel_cfg.pcb_trace_length_mm,
      dielectric_loss_db_per_inch_ghz=(
          channel_cfg.pcb_dielectric_loss_db_per_inch_ghz
      ),
      skin_loss_db_per_inch_sqrt_ghz=(
          channel_cfg.pcb_skin_loss_db_per_inch_sqrt_ghz
      ),
      package_loss_db_at_nyquist=(
          channel_cfg.package_connector_loss_db_at_nyquist
      ),
  )

  # 3. Add electrical RX front-end input-referred thermal noise before CTLE:
  # white at rx_noise_psd within the receiver noise bandwidth (0.75 x baud,
  # the IEEE COM receiver-filter convention), zero outside it, so the in-band
  # noise does not depend on samples_per_symbol.
  noisy_rx_v = pcb_rx_v + band_limited_noise(
      len(pcb_rx_v), fs, host_cfg.rx_noise_psd_mv_per_sqrt_ghz * 1e-3,
      0.75 * sim_cfg.baud_rate_gbaud, rng)
  if host_cfg.rx_afe_bw_ghz > 0:
    noisy_rx_v = apply_bessel_lowpass(
        noisy_rx_v, cutoff_ghz=host_cfg.rx_afe_bw_ghz, sample_rate_hz=fs
    )

  # 4. Continuous-Time Linear Equalizer (CTLE)
  ctle_codes: Tuple[float, ...] = ()
  if host_cfg.rx_ctle_stage_zeros_ghz:
    ctle_codes = tuple(host_cfg.rx_ctle_stage_codes) or adapt_multistage_ctle(
        noisy_rx_v, tx_symbols, sim_cfg, host_cfg, num_ffe_taps,
        ref_tap if ref_tap is not None else num_ffe_taps // 2, num_dfe_taps,
    )
    freqs = np.fft.fftfreq(len(noisy_rx_v), d=1.0 / fs)
    h = multistage_ctle_response(
        freqs, ctle_codes, host_cfg.rx_ctle_stage_max_codes,
        host_cfg.rx_ctle_stage_zeros_ghz, host_cfg.rx_ctle_stage_max_boost_db,
    )
    ctle_v = np.real(np.fft.ifft(np.fft.fft(noisy_rx_v) * h))
  else:
    ctle_v = apply_ctle(
        waveform=noisy_rx_v,
        sample_rate_hz=fs,
        dc_gain_db=host_cfg.ctle_dc_gain_db,
        peaking_gain_db=host_cfg.ctle_peaking_gain_db,
        zero_ghz=host_cfg.ctle_zero_ghz,
        pole1_ghz=host_cfg.ctle_pole1_ghz,
        pole2_ghz=host_cfg.ctle_pole2_ghz,
    )

  # 5. CDR sampling phase recovery and RX ADC quantization
  best_phase, best_lag, polarity = choose_sampling_phase(
      ctle_v, tx_symbols, sps, num_ffe_taps,
      ref_tap if ref_tap is not None else num_ffe_taps // 2, num_dfe_taps,
  )
  raw_symbol_samples = ctle_v[best_phase::sps][: len(tx_symbols)]
  if host_cfg.rx_sample_rj_ui > 0 or host_cfg.rx_sample_dj_pp_ui > 0:
    # Sampling-clock jitter: x(t + dt) ~= x(t) + dx/dt * dt, dt in samples.
    slope = np.gradient(ctle_v)[best_phase::sps][: len(raw_symbol_samples)]
    jitter_ui = np.zeros(len(raw_symbol_samples))
    if host_cfg.rx_sample_rj_ui > 0:
      jitter_ui += rng.normal(0.0, host_cfg.rx_sample_rj_ui,
                              size=len(jitter_ui))
    if host_cfg.rx_sample_dj_pp_ui > 0:
      jitter_ui += (rng.choice((-1.0, 1.0), size=len(jitter_ui))
                    * 0.5 * host_cfg.rx_sample_dj_pp_ui)
    raw_symbol_samples = raw_symbol_samples + slope * jitter_ui * sps
  aligned_samples = align_symbol_sequence(
      raw_symbol_samples, lag=best_lag, polarity=polarity
  )
  adc_samples = quantize_signal(
      aligned_samples, enob=host_cfg.rx_adc_enob, rng=rng
  )

  # 6. Adaptive T-spaced FFE + DFE
  eq_symbols, ffe_taps, dfe_taps, snr_db, ser, ber = equalize_ffe_dfe(
      rx_samples=adc_samples,
      tx_symbols=tx_symbols,
      num_ffe_taps=num_ffe_taps,
      num_dfe_taps=num_dfe_taps,
      ref_tap=ref_tap,
  )
  sliced_symbols, _, rx_bits = slice_pam4_symbols(eq_symbols)

  return ElectricalLinkResult(
      tx_symbols=tx_symbols,
      tx_bits=tx_bits,
      tx_waveform_v=tx_waveform_v,
      pcb_rx_waveform_v=pcb_rx_v,
      ctle_waveform_v=ctle_v,
      adc_samples=adc_samples,
      equalized_symbols=eq_symbols,
      sliced_symbols=sliced_symbols,
      rx_bits=rx_bits,
      ffe_taps=ffe_taps,
      dfe_taps=dfe_taps,
      pcb_loss_nyquist_db=pcb_loss_nyq_db,
      post_eq_snr_db=snr_db,
      ser=ser,
      ber=ber,
      optimal_sample_phase=best_phase,
      ctle_codes=ctle_codes,
      rx_input_waveform_v=noisy_rx_v,
  )
