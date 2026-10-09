"""Optical channel models for 1.6T-DR8: Line TX, CW Laser, SiPh MZM, SMF, PD/TIA.

Implements:
  - Stage 2: Client RX (HRX) to Line TX (OTX) digital forwarding, FIR
    pre-emphasis, arcsin MZM nonlinear pre-distortion, DAC quantization, and
    RF driver bandwidth filtering
  - Stage 3: O-band CW laser with Relative Intensity Noise (RIN), wavelength
    and frequency detuning, and Lorentzian linewidth Wiener phase noise
  - Stage 4: Push-pull Silicon Photonics (SiPh) Mach-Zehnder Modulator (MZM)
    with finite Extinction Ratio (ER), quadrature bias error, electro-optic
    bandwidth roll-off, insertion loss, and residual chirp (alpha_H)
  - Stage 5: O-band SMF-28 single-mode fiber propagation up to 6 km with
    wavelength-dependent chromatic dispersion D(lambda), PM-to-AM phase noise
    conversion, PMD, fiber attenuation, and MPO connector losses
  - Stage 6 & 7: PIN Photodiode + TIA front-end with thermal noise (IRND),
    signal-dependent quantum shot noise, dark current, overload compression,
    4th-order Bessel-Thomson filtering, Line RX ADC, and Media RX (ORX)
    adaptive FFE + DFE equalization
"""

from __future__ import annotations

import dataclasses
from typing import Tuple

import numpy as np

from dr8sim import config
from dr8sim import electrical_channel


SPEED_OF_LIGHT_M_PER_S = 299792458.0
ELEMENTARY_CHARGE_C = 1.602176634e-19


@dataclasses.dataclass
class OpticalSubLinkResult:
  """Simulation outputs for the optical line side (OTX -> Fiber -> ORX).

  Attributes:
    otx_drive_waveform_v: RF drive voltage applied to SiPh MZM electrodes (V).
    laser_field_sqrt_w: Complex optical field from CW laser before MZM
      (sqrt(W)).
    tx_optical_field_sqrt_w: Complex optical field at MZM output (sqrt(W)).
    tx_optical_power_mw: Instantaneous optical power at TX output (mW).
    rx_optical_field_sqrt_w: Complex optical field after SMF-28 fiber (sqrt(W)).
    rx_optical_power_mw: Instantaneous optical power incident on PIN PD (mW).
    tia_output_voltage_v: Electrical voltage waveform at TIA output (V).
    orx_adc_samples: Symbol-spaced samples at Line RX ADC output.
    orx_equalized_symbols: Equalized PAM4 samples after Media RX (ORX) FFE+DFE.
    orx_sliced_symbols: Hard-sliced PAM4 symbols at Media RX (ORX) output.
    orx_rx_bits: Recovered Gray-coded bits at Media RX (ORX) output.
    orx_ffe_taps: Converged Media RX (ORX) FFE tap weights.
    orx_dfe_taps: Converged Media RX (ORX) DFE tap weights.
    effective_wavelength_nm: Actual laser wavelength including frequency error
      (nm).
    tx_avg_power_dbm: Average launched optical power at TX output (dBm).
    tx_oma_outer_dbm: Outer Optical Modulation Amplitude (P3 - P0) at TX (dBm).
    tx_er_db: Measured outer Extinction Ratio 10*log10(P3 / P0) at TX (dB).
    tx_level_powers_mw: Array of 4 mean optical power levels [P0, P1, P2, P3]
      at TX output (mW).
    chromatic_dispersion_ps_nm_km: Dispersion coefficient D(lambda)
      (ps/(nm*km)).
    total_dispersion_ps_nm: Cumulative fiber dispersion D(lambda)*L (ps/nm).
    total_fiber_loss_db: Total optical path attenuation (fiber + connectors +
      splice) (dB).
    rx_avg_power_dbm: Average received optical power at PIN PD input (dBm).
    rx_oma_outer_dbm: Received outer OMA at PIN PD input (dBm).
    thermal_noise_rms_ua: RMS thermal noise current in receiver bandwidth (uA).
    shot_noise_rms_ua: RMS quantum shot noise current in receiver bandwidth
      (uA).
    rin_noise_rms_ua: Equivalent RMS RIN noise current at PD (uA).
    orx_snr_db: Post-ORX-equalizer decision-point SNR (dB).
    orx_ser: Symbol Error Ratio at Media RX (ORX) output.
    orx_ber: Pre-FEC Bit Error Ratio at Media RX (ORX) output.
    optimal_sample_phase: Optimal CDR sampling phase at ORX.
  """

  otx_drive_waveform_v: np.ndarray
  laser_field_sqrt_w: np.ndarray
  tx_optical_field_sqrt_w: np.ndarray
  tx_optical_power_mw: np.ndarray
  rx_optical_field_sqrt_w: np.ndarray
  rx_optical_power_mw: np.ndarray
  tia_output_voltage_v: np.ndarray
  orx_adc_samples: np.ndarray
  orx_equalized_symbols: np.ndarray
  orx_sliced_symbols: np.ndarray
  orx_rx_bits: np.ndarray
  orx_ffe_taps: np.ndarray
  orx_dfe_taps: np.ndarray
  effective_wavelength_nm: float
  tx_avg_power_dbm: float
  tx_oma_outer_dbm: float
  tx_er_db: float
  tx_level_powers_mw: np.ndarray
  chromatic_dispersion_ps_nm_km: float
  total_dispersion_ps_nm: float
  total_fiber_loss_db: float
  rx_avg_power_dbm: float
  rx_oma_outer_dbm: float
  thermal_noise_rms_ua: float
  shot_noise_rms_ua: float
  rin_noise_rms_ua: float
  orx_snr_db: float
  orx_ser: float
  orx_ber: float
  optimal_sample_phase: int


