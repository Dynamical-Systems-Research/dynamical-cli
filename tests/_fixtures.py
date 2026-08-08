from __future__ import annotations

from pathlib import Path

import yaml

REFERENCE_REQUIREMENT = {
    "document_type": "dynamical.campaign-requirement",
    "schema_version": "0.1.0",
    "requirement_id": "heated-beaker-source-and-embodied-proof",
    "objective": {
        "id": "select-and-check-heated-beaker-condition",
        "statement": "Run one bounded virtual thermal program.",
        "decision": "Decide if the virtual result merits a later physical experiment.",
        "proof_requirements": [
            {
                "id": "thermal-program-proof",
                "operation_id": "apply-thermal-program",
                "output_port_ids": ["thermal.sample_temperature_K"],
                "minimum_evidence_class": "simulator",
                "acceptance_rule": "The simulator trace and replay pass.",
                "independent_verification_required": True,
            },
        ],
    },
    "inputs": [
        {
            "id": "material.mass_kg",
            "state_type": "number",
            "unit": "kg",
            "value": 0.25,
            "facility_id": "matterix-heater-facility",
        },
        {
            "id": "material.temperature_K",
            "state_type": "number",
            "unit": "K",
            "value": 298.15,
            "facility_id": "matterix-heater-facility",
        },
    ],
    "steps": [
        {
            "step_id": "agitate",
            "operation_id": "agitate-sample",
            "minimum_evidence_class": "simulator",
            "parameters": [
                {
                    "name": "agitation-rate",
                    "value_type": "number",
                    "unit": "rpm",
                    "value": 300.0,
                }
            ],
            "input_bindings": [
                {
                    "target_port_id": "material.mass_kg",
                    "source_kind": "campaign_input",
                    "source_id": "material.mass_kg",
                }
            ],
            "depends_on": [],
            "required_policy_tags": ["simulation-only"],
        },
        {
            "step_id": "heat",
            "operation_id": "apply-thermal-program",
            "minimum_evidence_class": "simulator",
            "parameters": [
                {
                    "name": "target-temperature",
                    "value_type": "number",
                    "unit": "K",
                    "value": 343.15,
                },
                {
                    "name": "dwell-time",
                    "value_type": "number",
                    "unit": "s",
                    "value": 10.0,
                },
            ],
            "input_bindings": [
                {
                    "target_port_id": "material.mass_kg",
                    "source_kind": "campaign_input",
                    "source_id": "material.mass_kg",
                },
                {
                    "target_port_id": "material.temperature_K",
                    "source_kind": "campaign_input",
                    "source_id": "material.temperature_K",
                },
                {
                    "target_port_id": "instrument.agitation_rate_rpm",
                    "source_kind": "step_output",
                    "source_id": "agitate",
                    "source_port_id": "instrument.agitation_rate_rpm",
                },
            ],
            "depends_on": ["agitate"],
            "required_policy_tags": ["simulation-only", "scale-transfer-unvalidated"],
        },
    ],
    "max_cost_usd": 0.0,
    "max_duration_s": 1200.0,
}


def write_reference_requirement(path: Path) -> Path:
    path.write_text(yaml.safe_dump(REFERENCE_REQUIREMENT, sort_keys=False), encoding="utf-8")
    return path
