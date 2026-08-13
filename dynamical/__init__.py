"""Dynamical public API."""

from importlib.metadata import PackageNotFoundError, version

from .compiler import CompileResult, compile_facility, validate_compiled_world
from .schema import FacilityDocument, load_facility_manifest

__all__ = [
    "CompileResult",
    "FacilityDocument",
    "compile_facility",
    "load_facility_manifest",
    "validate_compiled_world",
]

try:
    __version__ = version("dynamical-cli")
except PackageNotFoundError:  # pragma: no cover - source tree without installed metadata
    __version__ = "0+unknown"
