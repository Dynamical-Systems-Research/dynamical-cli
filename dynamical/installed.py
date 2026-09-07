"""Installed facility bundles. Selecting a bundle never grants proposal authority."""

from importlib.resources import files
from pathlib import Path

SDL1 = Path(str(files("dynamical").joinpath("bundle", "reference-lab")))
FASTCAT = Path(str(files("dynamical").joinpath("bundle", "fastcat")))
ALIASES = {"sdl1": SDL1, "fastcat": FASTCAT}


def facility_bundle(facility_id: str) -> Path:
    # Unknown IDs still face the existing full-record authority comparison.
    return FASTCAT if facility_id == "dtu-fastcat" else SDL1


def facility_path(value: Path) -> Path:
    root = ALIASES.get(str(value))
    if root is not None:
        return root / "facility.yaml"
    if not value.is_file():
        raise ValueError(
            f"unknown facility {str(value)!r}; installed facilities: sdl1, fastcat; "
            "or supply a manifest path"
        )
    return value


def requirement_facility(requirement: Path) -> Path:
    """Select by declared station membership, never by requested provider authority."""
    from .schema import load_campaign_requirement, load_facility_manifest

    ids = {
        item.facility_id
        for item in load_campaign_requirement(requirement).inputs
        if item.facility_id
    }
    matches = set()
    for alias, root in ALIASES.items():
        document = load_facility_manifest(root / "facility.yaml")
        if ids & {station.id for station in document.workstations}:
            matches.add(alias)
    if len(matches) > 1:
        raise ValueError(
            "requirement names stations in multiple installed facilities; split the campaign "
            "or explicitly select --facility sdl1 or --facility fastcat"
        )
    return ALIASES[next(iter(matches))] / "facility.yaml" if matches else SDL1 / "facility.yaml"


def registry_path(value: Path | None, facility: Path) -> Path:
    if value is not None:
        root = ALIASES.get(str(value))
        return root / "registry.yaml" if root is not None else value
    from .schema import load_facility_manifest

    document = load_facility_manifest(facility)
    return facility_bundle(document.facility.id) / "registry.yaml"
