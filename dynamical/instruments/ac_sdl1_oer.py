"""Source-defined SDL1 OER protocol with unavailable physical responses.

The bundled protocol records SDL1 2c5a911 example/experiment.py:248-539,
205-227, and 1736-1767. It is a command/extraction specification, not a fitted
CV, impedance, staircase, or material-response model. Missing observations
and the unverified reference scale cannot produce a calibrated OER result.
"""

from __future__ import annotations

import hashlib
import json
from importlib import resources

from ..reasons import RuntimeReason
from . import InstrumentRequest, InstrumentResult, register

PROTOCOL_ID = "sdl1-oer-2c5a911"
PROTOCOL_RESOURCE = "bundle/reference-lab/protocols/sdl1-oer.json"
PROTOCOL_SHA256 = "5238b6230b5f5a13a8fc23f172fcb98276f10f2f7a10145a1d65fc9feb3bd9c2"


def _protocol_bytes() -> bytes:
    return resources.files("dynamical").joinpath(PROTOCOL_RESOURCE).read_bytes()


def load_protocol() -> dict:
    """Read only the frozen source profile; never accept a caller-supplied profile."""
    raw = _protocol_bytes()
    if hashlib.sha256(raw).hexdigest() != PROTOCOL_SHA256:
        raise ValueError("SDL1 source protocol differs from its frozen SHA256 binding")
    profile = json.loads(raw)
    if profile["protocol_id"] != PROTOCOL_ID:
        raise ValueError("SDL1 source protocol identity differs from its adapter")
    return profile


@register("measure-oer", "ac-sdl1-oer-protocol")
def measure_oer(request: InstrumentRequest) -> InstrumentResult:
    outputs = {
        "potential_at_10ma_cm2_v": None,
        "corrected_potential_at_10ma_cm2_v": None,
        "ohmic_resistance_ohm": None,
        "overpotential_v": None,
        "current_density_a_cm2": None,
    }
    reasons: list[RuntimeReason] = []
    if request.parameters != {"protocol_id": PROTOCOL_ID} or set(request.inputs) - {"sample.state"}:
        reasons.append(
            RuntimeReason(
                code="PARAMETER_OUT_OF_ENVELOPE",
                detail=(
                    "Only the fixed SDL1 protocol is admitted. Caller-provided resistance, "
                    "potential, current-density substitutions, or response inputs are not evidence."
                ),
                channel_id="instrument.protocol_id",
                recoverable=True,
            )
        )
    else:
        try:
            protocol = load_protocol()
        except (OSError, ValueError, KeyError, TypeError) as exc:
            reasons.append(
                RuntimeReason(
                    code="PROTOCOL_PROFILE_UNVERIFIED",
                    detail=str(exc),
                    channel_id="instrument.protocol_id",
                    recoverable=False,
                )
            )
        else:
            # This is the source command's headline selector, not observed current.
            outputs["current_density_a_cm2"] = protocol["headline"]["current_density_a_cm2"]
            reasons.extend(
                [
                    RuntimeReason(
                        code="PROTOCOL_RESPONSE_UNAVAILABLE",
                        detail=(
                            "The source-defined activation, CV, PEIS and staircase sequence has no "
                            "admitted SDL1 response model. Resistance and "
                            "last-third potentials remain unavailable; no physical steps executed."
                        ),
                        channel_id="corrected_potential_at_10ma_cm2_v",
                        recoverable=False,
                    ),
                    RuntimeReason(
                        code="REFERENCE_SCALE_UNVERIFIED",
                        detail=(
                            "SDL1 executable code does not establish the potential reference "
                            "scale or subtract equilibrium potential. An overpotential offset is "
                            "a Dynamical convention, not an upstream measured result."
                        ),
                        channel_id="overpotential_v",
                        recoverable=False,
                    ),
                ]
            )
    return InstrumentResult(
        outputs=outputs,
        uncertainty={},
        cost_usd=0.0,
        duration_s=550.0,
        applied_parameters={name: None for name in request.parameters},
        reasons=reasons,
    )
