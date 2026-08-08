"""Bounded two-zone thermal simulator for the 0.25 kg heater study.

The model resolves a well-mixed near-wall zone and a slower core zone.  It is
an engineering simulator, not a calibrated physical or chemical model.  The
source-backed 0.03375 kg Figure 5 simulation row remains a separate record and
is not used to fit or validate this 0.25 kg model.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

Scalar = str | int | float | bool

OPERATION_IDS = (
    "apply-thermal-program",
    "agitate-sample",
    "measure-plate-temperature",
    "measure-sample-temperature",
    "estimate-temperature-gradient",
    "measure-sample-mass",
    "measure-mixing-load",
    "estimate-reaction-progress",
    "measure-reaction-progress",
)
SOURCE_REFERENCE_TEMPERATURE_K = 309.5473922729492
SOURCE_REFERENCE_TIME_S = 930.308
SIMULATOR_ONLY_CLAIM = (
    "The 0.25 kg wall/core temperatures, instrument values, uncertainties, and "
    "reaction-progress proxy are simulator outputs. Dynamical defines the proxy as "
    "simulated core time above the 309.5473922729492 K Figure 5 reference-row "
    "temperature divided by that row's 930.308 s simulation timestamp. The source does "
    "not define this ratio as exposure or kinetics. It is not a measurement, kinetic fit, "
    "chemical-mechanism validation, physical scale-transfer evidence, or W2 evidence."
)
UNCERTAINTY_BASIS = (
    "Engineering one-sigma assumptions for simulator sensitivity only; not fitted "
    "measurement error or a calibrated confidence interval."
)

CHANNEL_UNITS: dict[str, str] = {
    "thermal.heat_input_W": "W",
    "thermal.heat_flow_to_sample_W": "W",
    "thermal.plate_temperature_K": "K",
    "thermal.sample_wall_temperature_K": "K",
    "thermal.sample_core_temperature_K": "K",
    "thermal.sample_temperature_K": "K",
    "thermal.reaction_progress_estimate": "1",
    "thermal.time_above_reference_temperature_s": "s",
    "instrument.heat_input_W": "W",
    "instrument.heat_flow_to_sample_W": "W",
    "instrument.agitation_rate_rpm": "rpm",
    "instrument.plate_temperature_K": "K",
    "instrument.sample_wall_temperature_K": "K",
    "instrument.sample_core_temperature_K": "K",
    "instrument.sample_temperature_K": "K",
    "instrument.sample_gradient_K": "K",
    "instrument.sample_mass_kg": "kg",
    "instrument.stirrer_torque_N_m": "N*m",
    "material.reaction_progress_estimate": "1",
    "simulator.uncertainty.heat_input_W": "W",
    "simulator.uncertainty.heat_flow_to_sample_W": "W",
    "simulator.uncertainty.agitation_rate_rpm": "rpm",
    "simulator.uncertainty.plate_temperature_K": "K",
    "simulator.uncertainty.sample_wall_temperature_K": "K",
    "simulator.uncertainty.sample_core_temperature_K": "K",
    "simulator.uncertainty.sample_temperature_K": "K",
    "simulator.uncertainty.sample_gradient_K": "K",
    "simulator.uncertainty.sample_mass_kg": "kg",
    "simulator.uncertainty.stirrer_torque_N_m": "N*m",
    "simulator.uncertainty.reaction_progress_estimate": "1",
    "simulator.scale_transfer_validated": "1",
    "simulator.uncertainty_basis": "1",
    "simulator.claim_boundary": "1",
}


def _finite(name: str, value: float) -> None:
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")


@dataclass(frozen=True)
class ThermalModelConfig:
    """Fixed 0.25 kg model assumptions in SI units."""

    sample_mass_kg: float = 0.25
    initial_sample_temperature_K: float = 298.15
    initial_plate_temperature_K: float = 298.15
    ambient_temperature_K: float = 298.15
    sample_specific_heat_J_per_kg_K: float = 4180.0
    wall_mass_fraction: float = 0.25
    plate_heat_capacity_J_per_K: float = 600.0
    maximum_heater_power_W: float = 600.0
    heater_control_gain_W_per_K: float = 25.0
    plate_to_wall_conductance_W_per_K: float = 20.0
    wall_core_conductance_W_per_K: float = 1.5
    agitation_conductance_W_per_K_per_rpm: float = 0.015
    plate_loss_W_per_K: float = 0.8
    wall_loss_W_per_K: float = 0.35
    core_loss_W_per_K: float = 0.15
    torque_coefficient_N_m_per_rpm: float = 3.0e-5
    torque_temperature_coefficient_per_K: float = 0.004

    def __post_init__(self) -> None:
        values = self.__dict__
        for name, value in values.items():
            _finite(name, value)
        if not math.isclose(self.sample_mass_kg, 0.25, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError("the bounded two-zone model requires exactly 0.25 kg")
        if not 0.0 < self.wall_mass_fraction < 1.0:
            raise ValueError("wall_mass_fraction must be between zero and one")
        positive = (
            "sample_specific_heat_J_per_kg_K",
            "plate_heat_capacity_J_per_K",
            "maximum_heater_power_W",
            "heater_control_gain_W_per_K",
            "plate_to_wall_conductance_W_per_K",
            "wall_core_conductance_W_per_K",
            "torque_coefficient_N_m_per_rpm",
        )
        if any(values[name] <= 0.0 for name in positive):
            raise ValueError("thermal capacities, rates, conductances, and gains must be positive")
        nonnegative = (
            "agitation_conductance_W_per_K_per_rpm",
            "plate_loss_W_per_K",
            "wall_loss_W_per_K",
            "core_loss_W_per_K",
            "torque_temperature_coefficient_per_K",
        )
        if any(values[name] < 0.0 for name in nonnegative):
            raise ValueError("thermal losses and sensitivity coefficients must be nonnegative")


@dataclass(frozen=True)
class ThermalControl:
    target_plate_temperature_K: float = 343.15
    agitation_rate_rpm: float = 300.0
    heater_enabled: bool = True

    def __post_init__(self) -> None:
        _finite("target_plate_temperature_K", self.target_plate_temperature_K)
        _finite("agitation_rate_rpm", self.agitation_rate_rpm)
        if not 303.15 <= self.target_plate_temperature_K <= 343.15:
            raise ValueError("target plate temperature must be in [303.15, 343.15] K")
        if not 0.0 <= self.agitation_rate_rpm <= 600.0:
            raise ValueError("agitation rate must be in [0, 600] rpm")


@dataclass(frozen=True)
class ThermalState:
    logical_time_s: float
    plate_temperature_K: float
    wall_temperature_K: float
    core_temperature_K: float
    time_above_reference_temperature_s: float
    reaction_progress_estimate: float


@dataclass(frozen=True)
class ThermalSnapshot:
    logical_time_s: float
    evidence_class: str
    quality: str
    channels: dict[str, float | bool | str]
    units: dict[str, str]
    claim_boundary: str


def initial_state(config: ThermalModelConfig | None = None) -> ThermalState:
    config = config or ThermalModelConfig()
    return ThermalState(
        logical_time_s=0.0,
        plate_temperature_K=config.initial_plate_temperature_K,
        wall_temperature_K=config.initial_sample_temperature_K,
        core_temperature_K=config.initial_sample_temperature_K,
        time_above_reference_temperature_s=0.0,
        reaction_progress_estimate=0.0,
    )


def _rates(
    state: ThermalState,
    control: ThermalControl,
    config: ThermalModelConfig,
) -> tuple[float, float, float, float, float]:
    heat_input_W = 0.0
    if control.heater_enabled:
        heat_input_W = min(
            config.maximum_heater_power_W,
            max(
                0.0,
                config.heater_control_gain_W_per_K
                * (control.target_plate_temperature_K - state.plate_temperature_K),
            ),
        )
    heat_to_wall_W = config.plate_to_wall_conductance_W_per_K * (
        state.plate_temperature_K - state.wall_temperature_K
    )
    internal_conductance = (
        config.wall_core_conductance_W_per_K
        + config.agitation_conductance_W_per_K_per_rpm * control.agitation_rate_rpm
    )
    heat_wall_to_core_W = internal_conductance * (
        state.wall_temperature_K - state.core_temperature_K
    )
    plate_rate = (
        heat_input_W
        - heat_to_wall_W
        - config.plate_loss_W_per_K * (state.plate_temperature_K - config.ambient_temperature_K)
    ) / config.plate_heat_capacity_J_per_K
    wall_capacity = (
        config.sample_mass_kg * config.wall_mass_fraction * config.sample_specific_heat_J_per_kg_K
    )
    core_capacity = (
        config.sample_mass_kg
        * (1.0 - config.wall_mass_fraction)
        * config.sample_specific_heat_J_per_kg_K
    )
    wall_rate = (
        heat_to_wall_W
        - heat_wall_to_core_W
        - config.wall_loss_W_per_K * (state.wall_temperature_K - config.ambient_temperature_K)
    ) / wall_capacity
    core_rate = (
        heat_wall_to_core_W
        - config.core_loss_W_per_K * (state.core_temperature_K - config.ambient_temperature_K)
    ) / core_capacity
    return (
        plate_rate,
        wall_rate,
        core_rate,
        heat_input_W,
        heat_to_wall_W,
    )


def advance_two_zone(
    state: ThermalState,
    control: ThermalControl,
    config: ThermalModelConfig,
    dt_s: float,
) -> ThermalState:
    """Advance one bounded explicit-Euler thermal step."""

    _finite("dt_s", dt_s)
    if not 0.0 < dt_s <= 1.0:
        raise ValueError("dt_s must be in (0, 1] s")
    plate_rate, wall_rate, core_rate, _, _ = _rates(state, control, config)
    next_core_temperature_K = state.core_temperature_K + core_rate * dt_s
    if state.core_temperature_K >= SOURCE_REFERENCE_TEMPERATURE_K:
        if next_core_temperature_K >= SOURCE_REFERENCE_TEMPERATURE_K:
            reference_increment_s = dt_s
        else:
            reference_increment_s = dt_s * (
                (state.core_temperature_K - SOURCE_REFERENCE_TEMPERATURE_K)
                / (state.core_temperature_K - next_core_temperature_K)
            )
    elif next_core_temperature_K > SOURCE_REFERENCE_TEMPERATURE_K:
        reference_increment_s = dt_s * (
            (next_core_temperature_K - SOURCE_REFERENCE_TEMPERATURE_K)
            / (next_core_temperature_K - state.core_temperature_K)
        )
    else:
        reference_increment_s = 0.0
    time_above_reference_s = state.time_above_reference_temperature_s + reference_increment_s
    progress = min(1.0, time_above_reference_s / SOURCE_REFERENCE_TIME_S)
    return ThermalState(
        logical_time_s=state.logical_time_s + dt_s,
        plate_temperature_K=state.plate_temperature_K + plate_rate * dt_s,
        wall_temperature_K=state.wall_temperature_K + wall_rate * dt_s,
        core_temperature_K=next_core_temperature_K,
        time_above_reference_temperature_s=time_above_reference_s,
        reaction_progress_estimate=progress,
    )


def observe_two_zone(
    state: ThermalState,
    control: ThermalControl,
    config: ThermalModelConfig,
) -> ThermalSnapshot:
    """Return instrument-like simulator observations and explicit uncertainties."""

    _, _, _, heat_input_W, heat_to_sample_W = _rates(state, control, config)
    sample_temperature_K = (
        config.wall_mass_fraction * state.wall_temperature_K
        + (1.0 - config.wall_mass_fraction) * state.core_temperature_K
    )
    gradient_K = state.wall_temperature_K - state.core_temperature_K
    torque_N_m = (
        config.torque_coefficient_N_m_per_rpm
        * control.agitation_rate_rpm
        * (
            1.0
            + config.torque_temperature_coefficient_per_K
            * max(0.0, sample_temperature_K - config.ambient_temperature_K)
        )
    )
    channels: dict[str, float | bool | str] = {
        "thermal.heat_input_W": heat_input_W,
        "thermal.heat_flow_to_sample_W": heat_to_sample_W,
        "thermal.plate_temperature_K": state.plate_temperature_K,
        "thermal.sample_wall_temperature_K": state.wall_temperature_K,
        "thermal.sample_core_temperature_K": state.core_temperature_K,
        "thermal.sample_temperature_K": sample_temperature_K,
        "thermal.reaction_progress_estimate": state.reaction_progress_estimate,
        "thermal.time_above_reference_temperature_s": state.time_above_reference_temperature_s,
        "instrument.heat_input_W": heat_input_W,
        "instrument.heat_flow_to_sample_W": heat_to_sample_W,
        "instrument.agitation_rate_rpm": control.agitation_rate_rpm,
        "instrument.plate_temperature_K": state.plate_temperature_K,
        "instrument.sample_wall_temperature_K": state.wall_temperature_K,
        "instrument.sample_core_temperature_K": state.core_temperature_K,
        "instrument.sample_temperature_K": sample_temperature_K,
        "instrument.sample_gradient_K": gradient_K,
        "instrument.sample_mass_kg": config.sample_mass_kg,
        "instrument.stirrer_torque_N_m": torque_N_m,
        "material.reaction_progress_estimate": state.reaction_progress_estimate,
        "simulator.uncertainty.heat_input_W": max(5.0, 0.08 * abs(heat_input_W)),
        "simulator.uncertainty.heat_flow_to_sample_W": max(2.0, 0.10 * abs(heat_to_sample_W)),
        "simulator.uncertainty.agitation_rate_rpm": max(2.0, 0.01 * control.agitation_rate_rpm),
        "simulator.uncertainty.plate_temperature_K": 0.75,
        "simulator.uncertainty.sample_wall_temperature_K": 1.5,
        "simulator.uncertainty.sample_core_temperature_K": 1.5,
        "simulator.uncertainty.sample_temperature_K": 1.2,
        "simulator.uncertainty.sample_gradient_K": math.sqrt(2.0) * 1.5,
        "simulator.uncertainty.sample_mass_kg": 0.0005,
        "simulator.uncertainty.stirrer_torque_N_m": max(0.001, 0.15 * torque_N_m),
        "simulator.uncertainty.reaction_progress_estimate": min(
            1.0, 0.08 + 0.25 * state.reaction_progress_estimate
        ),
        "simulator.scale_transfer_validated": False,
        "simulator.uncertainty_basis": UNCERTAINTY_BASIS,
        "simulator.claim_boundary": SIMULATOR_ONLY_CLAIM,
    }
    return ThermalSnapshot(
        logical_time_s=state.logical_time_s,
        evidence_class="simulator",
        quality="estimated",
        channels=channels,
        units=dict(CHANNEL_UNITS),
        claim_boundary=SIMULATOR_ONLY_CLAIM,
    )


def simulate_two_zone(
    duration_s: float,
    *,
    control: ThermalControl | None = None,
    config: ThermalModelConfig | None = None,
    dt_s: float = 0.5,
) -> ThermalSnapshot:
    """Execute the 0.25 kg model and return its final simulator observation."""

    _finite("duration_s", duration_s)
    _finite("dt_s", dt_s)
    if duration_s < 0.0:
        raise ValueError("duration_s must be nonnegative")
    if not 0.0 < dt_s <= 1.0:
        raise ValueError("dt_s must be in (0, 1] s")
    control = control or ThermalControl()
    config = config or ThermalModelConfig()
    state = initial_state(config)
    remaining = duration_s
    while remaining > 0.0:
        step_s = min(dt_s, remaining)
        state = advance_two_zone(state, control, config, step_s)
        remaining -= step_s
    return observe_two_zone(state, control, config)


def _number(values: Mapping[str, Scalar], name: str) -> float:
    value = values.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric")
    result = float(value)
    _finite(name, result)
    return result


def agitate_sample(
    inputs: Mapping[str, Scalar], parameters: Mapping[str, Scalar]
) -> dict[str, Scalar]:
    ThermalModelConfig(sample_mass_kg=_number(inputs, "material.mass_kg"))
    rate = _number(parameters, "agitation-rate")
    ThermalControl(agitation_rate_rpm=rate)
    return {
        "instrument.agitation_rate_rpm": rate,
        "simulator.uncertainty.agitation_rate_rpm": max(2.0, 0.01 * rate),
    }


def apply_thermal_program(
    inputs: Mapping[str, Scalar], parameters: Mapping[str, Scalar]
) -> dict[str, Scalar]:
    config = ThermalModelConfig(
        sample_mass_kg=_number(inputs, "material.mass_kg"),
        initial_sample_temperature_K=_number(inputs, "material.temperature_K"),
    )
    control = ThermalControl(
        target_plate_temperature_K=_number(parameters, "target-temperature"),
        agitation_rate_rpm=_number(inputs, "instrument.agitation_rate_rpm"),
    )
    snapshot = simulate_two_zone(_number(parameters, "dwell-time"), control=control, config=config)
    names = (
        "instrument.heat_input_W",
        "instrument.heat_flow_to_sample_W",
        "simulator.uncertainty.heat_input_W",
        "simulator.uncertainty.heat_flow_to_sample_W",
        "thermal.plate_temperature_K",
        "thermal.sample_wall_temperature_K",
        "thermal.sample_core_temperature_K",
        "thermal.sample_temperature_K",
        "thermal.reaction_progress_estimate",
        "thermal.time_above_reference_temperature_s",
    )
    return {name: snapshot.channels[name] for name in names}


def measure_plate_temperature(
    inputs: Mapping[str, Scalar], parameters: Mapping[str, Scalar]
) -> dict[str, Scalar]:
    del parameters
    value = _number(inputs, "thermal.plate_temperature_K")
    return {
        "instrument.plate_temperature_K": value,
        "simulator.uncertainty.plate_temperature_K": 0.75,
    }


def measure_sample_temperature(
    inputs: Mapping[str, Scalar], parameters: Mapping[str, Scalar]
) -> dict[str, Scalar]:
    del parameters
    wall = _number(inputs, "thermal.sample_wall_temperature_K")
    core = _number(inputs, "thermal.sample_core_temperature_K")
    sample = _number(inputs, "thermal.sample_temperature_K")
    return {
        "instrument.sample_wall_temperature_K": wall,
        "instrument.sample_core_temperature_K": core,
        "instrument.sample_temperature_K": sample,
        "simulator.uncertainty.sample_wall_temperature_K": 1.5,
        "simulator.uncertainty.sample_core_temperature_K": 1.5,
        "simulator.uncertainty.sample_temperature_K": 1.2,
    }


def estimate_temperature_gradient(
    inputs: Mapping[str, Scalar], parameters: Mapping[str, Scalar]
) -> dict[str, Scalar]:
    del parameters
    gradient = _number(inputs, "thermal.sample_wall_temperature_K") - _number(
        inputs, "thermal.sample_core_temperature_K"
    )
    return {
        "instrument.sample_gradient_K": gradient,
        "simulator.uncertainty.sample_gradient_K": math.sqrt(2.0) * 1.5,
    }


def measure_sample_mass(
    inputs: Mapping[str, Scalar], parameters: Mapping[str, Scalar]
) -> dict[str, Scalar]:
    del parameters
    mass = _number(inputs, "material.mass_kg")
    ThermalModelConfig(sample_mass_kg=mass)
    return {
        "instrument.sample_mass_kg": mass,
        "simulator.uncertainty.sample_mass_kg": 0.0005,
    }


def measure_mixing_load(
    inputs: Mapping[str, Scalar], parameters: Mapping[str, Scalar]
) -> dict[str, Scalar]:
    del parameters
    config = ThermalModelConfig()
    agitation = _number(inputs, "instrument.agitation_rate_rpm")
    sample_temperature = _number(inputs, "thermal.sample_temperature_K")
    torque = (
        config.torque_coefficient_N_m_per_rpm
        * agitation
        * (
            1.0
            + config.torque_temperature_coefficient_per_K
            * max(0.0, sample_temperature - config.ambient_temperature_K)
        )
    )
    return {
        "instrument.stirrer_torque_N_m": torque,
        "simulator.uncertainty.stirrer_torque_N_m": max(0.001, 0.15 * torque),
    }


def estimate_reaction_progress(
    inputs: Mapping[str, Scalar], parameters: Mapping[str, Scalar]
) -> dict[str, Scalar]:
    del parameters
    _number(inputs, "thermal.sample_core_temperature_K")
    time_above_reference_s = _number(inputs, "thermal.time_above_reference_temperature_s")
    progress = _number(inputs, "thermal.reaction_progress_estimate")
    expected_progress = min(1.0, time_above_reference_s / SOURCE_REFERENCE_TIME_S)
    if not math.isclose(progress, expected_progress, rel_tol=0.0, abs_tol=1e-9):
        raise ValueError("reaction-progress estimate must equal the reference-row proxy")
    if not 0.0 <= progress <= 1.0:
        raise ValueError("thermal.reaction_progress_estimate must be in [0, 1]")
    return {
        "material.reaction_progress_estimate": progress,
        "simulator.uncertainty.reaction_progress_estimate": min(1.0, 0.08 + 0.25 * progress),
    }


def measure_reaction_progress(
    inputs: Mapping[str, Scalar], parameters: Mapping[str, Scalar]
) -> dict[str, Scalar]:
    del inputs, parameters
    raise RuntimeError(
        "measure-reaction-progress has no admitted provider; physical execution must HOLD"
    )
