"""Decode and validate portable SmartIR Native profile codes."""

import base64
import binascii
import json
import zlib
from typing import Any

PROFILE_PREFIX = "IRP1:"
PROFILE_FORMAT = "smartir-native-profile"
MAX_COMPRESSED_SIZE = 2 * 1024 * 1024
MAX_DECOMPRESSED_SIZE = 16 * 1024 * 1024
MAX_TIMING = 10_000_000
VALID_HVAC_MODES = {"auto", "cool", "dry", "fan_only", "heat", "heat_cool"}


class InvalidProfile(ValueError):
    """Raised when a profile code is invalid or unsupported."""


def decode_profile_code(profile_code: str) -> dict[str, Any]:
    """Decode a versioned, gzip-compressed SmartIR Native profile."""
    clean = "".join(profile_code.split())
    if not clean.startswith(PROFILE_PREFIX):
        raise InvalidProfile("Unsupported profile code version")
    encoded = clean.removeprefix(PROFILE_PREFIX)
    if not encoded or len(encoded) > (MAX_COMPRESSED_SIZE * 4 // 3) + 8:
        raise InvalidProfile("Profile code is empty or too large")

    try:
        compressed = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as err:
        raise InvalidProfile("Profile code is not valid base64") from err

    if not compressed or len(compressed) > MAX_COMPRESSED_SIZE:
        raise InvalidProfile("Profile code is empty or too large")

    decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        raw = decompressor.decompress(compressed, MAX_DECOMPRESSED_SIZE + 1)
    except zlib.error as err:
        raise InvalidProfile("Profile code could not be decompressed") from err
    if (
        len(raw) > MAX_DECOMPRESSED_SIZE
        or decompressor.unconsumed_tail
        or decompressor.unused_data
        or not decompressor.eof
    ):
        raise InvalidProfile("Decompressed profile is too large or incomplete")

    try:
        profile = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as err:
        raise InvalidProfile("Profile does not contain valid JSON") from err

    if (
        not isinstance(profile, dict)
        or profile.get("format") != PROFILE_FORMAT
        or profile.get("version") != 1
        or not isinstance(profile.get("device"), dict)
    ):
        raise InvalidProfile("Profile structure or version is not supported")

    device = profile["device"]
    _validate_device(device)
    return device


def profile_name(device: dict[str, Any]) -> str:
    """Build a useful default entity name from profile metadata."""
    manufacturer = str(device.get("manufacturer") or "SmartIR").strip()
    models = device.get("supportedModels") or []
    model = str(models[0]).strip() if models else "Climate"
    return f"{manufacturer} {model}".strip()


def _validate_device(device: dict[str, Any]) -> None:
    """Validate the SmartIR metadata and command tree used by the entity."""
    for key in ("supportedModels", "operationModes", "fanModes"):
        value = device.get(key)
        if not isinstance(value, list) or not value:
            raise InvalidProfile(f"Missing or empty field: {key}")
        if not all(isinstance(item, str) and item for item in value):
            raise InvalidProfile(f"Invalid values in field: {key}")

    if not set(device["operationModes"]).issubset(VALID_HVAC_MODES):
        raise InvalidProfile("Profile contains an unsupported HVAC mode")
    for key in ("minTemperature", "maxTemperature", "precision"):
        if isinstance(device.get(key), bool) or not isinstance(
            device.get(key), int | float
        ):
            raise InvalidProfile(f"Missing or invalid field: {key}")
    if device["minTemperature"] >= device["maxTemperature"]:
        raise InvalidProfile("Minimum temperature must be below maximum temperature")
    if (swing_modes := device.get("swingModes")) is not None and (
        not isinstance(swing_modes, list)
        or not swing_modes
        or not all(isinstance(item, str) and item for item in swing_modes)
    ):
        raise InvalidProfile("Profile contains invalid swing modes")

    commands = device.get("commands")
    if not isinstance(commands, dict) or "off" not in commands:
        raise InvalidProfile("Profile must contain an off command")
    _validate_command_node(commands)


def _validate_command_node(node: Any) -> None:
    """Ensure every command leaf is a bounded signed timing array."""
    if isinstance(node, dict):
        if not node:
            raise InvalidProfile("Command tree contains an empty object")
        for value in node.values():
            _validate_command_node(value)
        return

    if not isinstance(node, list) or not node:
        raise InvalidProfile("Command tree contains an invalid value")
    if all(isinstance(value, int) and not isinstance(value, bool) for value in node):
        if any(value == 0 or abs(value) > MAX_TIMING for value in node):
            raise InvalidProfile("Timing array contains an invalid duration")
        return
    for value in node:
        _validate_command_node(value)
