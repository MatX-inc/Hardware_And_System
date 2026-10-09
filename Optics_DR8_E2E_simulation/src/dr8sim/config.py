"""Configuration dataclasses for 1.6T-DR8 (8x200G PAM4) end-to-end simulation.

Models a single 200 Gbps PAM4 lane (106.25 GBaud) of a 1.6T-DR8 optical
transceiver link up to 6 km over O-band standard single-mode fiber (SMF-28),
covering:
  1. Host TX (200G PAM4) -> Host PCB Trace (C2M) -> Optics Client RX (HRX)
  2. Digital Signal Processing from Client RX (HRX) to Line TX (OTX)
  3. O-band CW Laser with RIN, frequency/wavelength error, and phase noise
  4. Silicon Photonics (SiPh) Push-Pull Mach-Zehnder Modulator (MZM)
  5. SMF-28 Fiber Propagation up to 6 km (chromatic dispersion & attenuation)
  6. Optical Receiver (PIN PD + TIA) with thermal, shot, dark, and RIN noise
  7. Line RX ADC -> Media RX DSP (ORX) -> Client TX (HTX) -> Host PCB (M2C)
     -> Host RX SerDes
  8. Metrology: TDECQ, TECQ, R_LM, Receiver Sensitivity, and 6 km Link Budget
"""

from __future__ import annotations

import dataclasses
import json
import math
from typing import Any, Dict, Sequence, Tuple


def rin_oma_correction_db(er_db: float) -> float:
  """RIN_OMA minus laser RIN (dB) for square-wave modulation at `er_db`.

  With multiplicative laser RIN, the noise variance at power P is
  RIN * B * P^2. RIN_OMA references the noise averaged over the two levels of
  a square wave to OMA^2 = (P_high - P_low)^2, so with ER = P_high / P_low:

    RIN_OMA - RIN = 10 * log10((1 + ER^2) / (2 * (ER - 1)^2))

  e.g. +2.92 dB at ER = 3.5 dB, +0.71 dB at 5 dB, -0.23 dB at 6 dB,
  -1.40 dB at 8 dB.
  """
  er = 10.0 ** (er_db / 10.0)
  if er <= 1.0:
    raise ValueError(f'RIN_OMA needs an extinction ratio > 0 dB, got {er_db}')
  return 10.0 * math.log10((1.0 + er * er) / (2.0 * (er - 1.0) ** 2))


def rin_oma_to_laser_rin(rin_oma_db_hz: float, er_db: float) -> float:
  """Converts a RIN_OMA (dB/Hz) to the laser RIN the simulator applies."""
  return rin_oma_db_hz - rin_oma_correction_db(er_db)


def laser_rin_to_rin_oma(rin_db_hz: float, er_db: float) -> float:
  """Converts a laser RIN (dB/Hz) to the equivalent RIN_OMA."""
  return rin_db_hz + rin_oma_correction_db(er_db)