def mw_to_dbm(power_mw: float) -> float:
  """Converts optical power in mW to dBm."""
  return float(10.0 * np.log10(max(power_mw, 1e-15)))


def dbm_to_mw(power_dbm: float) -> float:
  """Converts optical power in dBm to mW."""
  return float(10.0 ** (power_dbm / 10.0))


def compute_effective_wavelength_nm(laser_cfg: config.LaserConfig) -> float:
  """Computes the actual laser wavelength including detuning and freq offset.

  Args:
    laser_cfg: Laser configuration parameters.

  Returns:
    Effective operating wavelength in nanometers.
  """
  lam_m = (laser_cfg.wavelength_nm + laser_cfg.wavelength_error_nm) * 1e-9
  d_lam_m = -(lam_m**2 / SPEED_OF_LIGHT_M_PER_S) * (
      laser_cfg.freq_offset_ghz * 1e9
  )
  return float((lam_m + d_lam_m) * 1e9)


def simulate_cw_laser(
    num_samples: int,
    sample_rate_hz: float,
    laser_cfg: config.LaserConfig,
    rng: np.random.Generator,
) -> Tuple[np.ndarray, float]:
  """Generates the complex optical field of an O-band CW laser with RIN & phase noise.

  Args:
    num_samples: Total number of time-domain waveform samples.
    sample_rate_hz: Simulation sampling frequency (Hz).
    laser_cfg: Laser configuration parameters.
    rng: NumPy random generator.

  Returns:
    Tuple of (laser_field_sqrt_w, effective_wavelength_nm).
  """
  dt_s = 1.0 / sample_rate_hz
  p0_w = dbm_to_mw(laser_cfg.cw_power_dbm) * 1e-3

  # 1. Relative Intensity Noise (RIN) over simulation Nyquist bandwidth fs / 2
  rin_linear_per_hz = 10.0 ** (laser_cfg.rin_db_hz / 10.0)
  rin_rms = np.sqrt(rin_linear_per_hz * (0.5 * sample_rate_hz))
  delta_rin = rng.normal(0.0, rin_rms, size=num_samples)
  inst_power_w = np.maximum(p0_w * (1.0 + delta_rin), 1e-12 * p0_w)

  # 2. Lorentzian linewidth Wiener phase noise
  linewidth_hz = max(0.0, laser_cfg.linewidth_mhz * 1e6)
  if linewidth_hz > 0:
    phase_step_std = np.sqrt(2.0 * np.pi * linewidth_hz * dt_s)
    phase_steps = rng.normal(0.0, phase_step_std, size=num_samples)
    phase_rad = np.cumsum(phase_steps)
  else:
    phase_rad = np.zeros(num_samples, dtype=np.float64)

  laser_field_sqrt_w = np.sqrt(inst_power_w) * np.exp(1j * phase_rad)
  eff_wavelength_nm = compute_effective_wavelength_nm(laser_cfg)
  return laser_field_sqrt_w, eff_wavelength_nm


