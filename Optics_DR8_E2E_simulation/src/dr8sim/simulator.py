"""End-to-end 1.6T-DR8 (8x200G PAM4) single-lane simulator, sweeps, and report.

Orchestrates all 8 physical and DSP stages from Host TX through O-band SMF-28
fiber to Host RX, and computes IEEE 802.3 TDECQ/TECQ, Receiver Sensitivity,
and Link Power Budget. Plots live in `plotting`; the 8-lane module wrapper
lives in `module`.
"""

from __future__ import annotations

import copy
import dataclasses
import math
from typing import Any, Dict, List, Sequence

import numpy as np

from dr8sim import config
from dr8sim import electrical_channel
from dr8sim import host_serdes
from dr8sim import metrology
from dr8sim import optical_channel
from dr8sim import return_path


@dataclasses.dataclass
class EndToEndSimulationResult:
  """Complete simulation results across all 8 stages of the 1.6T-DR8 link.

  Attributes:
    sim_cfg: Top-level simulation configuration used for the run.
    host_to_client_c2m: Stage 1 Host TX -> PCB Trace (C2M) -> Client RX result.
    optical_line: Stages 2-7 Line TX -> CW Laser -> SiPh MZM -> SMF-28 ->
      PIN PD/TIA -> Line RX ADC -> Media RX (ORX) DSP result.
    client_to_host_m2c: Stage 8 Client TX -> PCB Trace (M2C) -> Host RX result.
    tecq: 0 km Back-to-Back IEEE 802.3 TECQ measurement result.
    tdecq: IEEE 802.3 TDECQ at the configured fiber length.
    sensitivity_btb: Optional 0 km Receiver Sensitivity waterfall result.
    sensitivity_link: Optional Receiver Sensitivity waterfall result at the
      configured fiber length.
    link_budget: Optional Link Power Budget and Margin report.
    end_to_end_ser: Overall Symbol Error Ratio from Host TX to Host RX.
    end_to_end_ber: Overall Pre-FEC Bit Error Ratio from Host TX to Host RX.
  """

  sim_cfg: config.LinkSimulationConfig
  host_to_client_c2m: electrical_channel.ElectricalLinkResult
  optical_line: optical_channel.OpticalSubLinkResult
  client_to_host_m2c: electrical_channel.ElectricalLinkResult
  tecq: metrology.TdecqResult
  tdecq: metrology.TdecqResult
  sensitivity_btb: metrology.SensitivitySweepResult | None
  sensitivity_link: metrology.SensitivitySweepResult | None
  link_budget: metrology.LinkBudgetReport | None
  end_to_end_ser: float
  end_to_end_ber: float