@dataclasses.dataclass
class HostChannelConfig:
  """Configuration for Host-to-Optics (C2M) and Optics-to-Host (M2C) links.

  Attributes:
    tx_vppd: Host/Client TX differential peak-to-peak voltage swing (V).
    tx_fir_taps: 3-tap T-spaced FIR pre-emphasis weights [pre, main, post].
    tx_dac_bits: Resolution of the electrical TX DAC (bits).
    tx_rj_rms_ps: Random jitter RMS (ps) at electrical TX.
    tx_bw_ghz: Electrical TX analog 3-dB bandwidth (GHz).
    pcb_trace_length_mm: PCB trace length between Host ASIC and OSFP/QSFP-DD
      module connector (mm). 120 mm (~4.7 inches) is typical for switch blades.
    pcb_dielectric_loss_db_per_inch_ghz: Dielectric loss tangent factor
      (dB / inch / GHz).
    pcb_skin_loss_db_per_inch_sqrt_ghz: Conductor skin-effect loss factor
      (dB / inch / sqrt(GHz)).
    package_connector_loss_db_at_nyquist: Additional package + connector
      insertion loss at Nyquist frequency 53.125 GHz (dB).
    ctle_dc_gain_db: Continuous-Time Linear Equalizer (CTLE) DC gain relative
      to peak (dB, negative value indicates low-frequency attenuation).
    ctle_peaking_gain_db: CTLE peaking boost near Nyquist frequency (dB).
    ctle_zero_ghz: CTLE zero frequency (GHz).
    ctle_pole1_ghz: CTLE primary pole frequency (GHz).
    ctle_pole2_ghz: CTLE secondary pole frequency (GHz).
    rx_noise_psd_mv_per_sqrt_ghz: Electrical RX front-end noise spectral
      density (mV / sqrt(GHz)).
    rx_adc_bits: Resolution of electrical RX ADC (bits).
    rx_adc_enob: Effective Number of Bits (ENOB) of electrical RX ADC.
    rx_ffe_taps: Number of T-spaced Feed-Forward Equalizer (FFE) taps.
    rx_dfe_taps: Number of Decision-Feedback Equalizer (DFE) taps.
    retimed_forwarding: If True, the module DSP slices symbols before
      re-transmitting them (client RX -> line TX, and ORX -> client TX in the
      retimed architecture). If False, it forwards the DSP-equalized soft
      samples without slicing. The module equalizers still run either way, so
      False is not a true LPO model. With architecture 'lro', only the
      transmit direction uses this flag.
    tx_dj_pp_ps: TX dual-Dirac deterministic jitter, peak-to-peak (ps).
      0 disables it.
    tx_snr_db: TX signal-to-noise-and-distortion ratio (dB), applied as
      white noise at the DAC output. 0 disables it.
    rx_afe_bw_ghz: RX analog front-end 3-dB bandwidth (GHz), 4th-order
      Bessel, applied before the CTLE. 0 disables it.
    rx_ctle_stage_zeros_ghz: Multi-stage CTLE: zero frequency of each
      first-order peaking stage (GHz). Empty uses the single CTLE above.
    rx_ctle_stage_max_boost_db: Multi-stage CTLE: high-frequency boost of each
      stage at its maximum code (dB).
    rx_ctle_stage_max_codes: Multi-stage CTLE: maximum code of each stage.
    rx_ctle_stage_codes: Multi-stage CTLE: fixed codes per stage. Empty means
      adapt them (grid search for best post-equalizer SNR).
    rx_ffe_ref_tap: Index of the FFE main cursor (number of pre-cursor taps).
      -1 centers it.
    rx_sample_rj_ui: RX sampling-clock random jitter, RMS (UI).
    rx_sample_dj_pp_ui: RX sampling-clock dual-Dirac jitter, peak-to-peak (UI).
  """

  tx_vppd: float = 0.80
  tx_fir_taps: Tuple[float, ...] = (-0.12, 0.82, -0.06)
  tx_dac_bits: int = 7
  tx_rj_rms_ps: float = 0.15
  tx_bw_ghz: float = 65.0
  pcb_trace_length_mm: float = 120.0
  pcb_dielectric_loss_db_per_inch_ghz: float = 0.038
  pcb_skin_loss_db_per_inch_sqrt_ghz: float = 0.18
  package_connector_loss_db_at_nyquist: float = 3.5
  ctle_dc_gain_db: float = -8.0
  ctle_peaking_gain_db: float = 8.5
  ctle_zero_ghz: float = 16.0
  ctle_pole1_ghz: float = 53.125
  ctle_pole2_ghz: float = 75.0
  rx_noise_psd_mv_per_sqrt_ghz: float = 0.15
  rx_adc_bits: int = 7
  rx_adc_enob: float = 5.3
  rx_ffe_taps: int = 15
  rx_dfe_taps: int = 2
  retimed_forwarding: bool = True
  tx_dj_pp_ps: float = 0.0
  tx_snr_db: float = 0.0
  rx_afe_bw_ghz: float = 0.0
  rx_ctle_stage_zeros_ghz: Tuple[float, ...] = ()
  rx_ctle_stage_max_boost_db: Tuple[float, ...] = ()
  rx_ctle_stage_max_codes: Tuple[float, ...] = ()
  rx_ctle_stage_codes: Tuple[float, ...] = ()
  rx_ffe_ref_tap: int = -1
  rx_sample_rj_ui: float = 0.0
  rx_sample_dj_pp_ui: float = 0.0


@dataclasses.dataclass
class LaserConfig:
  """Configuration for the O-band Continuous-Wave (CW) Laser Source.

  Attributes:
    wavelength_nm: Nominal laser wavelength (nm). Standard O-band center is
      1310.0 nm (IEEE 802.3dj DR8 window is 1304.5 to 1317.5 nm).
    wavelength_error_nm: Wavelength detuning / offset from nominal (nm).
    freq_offset_ghz: Additional laser frequency error (GHz). Converted and
      added to wavelength_error_nm during simulation.
    cw_power_dbm: Laser CW optical output power before modulator losses (dBm).
    rin_db_hz: Laser Relative Intensity Noise (RIN) spectral density (dB/Hz),
      one-sided, relative to average CW power. This is what the simulation
      applies, unless rin_oma_db_hz is set.
    rin_oma_db_hz: Optional RIN_OMA (dB/Hz), the IEEE-style spec quantity.
      If set, it overrides rin_db_hz: the laser RIN is derived from it at the
      MZM target outer ER (see rin_oma_to_laser_rin). Note that a spec
      RIN_xOMA is measured with a reflection (x dB return loss); the model
      has no reflection, so the whole value is treated as intrinsic RIN.
    linewidth_mhz: Laser 3-dB Lorentzian linewidth (MHz) causing Wiener phase
      noise.
  """

  wavelength_nm: float = 1310.0
  wavelength_error_nm: float = 1.5
  freq_offset_ghz: float = 15.0
  cw_power_dbm: float = 9.5
  rin_db_hz: float = -142.0
  rin_oma_db_hz: float | None = None
  linewidth_mhz: float = 2.0