def compute_drive_vpp_for_target_er(
    target_er_db: float,
    vpi_volts: float,
    intrinsic_er_db: float,
) -> float:
  """Computes the RF peak-to-peak drive voltage required for a target outer ER.

  For a push-pull MZM biased at quadrature with a finite intrinsic ER
  (null depth eps^2 = 10^(-ER_int/10)), the normalized optical intensity is:
    I(V) ~ (1 + eps^2) + (1 - eps^2) * sin(pi * V / V_pi)
  where V spans [-V_pp / 2, +V_pp / 2]. Thus:
    s_max = (ER - 1)(1 + eps^2) / ((ER + 1)(1 - eps^2))
  The bias error is not compensated (it is a real residual impairment).

  Args:
    target_er_db: Desired outer Extinction Ratio in dB.
    vpi_volts: Modulator half-wave voltage V_pi (V).
    intrinsic_er_db: Intrinsic interferometer extinction ratio (dB).

  Returns:
    Required differential peak-to-peak RF drive swing V_pp (V).
  """
  er = 10.0 ** (target_er_db / 10.0)
  eps2 = 10.0 ** (-intrinsic_er_db / 10.0)  # null-to-peak intensity floor
  # I(V) ~ (1 + eps^2) + (1 - eps^2) * sin(pi V / V_pi), so the outer ER at
  # +/- V_pp/2 is reached with sin(pi V_pp / (2 V_pi)) = s below.
  s_max = (er - 1.0) * (1.0 + eps2) / ((er + 1.0) * (1.0 - eps2))
  s_max = float(np.clip(s_max, 0.01, 0.995))
  return float((2.0 * vpi_volts / np.pi) * np.arcsin(s_max))


def _line_tx_drive(
    symbols: np.ndarray,
    sim_cfg: config.LinkSimulationConfig,
    mzm_cfg: config.MzmConfig,
    rng: np.random.Generator | None,
    dac_full_scale: float | None = None,
    apply_dac: bool = True,
) -> Tuple[np.ndarray, float]:
  """Line-TX DSP + DAC + driver: FIR, arcsin predistortion, DAC, bandwidth.

  Returns:
    (drive waveform at the MZM electrodes in V, DAC full scale used in V).
  """
  sps = sim_cfg.samples_per_symbol
  fs = sim_cfg.sample_rate_hz

  # 1. Line TX DSP T-spaced FIR pre-emphasis normalized to DC gain = 1.0
  # so steady-state outer PAM4 levels remain at [-1, +1] while transitions are
  # boosted to pre-compensate RF driver + MZM EO roll-off.
  taps = np.asarray(mzm_cfg.line_tx_fir_taps, dtype=np.float64)
  dc_sum = float(np.sum(taps))
  if abs(dc_sum) > 1e-6:
    taps = taps / dc_sum
  main_idx = int(np.argmax(np.abs(taps)))
  pre_emp_full = np.convolve(symbols, taps, mode='full')
  pre_emp = pre_emp_full[main_idx : main_idx + len(symbols)]

  # 2. Nominal RF drive V_pp for the target outer ER (steady state +/-1)
  if mzm_cfg.target_outer_er_db > 0:
    vpp = compute_drive_vpp_for_target_er(
        target_er_db=mzm_cfg.target_outer_er_db,
        vpi_volts=mzm_cfg.vpi_volts,
        intrinsic_er_db=mzm_cfg.intrinsic_er_db,
    )
  else:
    vpp = mzm_cfg.drive_vpp_volts

  # 3. Optional arcsin pre-distortion to linearize the MZM's sin(pi*V/V_pi):
  # x -> (V_pi / pi) * arcsin(clip(s_nom * x)), s_nom = sin(pi*vpp/(2*V_pi)).
  s_nom = float(np.sin(0.5 * np.pi * vpp / mzm_cfg.vpi_volts))
  if mzm_cfg.enable_arcsin_predistortion and s_nom > 0.05:
    arg = np.clip(s_nom * pre_emp, -0.98, 0.98)
    pre_dist_v = (mzm_cfg.vpi_volts / np.pi) * np.arcsin(arg)
  else:
    pre_dist_v = pre_emp * (0.5 * vpp)

  # 4. Line TX DAC quantization
  if dac_full_scale is None:
    dac_full_scale = 2.05 * float(np.max(np.abs(pre_dist_v)))
  if apply_dac:
    dac_out_v = electrical_channel.quantize_signal(
        pre_dist_v,
        enob=mzm_cfg.line_tx_dac_enob,
        full_scale_pp=dac_full_scale,
        rng=rng,
    )
  else:
    dac_out_v = pre_dist_v

  # 5. Upsample and filter through composite RF driver + MZM EO bandwidth
  # Using the composite 3-dB bandwidth 1 / sqrt(1/f_drv^2 + 1/f_eo^2)
  composite_bw_ghz = 1.0 / np.sqrt(
      (1.0 / max(mzm_cfg.driver_bw_ghz, 1.0)) ** 2
      + (1.0 / max(mzm_cfg.eo_bw_ghz, 1.0)) ** 2
  )
  rf_waveform_v = np.repeat(dac_out_v, sps)
  drive_v = electrical_channel.apply_bessel_lowpass(
      rf_waveform_v, cutoff_ghz=composite_bw_ghz, sample_rate_hz=fs, order=4
  )
  return drive_v, dac_full_scale