def run_end_to_end_simulation(
    sim_cfg: config.LinkSimulationConfig | None = None,
    run_sensitivity_and_budget: bool = True,
) -> EndToEndSimulationResult:
  """Executes the complete 8-stage 1.6T-DR8 end-to-end simulation.

  Args:
    sim_cfg: Optional LinkSimulationConfig. Uses the default 1.6T-DR8 config
      if None.
    run_sensitivity_and_budget: If True, executes 0 km and link-length VOA
      sensitivity sweeps and computes the complete Link Power Budget.

  Returns:
    EndToEndSimulationResult with all waveforms, equalizers, and link metrics.
  """
  if sim_cfg is None:
    sim_cfg = config.LinkSimulationConfig()

  rng = np.random.default_rng(sim_cfg.random_seed)

  # Stage 1: Generate 200 Gbps PAM4 (106.25 GBaud) symbols at Host TX and
  # propagate through Host PCB trace (C2M) to Optics Client RX (HRX).
  tx_symbols, tx_level_idx, tx_bits = electrical_channel.generate_pam4_symbols(
      sim_cfg.num_symbols, rng
  )
  c2m = host_serdes.c2m_configs(sim_cfg)
  c2m_result = electrical_channel.simulate_electrical_segment(
      tx_symbols=tx_symbols,
      tx_bits=tx_bits,
      sim_cfg=sim_cfg,
      host_cfg=c2m.tx,
      rng=rng,
      rx_cfg=c2m.rx,
      channel_cfg=c2m.channel,
  )

  # Stage 2: Digital signal forwarding from Client RX (HRX) to Line TX (OTX).
  if sim_cfg.host_channel.retimed_forwarding:
    otx_input_symbols = c2m_result.sliced_symbols
  else:
    otx_input_symbols = np.clip(c2m_result.equalized_symbols, -1.1, 1.1)

  # Stages 3, 4, 5, 6, 7: CW Laser -> SiPh MZM -> SMF-28 Fiber ->
  # PIN PD + TIA -> Line RX ADC -> Media RX (ORX) FFE/DFE DSP.
  optical_result = optical_channel.simulate_optical_sublink(
      otx_input_symbols=otx_input_symbols,
      reference_tx_symbols=tx_symbols,
      sim_cfg=sim_cfg,
      rng=rng,
      additional_attenuation_db=0.0,
  )

  # Stage 8: receive direction to the Host RX SerDes. Retimed: ORX DSP ->
  # Client TX (HTX) -> M2C PCB. LRO: TIA -> linear driver -> M2C PCB, with the
  # host RX equalizing the whole optical + M2C channel.
  m2c_result, downstream_ber = return_path.simulate_receive_direction(
      opt_res=optical_result,
      ref_symbols=tx_symbols,
      ref_bits=tx_bits,
      sim_cfg=sim_cfg,
      rng=rng,
  )
  if sim_cfg.architecture == 'lro':
    downstream_ser = m2c_result.ser
  else:
    downstream_ser = optical_result.orx_ser + m2c_result.ser

  # Compute overall end-to-end Host TX -> Host RX SER and Pre-FEC BER
  _, rx_final_idx, rx_final_bits = electrical_channel.slice_pam4_symbols(
      m2c_result.equalized_symbols
  )
  trim_sym = 64
  trim_bits = 2 * trim_sym
  empirical_e2e_ser = float(
      np.mean(rx_final_idx[trim_sym:-trim_sym] != tx_level_idx[trim_sym:-trim_sym])
  )
  empirical_e2e_ber = float(
      np.mean(rx_final_bits[trim_bits:-trim_bits] != tx_bits[trim_bits:-trim_bits])
  )
  # Combine with analytical sub-link BER floor when zero errors are counted
  e2e_ser = max(empirical_e2e_ser, c2m_result.ser + downstream_ser)
  e2e_ber = max(empirical_e2e_ber, c2m_result.ber + downstream_ber)

  # Metrology: Calculate 0 km TECQ and link-length TDECQ
  tecq_result = metrology.calculate_tdecq(
      optical_power_mw=optical_result.tx_optical_power_mw,
      tx_symbols=tx_symbols,
      sim_cfg=sim_cfg,
      target_ser=sim_cfg.receiver.target_tdecq_ser,
  )
  tdecq_result = metrology.calculate_tdecq(
      optical_power_mw=optical_result.rx_optical_power_mw,
      tx_symbols=tx_symbols,
      sim_cfg=sim_cfg,
      target_ser=sim_cfg.receiver.target_tdecq_ser,
  )

  sens_btb = None
  sens_link = None
  budget_report = None
  if run_sensitivity_and_budget:
    # Use a faster symbol count (8192) if num_symbols > 8192 during multi-point
    # waterfall sweep to keep total runtime fast while preserving accuracy
    sweep_cfg_link = copy.deepcopy(sim_cfg)
    sweep_cfg_link.num_symbols = min(sim_cfg.num_symbols, 8192)
    sweep_cfg_btb = copy.deepcopy(sweep_cfg_link)
    sweep_cfg_btb.fiber.length_km = 0.0
    sweep_cfg_btb.fiber.total_channel_loss_db = None

    sens_btb = metrology.sweep_receiver_sensitivity(sweep_cfg_btb)
    sens_link = metrology.sweep_receiver_sensitivity(sweep_cfg_link)
    budget_report = metrology.compute_link_budget(
        sim_cfg=sim_cfg,
        opt_result=optical_result,
        tx_symbols=tx_symbols,
        sens_btb=sens_btb,
        sens_link=sens_link,
    )

  return EndToEndSimulationResult(
      sim_cfg=sim_cfg,
      host_to_client_c2m=c2m_result,
      optical_line=optical_result,
      client_to_host_m2c=m2c_result,
      tecq=tecq_result,
      tdecq=tdecq_result,
      sensitivity_btb=sens_btb,
      sensitivity_link=sens_link,
      link_budget=budget_report,
      end_to_end_ser=e2e_ser,
      end_to_end_ber=e2e_ber,
  )


