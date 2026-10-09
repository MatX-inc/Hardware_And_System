"""Compare the Condor model against measured Condor-to-Condor BER vs loss.

Measured points come from Broadcom CSAK board data (Triple test chip, same
Condor 3nm IP) summarized in docs/condor_host_serdes.md:
  ~45 dB bump-to-bump -> ~1e-10;  ~50 dB -> 1e-9..1e-7 (per-lane table:
  50-53 dB -> 1e-9..3e-5);  ~55 dB -> ~1e-6.
The CSAK lane dump in MatX-inc/Hardware_And_System (CSAK_WebBench fixtures)
shows the trained Condor RX uses a 6-tap FFE (3 pre, 2 post), DFE off in ER
mode, and link-trained TX FIR pre1 -34..-38 / main 130..134.

Run: python scripts/calibrate_condor_csak.py [key=value ...]
Extra key=value arguments override condor.* / host_channel.* settings.
"""

from __future__ import annotations

import dataclasses
import pathlib
import sys

import numpy as np

from dr8sim import config
from dr8sim import electrical_channel
from dr8sim import host_serdes

MEASURED = {45.0: (1e-10, 1e-10), 50.0: (1e-9, 1e-7), 55.0: (1e-6, 1e-6)}
PACKAGES_DB = 8.0  # both Condor packages together at Nyquist (~4 dB each)


def condor_link_ber(sim_cfg, total_il_db, seed=7):
  """Condor TX -> channel with `total_il_db` at Nyquist -> Condor RX."""
  base = sim_cfg.host_channel
  per_inch = (base.pcb_skin_loss_db_per_inch_sqrt_ghz
              * np.sqrt(sim_cfg.nyquist_freq_ghz)
              + base.pcb_dielectric_loss_db_per_inch_ghz
              * sim_cfg.nyquist_freq_ghz)
  trace_mm = max(total_il_db - PACKAGES_DB, 0.0) / per_inch * 25.4
  channel = dataclasses.replace(
      base, pcb_trace_length_mm=trace_mm,
      package_connector_loss_db_at_nyquist=PACKAGES_DB)
  tx = host_serdes.c2m_configs(sim_cfg).tx
  rx = host_serdes.m2c_configs(sim_cfg).rx
  rng = np.random.default_rng(seed)
  sym, _, bits = electrical_channel.generate_pam4_symbols(
      sim_cfg.num_symbols, rng)
  res = electrical_channel.simulate_electrical_segment(
      sym, bits, sim_cfg, tx, rng, rx_cfg=rx, channel_cfg=channel)
  return res


def main(argv):
  cfg = config.LinkSimulationConfig(host_serdes='condor')
  config.load_json(str(pathlib.Path(__file__).resolve().parents[1]
                       / 'configs' / 'condor_retimed_500m_4db.json'), cfg)
  # Measured CSAK settings: link-trained TX FIR, 6-tap RX FFE.
  config.apply_overrides(cfg, [
      'condor.tx_ffe_codes=[0,0,-38,130,0,0]',
      'condor.rx_ffe_taps=6', 'condor.rx_ffe_pre_taps=3',
      'condor.rx_dfe_taps=1',
  ] + list(argv))
  print(f'{"IL(dB)":>6} | {"SNR(dB)":>7} | {"model BER":>9} | measured   | PF codes')
  for il in (30.0, 40.0, 45.0, 50.0, 55.0):
    r = condor_link_ber(cfg, il)
    meas = MEASURED.get(il)
    m = '' if meas is None else (f'{meas[0]:.0e}' if meas[0] == meas[1]
                                 else f'{meas[0]:.0e}..{meas[1]:.0e}')
    print(f'{il:6.1f} | {r.post_eq_snr_db:7.2f} | {r.ber:9.2e} | {m:<10} | '
          f'{tuple(int(c) for c in r.ctle_codes)}')


if __name__ == '__main__':
  main(sys.argv[1:])
