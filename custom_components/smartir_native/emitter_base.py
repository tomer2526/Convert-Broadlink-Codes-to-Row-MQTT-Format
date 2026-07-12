"""Shared emitter helpers for SmartIR Native entities."""

from typing import Any

from infrared_protocols.commands import Command

from homeassistant.components.infrared import InfraredEmitterConsumerEntity
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import Event, EventStateChangedData, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_state_change_event


class StoredRawCommand(Command):
    """An IR command backed by signed microsecond timings."""

    def __init__(self, timings: list[int]) -> None:
        """Initialize a 38 kHz raw timing command."""
        super().__init__(modulation=38000)
        self._timings = timings

    def get_raw_timings(self) -> list[int]:
        """Return alternating positive pulse and negative space timings."""
        return self._timings


def normalize_key(value: str) -> str:
    """Normalize key text for tolerant SmartIR command lookups."""
    return "".join(char.lower() for char in value if char.isalnum())


def find_command_value(node: Any, *aliases: str) -> Any:
    """Find a command in a dict by direct or normalized alias."""
    if not isinstance(node, dict):
        return None
    for alias in aliases:
        if alias in node:
            return node[alias]
    lookup = {normalize_key(str(key)): key for key in node}
    for alias in aliases:
        if key := lookup.get(normalize_key(alias)):
            return node[key]
    return None


class SmartIrNativeEmitterEntity(InfraredEmitterConsumerEntity):
    """Base emitter consumer with common availability and send helpers."""

    _attr_assumed_state = True
    _attr_has_entity_name = True
    _attr_name = None
    _attr_should_poll = False

    def __init__(self, infrared_emitter_entity_id: str) -> None:
        """Initialize emitter state tracking."""
        self._infrared_emitter_entity_id = infrared_emitter_entity_id
        self._emitter_available = True

    async def async_added_to_hass(self) -> None:
        """Track emitter availability whenever the entity is added."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_track_state_change_event(
                self.hass,
                [self._infrared_emitter_entity_id],
                self._emitter_state_changed,
            )
        )
        self._refresh_emitter_availability()

    @property
    def available(self) -> bool:
        """Mark this entity unavailable when its emitter is unavailable."""
        return super().available and self._emitter_available

    @callback
    def _emitter_state_changed(self, _event: Event[EventStateChangedData]) -> None:
        """Update availability when the configured emitter state changes."""
        self._refresh_emitter_availability()
        self.async_write_ha_state()

    @callback
    def _refresh_emitter_availability(self) -> None:
        """Cache current emitter availability from Home Assistant state."""
        emitter_state = self.hass.states.get(self._infrared_emitter_entity_id)
        self._emitter_available = (
            emitter_state is not None and emitter_state.state != STATE_UNAVAILABLE
        )

    async def _send_value(self, value: Any) -> None:
        """Send one timing array or a sequence of timing arrays."""
        if not isinstance(value, list) or not value:
            raise HomeAssistantError("Profile command payload is invalid")
        if all(isinstance(item, int) and not isinstance(item, bool) for item in value):
            commands = [value]
        elif all(
            isinstance(item, list)
            and item
            and all(
                isinstance(nested, int) and not isinstance(nested, bool)
                for nested in item
            )
            for item in value
        ):
            commands = value
        else:
            raise HomeAssistantError("Profile command payload is invalid")
        for timings in commands:
            await self._send_command(StoredRawCommand(timings))