def sweep_fiber_reach(
    sim_cfg: config.LinkSimulationConfig,
    lengths_km: List[float] | None = None,
) -> List[Dict[str, float]]:
  """Sweeps fiber length and records TDECQ, SNR, OMA, and BER per length.

  Args:
    sim_cfg: Base LinkSimulationConfig.
    lengths_km: List of fiber lengths (km) to evaluate. Defaults to
      [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0].

  Returns:
    List of dicts with 'length_km' plus every SWEEP_METRICS key.
  """
  if lengths_km is None:
    lengths_km = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
  rows = sweep_parameter(
      sim_cfg, 'fiber.length_km', [float(x) for x in lengths_km]
  )
  for row in rows:
    row['length_km'] = row.pop('value')
    row.pop('key')
  return rows


# Scalar metrics recorded per point by `sweep_parameter`.
SWEEP_METRICS = (
    'dispersion_ps_nm',
    'tx_oma_dbm',
    'tx_er_db',
    'rx_oma_dbm',
    'rx_pavg_dbm',
    'tecq_db',
    'tdecq_db',
    'rlm',
    'c2m_snr_db',
    'orx_snr_db',
    'orx_ber',
    'e2e_ber',
)


def _sweep_row(res: EndToEndSimulationResult) -> Dict[str, float]:
  """Extracts the SWEEP_METRICS scalars from one simulation result."""
  opt = res.optical_line
  return {
      'dispersion_ps_nm': opt.total_dispersion_ps_nm,
      'tx_oma_dbm': opt.tx_oma_outer_dbm,
      'tx_er_db': opt.tx_er_db,
      'rx_oma_dbm': opt.rx_oma_outer_dbm,
      'rx_pavg_dbm': opt.rx_avg_power_dbm,
      'tecq_db': res.tecq.tdecq_db,
      'tdecq_db': res.tdecq.tdecq_db,
      'rlm': res.tecq.rlm,
      'c2m_snr_db': res.host_to_client_c2m.post_eq_snr_db,
      'orx_snr_db': opt.orx_snr_db,
      'orx_ber': opt.orx_ber,
      'e2e_ber': res.end_to_end_ber,
  }


def sweep_parameter(
    sim_cfg: config.LinkSimulationConfig,
    key: str,
    values: Sequence[Any],
    max_symbols: int | None = 8192,
) -> List[Dict[str, Any]]:
  """Sweeps any config field (dotted key) and records link metrics per value.

  Args:
    sim_cfg: Base configuration; it is not modified.
    key: Dotted config key, e.g. 'laser.rin_db_hz' or 'fiber.length_km'.
    values: Values to assign to `key`, one simulation each.
    max_symbols: Caps num_symbols per point to keep sweeps fast. None keeps
      the base config's num_symbols.

  Returns:
    One dict per value: {'key': key, 'value': value, **SWEEP_METRICS}.
  """
  config.get_by_path(sim_cfg, key)  # Fail fast on a bad key.
  rows: List[Dict[str, Any]] = []
  for value in values:
    cfg_step = copy.deepcopy(sim_cfg)
    config.set_by_path(cfg_step, key, value)
    if max_symbols is not None:
      cfg_step.num_symbols = min(cfg_step.num_symbols, max_symbols)
    res = run_end_to_end_simulation(cfg_step, run_sensitivity_and_budget=False)
    rows.append({'key': key, 'value': value, **_sweep_row(res)})
  return rows


def _fmt_db(value: float, fmt: str = '+.2f') -> str:
  """Formats a dB value, showing N/A for NaN (e.g. unbracketed sensitivity)."""
  return 'N/A' if value is None or math.isnan(value) else format(value, fmt)