def _mzm_field_transfer(
    drive_v: np.ndarray, mzm_cfg: config.MzmConfig
) -> np.ndarray:
  """Push-pull SiPh MZM field transfer incl. finite ER, IL, and chirp.

  Biased at quadrature (-pi/2 on the cos^2 curve so +V increases power):
    E_out / E_in = IL * [cos(phi/2) + j * eps * sin(phi/2)]
  with eps = 10^(-ER_int/20), so the null-to-peak power ratio is ER_int.
  A residual chirp exp(j * (alpha_H / 2) * ln I) is applied, with phase in
  the e^{+j omega t} convention used throughout (numpy FFT synthesis).
  """
  bias_err_rad = np.deg2rad(mzm_cfg.bias_error_deg)
  phi_t = (
      -mzm_cfg.bias_phase_rad
      + bias_err_rad
      + (np.pi * drive_v / mzm_cfg.vpi_volts)
  )
  eps = 10.0 ** (-mzm_cfg.intrinsic_er_db / 20.0)
  il_field = 10.0 ** (-mzm_cfg.insertion_loss_db / 20.0)
  transfer = il_field * (np.cos(0.5 * phi_t) + 1j * eps * np.sin(0.5 * phi_t))
  intensity_norm = np.maximum(np.abs(transfer) ** 2, 1e-9)
  chirp_phase = 0.5 * mzm_cfg.chirp_alpha * np.log(intensity_norm)
  return transfer * np.exp(1j * chirp_phase)


def _long_run_levels_mw(
    sim_cfg: config.LinkSimulationConfig,
    mzm_cfg: config.MzmConfig,
    dac_full_scale: float,
    cw_power_mw: float,
    run_symbols: int = 64,
    repeats: int = 4,
) -> np.ndarray:
  """Steady-state optical power of each PAM4 level (mW) from long runs."""
  sps = sim_cfg.samples_per_symbol
  levels = electrical_channel.PAM4_LEVELS
  pattern = np.tile(np.repeat(levels, run_symbols), repeats)
  # The signal path models the DAC as zero-mean noise, so the mean level has
  # no DAC error; skip the DAC here rather than rounding the levels.
  drive_v, _ = _line_tx_drive(pattern, sim_cfg, mzm_cfg, rng=None,
                              dac_full_scale=dac_full_scale, apply_dac=False)
  power = np.abs(_mzm_field_transfer(drive_v, mzm_cfg)) ** 2 * cw_power_mw
  run_len = run_symbols * sps
  q = run_len // 4
  out = np.zeros(4)
  for idx in range(4):
    chunks = [
        power[(r * 4 + idx) * run_len + q:(r * 4 + idx) * run_len + run_len - q]
        for r in range(repeats)
    ]
    out[idx] = float(np.mean(np.concatenate(chunks)))
  return out


