"""Electrical configs for each end of the C2M and M2C host links.

The link has four electrical SerDes ends:

  C2M: host ASIC TX  -> host channel -> module client RX
  M2C: module client TX (or LRO linear driver) -> host channel -> host ASIC RX

`host_channel` describes the module's client SerDes and, for the 'generic'
host model, the host ASIC SerDes too. With host_serdes = 'condor', the host
ASIC TX and RX use Broadcom Condor parameters (`config.CondorConfig`), and
the Phytile package loss is added to the host channel.
"""

from __future__ import annotations

import dataclasses
from typing import NamedTuple

from dr8sim import config


class SegmentConfigs(NamedTuple):
  """Electrical configs for one direction: TX end, channel, RX end."""

  tx: config.HostChannelConfig
  channel: config.HostChannelConfig
  rx: config.HostChannelConfig


def condor_tx_settings(condor: config.CondorConfig) -> dict:
  """Maps Condor TX FFE codes and amplitude onto HostChannelConfig fields.

  Condor drives full swing when sum(|codes|) = 168, so the peak output is
  tx_amp * sum(|codes|) / 168, and each tap weighs code / 168 of full scale.
  """
  codes = tuple(float(c) for c in condor.tx_ffe_codes)
  total = sum(abs(c) for c in codes)
  return dict(
      tx_vppd=condor.tx_amp_vppd * total / config.CONDOR_TX_FULL_SCALE_CODE,
      tx_fir_taps=codes,
      tx_dac_bits=condor.tx_dac_bits,
      tx_rj_rms_ps=condor.tx_rj_rms_ps,
      tx_dj_pp_ps=condor.tx_dj_pp_ps,
      tx_bw_ghz=condor.tx_bw_ghz,
      tx_snr_db=condor.tx_snr_db,
  )


def condor_rx_settings(condor: config.CondorConfig) -> dict:
  """Maps Condor RX parameters onto HostChannelConfig fields."""
  return dict(
      rx_noise_psd_mv_per_sqrt_ghz=condor.rx_noise_psd_mv_per_sqrt_ghz,
      rx_adc_enob=condor.rx_adc_enob,
      rx_ffe_taps=condor.rx_ffe_taps,
      rx_ffe_ref_tap=condor.rx_ffe_pre_taps,
      rx_dfe_taps=condor.rx_dfe_taps,
      rx_afe_bw_ghz=condor.rx_afe_bw_ghz,
      rx_ctle_stage_zeros_ghz=tuple(condor.rx_ctle_zeros_ghz),
      rx_ctle_stage_max_boost_db=tuple(condor.rx_ctle_max_boost_db),
      rx_ctle_stage_max_codes=tuple(condor.rx_ctle_max_codes),
      rx_ctle_stage_codes=tuple(condor.rx_ctle_codes),
      rx_sample_rj_ui=condor.rx_clock_rj_ui,
      rx_sample_dj_pp_ui=condor.rx_clock_dj_pp_ui,
  )


def _host_channel(sim_cfg: config.LinkSimulationConfig) -> config.HostChannelConfig:
  base = sim_cfg.host_channel
  if sim_cfg.host_serdes != 'condor':
    return base
  return dataclasses.replace(
      base,
      package_connector_loss_db_at_nyquist=(
          base.package_connector_loss_db_at_nyquist
          + sim_cfg.condor.package_loss_db_at_nyquist
      ),
  )


def c2m_configs(sim_cfg: config.LinkSimulationConfig) -> SegmentConfigs:
  """Host ASIC TX -> host channel -> module client RX."""
  base = sim_cfg.host_channel
  tx = base
  if sim_cfg.host_serdes == 'condor':
    tx = dataclasses.replace(base, **condor_tx_settings(sim_cfg.condor))
  return SegmentConfigs(tx=tx, channel=_host_channel(sim_cfg), rx=base)


def m2c_configs(sim_cfg: config.LinkSimulationConfig) -> SegmentConfigs:
  """Module client TX (or LRO driver) -> host channel -> host ASIC RX."""
  base = sim_cfg.host_channel
  rx = base
  if sim_cfg.host_serdes == 'condor':
    rx = dataclasses.replace(base, **condor_rx_settings(sim_cfg.condor))
  return SegmentConfigs(tx=base, channel=_host_channel(sim_cfg), rx=rx)
