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
    ax_sens.axhline(
        2.4e-4,
        color='black',
        linestyle='-.',
        linewidth=1.1,
        label='KP4 Pre-FEC Threshold (2.4e-4)',
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
  ax_reach.set_ylim(0.0, max([3.5] + [t * 1.25 for t in tdecqs]))
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
