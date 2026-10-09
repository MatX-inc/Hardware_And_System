"""Diagnostic plots for single-lane runs, DR8 module runs, and parameter sweeps.

Uses the non-interactive Agg backend so plots render without a display.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Sequence

import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt  # pylint: disable=g-import-not-at-top
import numpy as np

from dr8sim import electrical_channel
from dr8sim import simulator


def _plot_eye_diagram(
    ax: Any,
    waveform: np.ndarray,
    sps: int,
    title: str,
    ylabel: str,
    num_traces: int = 220,
    thresholds: np.ndarray | None = None,
    show_tdecq_slices: bool = False,
    best_phase: int | None = None,
) -> None:
  """Renders a 2-UI PAM4 eye diagram onto `ax`."""
  span = 2 * sps
  if best_phase is None:
    best_phase = sps // 2
  # Center the first eye around 0.5 UI and the second around 1.5 UI
  shift = (sps // 2) - best_phase
  shifted = np.roll(waveform, shift)
  total_segments = len(shifted) // span
  step = max(1, total_segments // num_traces)
  t_ui = np.linspace(0.0, 2.0, span + 1)

  for idx in range(2, min(total_segments - 2, 2 + num_traces * step), step):
    seg = shifted[idx * span : (idx + 1) * span + 1]
    if len(seg) == span + 1:
      ax.plot(t_ui, seg, color='#1f77b4', alpha=0.14, linewidth=0.7)

  if thresholds is not None:
    for th in thresholds:
      ax.axhline(
          th, color='#d62728', linestyle='--', linewidth=0.9, alpha=0.8
      )

  if show_tdecq_slices:
    for ui_center in (0.5, 1.5):
      ax.axvline(
          ui_center - 0.05,
          color='#2ca02c',
          linestyle=':',
          linewidth=1.1,
          alpha=0.85,
      )
      ax.axvline(
          ui_center + 0.05,
          color='#2ca02c',
          linestyle=':',
          linewidth=1.1,
          alpha=0.85,
      )

  ax.set_title(title, fontsize=10, fontweight='bold')
  ax.set_xlabel('Time (UI)', fontsize=9)
  ax.set_ylabel(ylabel, fontsize=9)
  ax.set_xlim(0.0, 2.0)
  ax.grid(True, linestyle=':', alpha=0.4)


def plot_dashboard(
    result: simulator.EndToEndSimulationResult,
    reach_sweep: List[Dict[str, float]],
    output_dir: str,
) -> Dict[str, str]:
  """Generates a 6-panel diagnostic dashboard for the 1.6T-DR8 simulation.

  Args:
    result: Result of `simulator.run_end_to_end_simulation`.
    reach_sweep: Output of `simulator.sweep_fiber_reach`.
    output_dir: Directory where PNG and PDF plots will be saved.

  Returns:
    Dictionary mapping artifact label to saved file path.
  """
  os.makedirs(output_dir, exist_ok=True)
  sps = result.sim_cfg.samples_per_symbol
  km = f'{result.sim_cfg.fiber.length_km:g} km'

  fig, axes = plt.subplots(2, 3, figsize=(18, 10.5), dpi=160)
  fig.suptitle(
      f'1.6T-DR8 {result.sim_cfg.architecture.upper()}'
      f'{" + Condor host" if result.sim_cfg.host_serdes == "condor" else ""} '
      '(8 x 200 Gbps PAM4 @ 106.25 GBaud) End-to-End Link Simulation '
      f'({km} O-Band SMF-28, WL = {result.optical_line.effective_wavelength_nm:.2f} nm)',
      fontsize=13,
      fontweight='bold',
      y=0.98,
  )

  # Panel (0, 0): Stage 1 Host-to-Client (C2M) CTLE Output Eye Diagram
  _plot_eye_diagram(
      ax=axes[0, 0],
      waveform=result.host_to_client_c2m.ctle_waveform_v * 1e3,
      sps=sps,
      title=(
          f'[Stage 1] C2M PCB ({result.sim_cfg.host_channel.pcb_trace_length_mm:.0f} mm, '
          f'IL={result.host_to_client_c2m.pcb_loss_nyquist_db:.1f} dB)\n'
          f'After CTLE -> HRX EQ SNR = {result.host_to_client_c2m.post_eq_snr_db:.1f} dB'
      ),
      ylabel='Differential Voltage (mV)',
      best_phase=result.host_to_client_c2m.optimal_sample_phase,
  )

  # Panel (0, 1): Stage 4 Optical TX (0 km BTB) Reference Equalized Eye
  _plot_eye_diagram(
      ax=axes[0, 1],
      waveform=result.tecq.ref_equalized_waveform_mw,
      sps=sps,
      title=(
          f'[Stages 3-4] SiPh MZM Optical TX (0 km BTB)\n'
          f'ER = {result.optical_line.tx_er_db:.2f} dB, '
          f'R_LM = {result.tecq.rlm:.3f}, TECQ = {result.tecq.tdecq_db:.2f} dB'
      ),
      ylabel='Optical Power (mW)',
      thresholds=result.tecq.thresholds_mw,
      show_tdecq_slices=True,
      best_phase=result.tecq.optimal_sample_phase,
  )

  # Panel (0, 2): Stage 5-6 dispersed optical eye after fiber (TDECQ Ref EQ)
  _plot_eye_diagram(
      ax=axes[0, 2],
      waveform=result.tdecq.ref_equalized_waveform_mw,
      sps=sps,
      title=(
          f'[Stages 5-6] {km} SMF-28 Optical RX '
          f'(CD = {result.optical_line.total_dispersion_ps_nm:+.2f} ps/nm)\n'
          f'TDECQ = {result.tdecq.tdecq_db:.2f} dB, '
          f'RX OMA = {result.optical_line.rx_oma_outer_dbm:+.2f} dBm'
      ),
      ylabel='Optical Power (mW)',
      thresholds=result.tdecq.thresholds_mw,
      show_tdecq_slices=True,
      best_phase=result.tdecq.optimal_sample_phase,
  )

  # Panel (1, 0): Stage 7-8 Equalized PAM4 Symbol Histograms (ORX & Host RX)
  ax_hist = axes[1, 0]
  bins = np.linspace(-1.45, 1.45, 140)
  ax_hist.hist(
      result.optical_line.orx_equalized_symbols[512:],
      bins=bins,
      density=True,
      alpha=0.55,
      color='#1f77b4',
      label=f'{"Reference DSP RX (not in LRO path)" if result.sim_cfg.architecture == "lro" else "Media RX"} (ORX {result.sim_cfg.receiver.orx_ffe_taps}t FFE+{result.sim_cfg.receiver.orx_dfe_taps}t DFE) SNR={result.optical_line.orx_snr_db:.1f} dB',
  )
  ax_hist.hist(
      result.client_to_host_m2c.equalized_symbols[512:],
      bins=bins,
      density=True,
      alpha=0.50,
      color='#ff7f0e',
      label=f'Final Host RX (After M2C PCB) SNR={result.client_to_host_m2c.post_eq_snr_db:.1f} dB',
  )
  for th in electrical_channel.PAM4_THRESHOLDS:
    ax_hist.axvline(th, color='#d62728', linestyle='--', linewidth=1.0)
  ax_hist.set_title(
      '[Stages 7-8] Equalized PAM4 Decision Histograms\n'
      f'ORX BER = {result.optical_line.orx_ber:.2e} | '
      f'Final Host RX BER = {result.end_to_end_ber:.2e}',
      fontsize=10,
      fontweight='bold',
  )
  ax_hist.set_xlabel('Normalized PAM4 Symbol Amplitude', fontsize=9)
  ax_hist.set_ylabel('Probability Density', fontsize=9)
  ax_hist.legend(loc='upper right', fontsize=8)
  ax_hist.grid(True, linestyle=':', alpha=0.4)

  # Panel (1, 1): Receiver Sensitivity Waterfall Curve (BER vs RX Outer OMA)
  ax_sens = axes[1, 1]
  if result.sensitivity_btb is not None and result.sensitivity_link is not None:
    oma_btb = [p.rx_oma_outer_dbm for p in result.sensitivity_btb.points]
    ber_btb = [max(p.orx_ber, 1e-10) for p in result.sensitivity_btb.points]
    oma_link = [p.rx_oma_outer_dbm for p in result.sensitivity_link.points]
    ber_link = [max(p.orx_ber, 1e-10) for p in result.sensitivity_link.points]
    ber_e2e = [max(p.host_rx_ber, 1e-10) for p in result.sensitivity_link.points]

    ax_sens.semilogy(
        oma_btb,
        ber_btb,
        'o-',
        color='#2ca02c',
        linewidth=1.8,
        label=f'0 km BTB ORX (Sens={result.sensitivity_btb.sensitivity_oma_dbm_at_kp4:+.2f} dBm)',
    )
    ax_sens.semilogy(
        oma_link,
        ber_link,
        's-',
        color='#1f77b4',
        linewidth=1.8,
        label=f'{km} SMF ORX (Sens={result.sensitivity_link.sensitivity_oma_dbm_at_kp4:+.2f} dBm)',
    )
    ax_sens.semilogy(
        oma_link,
        ber_e2e,
        '^--',
        color='#d62728',
        linewidth=1.5,
        label=f'{km} E2E Host RX (Sens={result.sensitivity_link.e2e_sensitivity_oma_dbm_at_kp4:+.2f} dBm)',
    )
    tgt = result.sim_cfg.receiver.target_pre_fec_ber
    ax_sens.axhline(
        tgt,
        color='black',
        linestyle='-.',
        linewidth=1.1,
        label=f'Pre-FEC BER target ({tgt:.1e})',
    )
    ax_sens.axhline(
        1.0e-3,
        color='gray',
        linestyle=':',
        linewidth=1.0,
        label='Concatenated FEC Threshold (1.0e-3)',
    )
  ax_sens.set_title(
      '[Metrology] Receiver Sensitivity Waterfall\n'
      'Pre-FEC BER vs. Received Outer OMA (dBm)',
      fontsize=10,
      fontweight='bold',
  )
  ax_sens.set_xlabel('Received Outer OMA (dBm)', fontsize=9)
  ax_sens.set_ylabel('Pre-FEC Bit Error Ratio (BER)', fontsize=9)
  ax_sens.set_ylim(1e-8, 5e-2)
  if result.sensitivity_btb is not None:
    ax_sens.legend(loc='lower left', fontsize=7.8)
  ax_sens.grid(True, which='both', linestyle=':', alpha=0.4)

  # Panel (1, 2): Fiber Reach Sweep TDECQ & ORX SNR
  ax_reach = axes[1, 2]
  lengths = [r['length_km'] for r in reach_sweep]
  tdecqs = [r['tdecq_db'] for r in reach_sweep]
  snrs = [r['orx_snr_db'] for r in reach_sweep]

  color_tdecq = '#d62728'
  color_snr = '#1f77b4'
  ax_reach.plot(
      lengths,
      tdecqs,
      'o-',
      color=color_tdecq,
      linewidth=2.0,
      label='TDECQ (dB)',
  )
  ax_reach.set_xlabel('SMF-28 Fiber Length (km)', fontsize=9)
  ax_reach.set_ylabel('IEEE 802.3 TDECQ (dB)', color=color_tdecq, fontsize=9)
  ax_reach.tick_params(axis='y', labelcolor=color_tdecq)
  ax_reach.set_ylim(0.0, max([3.5] + [t * 1.25 for t in tdecqs
                                       if t is not None and np.isfinite(t)]))
  ax_reach.grid(True, linestyle=':', alpha=0.4)

  ax_snr = ax_reach.twinx()
  ax_snr.plot(
      lengths,
      snrs,
      's--',
      color=color_snr,
      linewidth=1.8,
      label='Media RX Post-EQ SNR (dB)',
  )
  ax_snr.set_ylabel('ORX Post-EQ SNR (dB)', color=color_snr, fontsize=9)
  ax_snr.tick_params(axis='y', labelcolor=color_snr)

  margin_str = (
      f'Net {km} Margin = {result.link_budget.net_link_margin_db:+.2f} dB'
      if result.link_budget is not None
      else ''
  )
  ax_reach.set_title(
      f'[Link Budget] TDECQ & ORX SNR vs. Reach\n{margin_str}',
      fontsize=10,
      fontweight='bold',
  )

  fig.tight_layout(rect=[0.0, 0.0, 1.0, 0.95])
  png_path = os.path.join(output_dir, '1p6t_dr8_end_to_end_dashboard.png')
  pdf_path = os.path.join(output_dir, '1p6t_dr8_end_to_end_dashboard.pdf')
  fig.savefig(png_path, dpi=160)
  fig.savefig(pdf_path)
  plt.close(fig)

  return {'png': png_path, 'pdf': pdf_path}


def plot_module_summary(
    module_result: Any,
    output_dir: str,
) -> Dict[str, str]:
  """Plots per-lane TDECQ, ER, RX OMA, and BER for an 8-lane DR8 module run.

  Args:
    module_result: `module.ModuleResult`.
    output_dir: Directory where the PNG is saved.

  Returns:
    {'module_png': path}.
  """
  os.makedirs(output_dir, exist_ok=True)
  lanes = module_result.lanes
  spec = module_result.spec
  idx = np.array([lane.lane for lane in lanes])
  colors = ['#2ca02c' if lane.passed else '#d62728' for lane in lanes]

  fig, axes = plt.subplots(2, 2, figsize=(13, 8), dpi=140)
  fig.suptitle(
      f'1.6T-DR8 {module_result.architecture.upper()} module: {sum(lane.passed for lane in lanes)}/{len(lanes)} '
      f'lanes pass ({module_result.fiber_length_km:g} km SMF-28; '
      'green = pass, red = fail)',
      fontsize=13,
      fontweight='bold',
  )

  def _bars(ax, metric, title, ylabel, limit=None, limit_label=None,
            log=False):
    values = [lane.metrics[metric] for lane in lanes]
    if log:
      values = [max(v, 1e-12) for v in values]
      ax.set_yscale('log')
    ax.bar(idx, values, color=colors)
    if limit is not None:
      ax.axhline(limit, color='black', linestyle='--', linewidth=1.0,
                 label=limit_label)
      ax.legend(loc='best', fontsize=8)
    ax.set_title(title, fontsize=10, fontweight='bold')
    ax.set_xlabel('Lane', fontsize=9)
    ax.set_ylabel(ylabel, fontsize=9)
    ax.set_xticks(idx)
    ax.grid(True, which='both', axis='y', linestyle=':', alpha=0.4)

  _bars(axes[0, 0], 'tdecq_db', 'TDECQ per lane', 'TDECQ (dB)',
        spec.tdecq_max_db, f'max {spec.tdecq_max_db:g} dB')
  _bars(axes[0, 1], 'tx_er_db', 'Outer extinction ratio per lane', 'ER (dB)',
        spec.er_min_db, f'min {spec.er_min_db:g} dB')
  _bars(axes[1, 0], 'rx_oma_outer_dbm', 'Received outer OMA per lane',
        'OMA (dBm)')
  _bars(axes[1, 1], 'end_to_end_ber', 'End-to-end pre-FEC BER per lane',
        'BER', spec.pre_fec_ber_max, f'max {spec.pre_fec_ber_max:.1e}',
        log=True)

  fig.tight_layout(rect=[0.0, 0.0, 1.0, 0.94])
  path = os.path.join(output_dir, '1p6t_dr8_module_lanes.png')
  fig.savefig(path)
  plt.close(fig)
  return {'module_png': path}


def plot_sweep(
    rows: List[Dict[str, Any]],
    key: str,
    output_dir: str,
    metrics: Sequence[str] = (
        'tdecq_db', 'orx_snr_db', 'rx_oma_dbm', 'e2e_ber'
    ),
) -> Dict[str, str]:
  """Plots selected metrics against the swept parameter, one panel each.

  Args:
    rows: Output of `simulator.sweep_parameter`.
    key: Dotted config key that was swept (x axis label).
    output_dir: Directory where the PNG is saved.
    metrics: Metric names (keys of each row) to plot.

  Returns:
    {'sweep_png': path}.
  """
  os.makedirs(output_dir, exist_ok=True)
  x = [r['value'] for r in rows]
  n = len(metrics)
  fig, axes = plt.subplots(
      1, n, figsize=(4.2 * n, 3.8), dpi=140, squeeze=False
  )
  for ax, metric in zip(axes[0], metrics):
    y = [r[metric] for r in rows]
    if 'ber' in metric:
      ax.semilogy(x, [max(v, 1e-12) for v in y], 'o-', color='#1f77b4')
    else:
      ax.plot(x, y, 'o-', color='#1f77b4')
    ax.set_xlabel(key, fontsize=9)
    ax.set_title(metric, fontsize=10, fontweight='bold')
    ax.grid(True, which='both', linestyle=':', alpha=0.4)
  fig.suptitle(f'Sweep of {key}', fontsize=12, fontweight='bold')
  fig.tight_layout(rect=[0.0, 0.0, 1.0, 0.92])
  path = os.path.join(output_dir, f'sweep_{key.replace(".", "_")}.png')
  fig.savefig(path)
  plt.close(fig)
  return {'sweep_png': path}


def _psd_db(waveform: np.ndarray, fs: float, sps: int):
  """Welch PSD in dB re 1 V^2/GHz, returned as (freq_ghz, psd_db)."""
  from scipy import signal as sp_signal  # pylint: disable=g-import-not-at-top

  f, p = sp_signal.welch(waveform, fs=fs, nperseg=min(len(waveform), 512 * sps))
  return f / 1e9, 10.0 * np.log10(np.maximum(p * 1e9, 1e-30))


def _ctle_response_db(rx_cfg: Any, ctle_codes, freqs_hz: np.ndarray,
                      fs: float) -> np.ndarray:
  """Magnitude (dB) of the receiver's CTLE at `freqs_hz`."""
  if rx_cfg.rx_ctle_stage_zeros_ghz:
    h = electrical_channel.multistage_ctle_response(
        freqs_hz, ctle_codes, rx_cfg.rx_ctle_stage_max_codes,
        rx_cfg.rx_ctle_stage_zeros_ghz, rx_cfg.rx_ctle_stage_max_boost_db)
    return 20.0 * np.log10(np.abs(h))
  n = 1 << 16
  impulse = np.zeros(n)
  impulse[0] = 1.0
  h = np.fft.rfft(electrical_channel.apply_ctle(
      impulse, fs, rx_cfg.ctle_dc_gain_db, rx_cfg.ctle_peaking_gain_db,
      rx_cfg.ctle_zero_ghz, rx_cfg.ctle_pole1_ghz, rx_cfg.ctle_pole2_ghz))
  f = np.fft.rfftfreq(n, 1.0 / fs)
  return np.interp(freqs_hz, f, 20.0 * np.log10(np.abs(h) + 1e-30))