@dataclasses.dataclass
class MzmConfig:
  """Configuration for the Line TX DSP and Silicon Photonics (SiPh) MZM.

  Attributes:
    line_tx_fir_taps: Line TX DSP FIR pre-emphasis taps to pre-compensate RF
      driver and MZM electro-optic bandwidth roll-off.
    line_tx_dac_bits: Line TX DAC nominal resolution (bits).
    line_tx_dac_enob: Line TX DAC Effective Number of Bits (ENOB).
    enable_arcsin_predistortion: If True, applies arcsin pre-distortion in DSP
      to linearize the sinusoidal MZM transfer characteristic.
    predistortion_gain: Normalized swing factor (0 < gain < 1) for arcsin
      lookup table.
    driver_bw_ghz: RF modulator driver 3-dB electrical bandwidth (GHz).
    vpi_volts: MZM half-wave switching voltage V_pi (V).
    drive_vpp_volts: Differential peak-to-peak RF drive voltage applied to MZM
      arms (V). Controls the modulated Extinction Ratio (ER).
    bias_phase_rad: Nominal MZM bias point (rad). pi/2 is quadrature point.
    bias_error_deg: Phase bias error away from quadrature (degrees).
    intrinsic_er_db: Intrinsic interferometer extinction ratio due to Y-branch
      splitting imbalance and waveguide loss mismatch (dB).
    target_outer_er_db: Target modulated outer Extinction Ratio (dB). For SiPh
      DR8 designs, 4.5 to 5.5 dB is typical. If > 0, overrides drive_vpp_volts
      to achieve this nominal static outer ER before bandwidth filtering.
    insertion_loss_db: Optical insertion loss of the SiPh MZM + coupling (dB).
    eo_bw_ghz: MZM electro-optic 3-dB modulation bandwidth (GHz).
    chirp_alpha: Residual MZM phase-modulation chirp parameter (alpha_H).
      Push-pull SiPh MZM has near-zero chirp (~0.05 to 0.15).
  """

  line_tx_fir_taps: Tuple[float, ...] = (-0.07, 0.84, -0.09)
  line_tx_dac_bits: int = 8
  line_tx_dac_enob: float = 5.6
  enable_arcsin_predistortion: bool = True
  predistortion_gain: float = 0.82
  driver_bw_ghz: float = 62.0
  vpi_volts: float = 4.0
  drive_vpp_volts: float = 1.65
  bias_phase_rad: float = 1.5707963267948966  # pi / 2 (quadrature)
  bias_error_deg: float = 1.2
  intrinsic_er_db: float = 22.0
  target_outer_er_db: float = 5.0
  insertion_loss_db: float = 6.5
  eo_bw_ghz: float = 56.0
  chirp_alpha: float = -0.35


@dataclasses.dataclass
class FiberConfig:
  """Configuration for O-band SMF-28 Optical Fiber Channel (up to 6 km).

  Attributes:
    length_km: Fiber propagation distance (km). Supports 0 to 6+ km.
    attenuation_db_per_km: Fiber linear attenuation coefficient in O-band
      (dB/km). Standard G.652.D SMF-28 is ~0.33-0.35 dB/km at 1310 nm.
    connector_loss_db: Total connector insertion loss (dB) across patch panels
      and MPO-12/MPO-16 receptacles.
    splice_and_margin_loss_db: Additional fiber splice and cable aging loss
      (dB).
    zero_dispersion_wavelength_nm: SMF-28 zero-dispersion wavelength lambda_0
      (nm). Spec range is 1300 to 1324 nm (1300.0 nm represents the worst-case
      positive dispersion corner in the upper O-band).
    dispersion_slope_ps_nm2_km: Zero-dispersion slope S_0 (ps / (nm^2 * km)).
      Standard SMF-28 is 0.092 ps/(nm^2*km).
    override_dispersion_ps_nm_km: If not None, overrides the wavelength-derived
      dispersion coefficient D(lambda) with an explicit value (ps/(nm*km)).
    pmd_ps_per_sqrt_km: Polarization Mode Dispersion (PMD) coefficient
      (ps / sqrt(km)).
    mpi_penalty_db: Multi-Path Interference (MPI) budget allocation from
      discrete connector reflections (dB).
    total_channel_loss_db: If not None, the total passive channel insertion
      loss (fiber + connectors + splices) in dB, replacing the loss computed
      from the three terms above. Use it to simulate a spec channel loss such
      as 3.0 dB (DR8) or 4.0 dB (DR8-2). Dispersion still follows length_km.
  """

  length_km: float = 6.0
  attenuation_db_per_km: float = 0.35
  connector_loss_db: float = 1.0
  splice_and_margin_loss_db: float = 0.2
  zero_dispersion_wavelength_nm: float = 1300.0
  dispersion_slope_ps_nm2_km: float = 0.092
  override_dispersion_ps_nm_km: float | None = None
  pmd_ps_per_sqrt_km: float = 0.05
  mpi_penalty_db: float = 0.3
  total_channel_loss_db: float | None = None


