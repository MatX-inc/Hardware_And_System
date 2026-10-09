"""Receive direction of the module: optical RX -> M2C PCB -> host SerDes RX.

The architecture decides what sits between the TIA and the M2C trace:
  - 'retimed': the module's Media RX DSP (ADC + FFE/DFE) recovers symbols and
    the Client TX re-transmits them, so the host only sees the M2C trace.
  - 'lro': no DSP. The TIA output goes through a linear driver (CTLE peaking,
    AGC, bandwidth, soft saturation, noise) straight onto the M2C trace, and
    the host SerDes RX equalizes fiber + TIA + driver + trace together.
"""

from __future__ import annotations

from typing import Tuple

import numpy as np

from dr8sim import config
from dr8sim import electrical_channel
from dr8sim import host_serdes
from dr8sim import optical_channel

# RMS of equiprobable PAM4 levels {-1, -1/3, 1/3, 1} relative to the outer
# level: sqrt(5/9).
_PAM4_RMS_TO_PEAK = np.sqrt(5.0 / 9.0)


def lro_linear_driver(
    tia_voltage_v: np.ndarray,
    sim_cfg: config.LinkSimulationConfig,
    rng: np.random.Generator,
) -> np.ndarray:
  """Models the LRO module's linear TIA-to-host driver.

  CTLE peaking -> AGC to the target swing -> driver bandwidth -> additive
  output noise -> soft tanh saturation.

  Args:
    tia_voltage_v: TIA output waveform (V), AC-coupled.
    sim_cfg: Simulation config; uses sim_cfg.lro.
    rng: NumPy random generator.

  Returns:
    Driver output waveform (V) to drive onto the M2C trace.
  """
  lro = sim_cfg.lro
  fs = sim_cfg.sample_rate_hz
  v = tia_voltage_v

  if lro.driver_peaking_db > 0:
    # First-order peaking stage: unit DC gain, driver_peaking_db of
    # high-frequency boost above driver_peaking_zero_ghz.
    freqs = np.fft.fftfreq(len(v), d=1.0 / fs)
    h = electrical_channel.multistage_ctle_response(
        freqs,
        codes=(1.0,),
        max_codes=(1.0,),
        zeros_ghz=(lro.driver_peaking_zero_ghz,),
        max_boost_db=(lro.driver_peaking_db,),
    )
    v = np.real(np.fft.ifft(np.fft.fft(v) * h))

  # AGC: scale so the outer PAM4 levels sit at +/- driver_output_vppd / 2.
  rms = float(np.std(v))
  if rms > 0:
    v = v * (0.5 * lro.driver_output_vppd * _PAM4_RMS_TO_PEAK / rms)

  v = electrical_channel.apply_bessel_lowpass(
      v, cutoff_ghz=lro.driver_bw_ghz, sample_rate_hz=fs, order=4
  )

  if lro.driver_noise_mv_rms > 0:
    # White noise whose RMS within the driver noise bandwidth matches spec.
    noise_bw_hz = 1.04 * lro.driver_bw_ghz * 1e9
    std_full = (
        lro.driver_noise_mv_rms * 1e-3 * np.sqrt(0.5 * fs / noise_bw_hz)
    )
    noise = rng.normal(0.0, std_full, size=v.shape)
    v = v + electrical_channel.apply_bessel_lowpass(
        noise, cutoff_ghz=lro.driver_bw_ghz, sample_rate_hz=fs, order=4
    )

  v_sat = 0.5 * lro.driver_saturation_vppd
  if v_sat > 0:
    v = v_sat * np.tanh(v / v_sat)
  return v


def simulate_receive_direction(
    opt_res: optical_channel.OpticalSubLinkResult,
    ref_symbols: np.ndarray,
    ref_bits: np.ndarray,
    sim_cfg: config.LinkSimulationConfig,
    rng: np.random.Generator,
) -> Tuple[electrical_channel.ElectricalLinkResult, float]:
  """Runs the module receive direction from the TIA to the host SerDes RX.

  Args:
    opt_res: Optical sub-link result (TIA waveform and ORX DSP outputs).
    ref_symbols: Original Host TX symbols (LRO host RX trains on these).
    ref_bits: Original Host TX bits.
    sim_cfg: Simulation config.
    rng: NumPy random generator.

  Returns:
    (m2c_result, downstream_ber): the host RX result, and the BER contributed
    from the optical RX onward (ORX + M2C for retimed, host RX alone for LRO,
    where the host RX sees the whole optical + M2C channel).
  """
  host_cfg = sim_cfg.host_channel
  seg = host_serdes.m2c_configs(sim_cfg)
  if sim_cfg.architecture == 'lro':
    driver_v = lro_linear_driver(opt_res.tia_output_voltage_v, sim_cfg, rng)
    # A Condor host RX brings its own FFE/DFE; the generic host RX uses the
    # LRO tap counts.
    condor = sim_cfg.host_serdes == 'condor'
    m2c = electrical_channel.receive_electrical_waveform(
        tx_waveform_v=driver_v,
        ref_symbols=ref_symbols,
        tx_bits=ref_bits,
        sim_cfg=sim_cfg,
        host_cfg=seg.rx,
        rng=rng,
        num_ffe_taps=None if condor else sim_cfg.lro.host_rx_ffe_taps,
        num_dfe_taps=None if condor else sim_cfg.lro.host_rx_dfe_taps,
        channel_cfg=seg.channel,
    )
    return m2c, m2c.ber

  if host_cfg.retimed_forwarding:
    htx_symbols = opt_res.orx_sliced_symbols
  else:
    htx_symbols = np.clip(opt_res.orx_equalized_symbols, -1.1, 1.1)
  m2c = electrical_channel.simulate_electrical_segment(
      tx_symbols=htx_symbols,
      tx_bits=opt_res.orx_rx_bits,
      sim_cfg=sim_cfg,
      host_cfg=seg.tx,
      rng=rng,
      rx_cfg=seg.rx,
      channel_cfg=seg.channel,
  )
  return m2c, opt_res.orx_ber + m2c.ber
