"""Command-line interface for the 1.6T-DR8 simulator.

Subcommands:
  dr8sim run     Single-lane end-to-end run with report, link budget, plots.
  dr8sim module  All 8 lanes with per-lane overrides / Monte Carlo spread.
  dr8sim sweep   Sweep any config parameter and tabulate/plot link metrics.
  dr8sim config  Print the effective config as JSON (a starting point to edit).

Any config field can be changed with `--set dotted.key=value`, e.g.
`--set laser.rin_db_hz=-145 --set fiber.length_km=2`. Run `dr8sim config` to
see every key.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
from typing import Any, Dict, List, Sequence

import numpy as np

from dr8sim import config
from dr8sim import module
from dr8sim import simulator


# Shortcut flags kept from the original main.py -> dotted config key.
_SHORTCUTS = {
    'num_symbols': 'num_symbols',
    'fiber_length_km': 'fiber.length_km',
    'wavelength_nm': 'laser.wavelength_nm',
    'wavelength_error_nm': 'laser.wavelength_error_nm',
    'freq_offset_ghz': 'laser.freq_offset_ghz',
    'rin_db_hz': 'laser.rin_db_hz',
    'rin_oma_db_hz': 'laser.rin_oma_db_hz',
    'target_er_db': 'mzm.target_outer_er_db',
    'pcb_trace_length_mm': 'host_channel.pcb_trace_length_mm',
    'channel_loss_db': 'fiber.total_channel_loss_db',
    'seed': 'random_seed',
}


def _add_common(parser: argparse.ArgumentParser) -> None:
  """Adds config-file, --set, and shortcut flags shared by all subcommands."""
  parser.add_argument(
      '-c', '--config', metavar='FILE',
      help='JSON config file (link config, or module config with a "lane" '
           'key). Values not in the file keep their defaults.',
  )
  parser.add_argument(
      '-s', '--set', dest='overrides', action='append', default=[],
      metavar='KEY=VALUE',
      help='Override a link config field, e.g. laser.rin_db_hz=-145. '
           'Repeatable.',
  )
  for name, key in _SHORTCUTS.items():
    flag = name.replace('_', '-')
    parser.add_argument(
        f'--{flag}', f'--{name}', dest=name, type=float, default=None,
        help=f'Shortcut for --set {key}=VALUE.',
    )
  parser.add_argument(
      '--architecture', choices=config.ARCHITECTURES, default=None,
      help='module class: retimed (DSP both directions) or lro (DSP on '
           'transmit only, linear receive into the host SerDes)',
  )
  parser.add_argument(
      '--lro', dest='architecture', action='store_const', const='lro',
      help='shortcut for --architecture lro',
  )
  parser.add_argument(
      '--host-serdes', choices=config.HOST_SERDES_MODELS, default=None,
      help='SerDes at the host ASIC ends: generic, or condor (Broadcom '
           'Condor on the Phytile IOD as host TX and host RX)',
  )
  parser.add_argument(
      '--condor', dest='host_serdes', action='store_const', const='condor',
      help='shortcut for --host-serdes condor',
  )
  parser.add_argument(
      '--unretimed', action='store_true',
      help='soft forwarding: the module DSP equalizes but does not slice '
           'before re-transmitting (host_channel.retimed_forwarding=false). '
           'Not a true LPO model.',
  )


def _load_module_config(args: argparse.Namespace) -> config.ModuleConfig:
  """Builds a ModuleConfig from --config, shortcut flags, and --set."""
  module_cfg = config.ModuleConfig()
  if args.config:
    with open(args.config, encoding='utf-8') as f:
      data = json.load(f)
    if 'lane' in data:
      config.update_from_dict(module_cfg, data)
    else:
      config.update_from_dict(module_cfg.lane, data)
  lane = module_cfg.lane
  for name, key in _SHORTCUTS.items():
    value = getattr(args, name, None)
    if value is not None:
      config.set_by_path(lane, key, value)
  if args.unretimed:
    lane.host_channel.retimed_forwarding = False
  if args.architecture:
    lane.architecture = args.architecture
  if args.host_serdes:
    lane.host_serdes = args.host_serdes
  config.apply_overrides(lane, args.overrides)
  return module_cfg


def _write_json(path: str, payload: Any) -> None:
  with open(path, 'w', encoding='utf-8') as f:
    json.dump(payload, f, indent=2, default=_json_default)
    f.write('\n')


def _json_default(obj: Any) -> Any:
  if isinstance(obj, np.generic):
    return obj.item()
  if isinstance(obj, np.ndarray):
    return obj.tolist()
  raise TypeError(f'Not JSON serializable: {type(obj).__name__}')


def _print_reach_table(rows: List[Dict[str, Any]],
                       fixed_loss: bool = False) -> None:
  print('\nFiber Reach Sweep (SMF-28)' + (
      ': total channel loss held fixed, only dispersion varies'
      if fixed_loss else '') + ':')
  print(
      f'{"Length (km)":>11} | {"CD (ps/nm)":>11} | {"RX OMA (dBm)":>13} | '
      f'{"TDECQ (dB)":>10} | {"ORX SNR (dB)":>12} | {"ORX Pre-FEC BER":>15} | '
      f'{"E2E Pre-FEC BER":>15}'
  )
  print('-' * 104)
  for row in rows:
    print(
        f'{row["length_km"]:11.1f} | {row["dispersion_ps_nm"]:+11.3f} | '
        f'{row["rx_oma_dbm"]:+13.2f} | {row["tdecq_db"]:10.2f} | '
        f'{row["orx_snr_db"]:12.2f} | {row["orx_ber"]:15.3e} | '
        f'{row["e2e_ber"]:15.3e}'
    )


def cmd_run(args: argparse.Namespace) -> int:
  """Single-lane end-to-end simulation."""
  if (args.c2m_plot or args.path_plots) and not args.output_dir:
    raise ValueError('--c2m-plot / --path-plots need --output-dir')
  sim_cfg = _load_module_config(args).lane
  result = simulator.run_end_to_end_simulation(
      sim_cfg=sim_cfg, run_sensitivity_and_budget=not args.no_budget
  )
  report_text = simulator.format_simulation_report(result)
  print(report_text)

  reach_sweep: List[Dict[str, Any]] = []
  if not args.no_reach_sweep:
    reach_sweep = simulator.sweep_fiber_reach(
        sim_cfg=sim_cfg, lengths_km=args.reach_km
    )
    _print_reach_table(
        reach_sweep, fixed_loss=sim_cfg.fiber.total_channel_loss_db is not None)

  if args.output_dir:
    from dr8sim import plotting  # pylint: disable=g-import-not-at-top

    os.makedirs(args.output_dir, exist_ok=True)
    plots = plotting.plot_dashboard(
        result=result, reach_sweep=reach_sweep, output_dir=args.output_dir
    )
    if args.c2m_plot:
      plots.update(plotting.plot_c2m_stages(
          sim_cfg, result.host_to_client_c2m, args.output_dir,
          title=os.path.basename(args.config or 'defaults')))
    if args.path_plots:
      plots.update(plotting.plot_optical_and_return_stages(
          result, args.output_dir,
          title=os.path.basename(args.config or 'defaults')))
    with open(os.path.join(args.output_dir, 'simulation_report.txt'), 'w',
              encoding='utf-8') as f:
      f.write(report_text + '\n')
    config.save_json(sim_cfg, os.path.join(args.output_dir, 'config.json'))
    summary = simulator.summarize_result(result)
    summary['reach_sweep'] = [
        {k: (v if isinstance(v, str) else simulator.json_float(v))
         for k, v in row.items()}
        for row in reach_sweep
    ]
    summary['plots'] = plots
    _write_json(
        os.path.join(args.output_dir, 'simulation_summary.json'), summary
    )
    print(f'\nSaved report, summary, config, and plots to: {args.output_dir}')

  # Exit codes: 0 pass, 1 fail (negative margin, or BER over target when no
  # budget was run), 3 margin unknown (sensitivity not bracketed).
  if result.link_budget is not None:
    margin = result.link_budget.net_link_margin_db
    if math.isnan(margin):
      return 3
    return 0 if margin >= 0 else 1
  return 0 if result.end_to_end_ber <= sim_cfg.receiver.target_pre_fec_ber else 1


def _parse_lane_set(text: str) -> tuple[str, str]:
  if ':' not in text:
    raise argparse.ArgumentTypeError(
        f'--lane-set must look like LANE:key=value, got {text!r}'
    )
  lane, override = text.split(':', 1)
  return lane.strip(), override


def cmd_module(args: argparse.Namespace) -> int:
  """8-lane DR8 module simulation."""
  module_cfg = _load_module_config(args)
  if args.monte_carlo:
    module_cfg.monte_carlo = True
  config.apply_overrides(module_cfg.spread, args.spread_set)
  config.apply_overrides(module_cfg.spec, args.spec_set)
  for lane, override in args.lane_set:
    key, value = config.parse_override(override)
    module_cfg.lane_overrides.setdefault(lane, {})[key] = value

  result = module.run_module(module_cfg, run_budget=args.budget, jobs=args.jobs)
  report_text = module.format_module_report(result)
  print(report_text)

  if args.output_dir:
    from dr8sim import plotting  # pylint: disable=g-import-not-at-top

    os.makedirs(args.output_dir, exist_ok=True)
    plots = plotting.plot_module_summary(result, args.output_dir)
    with open(os.path.join(args.output_dir, 'module_report.txt'), 'w',
              encoding='utf-8') as f:
      f.write(report_text + '\n')
    config.save_json(
        module_cfg, os.path.join(args.output_dir, 'module_config.json')
    )
    summary = module.summarize_module(result)
    summary['plots'] = plots
    _write_json(os.path.join(args.output_dir, 'module_summary.json'), summary)
    print(f'\nSaved report, summary, config, and plot to: {args.output_dir}')

  return 0 if result.passed else 1


def cmd_sweep(args: argparse.Namespace) -> int:
  """Sweep one config parameter."""
  sim_cfg = _load_module_config(args).lane
  if args.range is not None:
    start, stop, num = args.range
    values: Sequence[Any] = [
        float(v) for v in np.linspace(start, stop, int(num))
    ]
  elif args.values:
    values = [config.parse_override(f'x={v}')[1] for v in args.values]
  else:
    print('sweep: give --values or --range', file=sys.stderr)
    return 2

  rows = simulator.sweep_parameter(
      sim_cfg, args.key, values,
      max_symbols=None if args.full_length else 8192,
  )
  metrics = args.metrics or list(simulator.SWEEP_METRICS)
  widths = {m: max(len(m), 10) for m in metrics}
  print(f'{args.key:>24} | ' + ' | '.join(f'{m:>{widths[m]}}' for m in metrics))
  print('-' * (27 + sum(widths.values()) + 3 * len(metrics)))
  for row in rows:
    cells = []
    for m in metrics:
      fmt = '.3e' if 'ber' in m else '.3f'
      cells.append(f'{row[m]:>{widths[m]}{fmt}}')
    print(f'{str(row["value"]):>24} | ' + ' | '.join(cells))

  if args.output_dir:
    from dr8sim import plotting  # pylint: disable=g-import-not-at-top

    os.makedirs(args.output_dir, exist_ok=True)
    safe_key = args.key.replace('.', '_')
    csv_path = os.path.join(args.output_dir, f'sweep_{safe_key}.csv')
    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
      writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
      writer.writeheader()
      writer.writerows(rows)
    plot_metrics = args.metrics or ['tdecq_db', 'orx_snr_db', 'rx_oma_dbm',
                                    'e2e_ber']
    plots = plotting.plot_sweep(rows, args.key, args.output_dir, plot_metrics)
    print(f'\nSaved {csv_path} and {plots["sweep_png"]}')
  return 0


def cmd_config(args: argparse.Namespace) -> int:
  """Print the effective configuration as JSON."""
  module_cfg = _load_module_config(args)
  payload = config.to_dict(module_cfg if args.module else module_cfg.lane)
  text = json.dumps(payload, indent=2)
  if args.output:
    with open(args.output, 'w', encoding='utf-8') as f:
      f.write(text + '\n')
    print(f'Wrote {args.output}')
  else:
    print(text)
  return 0


def build_parser() -> argparse.ArgumentParser:
  parser = argparse.ArgumentParser(
      prog='dr8sim',
      description=(
          'End-to-end 1.6T-DR8 (8x200G PAM4 @ 106.25 GBaud) optical link '
          'simulator.'
      ),
      formatter_class=argparse.RawDescriptionHelpFormatter,
      epilog=__doc__.split('\n\n', 1)[1],
  )
  sub = parser.add_subparsers(dest='command', required=True)

  p_run = sub.add_parser('run', help='single-lane end-to-end simulation')
  _add_common(p_run)
  p_run.add_argument('-o', '--output-dir', '--output_dir', dest='output_dir',
                     help='write report, JSON summary, config, and plots here')
  p_run.add_argument('--no-budget', action='store_true',
                     help='skip sensitivity sweeps and link budget (faster)')
  p_run.add_argument('--c2m-plot', action='store_true',
                     help='also plot eye + spectrum at each C2M step '
                          '(c2m_stages.png; needs --output-dir)')
  p_run.add_argument('--path-plots', action='store_true',
                     help='also plot eye + spectrum along the optical path '
                          'and the receive direction to the host '
                          '(optical_stages.png, return_stages.png)')
  p_run.add_argument('--no-reach-sweep', action='store_true',
                     help='skip the fiber-length sweep')
  p_run.add_argument('--reach-km', type=float, nargs='+',
                     default=[0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
                     help='fiber lengths for the reach sweep (km)')
  p_run.set_defaults(func=cmd_run)

  p_mod = sub.add_parser('module', help='8-lane DR8 module simulation')
  _add_common(p_mod)
  p_mod.add_argument('-o', '--output-dir', '--output_dir', dest='output_dir',
                     help='write report, JSON summary, config, and plot here')
  p_mod.add_argument('--lane-set', type=_parse_lane_set, action='append',
                     default=[], metavar='LANE:KEY=VALUE',
                     help='override one lane, e.g. 3:laser.cw_power_dbm=8.5')
  p_mod.add_argument('--monte-carlo', action='store_true',
                     help='apply Gaussian lane-to-lane spread')
  p_mod.add_argument('--spread-set', action='append', default=[],
                     metavar='KEY=VALUE',
                     help='set a spread sigma, e.g. wavelength_sigma_nm=2')
  p_mod.add_argument('--spec-set', action='append', default=[],
                     metavar='KEY=VALUE',
                     help='set a pass/fail limit, e.g. tdecq_max_db=3.0')
  p_mod.add_argument('--budget', action='store_true',
                     help='also run per-lane sensitivity sweeps + link budget')
  p_mod.add_argument('-j', '--jobs', type=int, default=1,
                     help='parallel worker processes (default 1)')
  p_mod.set_defaults(func=cmd_module)

  p_sweep = sub.add_parser('sweep', help='sweep one config parameter')
  _add_common(p_sweep)
  p_sweep.add_argument('key', help='dotted config key, e.g. laser.rin_db_hz')
  group = p_sweep.add_mutually_exclusive_group()
  group.add_argument('--values', nargs='+', help='explicit values to sweep')
  group.add_argument('--range', nargs=3, type=float,
                     metavar=('START', 'STOP', 'NUM'),
                     help='NUM evenly spaced values from START to STOP')
  p_sweep.add_argument('--metrics', nargs='+',
                       choices=simulator.SWEEP_METRICS,
                       help='metrics to show (default: all)')
  p_sweep.add_argument('--full-length', action='store_true',
                       help='use num_symbols as-is instead of capping at 8192')
  p_sweep.add_argument('-o', '--output-dir', '--output_dir', dest='output_dir',
                       help='write CSV and plot here')
  p_sweep.set_defaults(func=cmd_sweep)

  p_cfg = sub.add_parser('config', help='print the effective config as JSON')
  _add_common(p_cfg)
  p_cfg.add_argument('--module', action='store_true',
                     help='print the full module config (lanes, spread, spec)')
  p_cfg.add_argument('-o', '--output', help='write to this file')
  p_cfg.set_defaults(func=cmd_config)
  return parser


def main(argv: Sequence[str] | None = None) -> int:
  parser = build_parser()
  args = parser.parse_args(argv)
  try:
    return args.func(args)
  except (ValueError, TypeError) as e:
    parser.error(str(e))
  return 2


if __name__ == '__main__':
  sys.exit(main())
