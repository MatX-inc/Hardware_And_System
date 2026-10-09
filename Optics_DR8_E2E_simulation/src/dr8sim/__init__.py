"""1.6T-DR8 (8x200G PAM4) end-to-end optical link simulator."""

from dr8sim.config import LinkSimulationConfig
from dr8sim.config import ModuleConfig
from dr8sim.module import run_module
from dr8sim.simulator import run_end_to_end_simulation
from dr8sim.simulator import sweep_fiber_reach
from dr8sim.simulator import sweep_parameter

__version__ = '0.1.0'

__all__ = [
    'LinkSimulationConfig',
    'ModuleConfig',
    'run_end_to_end_simulation',
    'run_module',
    'sweep_fiber_reach',
    'sweep_parameter',
]