def modulate_siph_mzm(
    symbols: np.ndarray,
    laser_field_sqrt_w: np.ndarray,
    sim_cfg: config.LinkSimulationConfig,
    mzm_cfg: config.MzmConfig,
    rng: np.random.Generator,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, float, float, float, np.ndarray]:
  """Simulates the Line TX (OTX) DSP, RF driver, and SiPh push-pull MZM.

  Args:
    symbols: Input PAM4 symbol sequence from Client RX (HRX) in [-1, +1].
    laser_field_sqrt_w: Complex optical field from CW laser (sqrt(W)).
    sim_cfg: Top-level simulation parameters.
    mzm_cfg: Line TX and SiPh MZM configuration.
    rng: NumPy random generator.

  Returns:
    Tuple of:
      - otx_drive_waveform_v: RF voltage waveform driving MZM (V)
      - tx_optical_field_sqrt_w: Complex modulated optical field (sqrt(W))
      - tx_optical_power_mw: Modulated optical power waveform (mW)
      - tx_avg_power_dbm: Average TX optical power (dBm)
      - tx_oma_outer_dbm: Outer OMA = P3 - P0 (dBm)
      - tx_er_db: Outer Extinction Ratio 10*log10(P3 / P0) (dB)
      - level_powers_mw: 4-element array [P0, P1, P2, P3] (mW)
  """
  otx_drive_waveform_v, dac_full_scale = _line_tx_drive(
      symbols, sim_cfg, mzm_cfg, rng
  )
  tx_optical_field_sqrt_w = laser_field_sqrt_w * _mzm_field_transfer(
      otx_drive_waveform_v, mzm_cfg
  )
  tx_optical_power_mw = (np.abs(tx_optical_field_sqrt_w) ** 2) * 1e3
  tx_avg_power_dbm = mw_to_dbm(float(np.mean(tx_optical_power_mw)))

  # Steady-state (long-run) PAM4 levels, as IEEE OMA/ER are defined: drive the
  # same TX chain with long runs of each level (noise-free CW laser) and
  # average the middle of each run. Measuring at PRBS eye centers instead
  # would report main-cursor amplitudes that depend on the TX FIR.
  level_powers_mw = _long_run_levels_mw(
      sim_cfg, mzm_cfg, dac_full_scale,
      cw_power_mw=float(np.mean(np.abs(laser_field_sqrt_w) ** 2)) * 1e3,
  )
  p0_mw = max(float(level_powers_mw[0]), 1e-12)
  p3_mw = max(float(level_powers_mw[3]), p0_mw + 1e-12)
  oma_outer_mw = max(p3_mw - p0_mw, 1e-12)
  tx_oma_outer_dbm = mw_to_dbm(oma_outer_mw)
  tx_er_db = float(10.0 * np.log10(p3_mw / p0_mw))

  return (
      otx_drive_waveform_v,
      tx_optical_field_sqrt_w,
      tx_optical_power_mw,
      tx_avg_power_dbm,
      tx_oma_outer_dbm,
      tx_er_db,
      level_powers_mw,
  )


def compute_smf_dispersion_ps_nm_km(
    wavelength_nm: float,
    fiber_cfg: config.FiberConfig,
) -> float:
  """Computes O-band SMF-28 chromatic dispersion D(lambda) in ps/(nm*km).

  Uses the standard ITU-T G.652 / IEEE 802.3 zero-dispersion slope formula:
    D(lambda) = (S_0 / 4) * (lambda - lambda_0^4 / lambda^3)

  Args:
    wavelength_nm: Operating laser wavelength in nm.
    fiber_cfg: Fiber configuration parameters.

  Returns:
    Chromatic dispersion coefficient D(lambda) in ps / (nm * km).
  """
  if fiber_cfg.override_dispersion_ps_nm_km is not None:
    return float(fiber_cfg.override_dispersion_ps_nm_km)
  lam0 = fiber_cfg.zero_dispersion_wavelength_nm
  s0 = fiber_cfg.dispersion_slope_ps_nm2_km
  return float(0.25 * s0 * (wavelength_nm - (lam0**4) / (wavelength_nm**3)))