def format_simulation_report(result: EndToEndSimulationResult) -> str:
  """Formats a comprehensive engineering report of the 1.6T-DR8 simulation."""
  cfg = result.sim_cfg
  c2m = result.host_to_client_c2m
  opt = result.optical_line
  m2c = result.client_to_host_m2c
  bgt = result.link_budget
  km = f'{cfg.fiber.length_km:g} km'
  is_lro = cfg.architecture == 'lro'
  is_condor = cfg.host_serdes == 'condor'
  c2m_tx = host_serdes.c2m_configs(cfg).tx
  if is_lro:
    arch_text = 'LRO (TX retimed by DSP, RX linear to host SerDes)'
  else:
    arch_text = 'Retimed (DSP in both directions)'

  lines = [
      '=' * 80,
      ' 1.6T-DR8 (8 x 200 Gbps PAM4 @ 106.25 GBaud) END-TO-END SIMULATION REPORT',
      '=' * 80,
      f'Aggregate Capacity : {cfg.aggregate_bit_rate_tbps:.1f} Tbps '
      f'({cfg.num_lanes} lanes x {cfg.net_bit_rate_gbps_per_lane:.0f} Gbps/lane)',
      f'Signaling Format   : PAM4 @ {cfg.baud_rate_gbaud:.2f} GBaud '
      f'(Nyquist = {cfg.nyquist_freq_ghz:.3f} GHz, {cfg.samples_per_symbol} sps)',
      f'Simulated Symbols  : {cfg.num_symbols} PAM4 symbols '
      f'({2 * cfg.num_symbols} bits per lane)',
      f'Module Architecture: {arch_text}',
      f'Host SerDes        : '
      + ('Broadcom Condor 3nm (host TX and host RX)' if is_condor
         else 'generic (same model as module client SerDes)'),
      '-' * 80,
      '[Stage 1] Host TX'
      + (' (Broadcom Condor)' if is_condor else '')
      + ' -> Host PCB Trace (C2M) -> Optics Client RX (HRX)',
      *(
          [
              f'  Condor TX FFE Codes          : (pre3, pre2, pre1, main, '
              f'post1, post2) = {tuple(int(c) for c in cfg.condor.tx_ffe_codes)}',
              f'  Condor TX Amplitude / Swing  : amp = {cfg.condor.tx_amp_vppd:.2f} V '
              f'-> {c2m_tx.tx_vppd:.2f} Vppd, SNDR = {cfg.condor.tx_snr_db:.0f} dB, '
              f'RJ = {cfg.condor.tx_rj_rms_ps:.2f} ps, DJ = {cfg.condor.tx_dj_pp_ps:.2f} ps pp',
              f'  Phytile Package Loss         : {cfg.condor.package_loss_db_at_nyquist:.2f} dB '
              '@ Nyquist (assumed; added to host channel at both host ends)',
          ]
          if is_condor
          else [
              f'  Host TX Swing / FIR Taps     : {cfg.host_channel.tx_vppd:.2f} Vppd, '
              f'FIR = {tuple(cfg.host_channel.tx_fir_taps)}',
          ]
      ),
      f'  PCB Trace Length             : {cfg.host_channel.pcb_trace_length_mm:.1f} mm '
      f'({cfg.host_channel.pcb_trace_length_mm / 25.4:.2f} in)',
      f'  C2M Insertion Loss @ Nyquist : {c2m.pcb_loss_nyquist_db:.2f} dB '
      f'(@ {cfg.nyquist_freq_ghz:.3f} GHz)',
      f'  Client RX (HRX) Post-EQ SNR  : {c2m.post_eq_snr_db:.2f} dB '
      f'(FFE={cfg.host_channel.rx_ffe_taps}t, DFE={cfg.host_channel.rx_dfe_taps}t)',
      f'  Client RX (HRX) Pre-FEC BER  : {c2m.ber:.3e}',
      '-' * 80,
      '[Stages 2-4] Client RX -> Line TX (OTX) -> CW Laser -> SiPh MZM',
      f'  Forwarding Architecture      : '
      f'{"Retimed DSP" if cfg.host_channel.retimed_forwarding else "Linear / Unretimed"} '
      f'(Arcsin Pre-Distortion = {cfg.mzm.enable_arcsin_predistortion})',
      f'  Laser Nominal / Actual WL    : {cfg.laser.wavelength_nm:.2f} nm / '
      f'{opt.effective_wavelength_nm:.3f} nm '
      f'(dWL={cfg.laser.wavelength_error_nm:+.2f} nm, dF={cfg.laser.freq_offset_ghz:+.1f} GHz)',
      f'  Laser CW Power / RIN / LW    : {cfg.laser.cw_power_dbm:.2f} dBm, '
      f'RIN = {cfg.laser.rin_db_hz:.1f} dB/Hz, Linewidth = {cfg.laser.linewidth_mhz:.2f} MHz',
      f'  SiPh MZM Vpi / EO Bandwidth  : Vpi = {cfg.mzm.vpi_volts:.2f} V, '
      f'EO BW = {cfg.mzm.eo_bw_ghz:.1f} GHz, IL = {cfg.mzm.insertion_loss_db:.2f} dB',
      f'  Launched TX Average Power    : {opt.tx_avg_power_dbm:+.2f} dBm',
      f'  Launched TX Outer OMA        : {opt.tx_oma_outer_dbm:+.2f} dBm',
      f'  Launched TX Extinction Ratio : {opt.tx_er_db:.2f} dB',
      f'  Launched TX Eye Linearity    : R_LM = {result.tecq.rlm:.3f}',
      f'  Back-to-Back (0 km) TECQ     : {result.tecq.tdecq_db:.2f} dB '
      f'(Ref FFE C_eq = {result.tecq.noise_enhancement_factor:.3f})',
      '-' * 80,
      '[Stage 5] O-Band SMF-28 Fiber Propagation',
      f'  Fiber Length & Attenuation   : {cfg.fiber.length_km:.2f} km @ '
      f'{cfg.fiber.attenuation_db_per_km:.2f} dB/km '
      f'(Connectors = {cfg.fiber.connector_loss_db:.2f} dB, '
      f'Splice = {cfg.fiber.splice_and_margin_loss_db:.2f} dB)',
      f'  Total Optical Channel Loss   : {opt.total_fiber_loss_db:.2f} dB'
      + (
          ' (fixed by fiber.total_channel_loss_db)'
          if cfg.fiber.total_channel_loss_db is not None
          else ''
      ),
      f'  Chromatic Dispersion D(WL)   : {opt.chromatic_dispersion_ps_nm_km:+.3f} ps/(nm*km) '
      f'-> Cumulative CD = {opt.total_dispersion_ps_nm:+.3f} ps/nm',
      f'  TDECQ @ {km:<21}: {result.tdecq.tdecq_db:.2f} dB '
      f'(CD Eye Closure Penalty = {max(0.0, result.tdecq.tdecq_db - result.tecq.tdecq_db):+.2f} dB)',
      '-' * 80,
      (
          '[Stages 6-7] Optical Receiver (PIN PD + TIA); reference DSP RX '
          'shown for comparison (not in LRO path)'
          if is_lro
          else '[Stages 6-7] Optical Receiver (PIN PD + TIA) -> ADC -> '
          'Media RX (ORX) DSP'
      ),
      f'  Received Average Power       : {opt.rx_avg_power_dbm:+.2f} dBm',
      f'  Received Outer OMA           : {opt.rx_oma_outer_dbm:+.2f} dBm',
      f'  PD Responsivity / TIA Gain   : R = {cfg.receiver.responsivity_a_per_w:.2f} A/W, '
      f'Z_TIA = {cfg.receiver.tia_transimpedance_ohms:.0f} Ohms, '
      f'TIA BW = {cfg.receiver.tia_bw_ghz:.1f} GHz',
      f'  Noise Breakdown (RMS in BW)  : Thermal = {opt.thermal_noise_rms_ua:.2f} uA, '
      f'Shot = {opt.shot_noise_rms_ua:.2f} uA, RIN = {opt.rin_noise_rms_ua:.2f} uA',
      f'  {"Reference DSP RX SNR" if is_lro else "Media RX (ORX) Post-EQ SNR":<26}'
      f'   : {opt.orx_snr_db:.2f} dB '
      f'(FFE={cfg.receiver.orx_ffe_taps}t, DFE={cfg.receiver.orx_dfe_taps}t)',
      f'  {"Reference DSP RX BER" if is_lro else "Media RX (ORX) Pre-FEC BER":<26}'
      f'   : {opt.orx_ber:.3e} '
      f'(KP4 Target = {cfg.receiver.target_pre_fec_ber:.2e})',
      '-' * 80,
  ]
  if is_lro:
    lro = cfg.lro
    lines.extend([
        '[Stage 8] Linear RX: TIA -> Linear Driver -> Host PCB Trace (M2C) '
        '-> Host RX SerDes',
        f'  Linear Driver                : {lro.driver_output_vppd:.2f} Vppd '
        f'(AGC), BW = {lro.driver_bw_ghz:.1f} GHz, peaking = '
        f'{lro.driver_peaking_db:.1f} dB, noise = '
        f'{lro.driver_noise_mv_rms:.2f} mVrms',
        f'  M2C Insertion Loss @ Nyquist : {m2c.pcb_loss_nyquist_db:.2f} dB',
        f'  Host RX Post-EQ SNR          : {m2c.post_eq_snr_db:.2f} dB '
        + (
            '(Condor RX; equalizes optical + M2C)'
            if is_condor
            else f'(FFE={lro.host_rx_ffe_taps}t, DFE={lro.host_rx_dfe_taps}t; '
            'equalizes optical + M2C)'
        ),
        f'  LRO penalty vs DSP RX (SNR)  : '
        f'{opt.orx_snr_db - m2c.post_eq_snr_db:+.2f} dB',
    ])
  else:
    lines.extend([
        '[Stage 8] Client TX (HTX) -> Host PCB Trace (M2C) -> Host RX SerDes',
        f'  M2C Insertion Loss @ Nyquist : {m2c.pcb_loss_nyquist_db:.2f} dB',
        f'  Host RX Post-EQ SNR          : {m2c.post_eq_snr_db:.2f} dB',
    ])
  if is_condor:
    m2c_rx = host_serdes.m2c_configs(cfg).rx
    auto = '' if cfg.condor.rx_ctle_codes else ' (auto-adapted)'
    target = cfg.condor.host_link_ber_target
    lines.extend([
        f'  Condor Host RX               : PF1/PF2/PF3 = '
        f'{tuple(int(c) for c in m2c.ctle_codes)}{auto}, FFE = '
        f'{m2c_rx.rx_ffe_taps}t ({m2c_rx.rx_ffe_ref_tap} pre), '
        f'DFE = {m2c_rx.rx_dfe_taps}t, ADC ENOB = {m2c_rx.rx_adc_enob:g}',
        f'  Condor RX BER vs Broadcom AMI criterion ({target:.1e}): '
        f'{m2c.ber:.2e} -> {"PASS" if m2c.ber <= target else "FAIL"}',
    ])
  lines.extend([
      f'  Final End-to-End Pre-FEC BER : {result.end_to_end_ber:.3e} '
      f'(End-to-End SER = {result.end_to_end_ser:.3e})',
  ])

  if bgt is not None and result.sensitivity_link is not None:
    sens = result.sensitivity_link
    margin = bgt.net_link_margin_db
    if math.isnan(margin):
      verdict = 'UNKNOWN: sensitivity sweep did not reach target BER'
    else:
      verdict = 'PASS' if margin >= 0 else 'FAIL'
    lines.extend([
        '-' * 80,
        f'[Metrology & {km} Link Power Budget Summary]',
        f'  0 km BTB Receiver Sensitivity (OMA @ BER=2.4e-4) : '
        f'{_fmt_db(bgt.rx_sensitivity_oma_btb_dbm)} dBm',
        f'  {km:<5} ORX Receiver Sensitivity (OMA @ BER=2.4e-4): '
        f'{_fmt_db(bgt.rx_sensitivity_oma_link_dbm)} dBm '
        f'(P_avg = {_fmt_db(bgt.rx_sensitivity_pavg_link_dbm)} dBm)',
        f'  {km:<5} ORX Receiver Sensitivity (OMA @ BER=1.0e-3): '
        f'{_fmt_db(sens.sensitivity_oma_dbm_at_1e3)} dBm '
        f'(P_avg = {_fmt_db(sens.sensitivity_pavg_dbm_at_1e3)} dBm)',
        f'  {km:<5} E2E Host RX Sensitivity (OMA @ BER=2.4e-4) : '
        f'{_fmt_db(sens.e2e_sensitivity_oma_dbm_at_kp4)} dBm',
        f'  Total Available OMA Power Budget (TX_OMA - BTB)  : '
        f'{_fmt_db(bgt.total_power_budget_oma_db, ".2f")} dB',
        f'  - Fiber Attenuation ({bgt.fiber_length_km:.1f} km @ '
        f'{cfg.fiber.attenuation_db_per_km:.2f} dB/km)        : '
        f'{bgt.fiber_attenuation_db:.2f} dB',
        (
            '  - Connectors / Other (rest of fixed channel loss): '
            if cfg.fiber.total_channel_loss_db is not None
            else '  - MPO Connector Insertion Loss                   : '
        )
        + f'{bgt.connector_loss_db:.2f} dB',
        f'  - Fiber Splice & Aging Loss                      : '
        f'{bgt.splice_and_aging_loss_db:.2f} dB',
        f'  - Dispersion & Eye Closure Penalty Allocation    : '
        f'{_fmt_db(bgt.tdecq_allocation_db, ".2f")} dB',
        f'  - MPI & PMD/DGD Penalty Allocation               : '
        f'{bgt.mpi_and_dgd_penalty_db:.2f} dB',
        (
            '  - LRO Linear-RX Penalty (host RX vs DSP RX)      : '
            if is_lro
            else '  - Host M2C Concatenation Penalty                 : '
        )
        + f'{_fmt_db(bgt.host_m2c_concatenation_penalty_db, ".2f")} dB',
        f'  => NET UNALLOCATED LINK MARGIN @ {km:<16}: '
        f'{_fmt_db(margin)} dB ({verdict})',
    ])

  lines.append('=' * 80)
  return '\n'.join(lines)