def plot_c2m_stages(
    sim_cfg: Any,
    segment: Any,
    output_dir: str,
    filename: str = 'c2m_stages.png',
    title: str = '',
) -> Dict[str, str]:
  """Eye diagram and spectrum at each key step of an electrical C2M segment.

  Rows: host TX output, after host channel, RX input (+noise), after CTLE,
  ADC samples vs FFE+DFE output (symbol-spaced histograms), and the channel /
  CTLE / FFE frequency responses.

  Args:
    sim_cfg: LinkSimulationConfig of the run.
    segment: `electrical_channel.ElectricalLinkResult` (e.g. the C2M result).
    output_dir: Directory where the PNG is saved.
    filename: PNG file name.
    title: Optional title suffix.

  Returns:
    {'c2m_png': path}.
  """
  from dr8sim import host_serdes  # pylint: disable=g-import-not-at-top

  os.makedirs(output_dir, exist_ok=True)
  sps = sim_cfg.samples_per_symbol
  fs = sim_cfg.sample_rate_hz
  nyq = sim_cfg.nyquist_freq_ghz
  ref = segment.tx_symbols
  segs = host_serdes.c2m_configs(sim_cfg)

  stages = [
      ('1. Host TX output (FFE, DAC, BW, jitter)', segment.tx_waveform_v),
      ('2. After host channel (PCB + package)', segment.pcb_rx_waveform_v),
      ('3. RX input (+ RX noise' + (', AFE' if segs.rx.rx_afe_bw_ghz else '')
       + ')', segment.rx_input_waveform_v),
      ('4. After RX CTLE', segment.ctle_waveform_v),
  ]
  fig, axes = plt.subplots(len(stages) + 2, 2, figsize=(15, 4.0 * (len(stages) + 2)),
                           dpi=120, gridspec_kw={'width_ratios': [1, 1.25]})
  fig.suptitle(
      f'C2M signal along the chain{(" - " + title) if title else ""}\n'
      f'IL @ Nyquist {segment.pcb_loss_nyquist_db:.2f} dB, post-EQ SNR '
      f'{segment.post_eq_snr_db:.2f} dB, BER {segment.ber:.1e}',
      fontsize=13, fontweight='bold', y=0.995)

  for row, (name, wave) in enumerate(stages):
    if wave is None:
      continue
    phase, lag, pol = electrical_channel.find_optimal_sampling_phase(
        wave, ref, sps)
    _plot_eye_diagram(axes[row, 0], pol * wave * 1e3, sps, name, 'mV',
                      num_traces=250, best_phase=phase)
    f_ghz, psd = _psd_db(wave, fs, sps)
    ax = axes[row, 1]
    ax.plot(f_ghz, psd, color='#1f77b4', linewidth=1.0)
    ax.axvline(nyq, color='#d62728', linestyle='--', linewidth=1.0,
               label=f'Nyquist {nyq:.1f} GHz')
    ax.axvline(2 * nyq, color='gray', linestyle=':', linewidth=1.0,
               label=f'Baud {2 * nyq:.1f} GHz')
    ax.set_xlim(0, 160)
    top = float(np.max(psd[(f_ghz > 0.5) & (f_ghz < 160)]))
    ax.set_ylim(top - 70, top + 5)
    ax.set_title(name + ' - power spectral density', fontsize=10,
                 fontweight='bold')
    ax.set_xlabel('Frequency (GHz)', fontsize=9)
    ax.set_ylabel('PSD (dB re 1 V^2/GHz)', fontsize=9)
    ax.grid(True, linestyle=':', alpha=0.4)
    ax.legend(loc='upper right', fontsize=8)

  # Symbol-spaced stages: ADC samples (before FFE) vs equalized output.
  row = len(stages)
  ax = axes[row, 0]
  adc = segment.adc_samples - np.mean(segment.adc_samples)
  adc = adc * np.std(ref) / max(np.std(adc), 1e-15)
  bins = np.linspace(-1.6, 1.6, 160)
  ax.hist(adc[200:], bins=bins, density=True, alpha=0.5, color='#ff7f0e',
          label='5. ADC samples (before FFE), normalized')
  ax.hist(segment.equalized_symbols[200:], bins=bins, density=True,
          alpha=0.6, color='#1f77b4', label='6. After FFE + DFE (slicer input)')
  for th in electrical_channel.PAM4_THRESHOLDS:
    ax.axvline(th, color='#d62728', linestyle='--', linewidth=0.9)
  ax.set_title('5-6. Symbol-spaced samples: before vs after FFE + DFE',
               fontsize=10, fontweight='bold')
  ax.set_xlabel('Normalized amplitude', fontsize=9)
  ax.set_ylabel('Probability density', fontsize=9)
  ax.legend(loc='upper center', fontsize=8)
  ax.grid(True, linestyle=':', alpha=0.4)

  ax = axes[row, 1]
  taps = np.asarray(segment.ffe_taps)
  ax.stem(np.arange(len(taps)), taps, basefmt=' ', label='FFE taps')
  dfe = np.asarray(segment.dfe_taps)
  if len(dfe):
    ref_tap = int(np.argmax(np.abs(taps)))
    ax.stem(ref_tap + 1 + np.arange(len(dfe)), -dfe, linefmt='C3-',
            markerfmt='C3s', basefmt=' ', label='DFE taps (feedback, sign as applied)')
  ax.axhline(0, color='black', linewidth=0.6)
  ax.set_title('6. Converged FFE / DFE taps', fontsize=10, fontweight='bold')
  ax.set_xlabel('Tap index (T-spaced)', fontsize=9)
  ax.legend(loc='upper right', fontsize=8)
  ax.grid(True, linestyle=':', alpha=0.4)

  # Frequency responses.
  row += 1
  ax = axes[row, 1]
  f_hz = np.linspace(0, 160e9, 1601)
  ch = segs.channel
  inch = ch.pcb_trace_length_mm / 25.4
  fg = f_hz / 1e9
  il = (ch.pcb_skin_loss_db_per_inch_sqrt_ghz * inch * np.sqrt(fg)
        + ch.pcb_dielectric_loss_db_per_inch_ghz * inch * fg
        + ch.package_connector_loss_db_at_nyquist * np.sqrt(fg / nyq))
  ctle_db = _ctle_response_db(segs.rx, segment.ctle_codes, f_hz, fs)
  ctle_db = ctle_db - ctle_db[0]
  ax.plot(fg, -il, label='Host channel (−IL)', color='#1f77b4')
  ax.plot(fg, ctle_db, label='RX CTLE (rel. DC)', color='#2ca02c')
  ax.plot(fg, ctle_db - il, label='Channel × CTLE', color='#9467bd',
          linewidth=2)
  fb = 2 * nyq * 1e9
  ffe_h = np.abs(np.exp(-2j * np.pi * np.outer(f_hz, np.arange(len(taps))) / fb)
                 @ taps)
  ffe_db = 20 * np.log10(np.maximum(ffe_h, 1e-9))
  ax.plot(fg, ffe_db - ffe_db[0], label='FFE (rel. DC, periodic in fb)',
          color='#ff7f0e', linestyle='--')
  ax.axvline(nyq, color='#d62728', linestyle='--', linewidth=1.0)
  ax.set_xlim(0, 160)
  ax.set_ylim(-60, 25)
  ax.set_title('Frequency responses', fontsize=10, fontweight='bold')
  ax.set_xlabel('Frequency (GHz)', fontsize=9)
  ax.set_ylabel('dB', fontsize=9)
  ax.legend(loc='lower left', fontsize=8)
  ax.grid(True, linestyle=':', alpha=0.4)

  ax = axes[row, 0]
  ax.axis('off')
  tx = segs.tx
  ax.text(0.0, 1.0, '\n'.join([
      'Settings',
      f'Host TX: {tx.tx_vppd:.2f} Vppd, FFE {tuple(round(t, 3) for t in tx.tx_fir_taps)}',
      f'   DAC {tx.tx_dac_bits} bit, BW {tx.tx_bw_ghz:g} GHz, RJ {tx.tx_rj_rms_ps:g} ps'
      + (f', DJ {tx.tx_dj_pp_ps:g} ps' if tx.tx_dj_pp_ps else '')
      + (f', SNDR {tx.tx_snr_db:g} dB' if tx.tx_snr_db else ''),
      f'Channel: {ch.pcb_trace_length_mm:g} mm trace '
      f'({ch.pcb_skin_loss_db_per_inch_sqrt_ghz * np.sqrt(nyq) + ch.pcb_dielectric_loss_db_per_inch_ghz * nyq:.2f} dB/in @ Nyq)'
      f' + {ch.package_connector_loss_db_at_nyquist:g} dB pkg/conn',
      f'RX: noise {segs.rx.rx_noise_psd_mv_per_sqrt_ghz:g} mV/rtGHz, '
      f'ENOB {segs.rx.rx_adc_enob:g}, FFE {len(taps)}t, DFE {len(dfe)}t',
      f'CDR phase {segment.optimal_sample_phase}/{sps}',
      '',
      'Eyes: 250 traces, 2 UI, centered on each stage\'s best phase.',
      'PSD: Welch estimate of the oversampled waveform.',
  ]), va='top', family='monospace', fontsize=9)

  fig.tight_layout(rect=[0, 0, 1, 0.975])
  path = os.path.join(output_dir, filename)
  fig.savefig(path)
  plt.close(fig)
  return {'c2m_png': path}