def propagate_smf_fiber(
    tx_optical_field_sqrt_w: np.ndarray,
    wavelength_nm: float,
    sample_rate_hz: float,
    fiber_cfg: config.FiberConfig,
    additional_attenuation_db: float = 0.0,
) -> Tuple[np.ndarray, np.ndarray, float, float, float]:
  """Propagates the complex optical field through up to 6 km of SMF-28 fiber.

  Args:
    tx_optical_field_sqrt_w: Complex optical field at fiber input (sqrt(W)).
    wavelength_nm: Operating wavelength in nm.
    sample_rate_hz: Simulation sampling frequency (Hz).
    fiber_cfg: Fiber configuration parameters.
    additional_attenuation_db: Extra VOA attenuation (dB) for sensitivity sweeps.

  Returns:
    Tuple of:
      - rx_optical_field_sqrt_w: Complex optical field at fiber output (sqrt(W))
      - rx_optical_power_mw: Instantaneous optical power at fiber output (mW)
      - d_ps_nm_km: Dispersion coefficient D(lambda) in ps/(nm*km)
      - total_d_ps_nm: Cumulative dispersion D(lambda)*L in ps/nm
      - total_loss_db: Total optical path attenuation in dB
  """
  length_km = max(0.0, fiber_cfg.length_km)
  d_ps_nm_km = compute_smf_dispersion_ps_nm_km(wavelength_nm, fiber_cfg)
  total_d_ps_nm = d_ps_nm_km * length_km

  if fiber_cfg.total_channel_loss_db is not None:
    fiber_loss_db = fiber_cfg.total_channel_loss_db + additional_attenuation_db
  elif length_km > 0.0:
    fiber_loss_db = (
        fiber_cfg.attenuation_db_per_km * length_km
        + fiber_cfg.connector_loss_db
        + fiber_cfg.splice_and_margin_loss_db
        + additional_attenuation_db
    )
  else:
    fiber_loss_db = additional_attenuation_db

  n = len(tx_optical_field_sqrt_w)
  freqs_hz = np.fft.fftfreq(n, d=1.0 / sample_rate_hz)
  omega = 2.0 * np.pi * freqs_hz

  lam_m = wavelength_nm * 1e-9
  d_si_per_km = d_ps_nm_km * 1e-3  # s / (m * km)
  beta2_s2_per_km = -(
      lam_m**2 / (2.0 * np.pi * SPEED_OF_LIGHT_M_PER_S)
  ) * d_si_per_km

  # e^{+j omega t} convention (numpy FFT synthesis, consistent with the MZM
  # chirp sign): propagation multiplies by exp(-j * beta2 * L * omega^2 / 2).
  # With D > 0 (beta2 < 0) and alpha_H < 0 this gives the standard
  # |H| = |cos(theta) - alpha * sin(theta)| small-signal response.
  cd_phase = 0.5 * beta2_s2_per_km * length_km * (omega**2)
  h_fiber = np.exp(-1j * cd_phase)

  if length_km > 0 and fiber_cfg.pmd_ps_per_sqrt_km > 0:
    tau_pmd_s = fiber_cfg.pmd_ps_per_sqrt_km * 1e-12 * np.sqrt(length_km)
    h_fiber = h_fiber * np.exp(-0.25 * (omega * tau_pmd_s) ** 2)

  attenuation_field = 10.0 ** (-fiber_loss_db / 20.0)
  rx_optical_field_sqrt_w = (
      np.fft.ifft(np.fft.fft(tx_optical_field_sqrt_w) * h_fiber)
      * attenuation_field
  )
  rx_optical_power_mw = (np.abs(rx_optical_field_sqrt_w) ** 2) * 1e3

  return (
      rx_optical_field_sqrt_w,
      rx_optical_power_mw,
      d_ps_nm_km,
      total_d_ps_nm,
      float(fiber_loss_db),
  )