def _json_float(value: Any) -> Any:
  """Converts NaN/inf to None so the summary is strict JSON."""
  if value is None:
    return None
  value = float(value)
  return value if math.isfinite(value) else None


def summarize_result(result: EndToEndSimulationResult) -> Dict[str, Any]:
  """Returns the headline scalar metrics of a run as a JSON-ready dict."""
  cfg = result.sim_cfg
  opt = result.optical_line
  bgt = result.link_budget
  summary = {
      'aggregate_tbps': cfg.aggregate_bit_rate_tbps,
      'baud_rate_gbaud': cfg.baud_rate_gbaud,
      'architecture': cfg.architecture,
      'host_serdes': cfg.host_serdes,
      'm2c_ber': result.client_to_host_m2c.ber,
      'fiber_length_km': cfg.fiber.length_km,
      'total_channel_loss_db': opt.total_fiber_loss_db,
      'effective_wavelength_nm': opt.effective_wavelength_nm,
      'c2m_pcb_loss_nyquist_db': result.host_to_client_c2m.pcb_loss_nyquist_db,
      'c2m_snr_db': result.host_to_client_c2m.post_eq_snr_db,
      'c2m_ber': result.host_to_client_c2m.ber,
      'tx_avg_power_dbm': opt.tx_avg_power_dbm,
      'tx_oma_outer_dbm': opt.tx_oma_outer_dbm,
      'tx_er_db': opt.tx_er_db,
      'tx_rlm': result.tecq.rlm,
      'tecq_db': result.tecq.tdecq_db,
      'tdecq_db': result.tdecq.tdecq_db,
      'dispersion_ps_nm': opt.total_dispersion_ps_nm,
      'rx_avg_power_dbm': opt.rx_avg_power_dbm,
      'rx_oma_outer_dbm': opt.rx_oma_outer_dbm,
      'orx_snr_db': opt.orx_snr_db,
      'orx_ber': opt.orx_ber,
      'm2c_snr_db': result.client_to_host_m2c.post_eq_snr_db,
      'end_to_end_ber': result.end_to_end_ber,
  }
  if bgt is not None:
    summary.update({
        'rx_sensitivity_oma_btb_dbm': bgt.rx_sensitivity_oma_btb_dbm,
        'rx_sensitivity_oma_link_dbm': bgt.rx_sensitivity_oma_link_dbm,
        'rx_sensitivity_pavg_link_dbm': bgt.rx_sensitivity_pavg_link_dbm,
        'total_power_budget_oma_db': bgt.total_power_budget_oma_db,
        'net_link_margin_db': bgt.net_link_margin_db,
    })
  return {
      k: v if isinstance(v, str) else _json_float(v)
      for k, v in summary.items()
  }
