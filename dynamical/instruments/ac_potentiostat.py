"""Constant-current electrodeposition, modelled by Faraday's law.

Instrument physics only. No objective, no experiment order, no stopping rule.
Operating point and electrode area come from the admitted AC SDL1 source; the
model is an idealization with declared uncertainty and is not fitted to, or
validated against, any measured dataset. The AC SDL1 archive contains no
experimental electrodeposition data at all, so nothing here is described as
fitted or validated.
"""

from __future__ import annotations

from ..reasons import RuntimeReason
from . import InstrumentRequest, InstrumentResult, register

FARADAY_C_PER_MOL = 96485.33212
NICKEL_MOLAR_MASS_G = 58.6934
NICKEL_VALENCE = 2
NICKEL_DENSITY_G_CM3 = 8.908
ELECTRODE_AREA_CM2 = 0.2827

# Source-verified operating point: 2.827 mA = 0.010 A/cm^2 * 0.2827 cm^2.
# The envelope permits currents up to that same 0.010 A/cm^2-equivalent
# figure; anything higher is unverified extrapolation and is flagged, not
# silently accepted.
CURRENT_MIN_A = 0.0
CURRENT_MAX_A = 0.010
DURATION_MAX_S = 3600.0

# Relative standard uncertainty on deposited mass. Declared, not measured:
# it bounds current-source tolerance and current efficiency below unity.
MASS_RELATIVE_UNCERTAINTY = 0.05


@register("electrodeposit-constant-current", "ac-squidstat-simulator")
def electrodeposit(request: InstrumentRequest) -> InstrumentResult:
    current = float(request.parameters["current_a"])
    duration = float(request.parameters["duration_s"])
    reasons: list[RuntimeReason] = []
    if not CURRENT_MIN_A <= current <= CURRENT_MAX_A:
        reasons.append(
            RuntimeReason(
                code="PARAMETER_OUT_OF_ENVELOPE",
                detail=(
                    f"current {current} A is outside the admitted envelope "
                    f"[{CURRENT_MIN_A}, {CURRENT_MAX_A}] A"
                ),
                channel_id="instrument.current_a",
                recoverable=True,
            )
        )
    if duration > DURATION_MAX_S:
        reasons.append(
            RuntimeReason(
                code="PARAMETER_OUT_OF_ENVELOPE",
                detail=f"duration {duration} s exceeds {DURATION_MAX_S} s",
                channel_id="instrument.duration_s",
                recoverable=True,
            )
        )
    charge = current * duration
    mass = charge * NICKEL_MOLAR_MASS_G / (NICKEL_VALENCE * FARADAY_C_PER_MOL)
    thickness_cm = mass / (NICKEL_DENSITY_G_CM3 * ELECTRODE_AREA_CM2)
    outputs = {
        "charge_c": charge,
        "deposited_mass_g": mass,
        "deposited_thickness_um": thickness_cm * 1.0e4,
        "current_density_a_cm2": current / ELECTRODE_AREA_CM2,
    }
    # Write the deposit onto the sample so a downstream measurement is evidence
    # about this film rather than about its own requested parameters. The
    # nominal precursor composition is carried forward from the electrolyte
    # volumes upstream dispenses accumulated: metal fractions are normalized
    # over the seven admitted metals, and complexing-agent levels are recorded
    # relative to the total metal volume (the volume-ratio analogue of the
    # AMPERE-2 recipe units). Nothing is written when the run produced no
    # sample to act on; an electrolyte with no recorded composition leaves the
    # deposit's composition unrecorded rather than invented.
    deposited = None
    if request.sample is not None:
        state = {**request.sample.state, **outputs}
        metals = ("Ni", "Fe", "Cr", "Mn", "Co", "Zn", "Cu")
        volumes = {metal: state.get(f"electrolyte.{metal}_ml", 0.0) or 0.0 for metal in metals}
        total_metal_ml = sum(volumes.values())
        if total_metal_ml > 0.0:
            for metal in metals:
                state[f"deposited_fraction_{metal}"] = volumes[metal] / total_metal_ml
            for agent in ("NH4OH", "NaCi"):
                state[f"deposited_complexing_{agent}"] = (
                    state.get(f"electrolyte.{agent}_ml", 0.0) or 0.0
                ) / total_metal_ml
        deposited = request.sample.model_copy(update={"state": state})
    return InstrumentResult(
        outputs=outputs,
        sample=deposited,
        uncertainty={
            "deposited_mass_g": mass * MASS_RELATIVE_UNCERTAINTY,
            "deposited_thickness_um": thickness_cm * 1.0e4 * MASS_RELATIVE_UNCERTAINTY,
        },
        cost_usd=0.0,
        duration_s=duration,
        reasons=reasons,
    )
