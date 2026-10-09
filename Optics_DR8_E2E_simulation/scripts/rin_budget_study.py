"""How laser RIN affects the link budget: retimed vs LRO (generic / Condor).

For each RIN value, runs the full end-to-end simulation with sensitivity
sweeps and link budget, and records margin, end-to-end OMA sensitivity,
host RX SNR/BER at nominal power, TECQ, and the waterfall curves.

Run: python scripts/rin_budget_study.py [output_dir]
"""

from __future__ import annotations

import json
import os
import pathlib
import sys

import numpy as np

from dr8sim import config
from dr8sim import simulator

RIN_DB_HZ = [-200.0, -155.0, -150.0, -146.0, -142.0, -138.0, -135.0, -132.0]
_CONFIGS = pathlib.Path(__file__).resolve().parents[1] / 'configs'
CASES = {
    'Retimed (generic host)': (str(_CONFIGS / 'lro_500m_4db.json'),
                               ['architecture=retimed']),
    'LRO, generic host': (str(_CONFIGS / 'lro_500m_4db.json'), []),
    'LRO, Condor host': (str(_CONFIGS / 'condor_lro_500m_4db.json'), []),
}


def run_case(path, overrides, rin):
  cfg = config.LinkSimulationConfig()
  config.load_json(path, cfg)
  config.apply_overrides(cfg, list(overrides) + [f'laser.rin_db_hz={rin}'])
  res = simulator.run_end_to_end_simulation(cfg, True)
  b = res.link_budget
  sens = res.sensitivity_link
  return {
      'rin_db_hz': rin,
      'rin_oma_db_hz': cfg.rin_oma_db_hz,
      'margin_db': b.net_link_margin_db,
      'e2e_sens_oma_dbm': sens.e2e_sensitivity_oma_dbm_at_kp4,
      'orx_sens_oma_dbm': sens.sensitivity_oma_dbm_at_kp4,
      'rx_oma_dbm': b.rx_oma_outer_dbm,
      'tecq_db': res.tecq.tdecq_db,
      'host_snr_db': res.client_to_host_m2c.post_eq_snr_db,
      'host_ber': res.client_to_host_m2c.ber,
      'e2e_ber': res.end_to_end_ber,
      'orx_snr_db': res.optical_line.orx_snr_db,
      'rin_noise_ua': res.optical_line.rin_noise_rms_ua,
      'thermal_noise_ua': res.optical_line.thermal_noise_rms_ua,
      'shot_noise_ua': res.optical_line.shot_noise_rms_ua,
      'waterfall': [(p.rx_oma_outer_dbm, p.host_rx_ber) for p in sens.points],
  }


def main(argv):
  out_dir = argv[0] if argv else 'output/rin_study'
  os.makedirs(out_dir, exist_ok=True)
  results = {}
  for name, (path, ov) in CASES.items():
    results[name] = [run_case(path, ov, rin) for rin in RIN_DB_HZ]
    ref = results[name][0]['margin_db']
    print(f'\n{name}')
    print(f'{"RIN dB/Hz":>9} | {"RIN_OMA":>7} | {"RIN/th/shot uA":>15} | {"TECQ":>5} | '
          f'{"host SNR":>8} | {"host BER":>8} | {"E2E sens":>8} | '
          f'{"margin":>6} | {"RIN pen.":>8}')
    for r in results[name]:
      pen = ref - r['margin_db']
      print(f'{r["rin_db_hz"]:9.0f} | {r["rin_oma_db_hz"]:7.1f} | {r["rin_noise_ua"]:4.2f}/{r["thermal_noise_ua"]:4.2f}/'
            f'{r["shot_noise_ua"]:4.2f}  | {r["tecq_db"]:5.2f} | '
            f'{r["host_snr_db"]:8.2f} | {r["host_ber"]:8.1e} | '
            f'{r["e2e_sens_oma_dbm"]:+8.2f} | {r["margin_db"]:+6.2f} | {pen:8.2f}'
            .replace('nan', 'N/A'))
  def _clean(obj):
    if isinstance(obj, dict):
      return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
      return [_clean(v) for v in obj]
    if isinstance(obj, (float, np.floating)):
      return simulator.json_float(obj)
    return obj

  with open(os.path.join(out_dir, 'rin_study.json'), 'w') as f:
    json.dump(_clean(results), f, indent=2, allow_nan=False)
  plot(results, out_dir)