def receive_optical_signal(
    rx_optical_power_mw: np.ndarray,
    tx_symbols: np.ndarray,
    sim_cfg: config.LinkSimulationConfig,
    rx_cfg: config.ReceiverConfig,
    laser_cfg: config.LaserConfig,
    rng: np.random.Generator,
) -> Tuple[
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
    float,
    float,
    float,
    float,
    float,
    float,
    int,
]:
  """Simulates the PIN PD, TIA, noise sources, Line RX ADC, and Media RX DSP."""
  sps = sim_cfg.samples_per_symbol
  fs = sim_cfg.sample_rate_hz
  n = len(rx_optical_power_mw)

  # 1. PIN Photodiode square-law conversion: P_opt (W) -> I_pd (A)
  rx_power_w = rx_optical_power_mw * 1e-3
  dark_current_a = rx_cfg.dark_current_na * 1e-9
  ipd_clean_a = rx_cfg.responsivity_a_per_w * rx_power_w + dark_current_a

  # 2. Receiver noise generation over simulation Nyquist bandwidth (fs / 2)
  half_fs = 0.5 * fs
  irnd_a_sqrt_hz = rx_cfg.tia_irnd_pa_per_sqrt_hz * 1e-12
  thermal_std_full_a = irnd_a_sqrt_hz * np.sqrt(half_fs)
  thermal_noise_a = rng.normal(0.0, thermal_std_full_a, size=n)

  shot_std_inst_a = np.sqrt(
      2.0 * ELEMENTARY_CHARGE_C * np.maximum(ipd_clean_a, 1e-12) * half_fs
  )
  shot_noise_a = rng.normal(0.0, 1.0, size=n) * shot_std_inst_a

  # Composite PD + TIA 3-dB bandwidth (4th-order Bessel-Thomson response)
  composite_rx_bw_ghz = 1.0 / np.sqrt(
      (1.0 / max(rx_cfg.pd_bw_ghz, 1.0)) ** 2
      + (1.0 / max(rx_cfg.tia_bw_ghz, 1.0)) ** 2
  )
  b_neq_hz = 1.04 * composite_rx_bw_ghz * 1e9
  avg_ipd_a = float(np.mean(ipd_clean_a))
  thermal_noise_rms_ua = float(irnd_a_sqrt_hz * np.sqrt(b_neq_hz) * 1e6)
  shot_noise_rms_ua = float(
      np.sqrt(2.0 * ELEMENTARY_CHARGE_C * max(avg_ipd_a, 1e-12) * b_neq_hz)
      * 1e6
  )
  rin_linear = 10.0 ** (laser_cfg.rin_db_hz / 10.0)
  rin_noise_rms_ua = float(avg_ipd_a * np.sqrt(rin_linear * b_neq_hz) * 1e6)

  total_ipd_a = ipd_clean_a + thermal_noise_a + shot_noise_a

  # 3. Transimpedance Amplifier (TIA): 4th-order Bessel-Thomson filter + gain
  tia_filtered_a = electrical_channel.apply_bessel_lowpass(
      total_ipd_a, cutoff_ghz=composite_rx_bw_ghz, sample_rate_hz=fs, order=4
  )
  tia_ac_a = tia_filtered_a - np.mean(tia_filtered_a)
  tia_voltage_v = tia_ac_a * rx_cfg.tia_transimpedance_ohms

  # Model TIA overload soft compression when input optical power exceeds overload
  overload_w = dbm_to_mw(rx_cfg.overload_power_dbm) * 1e-3
  vmax_overload = (
      0.65
      * rx_cfg.responsivity_a_per_w
      * overload_w
      * rx_cfg.tia_transimpedance_ohms
  )
  if vmax_overload > 0:
    tia_voltage_v = vmax_overload * np.tanh(tia_voltage_v / vmax_overload)

  # 4. Line RX ADC front-end CDR sampling and quantization
  best_phase, best_lag, polarity = electrical_channel.choose_sampling_phase(
      tia_voltage_v, tx_symbols, sps, rx_cfg.orx_ffe_taps,
      rx_cfg.orx_reference_tap, rx_cfg.orx_dfe_taps,
  )
  raw_samples = tia_voltage_v[best_phase::sps][: len(tx_symbols)]
  aligned_samples = electrical_channel.align_symbol_sequence(
      raw_samples, lag=best_lag, polarity=polarity
  )
  orx_adc_samples = electrical_channel.quantize_signal(
      aligned_samples, enob=rx_cfg.adc_enob, rng=rng
  )

  # 5. Media RX (ORX) DSP Adaptive T-spaced FFE + DFE
  orx_eq_symbols, orx_ffe_taps, orx_dfe_taps, orx_snr_db, orx_ser, orx_ber = (
      electrical_channel.equalize_ffe_dfe(
          rx_samples=orx_adc_samples,
          tx_symbols=tx_symbols,
          num_ffe_taps=rx_cfg.orx_ffe_taps,
          num_dfe_taps=rx_cfg.orx_dfe_taps,
          ref_tap=rx_cfg.orx_reference_tap,
      )
  )
  orx_sliced_symbols, _, orx_rx_bits = electrical_channel.slice_pam4_symbols(
      orx_eq_symbols
  )

  return (
      tia_voltage_v,
      orx_adc_samples,
      orx_eq_symbols,
      orx_sliced_symbols,
      orx_rx_bits,
      orx_ffe_taps,
      orx_dfe_taps,
      thermal_noise_rms_ua,
      shot_noise_rms_ua,
      rin_noise_rms_ua,
      orx_snr_db,
      orx_ser,
      orx_ber,
      best_phase,
  )