@dataclasses.dataclass
class ReceiverConfig:
  """Configuration for the Optical Receiver (PIN PD + TIA) and Line RX DSP.

  Attributes:
    responsivity_a_per_w: PIN photodiode responsivity R (A/W) at 1310 nm.
    dark_current_na: Photodiode dark current I_d (nA).
    pd_bw_ghz: Photodiode 3-dB opto-electrical bandwidth (GHz).
    tia_transimpedance_ohms: TIA differential transimpedance gain Z_TIA (Ohms).
    tia_bw_ghz: TIA 3-dB bandwidth (GHz), modeled as a 4th-order
      Bessel-Thomson filter.
    tia_irnd_pa_per_sqrt_hz: TIA input-referred noise current density (IRND)
      (pA / sqrt(Hz)). Typical 200G TIA is 14 to 18 pA/sqrt(Hz).
    overload_power_dbm: Optical input power where TIA saturates/compresses
      (dBm).
    adc_bits: Line RX ADC nominal bit resolution.
    adc_enob: Line RX ADC Effective Number of Bits (ENOB).
    adc_bw_ghz: Line RX ADC front-end track-and-hold 3-dB bandwidth (GHz).
    orx_ffe_taps: Number of T-spaced FFE taps in the Media RX (ORX) DSP.
    orx_dfe_taps: Number of DFE taps in the Media RX (ORX) DSP.
    orx_reference_tap: Index of the main cursor tap in the ORX FFE.
    target_pre_fec_ber: Target Pre-FEC BER threshold for KP4 / IEEE 802.3dj
      DR8 sensitivity calculation (2.4e-4 for standard RS(544,514) KP4 FEC).
    target_tdecq_ser: Target Symbol Error Ratio (SER) for IEEE 802.3 TDECQ
      measurement (4.8e-4).
  """

  responsivity_a_per_w: float = 0.85
  dark_current_na: float = 10.0
  pd_bw_ghz: float = 65.0
  tia_transimpedance_ohms: float = 2500.0
  tia_bw_ghz: float = 56.0
  tia_irnd_pa_per_sqrt_hz: float = 16.0
  overload_power_dbm: float = 4.0
  adc_bits: int = 7
  adc_enob: float = 5.4
  adc_bw_ghz: float = 62.0
  orx_ffe_taps: int = 21
  orx_dfe_taps: int = 2
  orx_reference_tap: int = 10
  target_pre_fec_ber: float = 2.4e-4
  target_tdecq_ser: float = 4.8e-4


ARCHITECTURES = ('retimed', 'lro')
HOST_SERDES_MODELS = ('generic', 'condor')

# Condor TX FFE code ranges, order (pre3, pre2, pre1, main, post1, post2), from
# Broadcom's Condor 3nm IBIS-AMI v2 parameter sheet.
CONDOR_TX_CODE_RANGES = (
    (-8, 0), (0, 16), (-40, 0), (0, 168), (-64, 0), (-16, 16)
)
CONDOR_TX_FULL_SCALE_CODE = 168