def plot(results, out_dir):
  import matplotlib
  matplotlib.use('Agg')
  import matplotlib.pyplot as plt

  colors = {'Retimed (generic host)': '#2ca02c', 'LRO, generic host': '#1f77b4',
            'LRO, Condor host': '#d62728'}
  fig, axes = plt.subplots(2, 2, figsize=(14, 10), dpi=130)
  fig.suptitle('Laser RIN impact on the link budget (500 m, 4 dB channel)',
               fontsize=13, fontweight='bold')
  x_lab = 'Laser RIN (dB/Hz)  [RIN_OMA = RIN + 0.71 dB at ER 5 dB]'
  for name, rows in results.items():
    rows = rows[1:]  # skip the -200 dB/Hz reference on the RIN axis
    rin = [r['rin_db_hz'] for r in rows]
    ref = results[name][0]
    c = colors[name]
    axes[0, 0].plot(rin, [ref['margin_db'] - r['margin_db'] for r in rows], 'o-',
                    color=c, label=name)
    axes[0, 1].plot(rin, [r['margin_db'] for r in rows], 'o-', color=c, label=name)
    axes[0, 1].axhline(ref['margin_db'], color=c, linestyle=':', linewidth=1)
    axes[1, 0].semilogy(rin, [max(r['host_ber'], 1e-20) for r in rows], 'o-',
                        color=c, label=name)
  axes[0, 0].set_title('RIN penalty: margin lost vs no RIN', fontweight='bold')
  axes[0, 0].set_ylabel('Penalty (dB)')
  axes[0, 1].set_title('Net link margin (dotted: no RIN)', fontweight='bold')
  axes[0, 1].set_ylabel('Margin (dB)')
  axes[1, 0].set_title('Host RX BER at nominal power', fontweight='bold')
  axes[1, 0].axhline(1.5e-6, color='black', linestyle='--', linewidth=1,
                     label='Broadcom Condor criterion 1.5e-6')
  axes[1, 0].axhline(2.4e-4, color='gray', linestyle='-.', linewidth=1,
                     label='Pre-FEC target 2.4e-4 (default)')
  axes[1, 0].set_ylabel('BER')
  for ax in (axes[0, 0], axes[0, 1], axes[1, 0]):
    ax.set_xlabel(x_lab)
    ax.grid(True, which='both', linestyle=':', alpha=0.4)
    ax.legend(fontsize=8)
  ax = axes[1, 1]
  cmap = plt.get_cmap('viridis')
  rows = results['LRO, generic host']
  for i, r in enumerate(rows):
    pts = sorted(r['waterfall'])
    ax.semilogy([p[0] for p in pts], [max(p[1], 1e-12) for p in pts], 'o-',
                markersize=3, color=cmap(i / (len(rows) - 1)),
                label=f'RIN {r["rin_db_hz"]:.0f} dB/Hz' if r['rin_db_hz'] > -199
                else 'no RIN')
  ax.axhline(2.4e-4, color='gray', linestyle='-.', linewidth=1)
  ax.set_ylim(1e-12, 0.1)
  ax.set_title('LRO (generic host) end-to-end waterfall vs RIN',
               fontweight='bold')
  ax.set_xlabel('Received OMA (dBm)')
  ax.set_ylabel('Host RX BER')
  ax.grid(True, which='both', linestyle=':', alpha=0.4)
  ax.legend(fontsize=7, ncol=2)
  fig.tight_layout(rect=[0, 0, 1, 0.96])
  path = os.path.join(out_dir, 'rin_budget_study.png')
  fig.savefig(path)
  print(f'\nSaved {path}')


if __name__ == '__main__':
  main(sys.argv[1:])
