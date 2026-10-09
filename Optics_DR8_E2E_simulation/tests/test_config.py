"""Tests for config serialization and dotted-key overrides."""

import json

import pytest

from dr8sim import config


def test_round_trip_through_dict():
  cfg = config.LinkSimulationConfig()
  cfg.laser.rin_db_hz = -150.0
  cfg.mzm.line_tx_fir_taps = (-0.1, 0.8, -0.1)
  restored = config.update_from_dict(
      config.LinkSimulationConfig(), config.to_dict(cfg)
  )
  assert restored == cfg


def test_round_trip_through_json_file(tmp_path):
  cfg = config.ModuleConfig()
  cfg.lane.fiber.length_km = 2.0
  cfg.lane_overrides = {'3': {'laser.cw_power_dbm': 8.0}}
  path = tmp_path / 'module.json'
  config.save_json(cfg, str(path))
  loaded = config.load_json(str(path), config.ModuleConfig())
  assert loaded == cfg


def test_apply_overrides_coerces_types():
  cfg = config.LinkSimulationConfig()
  config.apply_overrides(cfg, [
      'num_symbols=4096',
      'fiber.length_km=2',
      'host_channel.retimed_forwarding=false',
      'host_channel.tx_fir_taps=[-0.1, 0.8, -0.1]',
      'fiber.override_dispersion_ps_nm_km=3.5',
  ])
  assert cfg.num_symbols == 4096 and isinstance(cfg.num_symbols, int)
  assert cfg.fiber.length_km == 2.0 and isinstance(cfg.fiber.length_km, float)
  assert cfg.host_channel.retimed_forwarding is False
  assert cfg.host_channel.tx_fir_taps == (-0.1, 0.8, -0.1)
  assert cfg.fiber.override_dispersion_ps_nm_km == 3.5
  config.apply_overrides(cfg, ['fiber.override_dispersion_ps_nm_km=none'])
  assert cfg.fiber.override_dispersion_ps_nm_km is None


def test_unknown_key_is_rejected():
  cfg = config.LinkSimulationConfig()
  with pytest.raises(ValueError, match='laser.rin_db'):
    config.set_by_path(cfg, 'laser.rin_db', -140)
  with pytest.raises(ValueError):
    config.update_from_dict(cfg, {'lasr': {}})


def test_shipped_presets_load():
  import pathlib
  root = pathlib.Path(__file__).resolve().parents[1] / 'configs'
  presets = sorted(root.glob('*.json'))
  assert presets, 'expected preset configs in configs/'
  for path in presets:
    data = json.loads(path.read_text())
    target = config.ModuleConfig() if 'lane' in data else (
        config.LinkSimulationConfig())
    config.update_from_dict(target, data)