def simulate_optical_sublink(
    otx_input_symbols: np.ndarray,
    reference_tx_symbols: np.ndarray,
    sim_cfg: config.LinkSimulationConfig,
    rng: np.random.Generator,
    additional_attenuation_db: float = 0.0,
) -> OpticalSubLinkResult:
  """Runs the complete optical line segment: Laser + MZM + SMF-28 + PD/TIA + ORX."""
  num_samples = len(otx_input_symbols) * sim_cfg.samples_per_symbol
  # Apply the effective laser RIN (derived from RIN_OMA when that is set).
  laser_cfg = dataclasses.replace(
      sim_cfg.laser, rin_db_hz=sim_cfg.effective_laser_rin_db_hz
  )

  # Stage 3: O-band CW Laser with RIN, frequency/wavelength error, & linewidth
  laser_field_sqrt_w, eff_wavelength_nm = simulate_cw_laser(
      num_samples=num_samples,
      sample_rate_hz=sim_cfg.sample_rate_hz,
      laser_cfg=laser_cfg,
      rng=rng,
  )

  # Stage 4: Line TX DSP + RF Driver + SiPh MZM
  (
      otx_drive_v,
      tx_opt_field,
      tx_opt_power_mw,
      tx_avg_dbm,
      tx_oma_dbm,
      tx_er_db,
      tx_level_powers_mw,
  ) = modulate_siph_mzm(
      symbols=otx_input_symbols,
      laser_field_sqrt_w=laser_field_sqrt_w,
      sim_cfg=sim_cfg,
      mzm_cfg=sim_cfg.mzm,
      rng=rng,
  )

  # Stage 5: SMF-28 Fiber Propagation (up to 6 km)
  (
      rx_opt_field,
      rx_opt_power_mw,
      d_ps_nm_km,
      total_d_ps_nm,
      total_fiber_loss_db,
  ) = propagate_smf_fiber(
      tx_optical_field_sqrt_w=tx_opt_field,
      wavelength_nm=eff_wavelength_nm,
      sample_rate_hz=sim_cfg.sample_rate_hz,
      fiber_cfg=sim_cfg.fiber,
      additional_attenuation_db=additional_attenuation_db,
  )

  rx_avg_dbm = mw_to_dbm(float(np.mean(rx_opt_power_mw)))
  rx_oma_dbm = tx_oma_dbm - total_fiber_loss_db

  # Stage 6 & 7: PIN PD + TIA + Noise + Line RX ADC + Media RX (ORX) DSP
  (
      tia_v,
      orx_adc,
      orx_eq,
      orx_sliced,
      orx_bits,
      orx_ffe,
      orx_dfe,
      th_rms_ua,
      shot_rms_ua,
      rin_rms_ua,
      orx_snr_db,
      orx_ser,
      orx_ber,
      best_phase,
  ) = receive_optical_signal(
      rx_optical_power_mw=rx_opt_power_mw,
      tx_symbols=reference_tx_symbols,
      sim_cfg=sim_cfg,
      rx_cfg=sim_cfg.receiver,
      laser_cfg=laser_cfg,
      rng=rng,
  )

  return OpticalSubLinkResult(
      otx_drive_waveform_v=otx_drive_v,
      laser_field_sqrt_w=laser_field_sqrt_w,
      tx_optical_field_sqrt_w=tx_opt_field,
      tx_optical_power_mw=tx_opt_power_mw,
      rx_optical_field_sqrt_w=rx_opt_field,
      rx_optical_power_mw=rx_opt_power_mw,
      tia_output_voltage_v=tia_v,
      orx_adc_samples=orx_adc,
      orx_equalized_symbols=orx_eq,
      orx_sliced_symbols=orx_sliced,
      orx_rx_bits=orx_bits,
      orx_ffe_taps=orx_ffe,
      orx_dfe_taps=orx_dfe,
      effective_wavelength_nm=eff_wavelength_nm,
      tx_avg_power_dbm=tx_avg_dbm,
      tx_oma_outer_dbm=tx_oma_dbm,
      tx_er_db=tx_er_db,
      tx_level_powers_mw=tx_level_powers_mw,
      chromatic_dispersion_ps_nm_km=d_ps_nm_km,
      total_dispersion_ps_nm=total_d_ps_nm,
      total_fiber_loss_db=total_fiber_loss_db,
      rx_avg_power_dbm=rx_avg_dbm,
      rx_oma_outer_dbm=rx_oma_dbm,
      thermal_noise_rms_ua=th_rms_ua,
      shot_noise_rms_ua=shot_rms_ua,
      rin_noise_rms_ua=rin_rms_ua,
      orx_snr_db=orx_snr_db,
      orx_ser=orx_ser,
      orx_ber=orx_ber,
      optimal_sample_phase=best_phase,
  )