@dataclasses.dataclass
class CondorConfig:
  """Broadcom Condor 3nm 200G SerDes (Phytile IOD) used as host TX and RX.

  Values marked [AMI] come from Broadcom's Condor 3nm IBIS-AMI v2 parameter
  sheet, [DS] from the BCM78005 datasheet, [COM] from the IEEE 802.3dj COM
  reference, and [ASSUMED] are placeholders where no MatX/Broadcom number was
  found. See docs/condor_host_serdes.md for sources and open items.

  Attributes:
    tx_ffe_codes: TX FFE integer codes (pre3, pre2, pre1, main, post1, post2).
      sum(|codes|) <= 168; 168 is full-scale drive. [AMI] Default is the AMI
      default (no equalization).
    tx_amp_vppd: TX amplitude setting, one of 0.68 / 0.9 / 1.1 V [AMI].
      Datasheet range 0.8-1.0 Vppd [DS].
    tx_dac_bits: TX DAC resolution (bits). [ASSUMED]
    tx_snr_db: TX SNDR (dB). [ASSUMED]
    tx_bw_ghz: TX analog bandwidth (GHz). [ASSUMED]
    tx_rj_rms_ps: TX random jitter RMS (ps). [AMI, units assumed ps]
    tx_dj_pp_ps: TX deterministic jitter peak-to-peak (ps). [AMI: +/-0.125]
    package_loss_db_at_nyquist: Phytile CoWoS-L package loss, bump to BGA, at
      53.125 GHz (dB). Added to the host channel at the host end. [ASSUMED;
      Broadcom has not supplied Phytile S-parameters. The TH6 design guide
      says its Condor package loss can exceed 4 dB on some channels.]
    rx_ctle_codes: Fixed peaking-filter codes (PF1, PF2, PF3). Empty means
      auto-adapt, the Broadcom default [AMI].
    rx_ctle_max_codes: Peaking-filter code ranges (30, 30, 23) [AMI].
    rx_ctle_zeros_ghz: Low / mid / high-frequency peaking-filter zero
      frequencies (GHz). [ASSUMED; dB per code is not documented]
    rx_ctle_max_boost_db: Boost of each peaking filter at max code (dB).
      [ASSUMED]
    rx_afe_bw_ghz: RX front-end bandwidth (GHz). [ASSUMED]
    rx_noise_psd_mv_per_sqrt_ghz: RX input-referred noise density
      (mV/sqrt(GHz)); 0.128 equals COM eta0 = 1.64e-8 V^2/GHz. [COM]
    rx_adc_enob: RX ADC effective bits. [ASSUMED]
    rx_ffe_taps: RX DSP FFE taps. [MEASURED] A link-trained Condor lane dump
      (CSAK bench, firmware D003_07) shows RXFFE(n3,n2,n1,m,p1,p2): 6 taps.
    rx_ffe_pre_taps: RX FFE pre-cursor taps. [MEASURED: 3]
    rx_dfe_taps: RX DFE taps. [MEASURED-ish] The same dump shows DFE(1,2) =
      (x, 0) in PAM4 ER mode, where Broadcom uses an "ECD" block instead.
      1 tap here stands in for the ECD.
    rx_clock_rj_ui: RX sampling-clock RJ RMS (UI). [AMI; units ambiguous]
      The AMI sheet gives Rx_Clock_PDF "-0.037 0.037 0.01" with no unit; read
      as UI (default, conservative). Read as ps it is ~10x smaller, which
      measured Condor BER-vs-loss data favors.
    rx_clock_dj_pp_ui: RX sampling-clock DJ peak-to-peak (UI). [AMI: +/-0.037]
    host_link_ber_target: Pre-FEC BER that Broadcom requires at a Condor RX
      in PAM4 IBIS-AMI link simulations (~1.5e-6, equivalent to ~1e-15
      post-FEC). [TH6 DG104 section 9.1.2]
  """

  tx_ffe_codes: Tuple[float, ...] = (0, 0, 0, 168, 0, 0)
  tx_amp_vppd: float = 0.9
  tx_dac_bits: int = 8
  tx_snr_db: float = 33.0
  tx_bw_ghz: float = 70.0
  tx_rj_rms_ps: float = 0.09
  tx_dj_pp_ps: float = 0.25
  package_loss_db_at_nyquist: float = 5.0
  rx_ctle_codes: Tuple[float, ...] = ()
  rx_ctle_max_codes: Tuple[float, ...] = (30, 30, 23)
  rx_ctle_zeros_ghz: Tuple[float, ...] = (1.5, 10.0, 30.0)
  rx_ctle_max_boost_db: Tuple[float, ...] = (4.0, 8.0, 10.0)
  rx_afe_bw_ghz: float = 70.0
  rx_noise_psd_mv_per_sqrt_ghz: float = 0.128
  rx_adc_enob: float = 6.5
  rx_ffe_taps: int = 6
  rx_ffe_pre_taps: int = 3
  rx_dfe_taps: int = 1
  rx_clock_rj_ui: float = 0.01
  rx_clock_dj_pp_ui: float = 0.074
  host_link_ber_target: float = 1.5e-6

  def validate(self) -> None:
    """Checks TX FFE codes against Condor's documented ranges."""
    if len(self.tx_ffe_codes) != 6:
      raise ValueError('condor.tx_ffe_codes needs 6 codes '
                       '(pre3, pre2, pre1, main, post1, post2)')
    names = ('pre3', 'pre2', 'pre1', 'main', 'post1', 'post2')
    for name, code, (lo, hi) in zip(
        names, self.tx_ffe_codes, CONDOR_TX_CODE_RANGES
    ):
      if not lo <= code <= hi:
        raise ValueError(f'condor {name} code {code:g} outside [{lo}, {hi}]')
    total = sum(abs(c) for c in self.tx_ffe_codes)
    if total > CONDOR_TX_FULL_SCALE_CODE:
      raise ValueError(
          f'condor sum(|tx_ffe_codes|) = {total:g} exceeds '
          f'{CONDOR_TX_FULL_SCALE_CODE}'
      )
    if self.rx_ctle_codes and len(self.rx_ctle_codes) != 3:
      raise ValueError('condor.rx_ctle_codes needs 3 codes or none (auto)')


@dataclasses.dataclass
class LroConfig:
  """Linear receive path of an LRO (Linear Receive Optics) module.

  In an LRO module the transmit direction (host -> fiber) is retimed by the
  module DSP, while the receive direction (fiber -> host) has no DSP:
  PD -> TIA -> linear driver -> M2C PCB trace -> host SerDes RX. The host RX
  equalizer must then undo fiber dispersion, the TIA/driver response, and the
  M2C trace loss together, and the optical RX noise reaches the host
  unfiltered by any module equalizer.

  Attributes:
    driver_output_vppd: Linear driver output swing after AGC (V, differential
      peak-to-peak of the outer PAM4 levels).
    driver_bw_ghz: Linear driver 3-dB bandwidth (GHz), 4th-order Bessel.
    driver_peaking_db: Built-in TIA/driver CTLE high-frequency boost (dB),
      first-order stage. 0 disables it. Match it to the host channel: too
      much peaking over-equalizes a short host trace.
    driver_peaking_zero_ghz: Zero frequency of the driver CTLE (GHz); the
      boost is reached a factor 10^(dB/20) above it.
    driver_saturation_vppd: Soft (tanh) saturation level of the driver output
      (V, differential). The ratio to driver_output_vppd sets nonlinearity.
    driver_noise_mv_rms: Driver output-referred RMS noise within its
      bandwidth (mV).
    host_rx_ffe_taps: Host SerDes RX FFE taps used on the LRO receive path
      (it equalizes the optical and M2C channel together).
    host_rx_dfe_taps: Host SerDes RX DFE taps used on the LRO receive path.
  """

  driver_output_vppd: float = 0.70
  driver_bw_ghz: float = 60.0
  driver_peaking_db: float = 3.0
  driver_peaking_zero_ghz: float = 20.0
  driver_saturation_vppd: float = 2.0
  driver_noise_mv_rms: float = 1.5
  host_rx_ffe_taps: int = 31
  host_rx_dfe_taps: int = 2


