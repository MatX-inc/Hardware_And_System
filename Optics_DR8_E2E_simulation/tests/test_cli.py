"""Smoke tests for the dr8sim command-line interface."""

import json

from dr8sim import cli

FAST = ['--num-symbols', '1024']


def test_config_prints_effective_json(capsys):
  assert cli.main(['config', '--set', 'fiber.length_km=2']) == 0
  data = json.loads(capsys.readouterr().out)
  assert data['fiber']['length_km'] == 2.0


def test_legacy_underscore_flags_still_work(capsys):
  assert cli.main(['config', '--fiber_length_km', '3']) == 0
  assert json.loads(capsys.readouterr().out)['fiber']['length_km'] == 3.0


def test_run_writes_outputs(tmp_path):
  rc = cli.main(['run', *FAST, '--no-budget', '--reach-km', '0', '2',
                 '-o', str(tmp_path)])
  assert rc == 0
  for name in ('simulation_report.txt', 'simulation_summary.json',
               'config.json', '1p6t_dr8_end_to_end_dashboard.png'):
    assert (tmp_path / name).exists(), name
  summary = json.loads((tmp_path / 'simulation_summary.json').read_text())
  assert len(summary['reach_sweep']) == 2


def test_sweep_writes_csv(tmp_path):
  rc = cli.main(['sweep', 'mzm.target_outer_er_db', '--values', '4', '5',
                 *FAST, '-o', str(tmp_path)])
  assert rc == 0
  lines = (tmp_path / 'sweep_mzm_target_outer_er_db.csv').read_text()
  assert len(lines.strip().splitlines()) == 3


def test_module_with_lane_set(tmp_path, capsys):
  rc = cli.main(['module', *FAST, '--lane-set', '1:laser.cw_power_dbm=9.0',
                 '-o', str(tmp_path)])
  out = capsys.readouterr().out
  assert 'lane 1 changes: laser.cw_power_dbm=9.0' in out
  assert rc in (0, 1)
  assert (tmp_path / 'module_summary.json').exists()
  assert (tmp_path / '1p6t_dr8_module_lanes.png').exists()


def test_bad_key_exits_with_error(capsys):
  try:
    cli.main(['config', '--set', 'laser.bogus=1'])
  except SystemExit as e:
    assert e.code == 2
  assert 'laser.bogus' in capsys.readouterr().err