def _draw_stage_row(ax_eye, ax_psd, name, wave, ref, sps, fs, nyq, unit,
                    scale, ac_couple=False):
  """Eye diagram (left) and PSD (right) for one waveform stage."""
  phase, _, pol = electrical_channel.find_optimal_sampling_phase(wave, ref, sps)
  shown = pol * wave * scale
  _plot_eye_diagram(ax_eye, shown, sps, name, unit, num_traces=250,
                    best_phase=phase)
  psd_in = wave - np.mean(wave) if ac_couple else wave
  f_ghz, psd = _psd_db(psd_in, fs, sps)
  ax_psd.plot(f_ghz, psd, color='#1f77b4', linewidth=1.0)
  ax_psd.axvline(nyq, color='#d62728', linestyle='--', linewidth=1.0,
                 label=f'Nyquist {nyq:.1f} GHz')
  ax_psd.axvline(2 * nyq, color='gray', linestyle=':', linewidth=1.0,
                 label=f'Baud {2 * nyq:.1f} GHz')
  ax_psd.set_xlim(0, 160)
  band = (f_ghz > 0.5) & (f_ghz < 160)
  top = float(np.max(psd[band]))
  ax_psd.set_ylim(top - 70, top + 5)
  ax_psd.set_title(name + ' - PSD', fontsize=10, fontweight='bold')
  ax_psd.set_xlabel('Frequency (GHz)', fontsize=9)
  ax_psd.set_ylabel('PSD (dB re 1 unit^2/GHz)', fontsize=9)
  ax_psd.grid(True, linestyle=':', alpha=0.4)
  ax_psd.legend(loc='upper right', fontsize=8)


