"""Tests for metrology helpers (R_LM, sensitivity interpolation)."""

import math

import numpy as np

from dr8sim import metrology


def test_rlm_is_one_for_equally_spaced_levels():
  assert metrology.compute_pam4_rlm(np.array([0.0, 1.0, 2.0, 3.0])) == 1.0


def test_rlm_drops_for_compressed_inner_levels():
  assert metrology.compute_pam4_rlm(np.array([0.0, 1.2, 1.8, 3.0])) < 1.0


def test_interpolation_hits_target_inside_waterfall():
  powers = np.array([-12.0, -10.0, -8.0, -6.0])
  bers = np.array([1e-2, 1e-3, 1e-4, 1e-5])
  p = metrology._interpolate_power_at_target_ber(powers, bers, 1e-3)  # pylint: disable=protected-access
  assert p == -10.0


def test_interpolation_is_nan_when_target_not_bracketed():
  powers = np.array([-8.0, -6.0, -4.0])
  bers = np.array([1e-5, 1e-6, 1e-7])  # never reaches 2.4e-4
  p = metrology._interpolate_power_at_target_ber(powers, bers, 2.4e-4)  # pylint: disable=protected-access
  assert math.isnan(p)