@dataclasses.dataclass
class LinkSimulationConfig:
  """Top-level configuration for a 1.6T-DR8 (8x200G PAM4) link simulation.

  Attributes:
    num_lanes: Number of parallel optical lanes (8 for 1.6T-DR8).
    baud_rate_gbaud: Symbol rate per lane (GBaud). 106.25 GBaud corresponds
      to 212.5 Gbps raw line rate (200 Gbps net payload).
    samples_per_symbol: Oversampling ratio for continuous-time waveform
      emulation (must be an even integer >= 8, default 16).
    num_symbols: Number of PAM4 symbols simulated per lane.
    random_seed: PRNG seed for reproducible stochastic noise and PRBS data.
    temperature_k: Operating temperature (Kelvin) for thermal noise checks.
    host_channel: Host-to-Optics (C2M) and Optics-to-Host (M2C) config.
    laser: O-band CW laser config.
    mzm: Line TX DSP and SiPh MZM config.
    fiber: SMF-28 fiber propagation config (up to 6 km).
    receiver: PIN PD, TIA, and Line RX DSP config.
    architecture: Module class. 'retimed': DSP in both directions. 'lro':
      DSP retimes the transmit direction only; the receive direction is
      linear and the host SerDes equalizes it (see LroConfig).
    lro: Linear receive path config, used when architecture is 'lro'.
    host_serdes: SerDes at the two host ASIC ends (host TX into the module,
      host RX out of it). 'generic' uses host_channel at every end, as the
      module's own client SerDes do. 'condor' models Broadcom Condor there
      (see CondorConfig); the module's client SerDes keep host_channel.
    condor: Condor host SerDes config, used when host_serdes is 'condor'.
  """

  num_lanes: int = 8
  baud_rate_gbaud: float = 106.25
  samples_per_symbol: int = 16
  num_symbols: int = 16384
  random_seed: int = 42
  temperature_k: float = 328.15  # 55 C module case temperature
  host_channel: HostChannelConfig = dataclasses.field(
      default_factory=HostChannelConfig
  )
  laser: LaserConfig = dataclasses.field(default_factory=LaserConfig)
  mzm: MzmConfig = dataclasses.field(default_factory=MzmConfig)
  fiber: FiberConfig = dataclasses.field(default_factory=FiberConfig)
  receiver: ReceiverConfig = dataclasses.field(default_factory=ReceiverConfig)
  architecture: str = 'retimed'
  lro: LroConfig = dataclasses.field(default_factory=LroConfig)
  host_serdes: str = 'generic'
  condor: CondorConfig = dataclasses.field(default_factory=CondorConfig)

  def __post_init__(self):
    self.validate()

  def validate(self) -> None:
    """Raises ValueError for settings the simulator cannot run."""
    if self.architecture not in ARCHITECTURES:
      raise ValueError(
          f'architecture must be one of {ARCHITECTURES}, '
          f'got {self.architecture!r}'
      )
    if (self.laser.rin_oma_db_hz is not None
        and self.mzm.target_outer_er_db <= 0):
      raise ValueError(
          'laser.rin_oma_db_hz needs mzm.target_outer_er_db > 0 to convert '
          'to laser RIN'
      )
    if self.host_serdes not in HOST_SERDES_MODELS:
      raise ValueError(
          f'host_serdes must be one of {HOST_SERDES_MODELS}, '
          f'got {self.host_serdes!r}'
      )
    self.condor.validate()

  @property
  def effective_laser_rin_db_hz(self) -> float:
    """Laser RIN applied in the simulation (from RIN_OMA if that is set)."""
    if self.laser.rin_oma_db_hz is not None:
      return rin_oma_to_laser_rin(
          self.laser.rin_oma_db_hz, self.mzm.target_outer_er_db
      )
    return self.laser.rin_db_hz

  @property
  def rin_oma_db_hz(self) -> float | None:
    """RIN_OMA equivalent of the applied laser RIN at the target outer ER."""
    if self.laser.rin_oma_db_hz is not None:
      return self.laser.rin_oma_db_hz
    if self.mzm.target_outer_er_db <= 0:
      return None
    return laser_rin_to_rin_oma(
        self.laser.rin_db_hz, self.mzm.target_outer_er_db
    )

  @property
  def symbol_period_s(self) -> float:
    """Returns the PAM4 symbol period T in seconds."""
    return 1.0 / (self.baud_rate_gbaud * 1e9)

  @property
  def sample_rate_hz(self) -> float:
    """Returns the simulation waveform sampling frequency in Hz."""
    return self.baud_rate_gbaud * 1e9 * self.samples_per_symbol

  @property
  def dt_s(self) -> float:
    """Returns the simulation time step dt in seconds."""
    return 1.0 / self.sample_rate_hz

  @property
  def nyquist_freq_ghz(self) -> float:
    """Returns the Nyquist frequency (baud_rate / 2) in GHz."""
    return 0.5 * self.baud_rate_gbaud

  @property
  def net_bit_rate_gbps_per_lane(self) -> float:
    """Returns the nominal net payload rate per lane (200 Gbps)."""
    return 200.0

  @property
  def aggregate_bit_rate_tbps(self) -> float:
    """Returns the aggregate 8-lane throughput in Tbps (1.6 Tbps)."""
    return (self.num_lanes * self.net_bit_rate_gbps_per_lane) / 1000.0