def _bessel_db(f_hz: np.ndarray, bw_ghz: float, order: int = 4) -> np.ndarray:
  """Magnitude (dB) of an analog Bessel-Thomson lowpass with 3-dB `bw_ghz`."""
  from scipy import signal as sp_signal  # pylint: disable=g-import-not-at-top

  b, a = sp_signal.bessel(order, 1.0, btype='low', analog=True, norm='mag')
  s = 1j * f_hz / (bw_ghz * 1e9)
  return 20.0 * np.log10(np.abs(np.polyval(b, s) / np.polyval(a, s)) + 1e-30)


def _fir_db(f_hz: np.ndarray, taps, baud_hz: float) -> np.ndarray:
  """Magnitude (dB, relative to DC) of a T-spaced FIR."""
  taps = np.asarray(taps, dtype=float)
  h = np.abs(np.exp(-2j * np.pi * np.outer(f_hz, np.arange(len(taps)))
                    / baud_hz) @ taps)
  return 20.0 * np.log10(np.maximum(h, 1e-9)) - 20.0 * np.log10(
      max(abs(np.sum(taps)), 1e-9))


def _composite_bw(*bws: float) -> float:
  return 1.0 / np.sqrt(sum((1.0 / max(b, 1.0)) ** 2 for b in bws))


