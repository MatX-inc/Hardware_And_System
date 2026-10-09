"""Shared fixtures for dr8sim tests."""

import numpy as np
import pytest

from dr8sim import config


@pytest.fixture
def small_cfg():
  """A short (2048-symbol) single-lane config for fast tests."""
  return config.LinkSimulationConfig(num_symbols=2048, random_seed=42)


@pytest.fixture
def rng():
  return np.random.default_rng(123)