@dataclasses.dataclass
class SpecLimits:
  """Pass/fail limits applied to each lane of a DR8 module simulation.

  Defaults are placeholders in the range of IEEE 802.3dj 200G/lane DR PMD
  limits. Check them against the spec revision you are designing to.

  Attributes:
    tdecq_max_db: Maximum TDECQ at the simulated reach (dB).
    er_min_db: Minimum outer extinction ratio (dB).
    rlm_min: Minimum PAM4 level-mismatch ratio R_LM.
    pre_fec_ber_max: Maximum end-to-end pre-FEC BER.
    link_margin_min_db: Minimum net link margin (dB). Only checked when the
      link budget was computed.
  """

  tdecq_max_db: float = 3.4
  er_min_db: float = 3.5
  rlm_min: float = 0.90
  pre_fec_ber_max: float = 2.4e-4
  link_margin_min_db: float = 0.0


@dataclasses.dataclass
class LaneSpread:
  """Gaussian lane-to-lane manufacturing spread for Monte Carlo DR8 runs.

  Each sigma is applied independently per lane as an additive offset to the
  corresponding base-config parameter. A sigma of 0 disables that spread.

  Attributes:
    cw_power_sigma_db: Laser CW power spread (dB).
    wavelength_sigma_nm: Laser wavelength spread (nm).
    er_sigma_db: MZM target outer ER spread (dB).
    bias_error_sigma_deg: MZM bias-point error spread (deg).
    mzm_il_sigma_db: MZM insertion loss spread (dB).
    connector_loss_sigma_db: Per-fiber MPO connector loss spread (dB).
    tia_irnd_sigma_pa: TIA input-referred noise density spread (pA/sqrt(Hz)).
  """

  cw_power_sigma_db: float = 0.3
  wavelength_sigma_nm: float = 1.0
  er_sigma_db: float = 0.2
  bias_error_sigma_deg: float = 1.0
  mzm_il_sigma_db: float = 0.3
  connector_loss_sigma_db: float = 0.15
  tia_irnd_sigma_pa: float = 1.0


# LaneSpread attribute -> dotted LinkSimulationConfig key it perturbs.
LANE_SPREAD_KEYS = {
    'cw_power_sigma_db': 'laser.cw_power_dbm',
    'wavelength_sigma_nm': 'laser.wavelength_error_nm',
    'er_sigma_db': 'mzm.target_outer_er_db',
    'bias_error_sigma_deg': 'mzm.bias_error_deg',
    'mzm_il_sigma_db': 'mzm.insertion_loss_db',
    'connector_loss_sigma_db': 'fiber.connector_loss_db',
    'tia_irnd_sigma_pa': 'receiver.tia_irnd_pa_per_sqrt_hz',
}


@dataclasses.dataclass
class ModuleConfig:
  """Configuration for a full 8-lane 1.6T-DR8 module simulation.

  Every lane starts from `lane`. `lane_overrides[i]` maps dotted config keys
  (e.g. 'laser.cw_power_dbm') to values for lane i, applied after any Monte
  Carlo spread.

  Attributes:
    lane: Base single-lane link configuration shared by all lanes.
    lane_overrides: Per-lane {dotted_key: value} overrides, keyed by lane
      index as a string (JSON object keys are strings).
    monte_carlo: If True, perturbs each lane with `spread`.
    spread: Lane-to-lane Gaussian spread used when `monte_carlo` is True.
    spec: Per-lane pass/fail limits.
    lane_seed_stride: Lane i uses random_seed + i * lane_seed_stride, so lanes
      see independent data and noise.
  """

  lane: LinkSimulationConfig = dataclasses.field(
      default_factory=LinkSimulationConfig
  )
  lane_overrides: Dict[str, Dict[str, Any]] = dataclasses.field(
      default_factory=dict
  )
  monte_carlo: bool = False
  spread: LaneSpread = dataclasses.field(default_factory=LaneSpread)
  spec: SpecLimits = dataclasses.field(default_factory=SpecLimits)
  lane_seed_stride: int = 1000


