"""Compatibility alias: this module moved to :mod:`panopti.witness`."""

from panopti.witness import WitnessChain, WitnessEntry, canonical_json_bytes, sha256_hex

__all__ = ["WitnessChain", "WitnessEntry", "canonical_json_bytes", "sha256_hex"]