def plot_optical_and_return_stages(
    result: simulator.EndToEndSimulationResult,
    output_dir: str,
    title: str = '',
) -> Dict[str, str]:
  """Stage-by-stage eyes and spectra along the optical path and back to host.

  Part A (optical_stages.png): line-TX drive -> MZM optical output -> after
  fiber -> TIA output, with optical-path frequency responses.
  Part B (return_stages.png): module output driver (LRO linear driver or
  retimed client TX) -> after M2C channel -> host RX input -> after host CTLE,
  then sampled values before/after host FFE+DFE and the electrical responses.

  Returns:
    {'optical_png': path, 'return_png': path}.
  """
  from dr8sim import host_serdes  # pylint: disable=g-import-not-at-top

  os.makedirs(output_dir, exist_ok=True)
  cfg = result.sim_cfg
  opt = result.optical_line
  m2c = result.client_to_host_m2c
  sps = cfg.samples_per_symbol
  fs = cfg.sample_rate_hz
  nyq = cfg.nyquist_freq_ghz
  baud_hz = 2 * nyq * 1e9
  ref = result.host_to_client_c2m.tx_symbols
  is_lro = cfg.architecture == 'lro'
  arch = 'LRO' if is_lro else 'Retimed'
  head = (f'{arch}{" + Condor host" if cfg.host_serdes == "condor" else ""}, '
          f'{cfg.fiber.length_km:g} km, {opt.total_fiber_loss_db:.1f} dB channel'
          f'{(" - " + title) if title else ""}')
  f_hz = np.linspace(1e8, 160e9, 1600)
  fg = f_hz / 1e9
  paths = {}

  # ---------------- Part A: optical path ----------------
  stages = [
      ('A1. Line-TX drive (FIR, predistortion, DAC, driver+EO BW)',
       opt.otx_drive_waveform_v, 'V', 1.0, False),
      ('A2. MZM optical output power', opt.tx_optical_power_mw, 'mW', 1.0,
       True),
      (f'A3. After {cfg.fiber.length_km:g} km fiber + channel loss',
       opt.rx_optical_power_mw, 'mW', 1.0, True),
      ('A4. TIA output (PD + noise + TIA BW + overload)',
       opt.tia_output_voltage_v, 'mV', 1e3, False),
  ]
  fig, axes = plt.subplots(len(stages) + 1, 2, figsize=(15, 4.0 * (len(stages) + 1)),
                           dpi=120, gridspec_kw={'width_ratios': [1, 1.25]})
  fig.suptitle(
      f'Optical path signal along the chain - {head}\n'
      f'TX OMA {opt.tx_oma_outer_dbm:+.2f} dBm, ER {opt.tx_er_db:.2f} dB, '
      f'TECQ {result.tecq.tdecq_db:.2f} dB | RX OMA {opt.rx_oma_outer_dbm:+.2f} dBm, '
      f'TDECQ {result.tdecq.tdecq_db:.2f} dB | ref DSP RX SNR {opt.orx_snr_db:.2f} dB',
      fontsize=12, fontweight='bold', y=0.997)
  for row, (name, wave, unit, scale, ac) in enumerate(stages):
    _draw_stage_row(axes[row, 0], axes[row, 1], name, wave, ref, sps, fs, nyq,
                    unit, scale, ac_couple=ac)

  row = len(stages)
  ax = axes[row, 1]
  mzm = cfg.mzm
  rx = cfg.receiver
  tx_bw = _composite_bw(mzm.driver_bw_ghz, mzm.eo_bw_ghz)
  rx_bw = _composite_bw(rx.pd_bw_ghz, rx.tia_bw_ghz)
  lam_m = opt.effective_wavelength_nm * 1e-9
  d_s_per_m = opt.total_dispersion_ps_nm * 1e-12 / 1e-9
  fade = np.abs(np.cos(np.pi * lam_m ** 2 * d_s_per_m * f_hz ** 2 / 299792458.0))
  ax.plot(fg, _fir_db(f_hz, mzm.line_tx_fir_taps, baud_hz),
          label='Line-TX FIR (rel. DC)', color='#ff7f0e', linestyle='--')
  ax.plot(fg, _bessel_db(f_hz, tx_bw),
          label=f'Driver + MZM EO ({tx_bw:.1f} GHz)', color='#1f77b4')
  ax.plot(fg, 20 * np.log10(np.maximum(fade, 1e-6)),
          label=f'Fiber CD power fading ({opt.total_dispersion_ps_nm:+.2f} ps/nm)',
          color='#8c564b')
  ax.plot(fg, _bessel_db(f_hz, rx_bw),
          label=f'PD + TIA ({rx_bw:.1f} GHz)', color='#2ca02c')
  ax.plot(fg, _bessel_db(f_hz, nyq), label='TDECQ reference Rx (Bessel, fb/2)',
          color='gray', linestyle=':')
  ax.plot(fg, _bessel_db(f_hz, tx_bw) + _bessel_db(f_hz, rx_bw)
          + 20 * np.log10(np.maximum(fade, 1e-6)),
          label='TX BW x fiber x RX BW', color='#9467bd', linewidth=2)
  ax.axvline(nyq, color='#d62728', linestyle='--', linewidth=1.0)
  ax.set_xlim(0, 160)
  ax.set_ylim(-40, 10)
  ax.set_title('Optical-path frequency responses (small-signal)', fontsize=10,
               fontweight='bold')
  ax.set_xlabel('Frequency (GHz)', fontsize=9)
  ax.set_ylabel('dB', fontsize=9)
  ax.legend(loc='lower left', fontsize=8)
  ax.grid(True, linestyle=':', alpha=0.4)
  ax = axes[row, 0]
  ax.axis('off')
  ax.text(0.0, 1.0, '\n'.join([
      'Settings (optical path)',
      f'Line TX FIR {tuple(mzm.line_tx_fir_taps)}, DAC ENOB {mzm.line_tx_dac_enob:g}, '
      f'arcsin predist {mzm.enable_arcsin_predistortion}',
      f'Driver {mzm.driver_bw_ghz:g} GHz, MZM EO {mzm.eo_bw_ghz:g} GHz, Vpi {mzm.vpi_volts:g} V, '
      f'target ER {mzm.target_outer_er_db:g} dB',
      f'Laser {cfg.laser.cw_power_dbm:g} dBm, RIN {cfg.effective_laser_rin_db_hz:.1f} dB/Hz'
      + (f' (RIN_OMA {cfg.rin_oma_db_hz:.1f})' if cfg.rin_oma_db_hz is not None else '')
      + ', '
      f'lambda {opt.effective_wavelength_nm:.2f} nm',
      f'Fiber {cfg.fiber.length_km:g} km, D {opt.chromatic_dispersion_ps_nm_km:+.3f} ps/nm/km, '
      f'loss {opt.total_fiber_loss_db:.2f} dB',
      f'PD {rx.responsivity_a_per_w:g} A/W, {rx.pd_bw_ghz:g} GHz; TIA {rx.tia_bw_ghz:g} GHz, '
      f'{rx.tia_transimpedance_ohms:g} ohm, IRND {rx.tia_irnd_pa_per_sqrt_hz:g} pA/rtHz',
      f'Noise @ TIA input (RMS): thermal {opt.thermal_noise_rms_ua:.2f} uA, '
      f'shot {opt.shot_noise_rms_ua:.2f} uA, RIN {opt.rin_noise_rms_ua:.2f} uA',
      '',
      'Optical PSDs are of the AC part of the power waveform.',
      'CD fading curve ignores chirp (alpha_H) and is only a guide.',
  ]), va='top', family='monospace', fontsize=9)
  fig.tight_layout(rect=[0, 0, 1, 0.975])
  paths['optical_png'] = os.path.join(output_dir, 'optical_stages.png')
  fig.savefig(paths['optical_png'])
  plt.close(fig)

  # ---------------- Part B: receive direction to host ----------------
  segs = host_serdes.m2c_configs(cfg)
  drv_name = ('B1. LRO linear driver output (peaking, AGC, BW, noise, sat.)'
              if is_lro else 'B1. Module client TX output (retimed)')
  stages = [
      (drv_name, m2c.tx_waveform_v),
      ('B2. After M2C channel (module + connector + host PCB + pkg)',
       m2c.pcb_rx_waveform_v),
      ('B3. Host RX input (+ RX noise'
       + (', AFE' if segs.rx.rx_afe_bw_ghz else '') + ')',
       m2c.rx_input_waveform_v),
      ('B4. After host RX CTLE' + (f' (PF codes {tuple(int(c) for c in m2c.ctle_codes)})'
                                   if m2c.ctle_codes else ''),
       m2c.ctle_waveform_v),
  ]
  fig, axes = plt.subplots(len(stages) + 2, 2, figsize=(15, 4.0 * (len(stages) + 2)),
                           dpi=120, gridspec_kw={'width_ratios': [1, 1.25]})
  fig.suptitle(
      f'Receive direction to host - {head}\n'
      f'M2C IL @ Nyquist {m2c.pcb_loss_nyquist_db:.2f} dB, host RX SNR '
      f'{m2c.post_eq_snr_db:.2f} dB, host RX BER {m2c.ber:.1e} '
      f'(ref DSP RX SNR {opt.orx_snr_db:.2f} dB)',
      fontsize=12, fontweight='bold', y=0.997)
  for row, (name, wave) in enumerate(stages):
    if wave is not None:
      _draw_stage_row(axes[row, 0], axes[row, 1], name, wave, ref, sps, fs,
                      nyq, 'mV', 1e3)

  row = len(stages)
  ax = axes[row, 0]
  bins = np.linspace(-1.6, 1.6, 160)
  adc = m2c.adc_samples - np.mean(m2c.adc_samples)
  adc = adc * np.std(ref) / max(np.std(adc), 1e-15)
  ax.hist(adc[200:], bins=bins, density=True, alpha=0.45, color='#ff7f0e',
          label='B5. Host ADC samples (before FFE), normalized')
  ax.hist(m2c.equalized_symbols[200:], bins=bins, density=True, alpha=0.6,
          color='#1f77b4', label='B6. Host FFE + DFE output (slicer input)')
  ax.hist(opt.orx_equalized_symbols[200:], bins=bins, density=True,
          histtype='step', color='black', linewidth=1.0,
          label='Module DSP RX output' + (' (reference only)' if is_lro else ''))
  for th in electrical_channel.PAM4_THRESHOLDS:
    ax.axvline(th, color='#d62728', linestyle='--', linewidth=0.9)
  ax.set_title('B5-B6. Symbol-spaced samples at the host RX', fontsize=10,
               fontweight='bold')
  ax.set_xlabel('Normalized amplitude', fontsize=9)
  ax.set_ylabel('Probability density', fontsize=9)
  ax.legend(loc='upper center', fontsize=7.5)
  ax.grid(True, linestyle=':', alpha=0.4)

  ax = axes[row, 1]
  taps = np.asarray(m2c.ffe_taps)
  ax.stem(np.arange(len(taps)), taps, basefmt=' ', label='Host FFE taps')
  dfe = np.asarray(m2c.dfe_taps)
  if len(dfe):
    ref_tap = int(np.argmax(np.abs(taps)))
    ax.stem(ref_tap + 1 + np.arange(len(dfe)), -dfe, linefmt='C3-',
            markerfmt='C3s', basefmt=' ', label='Host DFE taps')
  ax.axhline(0, color='black', linewidth=0.6)
  ax.set_title('B6. Converged host FFE / DFE taps', fontsize=10,
               fontweight='bold')
  ax.set_xlabel('Tap index (T-spaced)', fontsize=9)
  ax.legend(loc='upper right', fontsize=8)
  ax.grid(True, linestyle=':', alpha=0.4)

  row += 1
  ax = axes[row, 1]
  ch = segs.channel
  inch = ch.pcb_trace_length_mm / 25.4
  il = (ch.pcb_skin_loss_db_per_inch_sqrt_ghz * inch * np.sqrt(fg)
        + ch.pcb_dielectric_loss_db_per_inch_ghz * inch * fg
        + ch.package_connector_loss_db_at_nyquist * np.sqrt(fg / nyq))
  ctle_db = _ctle_response_db(segs.rx, m2c.ctle_codes, f_hz, fs)
  ctle_db = ctle_db - ctle_db[0]
  ffe_db = _fir_db(f_hz, taps, baud_hz)
  optical_db = (_bessel_db(f_hz, tx_bw) + _bessel_db(f_hz, rx_bw)
                + 20 * np.log10(np.maximum(fade, 1e-6)))
  # Retimed: the module DSP removes the optical channel; the host only sees
  # the M2C trace. LRO: the host sees optical + driver + trace.
  total = -il + ctle_db
  if is_lro:
    lro = cfg.lro
    peak = 20 * np.log10(np.abs(electrical_channel.multistage_ctle_response(
        f_hz, (1.0,), (1.0,), (lro.driver_peaking_zero_ghz,),
        (lro.driver_peaking_db,))))
    drv = peak + _bessel_db(f_hz, lro.driver_bw_ghz)
    ax.plot(fg, optical_db, label='Optical path (TX BW x fiber x PD/TIA)',
            color='#8c564b')
    ax.plot(fg, drv, label=f'LRO driver (+{lro.driver_peaking_db:g} dB peak, '
            f'{lro.driver_bw_ghz:g} GHz)', color='#17becf')
    total = total + optical_db + drv
  ax.plot(fg, -il, label='M2C channel (-IL)', color='#1f77b4')
  ax.plot(fg, ctle_db, label='Host CTLE (rel. DC)', color='#2ca02c')
  ax.plot(fg, total, label=('Optical x driver x M2C x CTLE' if is_lro
                            else 'M2C x CTLE'), color='#9467bd', linewidth=2)
  ax.plot(fg, ffe_db, label='Host FFE (rel. DC, periodic in fb)',
          color='#ff7f0e', linestyle='--')
  ax.axvline(nyq, color='#d62728', linestyle='--', linewidth=1.0)
  ax.set_xlim(0, 160)
  ax.set_ylim(-60, 25)
  ax.set_title('What the host RX must equalize (small-signal)', fontsize=10,
               fontweight='bold')
  ax.set_xlabel('Frequency (GHz)', fontsize=9)
  ax.set_ylabel('dB', fontsize=9)
  ax.legend(loc='lower left', fontsize=7.5)
  ax.grid(True, linestyle=':', alpha=0.4)
  ax = axes[row, 0]
  ax.axis('off')
  lines = ['Settings (receive direction)']
  if is_lro:
    lro = cfg.lro
    lines += [f'LRO driver: {lro.driver_output_vppd:g} Vppd AGC, peaking '
              f'{lro.driver_peaking_db:g} dB @ zero {lro.driver_peaking_zero_ghz:g} GHz,',
              f'   BW {lro.driver_bw_ghz:g} GHz, noise {lro.driver_noise_mv_rms:g} mVrms, '
              f'sat {lro.driver_saturation_vppd:g} Vppd']
  lines += [f'M2C: {ch.pcb_trace_length_mm:g} mm trace, '
            f'{ch.package_connector_loss_db_at_nyquist:g} dB pkg/conn, '
            f'IL {m2c.pcb_loss_nyquist_db:.2f} dB @ Nyq',
            f'Host RX ({cfg.host_serdes}): noise {segs.rx.rx_noise_psd_mv_per_sqrt_ghz:g} mV/rtGHz, '
            f'ENOB {segs.rx.rx_adc_enob:g}, FFE {len(taps)}t, DFE {len(dfe)}t',
            f'   sampling jitter RJ {segs.rx.rx_sample_rj_ui:g} UI, '
            f'DJ {segs.rx.rx_sample_dj_pp_ui:g} UI pp',
            '',
            'Black outline in B5-B6: module DSP receiver output on the',
            'same optical signal' + (' (not in the LRO path).' if is_lro else '.')]
  ax.text(0.0, 1.0, '\n'.join(lines), va='top', family='monospace', fontsize=9)
  fig.tight_layout(rect=[0, 0, 1, 0.975])
  paths['return_png'] = os.path.join(output_dir, 'return_stages.png')
  fig.savefig(paths['return_png'])
  plt.close(fig)
  return paths