def to_dict(cfg: Any) -> Dict[str, Any]:
  """Converts a config dataclass (recursively) to a JSON-friendly dict."""
  out = {}
  for field in dataclasses.fields(cfg):
    value = getattr(cfg, field.name)
    if dataclasses.is_dataclass(value):
      value = to_dict(value)
    elif isinstance(value, tuple):
      value = list(value)
    out[field.name] = value
  return out


# Fields whose value may legitimately be None (Optional in the dataclasses).
_OPTIONAL_KEYS = {
    'override_dispersion_ps_nm_km', 'total_channel_loss_db', 'rin_oma_db_hz',
}


def _coerce(current: Any, value: Any, key: str) -> Any:
  """Coerces `value` to the type of the existing field value `current`."""
  field_name = key.rsplit('.', 1)[-1]
  if value is None or (
      isinstance(value, str) and value.lower() in ('none', 'null')
  ):
    if current is None or field_name in _OPTIONAL_KEYS:
      return None
    raise ValueError(f'{key} cannot be None')
  if isinstance(current, bool):
    if isinstance(value, str):
      if value.lower() in ('true', '1', 'yes'):
        return True
      if value.lower() in ('false', '0', 'no'):
        return False
      raise ValueError(f'{key}: expected a boolean, got {value!r}')
    return bool(value)
  if isinstance(current, int):
    try:
      as_float = float(value)
    except (TypeError, ValueError):
      raise ValueError(f'{key}: expected an integer, got {value!r}') from None
    if not as_float.is_integer():
      raise ValueError(f'{key}: expected an integer, got {value!r}')
    return int(as_float)
  if isinstance(current, float) or current is None:
    try:
      return float(value)
    except (TypeError, ValueError):
      raise ValueError(f'{key}: expected a number, got {value!r}') from None
  if isinstance(current, tuple):
    if isinstance(value, (str, bytes)) or not hasattr(value, '__iter__'):
      raise ValueError(f'{key}: expected a list, got {value!r}')
    try:
      return tuple(float(v) for v in value)
    except (TypeError, ValueError):
      raise ValueError(f'{key}: expected a list of numbers') from None
  if isinstance(current, dict):
    return dict(value)
  return value


def update_from_dict(cfg: Any, values: Dict[str, Any], prefix: str = '') -> Any:
  """Updates a config dataclass in place from a (possibly partial) dict.

  Unknown keys raise ValueError so typos in config files are not silently
  ignored.

  Returns:
    The same `cfg` object, for chaining.
  """
  names = {f.name for f in dataclasses.fields(cfg)}
  for key, value in values.items():
    full_key = f'{prefix}{key}'
    if key not in names:
      raise ValueError(f'Unknown config key: {full_key}')
    current = getattr(cfg, key)
    if dataclasses.is_dataclass(current):
      if not isinstance(value, dict):
        raise ValueError(f'{full_key}: expected an object')
      update_from_dict(current, value, prefix=f'{full_key}.')
    else:
      setattr(cfg, key, _coerce(current, value, full_key))
  if hasattr(cfg, 'validate'):
    cfg.validate()
  return cfg


def set_by_path(cfg: Any, dotted_key: str, value: Any) -> None:
  """Sets a nested config field by dotted path, e.g. 'laser.rin_db_hz'."""
  parts = dotted_key.split('.')
  update = value
  for part in reversed(parts):
    update = {part: update}
  update_from_dict(cfg, update)


def get_by_path(cfg: Any, dotted_key: str) -> Any:
  """Reads a nested config field by dotted path."""
  obj = cfg
  for part in dotted_key.split('.'):
    if not dataclasses.is_dataclass(obj) or not hasattr(obj, part):
      raise ValueError(f'Unknown config key: {dotted_key}')
    obj = getattr(obj, part)
  return obj


def parse_override(text: str) -> Tuple[str, Any]:
  """Parses a 'dotted.key=value' string. Values are decoded as JSON if valid."""
  if '=' not in text:
    raise ValueError(f'Override must look like key=value, got {text!r}')
  key, raw = text.split('=', 1)
  try:
    value = json.loads(raw)
  except json.JSONDecodeError:
    value = raw
  return key.strip(), value


def apply_overrides(cfg: Any, overrides: Sequence[str]) -> Any:
  """Applies a list of 'dotted.key=value' overrides to `cfg` in place."""
  for text in overrides:
    key, value = parse_override(text)
    set_by_path(cfg, key, value)
  return cfg


def load_json(path: str, cfg: Any) -> Any:
  """Loads a JSON config file on top of the defaults already in `cfg`."""
  with open(path, encoding='utf-8') as f:
    return update_from_dict(cfg, json.load(f))


def save_json(cfg: Any, path: str) -> None:
  """Writes `cfg` as an indented JSON file."""
  with open(path, 'w', encoding='utf-8') as f:
    json.dump(to_dict(cfg), f, indent=2)
    f.write('\n')
